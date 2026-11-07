package app

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"sort"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"workshop/internal/store"
)

type Database struct {
	Pool *pgxpool.Pool
	Now  func() time.Time
}

func load(ctx context.Context, q *store.Queries) (*State, error) {
	b, err := q.LoadData(ctx)
	if err != nil {
		return nil, err
	}
	s := new(State)
	err = json.Unmarshal(b, s)
	sort.Slice(s.Employees, func(i, j int) bool { return s.Employees[i].Code < s.Employees[j].Code })
	sort.Slice(s.Centers, func(i, j int) bool { return s.Centers[i].Code < s.Centers[j].Code })
	sort.Slice(s.Products, func(i, j int) bool { return s.Products[i].Code < s.Products[j].Code })
	sort.Slice(s.Operations, func(i, j int) bool {
		a, b := s.Operations[i], s.Operations[j]
		if a.ProductID == b.ProductID {
			return a.Seq < b.Seq
		}
		return a.ProductID < b.ProductID
	})
	sort.Slice(s.Batches, func(i, j int) bool { return s.Batches[i].At < s.Batches[j].At })
	sort.Slice(s.Audits, func(i, j int) bool { return s.Audits[i].At > s.Audits[j].At })
	return s, err
}
func (db Database) Read(ctx context.Context) (*State, error) { return load(ctx, store.New(db.Pool)) }
func saveRows[T any](ctx context.Context, rows []T, fn func(context.Context, []byte) error) error {
	for _, r := range rows {
		b, err := json.Marshal(r)
		if err != nil {
			return err
		}
		if err = fn(ctx, b); err != nil {
			return err
		}
	}
	return nil
}
func save(ctx context.Context, q *store.Queries, s *State) error {
	checks := []func() error{
		func() error { return saveRows(ctx, s.Centers, q.SaveCenters) }, func() error { return saveRows(ctx, s.Employees, q.SaveEmployees) },
		func() error { return saveRows(ctx, s.Products, q.SaveProducts) }, func() error { return saveRows(ctx, s.Operations, q.SaveOperations) },
		func() error { return saveRows(ctx, s.Versions, q.SaveVersions) }, func() error { return saveRows(ctx, s.Orders, q.SaveOrders) },
		func() error { return saveRows(ctx, s.Snapshots, q.SaveSnapshots) }, func() error { return saveRows(ctx, s.Dispatches, q.SaveDispatches) },
		func() error { return saveRows(ctx, s.Members, q.SaveMembers) }, func() error { return saveRows(ctx, s.Batches, q.SaveBatches) },
		func() error { return saveRows(ctx, s.Revisions, q.SaveRevisions) }, func() error { return saveRows(ctx, s.Spans, q.SaveSpans) },
		func() error { return saveRows(ctx, s.Resolutions, q.SaveResolutions) },
		func() error { return saveRows(ctx, s.Trims, q.SaveTrims) }, func() error { return saveRows(ctx, s.Allocations, q.SaveAllocations) }, func() error { return saveRows(ctx, s.Audits, q.SaveAudits) },
		func() error { return saveRows(ctx, s.Requests, q.SaveRequests) },
	}
	for _, fn := range checks {
		if err := fn(); err != nil {
			return err
		}
	}
	return nil
}
func (db Database) Mutate(ctx context.Context, a Actor, action string, f url.Values) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	token := f.Get("request_id")
	if len(token) < 16 || len(token) > 128 {
		return "", fmt.Errorf("请求标识失效，请刷新后重试")
	}
	input := url.Values{}
	for k, v := range f {
		if k != "csrf" && k != "request_id" {
			input[k] = v
		}
	}
	hash := sha256.Sum256([]byte(action + "\n" + input.Encode()))
	fingerprint := hex.EncodeToString(hash[:])
	tx, err := db.Pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	q := store.New(tx)
	// ponytail: one transaction lock serializes workshop mutations across app instances;
	// switch to order/employee advisory locks and scoped queries when write throughput requires it.
	if err = q.Lock(ctx); err != nil {
		return "", err
	}
	s, err := load(ctx, q)
	if err != nil {
		return "", err
	}
	for _, r := range s.Requests {
		if r.ID == token {
			if r.Actor != a.ID || r.Fingerprint != fingerprint {
				return "", fmt.Errorf("请求标识已用于其他操作")
			}
			return r.Result, nil
		}
	}
	result, err := s.Apply(a, action, input, db.Now().UnixMicro())
	if err != nil {
		return "", err
	}
	s.Requests = append(s.Requests, Request{token, a.ID, fingerprint, result})
	if err = save(ctx, q, s); err != nil {
		return "", fmt.Errorf("保存失败（唯一键或数据约束）：%w", err)
	}
	if err = tx.Commit(ctx); err != nil {
		return "", err
	}
	return result, nil
}
func (db Database) Seed(ctx context.Context) error {
	tx, err := db.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	q := store.New(tx)
	if err = q.Lock(ctx); err != nil {
		return err
	}
	s, err := load(ctx, q)
	if err != nil {
		return err
	}
	if len(s.Centers) > 0 {
		return nil
	}
	now := db.Now().UnixMicro()
	a := Actor{"demo-seed", "admin"}
	actions := []struct {
		action string
		values url.Values
	}{
		{"center", url.Values{"code": {"WC01"}, "name": {"一车间 · 精密加工"}}}, {"center", url.Values{"code": {"WC02"}, "name": {"二车间 · 装配包装"}}},
		{"product", url.Values{"code": {"P001"}, "name": {"精密连接件 A型"}}},
	}
	for _, x := range actions {
		if _, err = s.Apply(a, x.action, x.values, now); err != nil {
			return err
		}
	}
	names := []string{"张明", "李华", "王芳", "赵强"}
	for i, name := range names {
		center := s.Centers[0].ID
		if i > 1 {
			center = s.Centers[1].ID
		}
		f := url.Values{"code": {fmt.Sprintf("E%03d", i+1)}, "name": {name}, "card": {fmt.Sprintf("100%d", i+1)}, "badge": {fmt.Sprintf("EMP-%03d", i+1)}, "center_id": {center}, "active": {"on"}}
		if _, err = s.Apply(a, "employee", f, now); err != nil {
			return err
		}
	}
	for i, name := range []string{"精密切削", "倒角打磨", "协作装配", "质量检验"} {
		f := url.Values{"product_id": {s.Products[0].ID}, "name": {name}, "seq": {fmt.Sprint((i + 1) * 10)}, "price": {"1.50"}, "standard": {"120"}, "multiplier": {"1"}}
		if i < 2 {
			f.Set("parallel", "on")
		}
		if i == 1 {
			f.Set("standard", "60")
			f.Set("multiplier", "4")
		}
		if i == 2 {
			f.Set("collaboration", "on")
		}
		if _, err = s.Apply(a, "operation", f, now+int64(i)); err != nil {
			return err
		}
	}
	if err = s.Import([]ImportOrder{{"DEMO-202611-001", "P001", "WC01", "100", "open", "DEMO 演示文件"}, {"DEMO-202611-002", "P001", "WC02", "200", "open", "DEMO 演示文件"}}); err != nil {
		return err
	}
	for i, op := range s.Operations {
		ids := []string{s.Employees[0].ID}
		if i == 2 {
			ids = []string{s.Employees[0].ID, s.Employees[1].ID, s.Employees[2].ID, s.Employees[3].ID}
		}
		f := url.Values{"order_id": {s.Orders[0].ID}, "operation_id": {op.ID}, "quantity": {"100"}, "members": ids}
		if _, err = s.Apply(a, "dispatch", f, now); err != nil {
			return err
		}
	}
	if err = save(ctx, q, s); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
