package app

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"workshop/internal/store"
)

func TestPostgresBulkDispatchAtomicIdempotentAndConcurrent(t *testing.T) {
	db, _ := realDB(t)
	s := read(t, db)
	order := s.Orders[0]
	op := s.Operations[0].ID
	makeOrder := func(name string) string {
		mutation(t, db, boss, "import", url.Values{"csv": {fmt.Sprintf("external_id,product_code,center_code,quantity,status,source\n%s,%s,%s,100,open,TEST", name, s.Product(order.ProductID).Code, s.Center(order.CenterID).Code)}})
		for _, o := range read(t, db).Orders {
			if o.ExternalID == name {
				return o.ID
			}
		}
		t.Fatal("order missing")
		return ""
	}
	id := makeOrder("BULK")
	e1, e2 := s.Employees[0].ID, s.Employees[1].ID
	f := url.Values{"order_id": {id}, "operation_id": {op}, "employees": {e1, e2}, "quantity_" + e1: {"20"}, "quantity_" + e2: {"90"}, "request_id": {NewID()}}
	before, _ := json.Marshal(read(t, db))
	if _, err := db.Mutate(context.Background(), boss, "dispatch-many", f); err == nil || !strings.Contains(err.Error(), "整批未保存") {
		t.Fatal(err)
	}
	after, _ := json.Marshal(read(t, db))
	if string(before) != string(after) {
		t.Fatal("failed batch changed business data")
	}
	f.Set("quantity_"+e2, "30")
	if _, err := db.Mutate(context.Background(), Actor{e1, "employee"}, "dispatch-many", f); err == nil {
		t.Fatal("employee bulk permission bypass")
	}
	mutation(t, db, boss, "dispatch-many", f)
	mutation(t, db, boss, "dispatch-many", f)
	current := read(t, db)
	count := 0
	for _, d := range current.Dispatches {
		if current.Snapshot(d.SnapshotID).OrderID == id {
			count++
			if lenMembers(current, d.ID) != 1 {
				t.Fatal("independent dispatch became collaboration")
			}
		}
	}
	if count != 2 {
		t.Fatal("retry duplicated dispatch", count)
	}
	concurrentID := makeOrder("CONCURRENT")
	var wg sync.WaitGroup
	outcomes := make(chan error, 2)
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := db.Mutate(context.Background(), boss, "dispatch-many", url.Values{"order_id": {concurrentID}, "operation_id": {op}, "employees": {e1, e2}, "quantity_" + e1: {"30"}, "quantity_" + e2: {"30"}, "request_id": {NewID()}})
			outcomes <- err
		}()
	}
	wg.Wait()
	close(outcomes)
	success := 0
	for err := range outcomes {
		if err == nil {
			success++
		}
	}
	if success != 1 {
		t.Fatal("concurrent capacity check", success)
	}
	current = read(t, db)
	var total int64
	for _, d := range current.Dispatches {
		if current.Snapshot(d.SnapshotID).OrderID == concurrentID {
			total += d.Quantity
		}
	}
	if total != 60*Scale {
		t.Fatal("partial batch or oversubscription", total)
	}
}

func lenMembers(s *State, id string) int {
	n := 0
	for _, m := range s.Members {
		if m.DispatchID == id {
			n++
		}
	}
	return n
}

