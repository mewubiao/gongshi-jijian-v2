package app

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

const Scale int64 = 10000

type Center struct {
	ID   string `json:"id"`
	Code string `json:"code"`
	Name string `json:"name"`
}
type Employee struct {
	ID       string `json:"id"`
	Code     string `json:"code"`
	Name     string `json:"name"`
	Card     string `json:"card"`
	Badge    string `json:"badge"`
	CenterID string `json:"center_id"`
	Active   bool   `json:"active"`
}
type Product struct {
	ID   string `json:"id"`
	Code string `json:"code"`
	Name string `json:"name"`
}
type Operation struct {
	ID        string `json:"id"`
	ProductID string `json:"product_id"`
	Seq       int64  `json:"seq"`
	Name      string `json:"name"`
}
type Version struct {
	ID            string `json:"id"`
	OperationID   string `json:"operation_id"`
	Price         int64  `json:"price"`
	Standard      int64  `json:"standard"`
	Multiplier    int64  `json:"multiplier"`
	Parallel      bool   `json:"parallel"`
	Collaboration bool   `json:"collaboration"`
	At            int64  `json:"at"`
	Actor         string `json:"actor"`
	Name          string `json:"name"`
	Seq           int64  `json:"seq"`
}
type Order struct {
	ID         string `json:"id"`
	ExternalID string `json:"external_id"`
	ProductID  string `json:"product_id"`
	CenterID   string `json:"center_id"`
	Quantity   int64  `json:"quantity"`
	Status     string `json:"status"`
	Source     string `json:"source"`
}
type Snapshot struct {
	ID          string `json:"id"`
	OrderID     string `json:"order_id"`
	OperationID string `json:"operation_id"`
	VersionID   string `json:"version_id"`
	Plan        int64  `json:"plan"`
	At          int64  `json:"at"`
}
type Dispatch struct {
	ID         string `json:"id"`
	SnapshotID string `json:"snapshot_id"`
	Quantity   int64  `json:"quantity"`
	LeaderID   string `json:"leader_id"`
	Status     string `json:"status"`
	At         int64  `json:"at"`
}
type Member struct {
	ID         string `json:"id"`
	DispatchID string `json:"dispatch_id"`
	EmployeeID string `json:"employee_id"`
	Present    bool   `json:"present"`
}
type Span struct {
	ID         string  `json:"id"`
	DispatchID string  `json:"dispatch_id"`
	EmployeeID string  `json:"employee_id"`
	Start      int64   `json:"start_us"`
	End        int64   `json:"end_us"`
	BatchID    *string `json:"batch_id"`
}
type Batch struct {
	ID         string `json:"id"`
	DispatchID string `json:"dispatch_id"`
	Good       int64  `json:"good"`
	Bad        int64  `json:"bad"`
	At         int64  `json:"at"`
	Status     string `json:"status"`
	Reason     string `json:"reason"`
}
type Revision struct {
	ID      string `json:"id"`
	BatchID string `json:"batch_id"`
	Good    int64  `json:"good"`
	Bad     int64  `json:"bad"`
	Reason  string `json:"reason"`
	Actor   string `json:"actor"`
	At      int64  `json:"at"`
}
type Trim struct {
	ID         string `json:"id"`
	BatchID    string `json:"batch_id"`
	EmployeeID string `json:"employee_id"`
	Start      int64  `json:"start_us"`
	End        int64  `json:"end_us"`
	Reason     string `json:"reason"`
	Actor      string `json:"actor"`
	At         int64  `json:"at"`
}
type Resolution struct {
	ID       string `json:"id"`
	BatchID  string `json:"batch_id"`
	Standard int64  `json:"standard"`
	Reason   string `json:"reason"`
	Actor    string `json:"actor"`
	At       int64  `json:"at"`
}
type Allocation struct {
	Algorithm  string `json:"algorithm"`
	Effective  int64  `json:"effective_us"`
	ID         string `json:"id"`
	BatchID    string `json:"batch_id"`
	EmployeeID string `json:"employee_id"`
	Good       int64  `json:"good"`
	Bad        int64  `json:"bad"`
	Raw        int64  `json:"raw_us"`
	Accounted  int64  `json:"accounted_us"`
	Cents      int64  `json:"cents"`
	Ready      bool   `json:"ready"`
	Reason     string `json:"reason"`
}
type Audit struct {
	ID      string          `json:"id"`
	Actor   string          `json:"actor"`
	Action  string          `json:"action"`
	Target  string          `json:"target"`
	At      int64           `json:"at"`
	Payload json.RawMessage `json:"payload"`
}
type Request struct {
	ID          string `json:"id"`
	Actor       string `json:"actor"`
	Fingerprint string `json:"fingerprint"`
	Result      string `json:"result"`
}
type State struct {
	Centers     []Center     `json:"centers"`
	Employees   []Employee   `json:"employees"`
	Products    []Product    `json:"products"`
	Operations  []Operation  `json:"operations"`
	Versions    []Version    `json:"versions"`
	Orders      []Order      `json:"orders"`
	Snapshots   []Snapshot   `json:"snapshots"`
	Dispatches  []Dispatch   `json:"dispatches"`
	Members     []Member     `json:"members"`
	Batches     []Batch      `json:"batches"`
	Revisions   []Revision   `json:"revisions"`
	Resolutions []Resolution `json:"resolutions"`
	Trims       []Trim       `json:"trims"`
	Spans       []Span       `json:"spans"`
	Allocations []Allocation `json:"allocations"`
	Audits      []Audit      `json:"audits"`
	Requests    []Request    `json:"requests"`
}

