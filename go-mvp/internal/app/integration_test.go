package app

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func realDB(t *testing.T) (Database, *atomic.Int64) {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL 未设置，跳过真实 PostgreSQL 集成测试")
	}
	ctx := context.Background()
	control, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	schema := "mvp_test_" + NewID()
	if _, err = control.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatal(err)
	}
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		pool.Close()
		if _, e := control.Exec(ctx, "DROP SCHEMA "+schema+" CASCADE"); e != nil {
			t.Error(e)
		}
		control.Close()
	})
	sql, err := os.ReadFile("../../db/schema.sql")
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if _, err = pool.Exec(ctx, string(sql)); err != nil {
			t.Fatal(err)
		}
	}
	clock := new(atomic.Int64)
	clock.Store(base)
	db := Database{pool, func() time.Time { return time.UnixMicro(clock.Load()) }}
	if err = db.Seed(ctx); err != nil {
		t.Fatal(err)
	}
	if err = db.Seed(ctx); err != nil {
		t.Fatal(err)
	}
	return db, clock
}
func mutation(t *testing.T, db Database, a Actor, action string, f url.Values) string {
	t.Helper()
	if f == nil {
		f = url.Values{}
	}
	if f.Get("request_id") == "" {
		f.Set("request_id", NewID())
	}
	id, err := db.Mutate(context.Background(), a, action, f)
	if err != nil {
		t.Fatalf("%s: %v", action, err)
	}
	return id
}
func read(t *testing.T, db Database) *State {
	t.Helper()
	s, err := db.Read(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func ordinaryTask(s *State) *Dispatch {
	for i := range s.Dispatches {
		d := &s.Dispatches[i]
		if s.Operation(s.Snapshot(d.SnapshotID).OperationID).Seq == 10 {
			return d
		}
	}
	panic("no task")
}
func TestPostgresIdempotenceBatchesAndAudit(t *testing.T) {
	db, clock := realDB(t)
	s := read(t, db)
	if len(s.Centers) != 2 || len(s.Employees) != 4 || len(s.Dispatches) != 4 {
		t.Fatal("seed 不幂等")
	}
	d := ordinaryTask(s)
	employee := Actor{d.LeaderID, "employee"}
	f := url.Values{"id": {d.ID}, "request_id": {NewID()}}
	var wg sync.WaitGroup
	errs := make(chan error, 8)
	for range 8 {
		wg.Add(1)
		go func() { defer wg.Done(); _, err := db.Mutate(context.Background(), employee, "start", f); errs <- err }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	s = read(t, db)
	if len(s.Spans) != 1 {
		t.Fatal("重复开工写入多个区间")
	}
	clock.Store(base + 20*minute)
	f = url.Values{"id": {d.ID}, "good": {"30"}, "request_id": {NewID()}}
	b1 := mutation(t, db, employee, "report", f)
	if b := mutation(t, db, employee, "report", f); b != b1 {
		t.Fatal("幂等回执不一致")
	}
	f.Set("good", "31")
	if _, err := db.Mutate(context.Background(), employee, "report", f); err == nil {
		t.Fatal("同键不同数量未拒绝")
	}
	clock.Store(base + 30*minute)
	mutation(t, db, employee, "pause", url.Values{"id": {d.ID}, "reason": {"设备检查"}})
	clock.Store(base + 40*minute)
	mutation(t, db, employee, "resume", url.Values{"id": {d.ID}})
	clock.Store(base + 50*minute)
	b2 := mutation(t, db, employee, "report", url.Values{"id": {d.ID}, "good": {"40"}})
	clock.Store(base + 60*minute)
	mutation(t, db, employee, "finish", url.Values{"id": {d.ID}, "good": {"30"}})
	mutation(t, db, boss, "approve", url.Values{"id": {b1}})
	mutation(t, db, boss, "reject", url.Values{"id": {b2}, "reason": {"复核记录"}})
	mutation(t, db, employee, "revise", url.Values{"id": {b2}, "good": {"40"}, "reason": {"核实一致，保留重报链"}})
	mutation(t, db, boss, "approve", url.Values{"id": {b2}})
	s = read(t, db)
	var good, raw, cents int64
	for _, a := range s.Allocations {
		good += a.Good
		raw += a.Raw
		cents += a.Cents
	}
	if len(s.Batches) != 3 || good != 100*Scale || raw != 50*minute || cents != 15000 || s.Dispatch(d.ID).Status != "finished" || len(s.Revisions) != 1 {
		t.Fatalf("batches=%d qty=%d raw=%d cents=%d", len(s.Batches), good, raw, cents)
	}
	before, _ := json.Marshal(s)
	mutation(t, db, boss, "center", url.Values{"code": {"WC03"}, "name": {"第三中心"}})
	s = read(t, db)
	if len(s.Audits) < 10 || len(before) == 0 {
		t.Fatal("缺少审计")
	}
}
func TestPostgresConcurrentFirstDispatch(t *testing.T) {
	db, _ := realDB(t)
	s := read(t, db)
	op := ""
	for _, o := range s.Operations {
		if o.Seq == 10 {
			op = o.ID
		}
	}
	mutation(t, db, boss, "import", url.Values{"csv": {"external_id,product_code,center_code,quantity,status,source\nCONCURRENT,P001,WC01,100,open,TEST"}})
	s = read(t, db)
	order := ""
	for _, o := range s.Orders {
		if o.ExternalID == "CONCURRENT" {
			order = o.ID
		}
	}
	f := url.Values{"order_id": {order}, "operation_id": {op}, "quantity": {"60"}, "members": {s.Employees[0].ID}}
	results := make(chan error, 2)
	for range 2 {
		go func() {
			copy := url.Values{}
			for k, v := range f {
				copy[k] = v
			}
			copy.Set("request_id", NewID())
			_, err := db.Mutate(context.Background(), boss, "dispatch", copy)
			results <- err
		}()
	}
	success := 0
	for range 2 {
		if <-results == nil {
			success++
		}
	}
	if success != 1 {
		t.Fatalf("预占成功次数 %d", success)
	}
	s = read(t, db)
	count := 0
	for _, snap := range s.Snapshots {
		if snap.OrderID == order && snap.OperationID == op {
			count++
		}
	}
	if count != 1 {
		t.Fatal("首次派工快照不唯一")
	}
	mutation(t, db, boss, "operation", url.Values{"id": {op}, "product_id": {s.Operation(op).ProductID}, "seq": {"10"}, "name": {"调价后切削"}, "price": {"9.99"}, "standard": {"222"}, "multiplier": {"1"}, "parallel": {"on"}})
	id := mutation(t, db, boss, "dispatch", url.Values{"order_id": {order}, "operation_id": {op}, "quantity": {"40"}, "members": {s.Employees[0].ID}})
	s = read(t, db)
	if s.Standard(s.Dispatch(id)).Price != 150 || s.Standard(s.Dispatch(id)).Standard != 120000000 {
		t.Fatal("旧工单后续派工未沿用首次快照")
	}
	mutation(t, db, boss, "import", url.Values{"csv": {"external_id,product_code,center_code,quantity,status,source\nAFTER-PRICE,P001,WC01,100,open,TEST"}})
	s = read(t, db)
	for _, o := range s.Orders {
		if o.ExternalID == "AFTER-PRICE" {
			order = o.ID
		}
	}
	id = mutation(t, db, boss, "dispatch", url.Values{"order_id": {order}, "operation_id": {op}, "quantity": {"10"}, "members": {s.Employees[0].ID}})
	s = read(t, db)
	if s.Standard(s.Dispatch(id)).Price != 999 || s.Standard(s.Dispatch(id)).Standard != 222000000 {
		t.Fatal("新工单未采用新标准")
	}
}

func TestPostgresImportRollbackAndTimeException(t *testing.T) {
	db, clock := realDB(t)
	s := read(t, db)
	count := len(s.Orders)
	_, err := db.Mutate(context.Background(), boss, "import", url.Values{"request_id": {NewID()}, "csv": {"external_id,product_code,center_code,quantity,status,source\nNEW-VALID,P001,WC01,100,open,TEST\nNEW-BAD,MISSING,WC01,10,open,TEST"}})
	if err == nil || len(read(t, db).Orders) != count {
		t.Fatal("CSV 异常行未整批回滚")
	}
	d := ordinaryTask(s)
	mutation(t, db, boss, "start", url.Values{"id": {d.ID}})
	clock.Store(base + 60*minute)
	mutation(t, db, boss, "finish", url.Values{"id": {d.ID}, "good": {"100"}})
	s = read(t, db)
	b := s.Batches[0].ID
	local := func(n int64) string {
		return time.UnixMicro(base + n*minute).In(time.FixedZone("CST", 8*3600)).Format("2006-01-02T15:04:05.000000")
	}
	mutation(t, db, boss, "trim", url.Values{"id": {b}, "employee_id": {d.LeaderID}, "start": {local(10)}, "end": {local(20)}, "reason": {"现场确认离岗"}})
	s = read(t, db)
	a := allocation(s, b, d.LeaderID)
	if a.Raw != 60*minute || a.Effective != 50*minute || a.Accounted != 50*minute || len(s.Trims) != 1 {
		t.Fatalf("时间修正落库错误 %+v", a)
	}
}
func client(t *testing.T) *http.Client {
	t.Helper()
	jar, _ := cookiejar.New(nil)
	return &http.Client{Jar: jar, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
}
func getBody(t *testing.T, c *http.Client, u string) (int, string) {
	t.Helper()
	r, e := c.Get(u)
	if e != nil {
		t.Fatal(e)
	}
	defer r.Body.Close()
	b, e := io.ReadAll(r.Body)
	if e != nil {
		t.Fatal(e)
	}
	return r.StatusCode, string(b)
}
func field(t *testing.T, body, name string) string {
	t.Helper()
	prefix := `name="` + name + `" value="`
	_, tail, ok := strings.Cut(body, prefix)
	if !ok {
		t.Fatalf("missing field %s: %s", name, body[:min(len(body), 300)])
	}
	v, _, _ := strings.Cut(tail, `"`)
	return v
}
func loginClient(t *testing.T, c *http.Client, baseURL, mode, identity, password string) string {
	t.Helper()
	_, body := getBody(t, c, baseURL+"/login")
	res, e := c.PostForm(baseURL+"/login", url.Values{"csrf": {field(t, body, "csrf")}, "mode": {mode}, "identity": {identity}, "password": {password}})
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode != 303 {
		t.Fatalf("login status %d", res.StatusCode)
	}
	_, body = getBody(t, c, baseURL+res.Header.Get("Location"))
	return field(t, body, "csrf")
}
func TestPostgresHTTPPermissionsCSRFIdleAndPages(t *testing.T) {
	db, clock := realDB(t)
	handler, e := NewServer(db, os.DirFS("../.."), true, "", "", false)
	if e != nil {
		t.Fatal(e)
	}
	srv := httptest.NewServer(handler)
	defer srv.Close()
	adminClient := client(t)
	adminCSRF := loginClient(t, adminClient, srv.URL, "admin", "", "WorkshopDemo!2026")
	supervisorClient := client(t)
	loginClient(t, supervisorClient, srv.URL, "supervisor", "", "SupervisorDemo!2026")
	if code, _ := getBody(t, supervisorClient, srv.URL+"/review"); code != 200 {
		t.Fatal("主管登录或审核权限错误")
	}
	for _, path := range []string{"/", "/orders", "/master", "/dispatch", "/review", "/stats", "/audit", "/terminal", "/export.csv"} {
		code, body := getBody(t, adminClient, srv.URL+path)
		if code != 200 || len(body) < 10 {
			t.Fatalf("%s status=%d %s", path, code, body)
		}
	}
	resp, e := adminClient.PostForm(srv.URL+"/action/center", url.Values{"code": {"BAD"}, "name": {"BAD"}, "request_id": {NewID()}})
	if e != nil {
		t.Fatal(e)
	}
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatal("missing CSRF accepted")
	}
	worker := client(t)
	workerCSRF := loginClient(t, worker, srv.URL, "employee", "1001", "")
	code, _ := getBody(t, worker, srv.URL+"/master")
	if code != 303 {
		t.Fatal("员工可以读取身份档案")
	}
	resp, e = worker.PostForm(srv.URL+"/action/center", url.Values{"csrf": {workerCSRF}, "code": {"BAD"}, "name": {"BAD"}, "request_id": {NewID()}})
	if e != nil {
		t.Fatal(e)
	}
	resp.Body.Close()
	if len(read(t, db).Centers) != 2 {
		t.Fatal("员工越权写入")
	}
	s := read(t, db)
	d := ordinaryTask(s)
	mutation(t, db, Actor{d.LeaderID, "employee"}, "start", url.Values{"id": {d.ID}})
	clock.Store(base + 16*1000000)
	code, _ = getBody(t, worker, srv.URL+"/terminal")
	if code != 303 {
		t.Fatal("idle session not expired")
	}
	s = read(t, db)
	if s.Dispatch(d.ID).Status != "running" || s.Spans[0].End != 0 {
		t.Fatal("退出停止生产计时")
	}
	clock.Store(base + minute)
	for _, path := range []string{"/review", "/stats"} {
		if code, _ = getBody(t, adminClient, srv.URL+path); code != 200 {
			t.Fatal(path)
		}
	}
	_ = adminCSRF
}
