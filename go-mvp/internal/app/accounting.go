package app

import (
	"fmt"
	"math/big"
	"sort"
	"strings"
)

const AlgorithmVersion = "interval-v2"

// Split uses largest remainders, with stable keys breaking ties. All totals are conserved.
func Split(total int64, keys []string, weights []*big.Int) []int64 {
	out := make([]int64, len(keys))
	sum := new(big.Int)
	for _, w := range weights {
		sum.Add(sum, w)
	}
	if total == 0 || sum.Sign() == 0 {
		return out
	}
	type remainder struct {
		i int
		r *big.Int
	}
	rs := make([]remainder, len(keys))
	var used int64
	for i, w := range weights {
		n := new(big.Int).Mul(big.NewInt(total), w)
		q, r := new(big.Int), new(big.Int)
		q.QuoRem(n, sum, r)
		out[i] = q.Int64()
		used += out[i]
		rs[i] = remainder{i, r}
	}
	sort.Slice(rs, func(i, j int) bool {
		c := rs[i].r.Cmp(rs[j].r)
		if c == 0 {
			return keys[rs[i].i] < keys[rs[j].i]
		}
		return c > 0
	})
	for i := int64(0); i < total-used; i++ {
		out[rs[i].i]++
	}
	return out
}
func roundedProduct(a, b, div int64) int64 {
	n := new(big.Int).Mul(big.NewInt(a), big.NewInt(b))
	n.Add(n, big.NewInt(div/2))
	return n.Quo(n, big.NewInt(div)).Int64()
}
func (s *State) Components(now int64) (map[string]string, map[string]string) {
	parent := map[string]string{}
	blocked := map[string][]string{}
	for _, b := range s.Batches {
		parent[b.ID] = b.ID
		if b.Status == "rejected" {
			blocked[b.ID] = append(blocked[b.ID], "批次 "+b.ID+" 被驳回，请修正重报")
		}
	}
	var root func(string) string
	root = func(x string) string {
		if parent[x] != x {
			parent[x] = root(parent[x])
		}
		return parent[x]
	}
	// ponytail: quadratic interval scan fits a workshop MVP; use indexed interval queries when history grows.
	working := s.WorkingSpans()
	for i, a := range working {
		ae := a.End
		if ae == 0 {
			ae = now
		}
		if ae <= a.Start {
			continue
		}
		for _, b := range working[i+1:] {
			be := b.End
			if be == 0 {
				be = now
			}
			if a.EmployeeID != b.EmployeeID || a.Start >= be || b.Start >= ae {
				continue
			}
			if a.BatchID != nil && b.BatchID != nil {
				ra, rb := root(*a.BatchID), root(*b.BatchID)
				parent[ra] = rb
			} else if a.BatchID != nil {
				blocked[*a.BatchID] = append(blocked[*a.BatchID], "待报工："+s.TaskLabel(b.DispatchID)+" 缺少重叠区间数量")
			} else if b.BatchID != nil {
				blocked[*b.BatchID] = append(blocked[*b.BatchID], "待报工："+s.TaskLabel(a.DispatchID)+" 缺少重叠区间数量")
			}
		}
	}
	roots := map[string]string{}
	for id := range parent {
		roots[id] = root(id)
	}
	groupReasons := map[string][]string{}
	versions := map[string]map[string]bool{}
	for _, b := range s.Batches {
		r := roots[b.ID]
		groupReasons[r] = append(groupReasons[r], blocked[b.ID]...)
		if versions[r] == nil {
			versions[r] = map[string]bool{}
		}
		versions[r][b.AccountingVersion()] = true
	}
	for r, vs := range versions {
		if len(vs) > 1 {
			groupReasons[r] = append(groupReasons[r], "版本依赖：关联批次核算版本不同，请在核算版本处理中记录原因并统一整组版本；已审版本须保留")
		}
		for v := range vs {
			if v != "interval-v1" && v != AlgorithmVersion {
				groupReasons[r] = append(groupReasons[r], "未知核算版本："+v)
			}
		}
	}
	reasons := map[string]string{}
	for r, messages := range groupReasons {
		sort.Strings(messages)
		var unique []string
		for _, msg := range messages {
			if len(unique) == 0 || unique[len(unique)-1] != msg {
				unique = append(unique, msg)
			}
		}
		reasons[r] = strings.Join(unique, "；")
	}
	return roots, reasons
}
func (s *State) Recalculate(now int64) {
	roots, reasons := s.Components(now)
	byBatch := map[string][]*Allocation{}
	// Rebuild unapproved results; approved rows are immutable business facts.
	kept := s.Allocations[:0]
	for _, a := range s.Allocations {
		if s.Batch(a.BatchID).Status == "approved" {
			kept = append(kept, a)
		}
	}
	s.Allocations = kept
	for i := range s.Batches {
		b := &s.Batches[i]
		if b.Status == "approved" {
			continue
		}
		raw := map[string]int64{}
		effective := map[string]int64{}
		for _, sp := range s.WorkingSpans() {
			if sp.BatchID != nil && *sp.BatchID == b.ID {
				effective[sp.EmployeeID] += sp.End - sp.Start
			}
		}
		for _, sp := range s.Spans {
			if sp.BatchID != nil && *sp.BatchID == b.ID {
				raw[sp.EmployeeID] += sp.End - sp.Start
			}
		}
		keys := make([]string, 0, len(raw))
		for id := range raw {
			keys = append(keys, id)
		}
		sort.Strings(keys)
		weights := make([]*big.Int, len(keys))
		var total int64
		for j, id := range keys {
			weights[j] = big.NewInt(effective[id])
			total += effective[id]
		}
		if total == 0 {
			reasons[roots[b.ID]] += "；批次 " + b.ID + " 没有有效参与时间，请审查原始区间与时间修正"
		}
		g, bad := s.Counts(b)
		gs, bs := Split(g, keys, weights), Split(bad, keys, weights)
		v := s.Standard(s.Dispatch(b.DispatchID))
		standard, _ := s.AccountingStandard(b.ID)
		if standard == 0 {
			reasons[roots[b.ID]] += "；批次 " + b.ID + " 冻结标准为零，请在零标准异常处理中追加核实依据"
		}
		cents := Split(roundedProduct(g, v.Price, Scale), keys, weights)
		for j, id := range keys {
			s.Allocations = append(s.Allocations, Allocation{Algorithm: b.AccountingVersion(), ID: b.ID + ":" + id, BatchID: b.ID, EmployeeID: id, Good: gs[j], Bad: bs[j], Raw: raw[id], Effective: effective[id], Cents: cents[j]})
		}
	}
	// Take pointers only after append stops, to avoid pointers into a reallocated slice.
	for i := range s.Allocations {
		a := &s.Allocations[i]
		byBatch[a.BatchID] = append(byBatch[a.BatchID], a)
	}
	// Use exact D02 ratios for time weights, never the four-place display quantities.
	exactWeight := func(a *Allocation) *big.Rat {
		var total int64
		for _, x := range byBatch[a.BatchID] {
			total += x.Effective
		}
		if total == 0 {
			return new(big.Rat)
		}
		b := s.Batch(a.BatchID)
		g, bad := s.Counts(b)
		std, _ := s.AccountingStandard(b.ID)
		n := new(big.Int).Mul(big.NewInt(std), big.NewInt(g+bad))
		n.Mul(n, big.NewInt(a.Effective))
		weight := new(big.Rat).SetFrac(n, big.NewInt(total))
		if b.AccountingVersion() == AlgorithmVersion {
			if a.Effective == 0 {
				return new(big.Rat)
			}
			// v2 projects exact personal quantity over its effective participation.
			// The fragment duration is common to active tasks and cancels out of
			// their ratio: standard * quantity * personalTime/teamTime / personalTime.
			// This assumes uniform output within each batch, not measured fragment output.
			weight.Quo(weight, new(big.Rat).SetInt64(a.Effective))
		}
		return weight
	}
	for _, e := range s.Employees {
		var spans []Span
		var boundaries []int64
		for _, sp := range s.WorkingSpans() {
			if sp.EmployeeID == e.ID && sp.BatchID != nil && sp.End > sp.Start {
				spans = append(spans, sp)
				boundaries = append(boundaries, sp.Start, sp.End)
			}
		}
		sort.Slice(boundaries, func(i, j int) bool { return boundaries[i] < boundaries[j] })
		for i := 1; i < len(boundaries); i++ {
			start, end := boundaries[i-1], boundaries[i]
			if start == end {
				continue
			}
			var active []*Allocation
			var keys []string
			var ratios []*big.Rat
			for _, sp := range spans {
				if sp.Start <= start && sp.End >= end {
					for _, a := range byBatch[*sp.BatchID] {
						if a.EmployeeID != e.ID {
							continue
						}
						active = append(active, a)
						keys = append(keys, a.BatchID)
						ratios = append(ratios, exactWeight(a))
					}
				}
			}
			if len(active) == 0 {
				continue
			}
			den := big.NewInt(1)
			for _, r := range ratios {
				den.Mul(den, r.Denom())
			}
			ws := make([]*big.Int, len(ratios))
			for j, r := range ratios {
				ws[j] = new(big.Int).Mul(r.Num(), new(big.Int).Quo(new(big.Int).Set(den), r.Denom()))
			}
			sum := new(big.Int)
			for _, w := range ws {
				sum.Add(sum, w)
			}
			if sum.Sign() == 0 {
				for _, a := range active {
					reason := "并行权重合计为零，请核对关联批次数量与标准"
					if !strings.Contains(reasons[roots[a.BatchID]], reason) {
						reasons[roots[a.BatchID]] += "；" + reason
					}
				}
				continue
			}
			values := Split(end-start, keys, ws)
			for j, a := range active {
				if s.Batch(a.BatchID).Status != "approved" {
					a.Accounted += values[j]
				}
			}
		}
	}
	for i := range s.Allocations {
		a := &s.Allocations[i]
		if s.Batch(a.BatchID).Status == "approved" {
			continue
		}
		a.Reason = strings.TrimPrefix(reasons[roots[a.BatchID]], "；")
		a.Ready = a.Reason == ""
		if a.Ready {
			_, a.Reason = s.AccountingStandard(a.BatchID)
		}
	}
}

func (s *State) TaskLabel(id string) string {
	d := s.Dispatch(id)
	return fmt.Sprintf("%s / %s（派工 %s）", s.Order(s.Snapshot(d.SnapshotID).OrderID).ExternalID, s.Standard(d).Name, id)
}

// WorkingSpans subtracts the union of append-only corrections; original clock records stay intact.
func (s *State) WorkingSpans() []Span {
	var out []Span
	for _, sp := range s.Spans {
		pieces := []Span{sp}
		if sp.BatchID != nil {
			for _, trim := range s.Trims {
				if trim.BatchID != *sp.BatchID || trim.EmployeeID != sp.EmployeeID {
					continue
				}
				var next []Span
				for _, p := range pieces {
					if trim.End <= p.Start || trim.Start >= p.End {
						next = append(next, p)
						continue
					}
					if trim.Start > p.Start {
						left := p
						left.End = trim.Start
						next = append(next, left)
					}
					if trim.End < p.End {
						right := p
						right.Start = trim.End
						next = append(next, right)
					}
				}
				pieces = next
			}
		}
		out = append(out, pieces...)
	}
	return out
}