func NewID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

// Decimal parses unsigned fixed point values without a floating point payroll path.
func Decimal(s string, digits int) (int64, error) {
	p := strings.Split(strings.TrimSpace(s), ".")
	if len(p) > 2 || len(p[0]) == 0 {
		return 0, fmt.Errorf("请输入非负数字")
	}
	for _, part := range p {
		for _, c := range part {
			if c < '0' || c > '9' {
				return 0, fmt.Errorf("请输入非负数字")
			}
		}
	}
	frac := ""
	if len(p) == 2 {
		frac = p[1]
	}
	if len(frac) > digits {
		return 0, fmt.Errorf("最多保留 %d 位小数", digits)
	}
	v, err := strconv.ParseInt(p[0]+frac+strings.Repeat("0", digits-len(frac)), 10, 64)
	if err != nil || v > 1000000000000 {
		return 0, fmt.Errorf("数字超出范围")
	}
	return v, nil
}
func Quantity(n int64) string { return fmt.Sprintf("%d.%04d", n/Scale, n%Scale) }
func Money(n int64) string    { return fmt.Sprintf("%d.%02d", n/100, n%100) }
func Date(n int64) string {
	return time.UnixMicro(n).In(time.FixedZone("CST", 8*3600)).Format("2006-01-02 15:04:05")
}
func find[T any](xs []T, id string, key func(T) string) *T {
	for i := range xs {
		if key(xs[i]) == id {
			return &xs[i]
		}
	}
	return nil
}
func (s *State) Employee(id string) *Employee {
	return find(s.Employees, id, func(x Employee) string { return x.ID })
}
func (s *State) Center(id string) *Center {
	return find(s.Centers, id, func(x Center) string { return x.ID })
}
func (s *State) Product(id string) *Product {
	return find(s.Products, id, func(x Product) string { return x.ID })
}
func (s *State) Operation(id string) *Operation {
	return find(s.Operations, id, func(x Operation) string { return x.ID })
}
func (s *State) Order(id string) *Order {
	return find(s.Orders, id, func(x Order) string { return x.ID })
}
func (s *State) Snapshot(id string) *Snapshot {
	return find(s.Snapshots, id, func(x Snapshot) string { return x.ID })
}
func (s *State) Version(id string) *Version {
	return find(s.Versions, id, func(x Version) string { return x.ID })
}
func (s *State) Dispatch(id string) *Dispatch {
	return find(s.Dispatches, id, func(x Dispatch) string { return x.ID })
}
func (s *State) Batch(id string) *Batch {
	return find(s.Batches, id, func(x Batch) string { return x.ID })
}
func (s *State) CurrentVersion(op string) *Version {
	var v *Version
	for i := range s.Versions {
		x := &s.Versions[i]
		if x.OperationID == op && (v == nil || x.At >= v.At) {
			v = x
		}
	}
	return v
}
func (s *State) Standard(d *Dispatch) *Version { return s.Version(s.Snapshot(d.SnapshotID).VersionID) }
func (s *State) Counts(b *Batch) (int64, int64) {
	g, bad := b.Good, b.Bad
	var at int64
	for _, r := range s.Revisions {
		if r.BatchID == b.ID && r.At >= at {
			g, bad, at = r.Good, r.Bad, r.At
		}
	}
	return g, bad
}
func (s *State) IsMember(d, e string) bool {
	for _, m := range s.Members {
		if m.DispatchID == d && m.EmployeeID == e {
			return true
		}
	}
	return false
}
func (s *State) EffectiveGood(id string) int64 { g, _ := s.Counts(s.Batch(id)); return g }
func (s *State) EffectiveBad(id string) int64  { _, b := s.Counts(s.Batch(id)); return b }
func (s *State) OverQuantity(id string) bool {
	var n int64
	for _, b := range s.Batches {
		if b.DispatchID == id {
			g, bad := s.Counts(&b)
			n += g + bad
		}
	}
	return n > s.Dispatch(id).Quantity
}
func (s *State) Event(actor, action, target string, at int64, payload any) {
	p, err := json.Marshal(payload)
	if err != nil {
		panic(err)
	}
	s.Audits = append(s.Audits, Audit{NewID(), actor, action, target, at, p})
}

func (s *State) AccountingStandard(batch string) (int64, string) {
	b := s.Batch(batch)
	standard := s.Standard(s.Dispatch(b.DispatchID)).Standard
	if standard != 0 {
		return standard, ""
	}
	var latest *Resolution
	for i := range s.Resolutions {
		r := &s.Resolutions[i]
		if r.BatchID == batch && (latest == nil || r.At >= latest.At) {
			latest = r
		}
	}
	if latest != nil {
		return latest.Standard, "人工异常核算：" + latest.Reason
	}
	return 0, ""
}
