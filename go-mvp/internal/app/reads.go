package app

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"workshop/internal/store"
)

func (db Database) readTerminal(ctx context.Context, row store.Session, task string) (*State, error) {
	employee := ""
	if row.Role == "employee" {
		employee = row.EmployeeID
	}
	b, err := store.New(db.Pool).LoadTerminal(ctx, store.LoadTerminalParams{Employee: employee, Task: task})
	if err != nil {
		return nil, err
	}
	state := new(State)
	err = json.Unmarshal(b, state)
	return state, err
}

type StatsResult struct {
	Rows, Monthly []Stat
	Summary       Stat
	Total         int64
}

type DispatchOption struct {
	OrderID, OperationID string
	Plan, Remaining      int64
	Frozen               bool
}

func dispatchOptions(s *State) []DispatchOption {
	var out []DispatchOption
	for _, o := range s.Orders {
		if o.Status != "open" {
			continue
		}
		frozen := false
		for _, snap := range s.Snapshots {
			if snap.OrderID == o.ID {
				frozen = true
				break
			}
		}
		for _, op := range s.Operations {
			if op.ProductID != o.ProductID {
				continue
			}
			option := DispatchOption{OrderID: o.ID, OperationID: op.ID, Frozen: frozen}
			if !frozen {
				if v := s.CurrentVersion(op.ID); v != nil {
					option.Plan = roundedProduct(o.Quantity, v.Multiplier, Scale)
				}
			}
			for _, snap := range s.Snapshots {
				if snap.OrderID == o.ID && snap.OperationID == op.ID {
					option.Plan = snap.Plan
					for _, d := range s.Dispatches {
						if d.SnapshotID == snap.ID {
							option.Remaining -= d.Quantity
						}
					}
				}
			}
			option.Remaining += option.Plan
			out = append(out, option)
		}
	}
	return out
}

func (db Database) readStats(ctx context.Context, row store.Session, f url.Values, limit, offset int64) (StatsResult, error) {
	result := StatsResult{}
	from, to := int64(-9223372036854775808), int64(9223372036854775807)
	loc := time.FixedZone("CST", 8*3600)
	for _, key := range []string{"month", "from", "to"} {
		value := f.Get(key)
		if value == "" {
			continue
		}
		layout := "2006-01-02"
		if key == "month" {
			layout = "2006-01"
		}
		d, err := time.ParseInLocation(layout, value, loc)
		if err != nil {
			return result, fmt.Errorf("%s 日期格式不正确", key)
		}
		switch key {
		case "month":
			from = max(from, d.UnixMicro())
			to = min(to, d.AddDate(0, 1, 0).UnixMicro())
		case "from":
			from = max(from, d.UnixMicro())
		case "to":
			to = min(to, d.AddDate(0, 0, 1).UnixMicro())
		}
	}
	viewer := ""
	if row.Role == "employee" {
		viewer = row.EmployeeID
	}
	// A negative limit is reserved for full filtered export, never for the list page.
	if limit < 0 {
		limit = 9223372036854775807
	}
	b, err := store.New(db.Pool).ReadStats(ctx, store.ReadStatsParams{Viewer: viewer, Employee: f.Get("employee"), Center: f.Get("center"), OrderID: f.Get("order"), Operation: f.Get("operation"), Phase: f.Get("status"), FromUs: from, ToUs: to, RowLimit: limit, RowOffset: offset})
	if err != nil {
		return result, err
	}
	err = json.Unmarshal(b, &result)
	return result, err
}

func (s *Server) statsPage(w http.ResponseWriter, r *http.Request) {
	row, f := session(r), r.URL.Query()
	now := s.db.Now().UnixMicro()
	if f.Get("month") == "" && f.Get("all") == "" && f.Get("from") == "" && f.Get("to") == "" {
		f.Set("month", Date(now)[:7])
	}
	page, _ := strconv.ParseInt(f.Get("page"), 10, 64)
	page = max(1, min(page, 10000000))
	result, err := s.db.readStats(r.Context(), row, f, 50, (page-1)*50)
	if err != nil {
		http.Error(w, friendly(err), 422)
		return
	}
	viewer := ""
	if row.Role == "employee" {
		viewer = row.EmployeeID
	}
	b, err := store.New(s.db.Pool).StatsDirectory(r.Context(), viewer)
	if err != nil {
		http.Error(w, "筛选资料读取失败", 500)
		return
	}
	state := new(State)
	if err = json.Unmarshal(b, state); err != nil {
		http.Error(w, "筛选资料读取失败", 500)
		return
	}
	p := Page{S: state, Session: row, Path: "/stats", Title: "工时与计件统计", Demo: s.demo, Now: now, Filter: f, Rows: result.Rows, Summary: result.Summary, Monthly: result.Monthly, Total: result.Total, PageNumber: page, ReturnQuery: f.Encode()}
	link := func(n int64) string { f.Set("page", strconv.FormatInt(n, 10)); return "/stats?" + f.Encode() }
	if page > 1 {
		p.Prev = link(page - 1)
	}
	if page*50 < result.Total {
		p.Next = link(page + 1)
	}
	s.render(w, "layout", p)
}
