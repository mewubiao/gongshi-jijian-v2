package app

import (
	"encoding/json"
	"math/big"
	"net/url"
	"testing"
	"time"
)

const base int64 = 1800000000000000
const minute int64 = 60000000

var boss = Actor{"admin", "admin"}

func fixture() *State {
	s := &State{Centers: []Center{{"c", "WC", "车间"}}, Products: []Product{{"p", "P", "产品"}}, Orders: []Order{{"o", "WO", "p", "c", 100 * Scale, "open", "DEMO"}}}
	for _, id := range []string{"e1", "e2", "e3", "e4"} {
		s.Employees = append(s.Employees, Employee{id, id, id, id, id, "c", true})
	}
	for i, id := range []string{"a", "b"} {
		s.Operations = append(s.Operations, Operation{id, "p", int64(i + 1), id})
		s.Versions = append(s.Versions, Version{id, id, 150, int64(120-i*60) * 1000000, Scale, true, false, base, "admin", id, int64(i + 1)})
		s.Snapshots = append(s.Snapshots, Snapshot{id, "o", id, id, 100 * Scale, base})
		s.Dispatches = append(s.Dispatches, Dispatch{id, id, 100 * Scale, "e1", "ready", base})
		s.Members = append(s.Members, Member{id, id, "e1", true})
	}
	return s
}
func must(t *testing.T, s *State, action string, f url.Values, at int64) string {
	t.Helper()
	id, e := s.Apply(boss, action, f, base+at)
	if e != nil {
		t.Fatalf("%s: %v", action, e)
	}
	return id
}
func move(t *testing.T, s *State, action, id string, at int64) {
	t.Helper()
	must(t, s, action, url.Values{"id": {id}, "reason": {"测试暂停"}}, at)
}
func complete(t *testing.T, s *State, id, good string, at int64) string {
	t.Helper()
	must(t, s, "finish", url.Values{"id": {id}, "good": {good}, "bad": {"0"}}, at)
	return s.Batches[len(s.Batches)-1].ID
}
func allocation(s *State, batch, employee string) Allocation {
	for _, a := range s.Allocations {
		if a.BatchID == batch && a.EmployeeID == employee {
			return a
		}
	}
	panic("missing allocation")
}
func TestParallelFullPartialPause(t *testing.T) {
	for _, tc := range []struct {
		name                             string
		startB, endA, endB, wantA, wantB int64
		pause                            bool
	}{
		{"完整重叠20与40", 0, 60, 60, 20 * minute, 40 * minute, false},
		{"部分重叠40与50", 30, 60, 90, 40 * minute, 50 * minute, false},
		{"暂停不计且总时间守恒", 0, 60, 60, 800000000, 2800000000, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := fixture()
			move(t, s, "start", "a", 0)
			move(t, s, "start", "b", tc.startB*minute)
			if tc.pause {
				move(t, s, "pause", "a", 20*minute)
				move(t, s, "resume", "a", 40*minute)
			}
			a := complete(t, s, "a", "10", tc.endA*minute)
			b := complete(t, s, "b", "40", tc.endB*minute)
			aa, bb := allocation(s, a, "e1"), allocation(s, b, "e1")
			if !aa.Ready || !bb.Ready || aa.Accounted != tc.wantA || bb.Accounted != tc.wantB {
				t.Fatalf("A=%+v B=%+v", aa, bb)
			}
			move(t, s, "approve", a, tc.endB*minute)
			if s.Batch(a).Status != "approved" || s.Batch(b).Status != "approved" {
				t.Fatal("联合审核未原子通过")
			}
			before, _ := json.Marshal(s.Allocations)
			s.Recalculate(base + 200*minute)
			after, _ := json.Marshal(s.Allocations)
			if string(before) != string(after) {
				t.Fatal("已审结果被重算")
			}
		})
	}
}
func TestCollaborationMembershipAndConservation(t *testing.T) {
	for _, unequal := range []bool{false, true} {
		s := fixture()
		s.Versions[0].Collaboration = true
		s.Versions[0].Parallel = false
		for _, e := range []string{"e2", "e3", "e4"} {
			s.Members = append(s.Members, Member{NewID(), "a", e, true})
		}
		move(t, s, "start", "a", 0)
		end := 60 * minute
		if unequal {
			end = 120 * minute
			must(t, s, "transfer", url.Values{"id": {"a"}, "employee_id": {"e4"}}, 60*minute)
			for _, e := range []string{"e1", "e2", "e3"} {
				must(t, s, "leave", url.Values{"id": {"a"}, "employee_id": {e}}, 60*minute)
			}
		}
		batch := complete(t, s, "a", "100", end)
		var quantity, money int64
		for i, e := range []string{"e1", "e2", "e3", "e4"} {
			a := allocation(s, batch, e)
			want := 25 * Scale
			if unequal {
				want = 20 * Scale
				if i == 3 {
					want = 40 * Scale
				}
			}
			if a.Good != want || a.Accounted != a.Raw {
				t.Fatalf("%s got %+v want %d", e, a, want)
			}
			quantity += a.Good
			money += a.Cents
		}
		if quantity != 100*Scale || money != 15000 {
			t.Fatalf("quantity=%d cents=%d", quantity, money)
		}
	}
}
func TestBatch30_40_30AndFrozenPrice(t *testing.T) {
	s := fixture()
	move(t, s, "start", "a", 0)
	must(t, s, "report", url.Values{"id": {"a"}, "good": {"30"}}, 20*minute)
	s.Versions = append(s.Versions, Version{"new", "a", 999, 999000000, 4 * Scale, false, false, base + 21*minute, "admin", "a", 1})
	must(t, s, "report", url.Values{"id": {"a"}, "good": {"40"}}, 40*minute)
	complete(t, s, "a", "30", 60*minute)
	var q, raw, money int64
	for _, a := range s.Allocations {
		q += a.Good
		raw += a.Raw
		money += a.Cents
	}
	if q != 100*Scale || raw != 60*minute || money != 15000 || s.Standard(s.Dispatch("a")).Price != 150 {
		t.Fatalf("q %d raw %d money %d", q, raw, money)
	}
	if len(s.Batches) != 3 {
		t.Fatal("分批次数不正确")
	}
}
func TestJoinPauseTransferAndPermissions(t *testing.T) {
	s := fixture()
	s.Versions[0].Collaboration = true
	s.Versions[0].Parallel = false
	move(t, s, "start", "a", 0)
	if _, err := s.Apply(Actor{"e2", "employee"}, "pause", url.Values{"id": {"a"}, "reason": {"x"}}, base+minute); err == nil {
		t.Fatal("普通成员越权")
	}
	if _, err := s.Apply(boss, "start", url.Values{"id": {"b"}}, base+minute); err == nil {
		t.Fatal("协作与并行未拦截")
	}
	move(t, s, "pause", "a", 20*minute)
	must(t, s, "join", url.Values{"id": {"a"}, "employee_id": {"e2"}}, 25*minute)
	move(t, s, "resume", "a", 30*minute)
	if _, err := s.Apply(boss, "leave", url.Values{"id": {"a"}, "employee_id": {"e1"}}, base+40*minute); err == nil {
		t.Fatal("组长未移交就离开")
	}
	batch := complete(t, s, "a", "70", 60*minute)
	a, b := allocation(s, batch, "e1"), allocation(s, batch, "e2")
	if a.Raw != 50*minute || b.Raw != 30*minute || a.Good != 437500 || b.Good != 262500 {
		t.Fatalf("%+v %+v", a, b)
	}
}
func TestPendingRevisionAndNoDuplicateQuantity(t *testing.T) {
	s := fixture()
	move(t, s, "start", "a", 0)
	move(t, s, "start", "b", 0)
	must(t, s, "report", url.Values{"id": {"a"}, "good": {"10"}}, 60*minute)
	batch := s.Batches[0].ID
	if allocation(s, batch, "e1").Ready {
		t.Fatal("未报数量未阻断")
	}
	if _, err := s.Apply(boss, "approve", url.Values{"id": {batch}}, base+60*minute); err == nil {
		t.Fatal("不完整组获审核")
	}
	move(t, s, "pause", "a", 60*minute)
	complete(t, s, "b", "40", 60*minute)
	move(t, s, "reject", batch, 61*minute)
	must(t, s, "revise", url.Values{"id": {batch}, "good": {"20"}, "reason": {"核实修正"}}, 62*minute)
	if len(s.Batches) != 2 || s.Batches[0].Good != 10*Scale || s.EffectiveGood(batch) != 20*Scale {
		t.Fatal("原始值/修正链错误")
	}
	move(t, s, "approve", batch, 63*minute)
	if _, err := s.Apply(boss, "revise", url.Values{"id": {batch}, "good": {"50"}, "reason": {"不应覆盖"}}, base+64*minute); err == nil {
		t.Fatal("已审核允许覆盖")
	}
}
func TestDispatchMultiplierDefaultsAndHistoricalVersion(t *testing.T) {
	s := fixture()
	s.Dispatches = nil
	s.Members = nil
	s.Snapshots = nil
	s.Versions[1].Multiplier = 4 * Scale
	id := must(t, s, "dispatch", url.Values{"order_id": {"o"}, "operation_id": {"b"}, "quantity": {"400"}, "members": {"e1"}}, minute)
	if s.Snapshot(s.Dispatch(id).SnapshotID).Plan != 400*Scale {
		t.Fatal("100×4换算失败")
	}
	if _, err := s.Apply(boss, "dispatch", url.Values{"order_id": {"o"}, "operation_id": {"b"}, "quantity": {"1"}, "members": {"e2"}}, base+2*minute); err == nil {
		t.Fatal("累计超派未拦截")
	}
}
func TestLargestRemainderAndDecimal(t *testing.T) {
	got := Split(10000, []string{"e1", "e2", "e3"}, []*big.Int{big.NewInt(1), big.NewInt(1), big.NewInt(1)})
	if got[0] != 3334 || got[1] != 3333 || got[2] != 3333 {
		t.Fatal(got)
	}
	for _, bad := range []string{"-1", "NaN", "1e3", "1.00001", "+1", ".5", "1,000", "99999999999999999999"} {
		if _, err := Decimal(bad, 4); err == nil {
			t.Fatalf("accepted %s", bad)
		}
	}
	if n, err := Decimal("12.3456", 4); err != nil || n != 123456 {
		t.Fatal(n, err)
	}
}
func TestZeroStandardRetainsRawPending(t *testing.T) {
	s := fixture()
	s.Versions[0].Standard = 0
	move(t, s, "start", "a", 0)
	b := complete(t, s, "a", "10", minute)
	a := allocation(s, b, "e1")
	if a.Ready || a.Raw != minute || a.Reason == "" {
		t.Fatal(a)
	}
	must(t, s, "resolve", url.Values{"id": {b}, "standard": {"120"}, "reason": {"现场核对，人工异常依据"}}, 2*minute)
	a = allocation(s, b, "e1")
	if !a.Ready || a.Accounted != minute || s.Versions[0].Standard != 0 || len(s.Resolutions) != 1 {
		t.Fatal("异常处理未留痕或覆盖原标准", a)
	}
}

