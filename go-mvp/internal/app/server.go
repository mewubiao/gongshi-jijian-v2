package app

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/csv"
	"fmt"
	"html/template"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"golang.org/x/crypto/bcrypt"
	"workshop/internal/store"
)

type Server struct {
	db                        Database
	templates                 *template.Template
	demo, secure              bool
	adminHash, supervisorHash []byte
	limiterMu                 sync.Mutex
	attempts                  map[string][]time.Time
}
type Page struct {
	Monthly                    []Stat
	S                          *State
	Session                    store.Session
	Path, Title, Error, Notice string
	Demo                       bool
	Now                        int64
	Tasks                      []Task
	Rows                       []Stat
	Summary                    Stat
	Filter                     url.Values
}
type Task struct {
	Raw                                  int64
	ActiveMembers                        int64
	D                                    Dispatch
	Name, Order, Product, Center, Leader string
	Mine                                 bool
	Members                              []MemberView
	Reported                             int64
	Price, Standard                      int64
}
type MemberView struct {
	ID, Name string
	Present  bool
}
type Stat struct {
	At                                                       int64
	EmployeeID                                               string
	Effective                                                int64
	Employee, Center, Order, Operation, Date, Status, Reason string
	Good, Bad, Raw, Accounted, Cents                         int64
	Ready                                                    bool
	BatchID                                                  string
}

func NewServer(db Database, assets fs.FS, demo bool, admin, supervisor string, secure bool) (http.Handler, error) {
	if demo {
		if admin == "" {
			admin = "WorkshopDemo!2026"
		}
		if supervisor == "" {
			supervisor = "SupervisorDemo!2026"
		}
	}
	if len(admin) < 12 {
		return nil, fmt.Errorf("ADMIN_PASSWORD 至少 12 字符（演示可指定 -demo）")
	}
	if supervisor != "" && len(supervisor) < 12 {
		return nil, fmt.Errorf("SUPERVISOR_PASSWORD 至少 12 字符")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(admin), bcrypt.DefaultCost)
	if err != nil {
		return nil, err
	}
	var sh []byte
	if supervisor != "" {
		sh, err = bcrypt.GenerateFromPassword([]byte(supervisor), bcrypt.DefaultCost)
		if err != nil {
			return nil, err
		}
	}
	fns := template.FuncMap{"batchid": func(p *string) string {
		if p == nil {
			return ""
		}
		return *p
	}, "localtime": func(n int64) string {
		return time.UnixMicro(n).In(time.FixedZone("CST", 8*3600)).Format("2006-01-02T15:04:05.000000")
	}, "qty": Quantity, "money": Money, "date": Date, "minutes": func(v int64) string { return fmt.Sprintf("%.2f", float64(v)/60000000) }, "seconds": func(v int64) string { return fmt.Sprintf("%d.%06d", v/1000000, v%1000000) }, "nonce": NewID, "status": status, "short": func(s string) string {
		if len(s) > 8 {
			return s[:8]
		}
		return s
	}, "admin": func(s store.Session) bool { return s.Role == "admin" || s.Role == "supervisor" }}
	tmpl, err := template.New("").Funcs(fns).ParseFS(assets, "web/*.html")
	if err != nil {
		return nil, err
	}
	s := &Server{db: db, templates: tmpl, demo: demo, secure: secure, adminHash: hash, supervisorHash: sh, attempts: map[string][]time.Time{}}
	r := chi.NewRouter()
	r.Use(middleware.Recoverer)
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("X-Content-Type-Options", "nosniff")
			w.Header().Set("Referrer-Policy", "same-origin")
			w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
			w.Header().Set("Cache-Control", "no-store")
			next.ServeHTTP(w, r)
		})
	})
	static, _ := fs.Sub(assets, "web/static")
	r.Handle("/static/*", http.StripPrefix("/static/", http.FileServer(http.FS(static))))
	r.Get("/healthz", func(w http.ResponseWriter, r *http.Request) {
		ctx, c := context.WithTimeout(r.Context(), 2*time.Second)
		defer c()
		if err := db.Pool.Ping(ctx); err != nil {
			http.Error(w, "database unavailable", 503)
			return
		}
		w.Write([]byte("ok"))
	})
	r.Get("/login", s.loginPage)
	r.Post("/login", s.login)
	r.Group(func(r chi.Router) {
		r.Use(s.auth)
		r.Get("/", s.page)
		for _, path := range []string{"/orders", "/master", "/dispatch", "/review", "/stats", "/audit", "/terminal"} {
			r.Get(path, s.page)
		}
		r.Post("/action/{action}", s.action)
		r.Post("/logout", s.logout)
		r.Post("/session/touch", s.touch)
		r.Get("/export.csv", s.export)
	})
	return r, nil
}
func status(v string) string {
	m := map[string]string{"ready": "待开工", "running": "进行中", "paused": "已暂停", "finished": "已完工", "pending": "待审核", "approved": "已通过", "rejected": "已驳回", "open": "未关闭", "closed": "已关闭", "admin": "管理员", "supervisor": "主管", "employee": "员工"}
	if x, ok := m[v]; ok {
		return x
	}
	return v
}

