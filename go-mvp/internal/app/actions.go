package app

import (
	"encoding/csv"
	"fmt"
	"io"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Actor struct{ ID, Role string }

func (a Actor) Admin() bool { return a.Role == "admin" || a.Role == "supervisor" }
func required(f url.Values, keys ...string) error {
	for _, k := range keys {
		if strings.TrimSpace(f.Get(k)) == "" {
			return fmt.Errorf("请填写 %s", k)
		}
	}
	return nil
}
func quantity(f url.Values, key string) (int64, error) {
	v := f.Get(key)
	if v == "" {
		v = "0"
	}
	return Decimal(v, 4)
}
func (s *State) conflict(d *Dispatch, employee string) error {
	for _, member := range s.Members {
		if member.EmployeeID != employee || !member.Present || member.DispatchID == d.ID {
			continue
		}
		other := s.Dispatch(member.DispatchID)
		if other.Status != "running" && other.Status != "paused" {
			continue
		}
		if !s.Standard(d).Parallel || !s.Standard(other).Parallel {
			return fmt.Errorf("员工 %s 与 %s 冲突：涉及的每项任务都须开启并行；暂停仍保留占用", s.Employee(employee).Name, s.TaskLabel(other.ID))
		}
	}
	return nil
}
func (s *State) open(d *Dispatch, employee string, now int64) error {
	if err := s.conflict(d, employee); err != nil {
		return err
	}
	s.Spans = append(s.Spans, Span{ID: NewID(), DispatchID: d.ID, EmployeeID: employee, Start: now})
	return nil
}
func (s *State) close(d, employee string, now int64) {
	for i := range s.Spans {
		sp := &s.Spans[i]
		if sp.DispatchID == d && sp.End == 0 && (employee == "" || sp.EmployeeID == employee) {
			sp.End = now
		}
	}
}
func (s *State) Apply(a Actor, action string, f url.Values, now int64) (string, error) {
	id := f.Get("id")
	result := id
	switch action {
	case "center", "employee", "product", "operation", "dispatch", "import", "approve", "reject", "revise", "resolve", "trim", "accounting-version":
		if !a.Admin() && action != "revise" {
			return "", fmt.Errorf("需要管理员或主管权限")
		}
	}
	if action == "trim" || action == "resolve" || action == "revise" {
		if b := s.Batch(id); b != nil && b.Status != "approved" {
			roots, _ := s.Components(now)
			for _, x := range s.Batches {
				if roots[x.ID] == roots[id] && x.Status == "approved" {
					return "", fmt.Errorf("关联批次 %s 已审核锁定；不能改变数量或时间核算依据", x.ID)
				}
			}
		}
	}

	switch action {
	case "center":
		if err := required(f, "code", "name"); err != nil {
			return "", err
		}
		c := s.Center(id)
		if c == nil {
			result = NewID()
			s.Centers = append(s.Centers, Center{result, f.Get("code"), f.Get("name")})
		} else {
			c.Code = f.Get("code")
			c.Name = f.Get("name")
		}
	case "product":
		if err := required(f, "code", "name"); err != nil {
			return "", err
		}
		p := s.Product(id)
		if p == nil {
			result = NewID()
			s.Products = append(s.Products, Product{result, f.Get("code"), f.Get("name")})
		} else {
			p.Code = f.Get("code")
			p.Name = f.Get("name")
		}
	case "employee":
		if err := required(f, "code", "name", "card", "badge", "center_id"); err != nil {
			return "", err
		}
		if s.Center(f.Get("center_id")) == nil {
			return "", fmt.Errorf("工作中心不存在")
		}
		e := s.Employee(id)
		n := Employee{id, f.Get("code"), f.Get("name"), f.Get("card"), f.Get("badge"), f.Get("center_id"), f.Get("active") == "on"}
		for _, x := range s.Employees {
			if x.ID != id && (x.Card == n.Card || x.Badge == n.Badge || x.Card == n.Badge || x.Badge == n.Card || x.Code == n.Code) {
				return "", fmt.Errorf("员工编码、卡号或工牌码重复")
			}
		}
		if e == nil {
			n.ID = NewID()
			result = n.ID
			s.Employees = append(s.Employees, n)
		} else {
			*e = n
		}
	case "operation":
		if err := required(f, "product_id", "seq", "name", "price", "standard"); err != nil {
			return "", err
		}
		if s.Product(f.Get("product_id")) == nil {
			return "", fmt.Errorf("产品不存在")
		}
		price, err := Decimal(f.Get("price"), 2)
		if err != nil {
			return "", err
		}
		if price > 100000000 {
			return "", fmt.Errorf("单价超出范围")
		}
		seconds, err := Decimal(f.Get("standard"), 6)
		if err != nil || seconds > 86400000000 {
			return "", fmt.Errorf("标准工时须为 0–86400 秒，最多六位小数")
		}
		mult := f.Get("multiplier")
		if mult == "" {
			mult = "1"
		}
		m, err := Decimal(mult, 4)
		if err != nil || m <= 0 || m > 1000000000 {
			return "", fmt.Errorf("单件数量无效")
		}
		seq, err := strconv.ParseInt(f.Get("seq"), 10, 64)
		if err != nil || seq <= 0 {
			return "", fmt.Errorf("工序顺序须为正整数")
		}
		op := s.Operation(id)
		for _, x := range s.Operations {
			if x.ID != id && x.ProductID == f.Get("product_id") && x.Seq == seq {
				return "", fmt.Errorf("同一产品工序顺序重复")
			}
		}
		if op == nil {
			result = NewID()
			s.Operations = append(s.Operations, Operation{result, f.Get("product_id"), seq, f.Get("name")})
		} else {
			if op.ProductID != f.Get("product_id") {
				return "", fmt.Errorf("已建工序不可改变所属产品")
			}
			op.Seq = seq
			op.Name = f.Get("name")
		}
		versionAt := now
		if latest := s.CurrentVersion(result); latest != nil && versionAt <= latest.At {
			versionAt = latest.At + 1
		}
		s.Versions = append(s.Versions, Version{NewID(), result, price, seconds, m, f.Get("parallel") == "on", f.Get("collaboration") == "on", versionAt, a.ID, f.Get("name"), seq})
	case "import":
		rows, err := (CSVSource{Reader: strings.NewReader(f.Get("csv"))}).ReadOrders()
		if err != nil {
			return "", err
		}
		if err = s.Import(rows); err != nil {
			return "", err
		}
		result = fmt.Sprintf("导入 %d 条", len(rows))
	case "dispatch":
		order := s.Order(f.Get("order_id"))
		op := s.Operation(f.Get("operation_id"))
		if order == nil || op == nil || op.ProductID != order.ProductID || order.Status != "open" {
			return "", fmt.Errorf("请选择未关闭工单的对应工序")
		}
		q, err := quantity(f, "quantity")
		if err != nil || q <= 0 {
			return "", fmt.Errorf("派工量须大于零")
		}
		v := s.CurrentVersion(op.ID)
		if v == nil {
			return "", fmt.Errorf("工序缺少有效版本")
		}
		var snap *Snapshot
		for i := range s.Snapshots {
			if s.Snapshots[i].OrderID == order.ID && s.Snapshots[i].OperationID == op.ID {
				snap = &s.Snapshots[i]
				v = s.Version(snap.VersionID)
			}
		}
		if snap == nil {
			for _, x := range s.Snapshots {
				if x.OrderID == order.ID {
					return "", fmt.Errorf("该工单工艺已冻结，不能追加新档案工序")
				}
			}
			for _, route := range s.Operations {
				if route.ProductID != order.ProductID {
					continue
				}
				version := s.CurrentVersion(route.ID)
				if version == nil {
					return "", fmt.Errorf("工艺缺少版本")
				}
				plan := roundedProduct(order.Quantity, version.Multiplier, Scale)
				if plan == 0 {
					return "", fmt.Errorf("换算后计划量为零")
				}
				s.Snapshots = append(s.Snapshots, Snapshot{NewID(), order.ID, route.ID, version.ID, plan, now})
			}
			for i := range s.Snapshots {
				if s.Snapshots[i].OrderID == order.ID && s.Snapshots[i].OperationID == op.ID {
					snap = &s.Snapshots[i]
					v = s.Version(snap.VersionID)
				}
			}
		}
		var reserved int64
		for _, x := range s.Dispatches {
			if x.SnapshotID == snap.ID {
				reserved += x.Quantity
			}
		}
		if reserved+q > snap.Plan {
			return "", fmt.Errorf("超过工序剩余可派量 %s", Quantity(snap.Plan-reserved))
		}
		ids := f["members"]
		if len(ids) == 0 {
			return "", fmt.Errorf("至少选择一名员工")
		}
		if len(ids) > 1 && !v.Collaboration {
			return "", fmt.Errorf("该冻结工序未允许多人协作")
		}
		seen := map[string]bool{}
		for _, e := range ids {
			employee := s.Employee(e)
			if seen[e] || employee == nil || !employee.Active {
				return "", fmt.Errorf("成员重复、不存在或已停用")
			}
			seen[e] = true
		}
		leader := f.Get("leader_id")
		if leader == "" {
			leader = ids[0]
		}
		if !seen[leader] {
			return "", fmt.Errorf("组长必须属于所选成员")
		}
		result = NewID()
		s.Dispatches = append(s.Dispatches, Dispatch{result, snap.ID, q, leader, "ready", now})
		for _, e := range ids {
			s.Members = append(s.Members, Member{NewID(), result, e, true})
		}
	case "start", "pause", "resume", "report", "finish", "join", "leave", "transfer":
		d := s.Dispatch(id)
		if d == nil {
			return "", fmt.Errorf("派工不存在")
		}
		if !a.Admin() && d.LeaderID != a.ID {
			return "", fmt.Errorf("仅当前组长可操作此派工")
		}
		switch action {
		case "start", "resume":
			want := "ready"
			if action == "resume" {
				want = "paused"
			}
			if d.Status != want {
				return "", fmt.Errorf("当前状态不可开工或继续")
			}
			// Validate every member before opening any span (also for direct State callers).
			for _, m := range s.Members {
				if m.DispatchID == id && m.Present {
					if !s.Employee(m.EmployeeID).Active {
						return "", fmt.Errorf("存在停用成员")
					}
					if err := s.conflict(d, m.EmployeeID); err != nil {
						return "", err
					}
				}
			}
			for _, m := range s.Members {
				if m.DispatchID == id && m.Present {
					s.Spans = append(s.Spans, Span{ID: NewID(), DispatchID: id, EmployeeID: m.EmployeeID, Start: now})
				}
			}
			d.Status = "running"
		case "pause":
			if d.Status != "running" {
				return "", fmt.Errorf("仅进行中可暂停")
			}
			if err := required(f, "reason"); err != nil {
				return "", err
			}
			s.close(id, "", now)
			d.Status = "paused"
		case "join", "leave", "transfer":
			if d.Status == "finished" {
				return "", fmt.Errorf("任务已完工")
			}
			employee := f.Get("employee_id")
			e := s.Employee(employee)
			if e == nil || !e.Active {
				return "", fmt.Errorf("成员不存在或停用")
			}
			var m *Member
			for i := range s.Members {
				if s.Members[i].DispatchID == id && s.Members[i].EmployeeID == employee {
					m = &s.Members[i]
				}
			}
			if action == "transfer" {
				if m == nil || !m.Present {
					return "", fmt.Errorf("新组长须在场")
				}
				d.LeaderID = employee
				break
			}
			if !s.Standard(d).Collaboration {
				return "", fmt.Errorf("仅协作工序可变更成员")
			}
			if action == "leave" {
				if employee == d.LeaderID {
					return "", fmt.Errorf("组长离开前请移交")
				}
				if m == nil || !m.Present {
					return "", fmt.Errorf("成员不在场")
				}
				s.close(id, employee, now)
				m.Present = false
			} else {
				if m != nil && m.Present {
					return "", fmt.Errorf("成员已在场")
				}
				if d.Status == "paused" {
					if err := s.conflict(d, employee); err != nil {
						return "", err
					}
				}
				if d.Status == "running" {
					if err := s.open(d, employee, now); err != nil {
						return "", err
					}
				}
				if m == nil {
					s.Members = append(s.Members, Member{NewID(), id, employee, true})
				} else {
					m.Present = true
				}
			}
		case "report":
			if d.Status != "running" && d.Status != "paused" {
				return "", fmt.Errorf("请先开工")
			}
			g, err := quantity(f, "good")
			if err != nil {
				return "", err
			}
			bad, err := quantity(f, "bad")
			if err != nil || g+bad == 0 || g+bad > 1000000000000 {
				return "", fmt.Errorf("本次新增数量须大于零且不超范围")
			}
			s.close(id, "", now)
			var raw int64
			for _, sp := range s.Spans {
				if sp.DispatchID == id && sp.BatchID == nil {
					raw += sp.End - sp.Start
				}
			}
			if raw <= 0 {
				return "", fmt.Errorf("本批次尚无有效参与工时")
			}
			result = NewID()
			s.Batches = append(s.Batches, Batch{Algorithm: AlgorithmVersion, ID: result, DispatchID: id, Good: g, Bad: bad, At: now, Status: "pending"})
			for i := range s.Spans {
				sp := &s.Spans[i]
				if sp.DispatchID == id && sp.BatchID == nil && sp.End > 0 {
					batchID := result
					sp.BatchID = &batchID
				}
			}
			if d.Status == "running" {
				for _, m := range s.Members {
					if m.DispatchID == id && m.Present {
						if err := s.open(d, m.EmployeeID, now); err != nil {
							return "", err
						}
					}
				}
			}
		case "finish":
			if d.Status != "running" && d.Status != "paused" {
				return "", fmt.Errorf("当前状态不可完工")
			}
			// A final incremental quantity can be submitted at the same timestamp as completion.
			if f.Get("good") != "" || f.Get("bad") != "" {
				g, err := quantity(f, "good")
				if err != nil {
					return "", err
				}
				bad, err := quantity(f, "bad")
				if err != nil {
					return "", err
				}
				if g+bad > 0 {
					d.Status = "paused"
					s.close(id, "", now)
					if _, err = s.Apply(a, "report", url.Values{"id": {id}, "good": {f.Get("good")}, "bad": {f.Get("bad")}}, now); err != nil {
						return "", err
					}
				}
			}
			s.close(id, "", now)
			var unreported int64
			for _, sp := range s.Spans {
				if sp.DispatchID == id && sp.BatchID == nil {
					unreported += sp.End - sp.Start
				}
			}
			if unreported > 0 {
				return "", fmt.Errorf("还有未报工时间，请暂停后提交最后一批，再完工")
			}
			var count int64
			for _, b := range s.Batches {
				if b.DispatchID == id {
					g, bad := s.Counts(&b)
					count += g + bad
				}
			}
			if count == 0 {
				return "", fmt.Errorf("请先报工")
			}
			d.Status = "finished"
		}
	case "accounting-version":
		b := s.Batch(id)
		if b == nil || b.Status == "approved" {
			return "", fmt.Errorf("请选择未审核批次")
		}
		if err := required(f, "reason", "algorithm"); err != nil {
			return "", err
		}
		version := f.Get("algorithm")
		if version != "interval-v1" && version != AlgorithmVersion {
			return "", fmt.Errorf("核算版本无效")
		}
		roots, _ := s.Components(now)
		for _, x := range s.Batches {
			if roots[x.ID] == roots[id] && x.Status == "approved" && x.AccountingVersion() != version {
				return "", fmt.Errorf("关联批次 %s 已按 %s 锁定，请选择相同版本；已审历史不可重算", x.ID, x.AccountingVersion())
			}
		}
		// Preserve the complete before/after decision in the ordinary audit event.
		f.Del("version_changes")
		for i := range s.Batches {
			x := &s.Batches[i]
			if roots[x.ID] == roots[id] && x.Status != "approved" {
				f.Add("version_changes", x.ID+":"+x.AccountingVersion()+"→"+version)
				x.Algorithm = version
			}
		}
	case "trim":
		b := s.Batch(id)
		if b == nil || b.Status == "approved" {
			return "", fmt.Errorf("批次不存在或已审核")
		}
		if err := required(f, "reason", "employee_id", "start", "end"); err != nil {
			return "", err
		}
		loc := time.FixedZone("CST", 8*3600)
		start, err := time.ParseInLocation("2006-01-02T15:04:05.999999", f.Get("start"), loc)
		if err != nil {
			return "", fmt.Errorf("修正开始时间须精确到秒")
		}
		end, err := time.ParseInLocation("2006-01-02T15:04:05.999999", f.Get("end"), loc)
		if err != nil || !end.After(start) {
			return "", fmt.Errorf("修正结束时间须晚于开始")
		}
		covered := int64(0)
		for _, sp := range s.Spans {
			if sp.BatchID != nil && *sp.BatchID == id && sp.EmployeeID == f.Get("employee_id") {
				lo, hi := max(sp.Start, start.UnixMicro()), min(sp.End, end.UnixMicro())
				if hi > lo {
					covered += hi - lo
				}
			}
		}
		if covered != end.UnixMicro()-start.UnixMicro() {
			return "", fmt.Errorf("扣除区间须完全落在该员工本批原始有效参与区间内")
		}
		roots, _ := s.Components(now)
		for _, batch := range s.Batches {
			if roots[batch.ID] == roots[id] && batch.Status == "approved" {
				return "", fmt.Errorf("关联批次已锁定，不能改时间")
			}
		}
		s.Trims = append(s.Trims, Trim{NewID(), id, f.Get("employee_id"), start.UnixMicro(), end.UnixMicro(), f.Get("reason"), a.ID, now})
	case "resolve":
		b := s.Batch(id)
		if b == nil || b.Status == "approved" {
			return "", fmt.Errorf("批次不存在或已审核")
		}
		if err := required(f, "reason", "standard"); err != nil {
			return "", err
		}
		standard, err := Decimal(f.Get("standard"), 6)
		if err != nil || standard <= 0 || standard > 86400000000 {
			return "", fmt.Errorf("人工核算标准须大于零且不超过86400秒")
		}
		roots, reasons := s.Components(now)
		if reasons[roots[id]] != "" {
			return "", fmt.Errorf("请先解决关联未报工/驳回批次：%s", reasons[roots[id]])
		}
		found := false
		for _, batch := range s.Batches {
			if roots[batch.ID] == roots[id] && s.Standard(s.Dispatch(batch.DispatchID)).Standard == 0 {
				resolutionAt := now
				for _, r := range s.Resolutions {
					if r.BatchID == batch.ID && r.At >= resolutionAt {
						resolutionAt = r.At + 1
					}
				}
				s.Resolutions = append(s.Resolutions, Resolution{NewID(), batch.ID, standard, f.Get("reason"), a.ID, resolutionAt})
				found = true
			}
		}
		if !found {
			return "", fmt.Errorf("此组没有零标准异常，不能替换冻结标准")
		}
	case "approve", "reject", "revise":
		b := s.Batch(id)
		if b == nil {
			return "", fmt.Errorf("报工不存在")
		}
		if b.Status == "approved" {
			return "", fmt.Errorf("已审核批次已锁定；后续工资调整需独立审批，不可覆盖")
		}
		if action == "revise" {
			d := s.Dispatch(b.DispatchID)
			if !a.Admin() && a.ID != d.LeaderID {
				return "", fmt.Errorf("仅主管或当前组长可修正")
			}
			if b.Status != "rejected" && !a.Admin() {
				return "", fmt.Errorf("员工仅可重报被驳回批次")
			}
			if err := required(f, "reason"); err != nil {
				return "", err
			}
			g, err := quantity(f, "good")
			if err != nil {
				return "", err
			}
			bad, err := quantity(f, "bad")
			if err != nil || g+bad == 0 || g+bad > 1000000000000 {
				return "", fmt.Errorf("修正数量无效")
			}
			revisionAt := now
			for _, r := range s.Revisions {
				if r.BatchID == id && r.At >= revisionAt {
					revisionAt = r.At + 1
				}
			}
			s.Revisions = append(s.Revisions, Revision{NewID(), id, g, bad, f.Get("reason"), a.ID, revisionAt})
			b.Status = "pending"
			b.Reason = ""
		} else if action == "reject" {
			if err := required(f, "reason"); err != nil {
				return "", err
			}
			b.Status = "rejected"
			b.Reason = f.Get("reason")
		} else {
			s.Recalculate(now)
			roots, _ := s.Components(now)
			found := false
			for _, alloc := range s.Allocations {
				if roots[alloc.BatchID] == roots[id] {
					found = true
					if !alloc.Ready {
						return "", fmt.Errorf("尚不能审核：%s", alloc.Reason)
					}
				}
			}
			if !found {
				return "", fmt.Errorf("缺少分配明细")
			}
			for i := range s.Batches {
				if roots[s.Batches[i].ID] == roots[id] {
					s.Batches[i].Status = "approved"
				}
			}
		}
	default:
		return "", fmt.Errorf("未知操作")
	}
	s.Recalculate(now)
	s.Event(a.ID, action, result, now, f)
	return result, nil
}

// OrderSource is the import boundary. No on-site ERP schema or credentials are assumed.
type OrderSource interface{ ReadOrders() ([]ImportOrder, error) }
type CSVSource struct{ Reader io.Reader }

func (source CSVSource) ReadOrders() ([]ImportOrder, error) { return ParseOrders(source.Reader) }

type ImportOrder struct{ ExternalID, ProductCode, CenterCode, Quantity, Status, Source string }

func ParseOrders(r io.Reader) ([]ImportOrder, error) {
	rows, err := csv.NewReader(io.LimitReader(r, 2<<20)).ReadAll()
	if err != nil {
		return nil, fmt.Errorf("CSV 格式错误：%w", err)
	}
	if len(rows) > 0 && len(rows[0]) > 0 {
		rows[0][0] = strings.TrimPrefix(rows[0][0], "\ufeff")
	}
	expected := "external_id,product_code,center_code,quantity,status,source"
	if len(rows) < 2 || strings.Join(rows[0], ",") != expected {
		return nil, fmt.Errorf("CSV 表头须为 %s，且至少一条工单", expected)
	}
	var out []ImportOrder
	for i, row := range rows[1:] {
		if len(row) != 6 {
			return nil, fmt.Errorf("第 %d 行须为六列", i+2)
		}
		out = append(out, ImportOrder{row[0], row[1], row[2], row[3], row[4], row[5]})
	}
	return out, nil
}
func (s *State) Import(rows []ImportOrder) error {
	for _, r := range rows {
		var product, center string
		for _, p := range s.Products {
			if p.Code == r.ProductCode {
				product = p.ID
			}
		}
		for _, c := range s.Centers {
			if c.Code == r.CenterCode {
				center = c.ID
			}
		}
		q, err := Decimal(r.Quantity, 4)
		if err != nil || q <= 0 || product == "" || center == "" || r.ExternalID == "" || r.Source == "" || (r.Status != "open" && r.Status != "closed") {
			return fmt.Errorf("工单 %s：标识、产品、中心、数量、状态或来源无效", r.ExternalID)
		}
		var order *Order
		for i := range s.Orders {
			if s.Orders[i].ExternalID == r.ExternalID {
				order = &s.Orders[i]
			}
		}
		if order == nil {
			s.Orders = append(s.Orders, Order{NewID(), r.ExternalID, product, center, q, r.Status, r.Source})
			continue
		}
		for _, snap := range s.Snapshots {
			if snap.OrderID == order.ID && (order.ProductID != product || order.CenterID != center || order.Quantity != q) {
				return fmt.Errorf("工单 %s 已冻结派工，产品/中心/数量变化须人工处置，原镜像保留", r.ExternalID)
			}
		}
		order.ProductID = product
		order.CenterID = center
		order.Quantity = q
		order.Status = r.Status
		order.Source = r.Source
	}
	return nil
}
