-- name: LoadData :one
SELECT jsonb_build_object(
 'centers', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM centers t),
 'employees', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM employees t),
 'products', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM products t),
 'operations', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM operations t),
 'versions', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM versions t),
 'orders', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM orders t),
 'snapshots', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM snapshots t),
 'dispatches', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM dispatches t),
 'members', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM members t),
 'batches', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM batches t),
 'revisions', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM revisions t),
 'spans', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM spans t),
 'allocations', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM allocations t),
 'audits', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM audits t),
 'trims', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM trims t),
 'resolutions', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM resolutions t),
 'requests', (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb) FROM requests t))::jsonb;

-- name: SaveCenters :exec
INSERT INTO centers (id, code, name) SELECT x.id, x.code, x.name FROM jsonb_populate_record(NULL::centers, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET code=EXCLUDED.code, name=EXCLUDED.name;

-- name: SaveEmployees :exec
INSERT INTO employees (id, code, name, card, badge, center_id, active) SELECT x.id, x.code, x.name, x.card, x.badge, x.center_id, x.active FROM jsonb_populate_record(NULL::employees, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET code=EXCLUDED.code, name=EXCLUDED.name, card=EXCLUDED.card, badge=EXCLUDED.badge, center_id=EXCLUDED.center_id, active=EXCLUDED.active;

-- name: SaveProducts :exec
INSERT INTO products (id, code, name) SELECT x.id, x.code, x.name FROM jsonb_populate_record(NULL::products, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET code=EXCLUDED.code, name=EXCLUDED.name;

-- name: SaveOperations :exec
INSERT INTO operations (id, product_id, seq, name) SELECT x.id, x.product_id, x.seq, x.name FROM jsonb_populate_record(NULL::operations, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET product_id=EXCLUDED.product_id, seq=EXCLUDED.seq, name=EXCLUDED.name;

-- name: SaveVersions :exec
INSERT INTO versions (id, operation_id, price, standard, multiplier, parallel, collaboration, at, actor, name, seq) SELECT x.id, x.operation_id, x.price, x.standard, x.multiplier, x.parallel, x.collaboration, x.at, x.actor, x.name, x.seq FROM jsonb_populate_record(NULL::versions, $1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: SaveOrders :exec
INSERT INTO orders (id, external_id, product_id, center_id, quantity, status, source) SELECT x.id, x.external_id, x.product_id, x.center_id, x.quantity, x.status, x.source FROM jsonb_populate_record(NULL::orders, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET external_id=EXCLUDED.external_id, product_id=EXCLUDED.product_id, center_id=EXCLUDED.center_id, quantity=EXCLUDED.quantity, status=EXCLUDED.status, source=EXCLUDED.source;

-- name: SaveSnapshots :exec
INSERT INTO snapshots (id, order_id, operation_id, version_id, plan, at) SELECT x.id, x.order_id, x.operation_id, x.version_id, x.plan, x.at FROM jsonb_populate_record(NULL::snapshots, $1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: SaveDispatches :exec
INSERT INTO dispatches (id, snapshot_id, quantity, leader_id, status, at) SELECT x.id, x.snapshot_id, x.quantity, x.leader_id, x.status, x.at FROM jsonb_populate_record(NULL::dispatches, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET leader_id=EXCLUDED.leader_id, status=EXCLUDED.status;

-- name: SaveMembers :exec
INSERT INTO members (id, dispatch_id, employee_id, present) SELECT x.id, x.dispatch_id, x.employee_id, x.present FROM jsonb_populate_record(NULL::members, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET present=EXCLUDED.present;

-- name: SaveBatches :exec
INSERT INTO batches (id, dispatch_id, good, bad, at, status, reason, algorithm) SELECT x.id, x.dispatch_id, x.good, x.bad, x.at, x.status, x.reason, x.algorithm FROM jsonb_populate_record(NULL::batches, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status, reason=EXCLUDED.reason, algorithm=EXCLUDED.algorithm;

-- name: SaveRevisions :exec
INSERT INTO revisions (id, batch_id, good, bad, reason, actor, at) SELECT x.id, x.batch_id, x.good, x.bad, x.reason, x.actor, x.at FROM jsonb_populate_record(NULL::revisions, $1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: SaveSpans :exec
INSERT INTO spans (id, dispatch_id, employee_id, start_us, end_us, batch_id) SELECT x.id, x.dispatch_id, x.employee_id, x.start_us, x.end_us, x.batch_id FROM jsonb_populate_record(NULL::spans, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET end_us=EXCLUDED.end_us, batch_id=EXCLUDED.batch_id;

-- name: SaveAllocations :exec
INSERT INTO allocations (id, algorithm, batch_id, employee_id, good, bad, raw_us, effective_us, accounted_us, cents, ready, reason) SELECT x.id, x.algorithm, x.batch_id, x.employee_id, x.good, x.bad, x.raw_us, x.effective_us, x.accounted_us, x.cents, x.ready, x.reason FROM jsonb_populate_record(NULL::allocations, $1::jsonb) AS x ON CONFLICT(id) DO UPDATE SET algorithm=EXCLUDED.algorithm, good=EXCLUDED.good, bad=EXCLUDED.bad, raw_us=EXCLUDED.raw_us, effective_us=EXCLUDED.effective_us, accounted_us=EXCLUDED.accounted_us, cents=EXCLUDED.cents, ready=EXCLUDED.ready, reason=EXCLUDED.reason;

-- name: SaveAudits :exec
INSERT INTO audits (id, actor, action, target, at, payload) SELECT x.id, x.actor, x.action, x.target, x.at, x.payload FROM jsonb_populate_record(NULL::audits, $1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: SaveRequests :exec
INSERT INTO requests (id, actor, fingerprint, result) SELECT x.id, x.actor, x.fingerprint, x.result FROM jsonb_populate_record(NULL::requests, $1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: Lock :exec
SELECT pg_advisory_xact_lock(762142311);

-- name: GetSession :one
SELECT * FROM sessions WHERE token=$1;

-- name: PutSession :exec
INSERT INTO sessions(token,employee_id,role,csrf,expires,last_seen) VALUES($1,$2,$3,$4,$5,$6);

-- name: TouchSession :exec
UPDATE sessions SET last_seen=$2 WHERE token=$1;

-- name: DeleteSession :exec
DELETE FROM sessions WHERE token=$1;

-- name: IdentifyEmployee :one
SELECT id FROM employees WHERE active AND (card=$1 OR badge=$1) LIMIT 1;

-- name: LoadTerminal :one
WITH selected AS MATERIALIZED (
 SELECT d.* FROM dispatches d
 WHERE (sqlc.arg(employee)::text='' OR EXISTS (SELECT 1 FROM members m WHERE m.dispatch_id=d.id AND m.employee_id=sqlc.arg(employee)))
 AND (sqlc.arg(task)::text='' OR d.id=sqlc.arg(task))
), snaps AS MATERIALIZED (SELECT s.* FROM snapshots s WHERE s.id IN (SELECT snapshot_id FROM selected)),
 reports AS MATERIALIZED (SELECT b.* FROM batches b WHERE b.dispatch_id IN (SELECT id FROM selected))
SELECT jsonb_build_object(
 'employees',(SELECT coalesce(jsonb_agg(to_jsonb(e)),'[]') FROM employees e WHERE e.active OR e.id IN (SELECT leader_id FROM selected) OR e.id IN (SELECT employee_id FROM members WHERE dispatch_id IN (SELECT id FROM selected))),
 'centers',(SELECT coalesce(jsonb_agg(to_jsonb(c)),'[]') FROM centers c),
 'products',(SELECT coalesce(jsonb_agg(to_jsonb(p)),'[]') FROM products p WHERE p.id IN (SELECT product_id FROM orders WHERE id IN (SELECT order_id FROM snaps))),
 'orders',(SELECT coalesce(jsonb_agg(to_jsonb(o)),'[]') FROM orders o WHERE o.id IN (SELECT order_id FROM snaps)),
 'snapshots',(SELECT coalesce(jsonb_agg(to_jsonb(s)),'[]') FROM snaps s),
 'versions',(SELECT coalesce(jsonb_agg(to_jsonb(v)),'[]') FROM versions v WHERE v.id IN (SELECT version_id FROM snaps)),
 'dispatches',(SELECT coalesce(jsonb_agg(to_jsonb(d)),'[]') FROM selected d),
 'members',(SELECT coalesce(jsonb_agg(to_jsonb(m)),'[]') FROM members m WHERE m.dispatch_id IN (SELECT id FROM selected)),
 'batches',(SELECT coalesce(jsonb_agg(to_jsonb(b)),'[]') FROM reports b),
 'revisions',(SELECT coalesce(jsonb_agg(to_jsonb(r)),'[]') FROM revisions r WHERE r.batch_id IN (SELECT id FROM reports)),
 'spans',(SELECT coalesce(jsonb_agg(to_jsonb(s)),'[]') FROM spans s WHERE s.dispatch_id IN (SELECT id FROM selected))
)::jsonb;

-- name: ReadStats :one
WITH filtered AS MATERIALIZED (
 SELECT b.at AS "At", a.employee_id AS "EmployeeID", e.name AS "Employee", c.name AS "Center", o.external_id AS "Order", v.name AS "Operation",
 to_char(to_timestamp(b.at/1000000.0) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS "Date",
 CASE WHEN b.status='pending' AND NOT a.ready THEN 'unaccounted' ELSE b.status END AS "Status",
 a.reason AS "Reason", a.good AS "Good", a.bad AS "Bad", a.raw_us AS "Raw", a.effective_us AS "Effective", a.accounted_us AS "Accounted", a.cents AS "Cents", a.ready AS "Ready", b.id AS "BatchID", a.id AS "ID", a.algorithm AS "Algorithm"
 FROM allocations a JOIN batches b ON b.id=a.batch_id JOIN employees e ON e.id=a.employee_id
 JOIN dispatches d ON d.id=b.dispatch_id JOIN snapshots s ON s.id=d.snapshot_id JOIN orders o ON o.id=s.order_id
 JOIN centers c ON c.id=o.center_id JOIN versions v ON v.id=s.version_id
 WHERE (sqlc.arg(viewer)::text='' OR a.employee_id=sqlc.arg(viewer))
 AND (sqlc.arg(employee)::text='' OR a.employee_id=sqlc.arg(employee))
 AND (sqlc.arg(center)::text='' OR o.center_id=sqlc.arg(center))
 AND (sqlc.arg(order_id)::text='' OR o.id=sqlc.arg(order_id))
 AND (sqlc.arg(operation)::text='' OR s.operation_id=sqlc.arg(operation))
 AND b.at>=sqlc.arg(from_us)::bigint AND b.at<sqlc.arg(to_us)::bigint
 AND (sqlc.arg(phase)::text='' OR CASE WHEN b.status='pending' AND NOT a.ready THEN 'unaccounted' ELSE b.status END=sqlc.arg(phase))
), paged AS (SELECT * FROM filtered ORDER BY "At", "BatchID", "ID" LIMIT sqlc.arg(row_limit)::bigint OFFSET sqlc.arg(row_offset)::bigint),
 totals AS (SELECT coalesce(sum("Good"),0) AS "Good", coalesce(sum("Bad"),0) AS "Bad", coalesce(sum("Raw"),0) AS "Raw", coalesce(sum("Effective"),0) AS "Effective", coalesce(sum("Accounted"),0) AS "Accounted", coalesce(sum("Cents"),0) AS "Cents" FROM filtered WHERE "Status"='approved'),
 monthly AS (SELECT "EmployeeID", "Employee", sum("Good") AS "Good", sum("Bad") AS "Bad", sum("Accounted") AS "Accounted", sum("Cents") AS "Cents" FROM filtered WHERE "Status"='approved' GROUP BY "EmployeeID", "Employee")
SELECT jsonb_build_object('Rows',(SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY "At","BatchID","ID"),'[]') FROM paged p),
 'Total',(SELECT count(*) FROM filtered), 'Summary',(SELECT to_jsonb(t) FROM totals t),
 'Monthly',(SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY "EmployeeID"),'[]') FROM monthly m))::jsonb;

-- name: StatsDirectory :one
SELECT jsonb_build_object(
 'employees',(SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY code),'[]') FROM employees e WHERE sqlc.arg(viewer)::text='' OR e.id=sqlc.arg(viewer)),
 'centers',(SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY code),'[]') FROM centers c),
 'orders',(SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY external_id),'[]') FROM orders o),
 'operations',(SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY product_id,seq),'[]') FROM operations o),
 'dispatches',(SELECT coalesce(jsonb_agg(to_jsonb(d)),'[]') FROM dispatches d WHERE d.leader_id=sqlc.arg(viewer) AND EXISTS (SELECT 1 FROM batches b WHERE b.dispatch_id=d.id AND b.status='rejected')),
 'batches',(SELECT coalesce(jsonb_agg(to_jsonb(b)),'[]') FROM batches b JOIN dispatches d ON d.id=b.dispatch_id WHERE d.leader_id=sqlc.arg(viewer) AND b.status='rejected'),
 'revisions',(SELECT coalesce(jsonb_agg(to_jsonb(r)),'[]') FROM revisions r JOIN batches b ON b.id=r.batch_id JOIN dispatches d ON d.id=b.dispatch_id WHERE d.leader_id=sqlc.arg(viewer) AND b.status='rejected')
)::jsonb;

-- name: SaveResolutions :exec
INSERT INTO resolutions (id,batch_id,standard,reason,actor,at) SELECT x.id,x.batch_id,x.standard,x.reason,x.actor,x.at FROM jsonb_populate_record(NULL::resolutions,$1::jsonb) AS x ON CONFLICT(id) DO NOTHING;

-- name: SaveTrims :exec
INSERT INTO trims (id,batch_id,employee_id,start_us,end_us,reason,actor,at) SELECT x.id,x.batch_id,x.employee_id,x.start_us,x.end_us,x.reason,x.actor,x.at FROM jsonb_populate_record(NULL::trims,$1::jsonb) AS x ON CONFLICT(id) DO NOTHING;