type sessionKey struct{}

func session(r *http.Request) store.Session { return r.Context().Value(sessionKey{}).(store.Session) }
func (s *Server) cookie(w http.ResponseWriter, token string, age int) {
	http.SetCookie(w, &http.Cookie{Name: "workshop_session", Value: token, Path: "/", HttpOnly: true, Secure: s.secure, SameSite: http.SameSiteStrictMode, MaxAge: age})
}
func (s *Server) createSession(ctx context.Context, w http.ResponseWriter, id, role string) (store.Session, error) {
	now := s.db.Now().UnixMicro()
	row := store.Session{Token: NewID() + NewID(), EmployeeID: id, Role: role, Csrf: NewID(), Expires: now + int64(8*time.Hour/time.Microsecond), LastSeen: now}
	err := store.New(s.db.Pool).PutSession(ctx, store.PutSessionParams(row))
	if err == nil {
		s.cookie(w, row.Token, 8*3600)
	}
	return row, err
}
func redirect(w http.ResponseWriter, r *http.Request, path string) {
	if r.Header.Get("HX-Request") == "true" {
		w.Header().Set("HX-Redirect", path)
		w.WriteHeader(200)
	} else {
		http.Redirect(w, r, path, http.StatusSeeOther)
	}
}
func (s *Server) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := r.Cookie("workshop_session")
		if err != nil {
			redirect(w, r, "/login")
			return
		}
		row, err := store.New(s.db.Pool).GetSession(r.Context(), c.Value)
		now := s.db.Now().UnixMicro()
		if err != nil || row.Role == "guest" || now >= row.Expires || (row.Role == "employee" && now-row.LastSeen >= 15000000) {
			s.cookie(w, "", -1)
			redirect(w, r, "/login?notice="+url.QueryEscape("身份已退出，后台生产计时继续"))
			return
		}
		if row.Role == "employee" {
			var active bool
			if err = s.db.Pool.QueryRow(r.Context(), "SELECT active FROM employees WHERE id=$1", row.EmployeeID).Scan(&active); err != nil || !active {
				s.cookie(w, "", -1)
				redirect(w, r, "/login")
				return
			}
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), sessionKey{}, row)))
	})
}
func csrf(r *http.Request, row store.Session) bool {
	if err := r.ParseForm(); err != nil {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(r.Form.Get("csrf")), []byte(row.Csrf)) == 1
}
func (s *Server) loginPage(w http.ResponseWriter, r *http.Request) {
	row, err := s.createSession(r.Context(), w, "", "guest")
	if err != nil {
		http.Error(w, "数据库会话创建失败", 500)
		return
	}
	s.render(w, "login", Page{Session: row, Demo: s.demo, Error: r.URL.Query().Get("error"), Notice: r.URL.Query().Get("notice")})
}
func (s *Server) allowLogin(r *http.Request) bool {
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	s.limiterMu.Lock()
	defer s.limiterMu.Unlock()
	now := s.db.Now()
	var recent []time.Time
	for _, t := range s.attempts[host] {
		if now.Sub(t) < time.Minute {
			recent = append(recent, t)
		}
	}
	allowed := len(recent) < 15
	s.attempts[host] = append(recent, now)
	return allowed
}
func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	if !s.allowLogin(r) {
		http.Error(w, "登录尝试过多，请稍后再试", 429)
		return
	}
	c, err := r.Cookie("workshop_session")
	if err != nil {
		http.Error(w, "请刷新登录页", 403)
		return
	}
	q := store.New(s.db.Pool)
	pre, err := q.GetSession(r.Context(), c.Value)
	if err != nil || pre.Role != "guest" || pre.Expires <= s.db.Now().UnixMicro() || !csrf(r, pre) {
		http.Error(w, "会话校验失败", 403)
		return
	}
	role, id := "employee", ""
	switch r.Form.Get("mode") {
	case "admin", "supervisor":
		role = r.Form.Get("mode")
		hash := s.adminHash
		if role == "supervisor" {
			hash = s.supervisorHash
		}
		if bcrypt.CompareHashAndPassword(hash, []byte(r.Form.Get("password"))) == nil {
			id = role
		}
	default:
		state, e := s.db.Read(r.Context())
		if e == nil {
			for _, employee := range state.Employees {
				if employee.Active && (employee.Card == r.Form.Get("identity") || employee.Badge == r.Form.Get("identity")) {
					id = employee.ID
					break
				}
			}
		}
	}
	if id == "" {
		redirect(w, r, "/login?error="+url.QueryEscape("账号、密码或工牌码不正确"))
		return
	}
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	s.limiterMu.Lock()
	delete(s.attempts, host)
	s.limiterMu.Unlock()
	if _, err = s.createSession(r.Context(), w, id, role); err != nil {
		http.Error(w, "创建会话失败", 500)
		return
	}
	_ = q.DeleteSession(r.Context(), pre.Token)
	dest := "/"
	if role == "employee" {
		dest = "/terminal"
	}
	redirect(w, r, dest)
}
func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	row := session(r)
	if !csrf(r, row) {
		http.Error(w, "CSRF 校验失败", 403)
		return
	}
	_ = store.New(s.db.Pool).DeleteSession(r.Context(), row.Token)
	s.cookie(w, "", -1)
	redirect(w, r, "/login?notice="+url.QueryEscape("身份已退出，后台计时继续"))
}
func (s *Server) touch(w http.ResponseWriter, r *http.Request) {
	row := session(r)
	if !csrf(r, row) {
		http.Error(w, "CSRF 校验失败", 403)
		return
	}
	err := store.New(s.db.Pool).TouchSession(r.Context(), store.TouchSessionParams{Token: row.Token, LastSeen: s.db.Now().UnixMicro()})
	if err != nil {
		http.Error(w, "会话更新失败", 500)
		return
	}
	w.WriteHeader(204)
}
func (s *Server) action(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	row := session(r)
	if !csrf(r, row) {
		http.Error(w, "CSRF 校验失败", 403)
		return
	}
	action := chi.URLParam(r, "action")
	_, err := s.db.Mutate(r.Context(), Actor{row.EmployeeID, row.Role}, action, r.Form)
	dest := r.Form.Get("return")
	allowed := map[string]bool{"/": true, "/orders": true, "/master": true, "/dispatch": true, "/review": true, "/terminal": true}
	if !allowed[dest] {
		dest = "/terminal"
	}
	if err != nil {
		log.Printf("操作 %s 失败: %v", action, err)
		redirect(w, r, dest+"?error="+url.QueryEscape(friendly(err)))
		return
	}
	_ = store.New(s.db.Pool).TouchSession(r.Context(), store.TouchSessionParams{Token: row.Token, LastSeen: s.db.Now().UnixMicro()})
	redirect(w, r, dest+"?notice="+url.QueryEscape("操作已保存"))
}
func friendly(err error) string {
	if strings.Contains(err.Error(), "SQLSTATE") {
		return "保存失败：编码、卡号或数据约束冲突，请核对输入"
	}
	return err.Error()
}
func (s *Server) page(w http.ResponseWriter, r *http.Request) {
	row := session(r)
	if row.Role == "employee" && r.URL.Path != "/terminal" && r.URL.Path != "/stats" {
		redirect(w, r, "/terminal")
		return
	}
	state, err := s.db.Read(r.Context())
	if err != nil {
		log.Print(err)
		http.Error(w, "数据库读取失败", 500)
		return
	}
	p := Page{S: state, Session: row, Path: r.URL.Path, Title: map[string]string{"/": "生产概览", "/orders": "生产工单", "/master": "基础资料", "/dispatch": "派工管理", "/review": "报工审核", "/stats": "工时与计件统计", "/audit": "操作审计", "/terminal": "车间公共终端"}[r.URL.Path], Error: r.URL.Query().Get("error"), Notice: r.URL.Query().Get("notice"), Demo: s.demo, Now: s.db.Now().UnixMicro(), Filter: r.URL.Query()}
	if row.Role == "employee" {
		p.Title = state.Employee(row.EmployeeID).Name + " · 我的任务"
	}
	for _, d := range state.Dispatches {
		if row.Role == "employee" && !state.IsMember(d.ID, row.EmployeeID) {
			continue
		}
		snap := state.Snapshot(d.SnapshotID)
		o := state.Order(snap.OrderID)
		v := state.Standard(&d)
		task := Task{D: d, Name: v.Name, Order: o.ExternalID, Product: state.Product(o.ProductID).Name, Center: state.Center(o.CenterID).Name, Leader: state.Employee(d.LeaderID).Name, Mine: row.Role != "employee" || row.EmployeeID == d.LeaderID, Price: v.Price, Standard: v.Standard}
		for _, m := range state.Members {
			if m.DispatchID == d.ID {
				task.Members = append(task.Members, MemberView{m.EmployeeID, state.Employee(m.EmployeeID).Name, m.Present})
			}
		}
		for _, b := range state.Batches {
			if b.DispatchID == d.ID {
				g, bad := state.Counts(&b)
				task.Reported += g + bad
			}
		}
		for _, sp := range state.Spans {
			if sp.DispatchID == d.ID {
				end := sp.End
				if end == 0 {
					end = p.Now
					task.ActiveMembers++
				}
				task.Raw += end - sp.Start
			}
		}
		p.Tasks = append(p.Tasks, task)
	}
	sort.Slice(p.Tasks, func(i, j int) bool { return p.Tasks[i].D.At > p.Tasks[j].D.At })
	p.Rows = stats(state, row, p.Filter)
	for _, x := range p.Rows {
		if x.Status == "approved" {
			p.Summary.Good += x.Good
			p.Summary.Bad += x.Bad
			p.Summary.Raw += x.Raw
			p.Summary.Accounted += x.Accounted
			p.Summary.Cents += x.Cents
		}
	}
	if p.Path == "/stats" && p.Filter.Get("month") == "" && p.Filter.Get("all") == "" && p.Filter.Get("from") == "" && p.Filter.Get("to") == "" {
		p.Filter.Set("month", Date(p.Now)[:7])
		p.Rows = stats(state, row, p.Filter)
		p.Summary = Stat{}
		for _, x := range p.Rows {
			if x.Status == "approved" {
				p.Summary.Good += x.Good
				p.Summary.Bad += x.Bad
				p.Summary.Raw += x.Raw
				p.Summary.Accounted += x.Accounted
				p.Summary.Cents += x.Cents
			}
		}
	}
	indices := map[string]int{}
	for _, x := range p.Rows {
		if x.Status != "approved" {
			continue
		}
		i, ok := indices[x.EmployeeID]
		if !ok {
			i = len(p.Monthly)
			indices[x.EmployeeID] = i
			p.Monthly = append(p.Monthly, Stat{EmployeeID: x.EmployeeID, Employee: x.Employee})
		}
		m := &p.Monthly[i]
		m.Good += x.Good
		m.Bad += x.Bad
		m.Raw += x.Raw
		m.Effective += x.Effective
		m.Accounted += x.Accounted
		m.Cents += x.Cents
	}
	name := "layout"
	if r.Header.Get("HX-Target") == "task-list" {
		name = "tasks"
	}
	s.render(w, name, p)
}
func stats(state *State, row store.Session, f url.Values) []Stat {
	var out []Stat
	for _, a := range state.Allocations {
		if row.Role == "employee" && a.EmployeeID != row.EmployeeID {
			continue
		}
		b := state.Batch(a.BatchID)
		d := state.Dispatch(b.DispatchID)
		snap := state.Snapshot(d.SnapshotID)
		order := state.Order(snap.OrderID)
		date := Date(b.At)[:10]
		if v := f.Get("month"); v != "" && !strings.HasPrefix(date, v) {
			continue
		}
		if v := f.Get("from"); v != "" && date < v {
			continue
		}
		if v := f.Get("to"); v != "" && date > v {
			continue
		}
		if v := f.Get("employee"); v != "" && a.EmployeeID != v {
			continue
		}
		if v := f.Get("center"); v != "" && order.CenterID != v {
			continue
		}
		if v := f.Get("order"); v != "" && order.ID != v {
			continue
		}
		if v := f.Get("operation"); v != "" && snap.OperationID != v {
			continue
		}
		if v := f.Get("status"); v != "" && b.Status != v {
			continue
		}
		out = append(out, Stat{At: b.At, EmployeeID: a.EmployeeID, Employee: state.Employee(a.EmployeeID).Name, Center: state.Center(order.CenterID).Name, Order: order.ExternalID, Operation: state.Version(snap.VersionID).Name, Date: date, Status: b.Status, Reason: a.Reason, Good: a.Good, Bad: a.Bad, Raw: a.Raw, Effective: a.Effective, Accounted: a.Accounted, Cents: a.Cents, Ready: a.Ready, BatchID: b.ID})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].At == out[j].At {
			return out[i].BatchID < out[j].BatchID
		}
		return out[i].At < out[j].At
	})
	return out
}
func (s *Server) export(w http.ResponseWriter, r *http.Request) {
	state, err := s.db.Read(r.Context())
	if err != nil {
		http.Error(w, "数据库读取失败", 500)
		return
	}
	w.Header().Set("Content-Type", "text/csv; charset=utf-8")
	w.Header().Set("Content-Disposition", "attachment; filename=workshop-report.csv")
	w.Write([]byte{0xef, 0xbb, 0xbf})
	out := csv.NewWriter(w)
	_ = out.Write([]string{"日期", "员工", "车间", "工单", "工序", "状态", "合格工序量", "不良工序量", "原始分钟", "修正后有效分钟", "核算分钟", "计件元", "核算情况"})
	safe := func(v string) string {
		if strings.ContainsAny(strings.TrimLeft(v, " \t\r\n")[:min(1, len(strings.TrimLeft(v, " \t\r\n")))], "=+-@") {
			return "'" + v
		}
		return v
	}
	for _, x := range stats(state, session(r), r.URL.Query()) {
		_ = out.Write([]string{x.Date, safe(x.Employee), safe(x.Center), safe(x.Order), safe(x.Operation), status(x.Status), Quantity(x.Good), Quantity(x.Bad), fmt.Sprintf("%.6f", float64(x.Raw)/60000000), fmt.Sprintf("%.6f", float64(x.Effective)/60000000), fmt.Sprintf("%.6f", float64(x.Accounted)/60000000), Money(x.Cents), safe(x.Reason)})
	}
	out.Flush()
}
func (s *Server) render(w http.ResponseWriter, name string, p Page) {
	var b bytes.Buffer
	if err := s.templates.ExecuteTemplate(&b, name, p); err != nil {
		log.Printf("模板错误: %v", err)
		http.Error(w, "页面渲染失败", 500)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(b.Bytes())
}