func TestPostgresFilteredStatsPaginationExportAndTerminalScope(t *testing.T) {
	db, _ := realDB(t)
	s := read(t, db)
	d := *ordinaryTask(s)
	at := time.Date(2026, 11, 1, 0, 0, 0, 0, time.FixedZone("CST", 8*3600)).UnixMicro()
	for i := range 123 {
		id := fmt.Sprintf("history-%03d", i)
		status := "approved"
		if i%3 == 1 {
			status = "pending"
		}
		if i%3 == 2 {
			status = "rejected"
		}
		s.Batches = append(s.Batches, Batch{ID: id, DispatchID: d.ID, Algorithm: AlgorithmVersion, Good: Scale, Bad: 0, At: at + int64(i-1)*minute, Status: status})
		s.Allocations = append(s.Allocations, Allocation{ID: id + ":a", BatchID: id, EmployeeID: d.LeaderID, Good: Scale, Raw: minute, Effective: minute, Accounted: minute, Cents: 150, Ready: i%4 != 0, Reason: "", Algorithm: AlgorithmVersion})
	}
	if err := save(context.Background(), store.New(db.Pool), s, nil); err != nil {
		t.Fatal(err)
	}
	s = read(t, db)
	adminSession := store.Session{Token: NewID(), EmployeeID: "admin", Role: "admin", Csrf: NewID(), Expires: db.Now().Add(time.Hour).UnixMicro(), LastSeen: db.Now().UnixMicro()}
	if err := store.New(db.Pool).PutSession(context.Background(), store.PutSessionParams(adminSession)); err != nil {
		t.Fatal(err)
	}
	handler, err := NewServer(db, os.DirFS("../.."), true, "", "", false)
	if err != nil {
		t.Fatal(err)
	}
	request := func(path string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("GET", path, nil)
		r.AddCookie(&http.Cookie{Name: "workshop_session", Value: adminSession.Token})
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatalf("HTTP %s: %d %s", path, w.Code, w.Body.String())
		}
		return w
	}
	page := request("/stats?all=1&page=2")
	if !strings.Contains(page.Body.String(), "共 123 条") || !strings.Contains(page.Body.String(), "上一页") || !strings.Contains(page.Body.String(), "下一页") {
		t.Fatal("HTTP pagination metadata missing")
	}
	export := request("/export.csv?all=1&page=2")
	rows, err := csv.NewReader(strings.NewReader(strings.TrimPrefix(export.Body.String(), "\ufeff"))).ReadAll()
	if err != nil || len(rows) != 124 {
		t.Fatal("CSV exported only the current page", len(rows), err)
	}
	for _, row := range []store.Session{{Role: "admin"}, {Role: "employee", EmployeeID: d.LeaderID}, {Role: "employee", EmployeeID: s.Employees[3].ID}} {
		for _, f := range []url.Values{{}, {"month": {"2026-11"}}, {"from": {"2026-11-01"}, "to": {"2026-11-01"}}, {"status": {"approved"}}, {"status": {"unaccounted"}}, {"employee": {s.Employees[1].ID}}, {"center": {s.Order(s.Snapshot(d.SnapshotID).OrderID).CenterID}, "operation": {s.Snapshot(d.SnapshotID).OperationID}}, {"month": {"2026-10"}, "from": {"2026-11-01"}}} {
			expected := stats(s, row, f)
			full, err := db.readStats(context.Background(), row, f, -1, 0)
			if err != nil {
				t.Fatal(err)
			}
			if full.Total != int64(len(expected)) || len(full.Rows) != len(expected) {
				t.Fatalf("filter %v row %v: expected %d got %d", f, row, len(expected), full.Total)
			}
			for i := range expected {
				if !reflect.DeepEqual(expected[i], full.Rows[i]) {
					t.Fatalf("SQL differs from ledger: %+v != %+v", expected[i], full.Rows[i])
				}
			}
			var cents, good int64
			for _, r := range expected {
				if r.Status == "approved" {
					cents += r.Cents
					good += r.Good
				}
			}
			if full.Summary.Cents != cents || full.Summary.Good != good {
				t.Fatal("totals filter mismatch")
			}
			var paged []Stat
			for offset := int64(0); offset < full.Total; offset += 50 {
				part, err := db.readStats(context.Background(), row, f, 50, offset)
				if err != nil {
					t.Fatal(err)
				}
				if part.Total != full.Total || part.Summary != full.Summary || len(part.Rows) > 50 {
					t.Fatal("pagination changes totals")
				}
				paged = append(paged, part.Rows...)
			}
			if len(paged) != len(expected) {
				t.Fatal("export/page coverage")
			}
		}
	}
	own, err := db.readTerminal(context.Background(), store.Session{Role: "employee", EmployeeID: s.Employees[3].ID}, d.ID)
	if err != nil || len(own.Dispatches) != 0 {
		t.Fatal("terminal task visibility", err)
	}
	own, err = db.readTerminal(context.Background(), store.Session{Role: "employee", EmployeeID: d.LeaderID}, d.ID)
	if err != nil || len(own.Dispatches) != 1 || len(own.Audits) != 0 || len(own.Requests) != 0 || len(own.Allocations) != 0 {
		t.Fatal("terminal is not scoped", err)
	}
}