func TestTimeCorrectionUnionPreservesRawAndQuantities(t *testing.T) {
	s := fixture()
	s.Versions[0].Collaboration = true
	s.Members = append(s.Members, Member{"m2", "a", "e2", true})
	move(t, s, "start", "a", 0)
	batch := complete(t, s, "a", "100", 60*minute)
	local := func(n int64) string {
		return time.UnixMicro(base + n*minute).In(time.FixedZone("CST", 8*3600)).Format("2006-01-02T15:04:05.000000")
	}
	for _, bounds := range [][2]int64{{10, 20}, {15, 25}} {
		must(t, s, "trim", url.Values{"id": {batch}, "employee_id": {"e1"}, "start": {local(bounds[0])}, "end": {local(bounds[1])}, "reason": {"设备离岗核实"}}, 61*minute)
	}
	a, b := allocation(s, batch, "e1"), allocation(s, batch, "e2")
	if a.Raw != 60*minute || a.Effective != 45*minute || a.Accounted != 45*minute || a.Good != 428571 || b.Good != 571429 || a.Cents+b.Cents != 15000 || len(s.Trims) != 2 {
		t.Fatalf("A=%+v B=%+v", a, b)
	}
	move(t, s, "approve", batch, 62*minute)
	if _, err := s.Apply(boss, "trim", url.Values{"id": {batch}, "employee_id": {"e1"}, "start": {local(30)}, "end": {local(35)}, "reason": {"should reject"}}, base+63*minute); err == nil {
		t.Fatal("已审时间可修改")
	}
}
