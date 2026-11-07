CREATE TABLE IF NOT EXISTS centers (
 id text PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL CHECK(name <> '')
);
CREATE TABLE IF NOT EXISTS employees (
 id text PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL CHECK(name <> ''),
 card text NOT NULL UNIQUE CHECK(card <> ''), badge text NOT NULL UNIQUE CHECK(badge <> ''),
 center_id text NOT NULL REFERENCES centers, active boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS products (
 id text PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL CHECK(name <> '')
);
CREATE TABLE IF NOT EXISTS operations (
 id text PRIMARY KEY, product_id text NOT NULL REFERENCES products,
 seq bigint NOT NULL CHECK(seq>0), name text NOT NULL CHECK(name <> ''), UNIQUE(product_id, seq)
);
CREATE TABLE IF NOT EXISTS versions (
 id text PRIMARY KEY, operation_id text NOT NULL REFERENCES operations,
 price bigint NOT NULL CHECK(price BETWEEN 0 AND 100000000),
 standard bigint NOT NULL CHECK(standard BETWEEN 0 AND 86400000000),
 multiplier bigint NOT NULL DEFAULT 10000 CHECK(multiplier BETWEEN 1 AND 1000000000),
 parallel boolean NOT NULL DEFAULT false, collaboration boolean NOT NULL DEFAULT false,
 at bigint NOT NULL, actor text NOT NULL, name text NOT NULL, seq bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
 id text PRIMARY KEY, external_id text NOT NULL UNIQUE, product_id text NOT NULL REFERENCES products,
 center_id text NOT NULL REFERENCES centers, quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000000),
 status text NOT NULL CHECK(status IN ('open','closed')), source text NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES orders, operation_id text NOT NULL REFERENCES operations,
 version_id text NOT NULL REFERENCES versions, plan bigint NOT NULL CHECK(plan>0), at bigint NOT NULL,
 UNIQUE(order_id, operation_id)
);
CREATE TABLE IF NOT EXISTS dispatches (
 id text PRIMARY KEY, snapshot_id text NOT NULL REFERENCES snapshots,
 quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000000),
 leader_id text NOT NULL REFERENCES employees,
 status text NOT NULL CHECK(status IN ('ready','running','paused','finished')),
 at bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
 id text PRIMARY KEY, dispatch_id text NOT NULL REFERENCES dispatches,
 employee_id text NOT NULL REFERENCES employees, present boolean NOT NULL,
 UNIQUE(dispatch_id, employee_id)
);
CREATE TABLE IF NOT EXISTS batches (
 id text PRIMARY KEY, dispatch_id text NOT NULL REFERENCES dispatches,
 good bigint NOT NULL CHECK(good>=0), bad bigint NOT NULL CHECK(bad>=0),
 at bigint NOT NULL, status text NOT NULL CHECK(status IN ('pending','approved','rejected')),
 reason text NOT NULL DEFAULT '', CHECK(good+bad>0 AND good+bad<=1000000000000)
);
CREATE TABLE IF NOT EXISTS revisions (
 id text PRIMARY KEY, batch_id text NOT NULL REFERENCES batches,
 good bigint NOT NULL CHECK(good>=0), bad bigint NOT NULL CHECK(bad>=0),
 reason text NOT NULL CHECK(reason<>''), actor text NOT NULL, at bigint NOT NULL,
 CHECK(good+bad>0 AND good+bad<=1000000000000)
);
CREATE TABLE IF NOT EXISTS spans (
 id text PRIMARY KEY, dispatch_id text NOT NULL REFERENCES dispatches,
 employee_id text NOT NULL REFERENCES employees, start_us bigint NOT NULL,
 end_us bigint NOT NULL DEFAULT 0, batch_id text REFERENCES batches,
 CHECK(end_us=0 OR end_us>=start_us), CHECK(batch_id IS NULL OR end_us>0)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_span ON spans(dispatch_id,employee_id) WHERE end_us=0;
CREATE INDEX IF NOT EXISTS span_employee ON spans(employee_id,start_us,end_us);
CREATE TABLE IF NOT EXISTS allocations (
 id text PRIMARY KEY, algorithm text NOT NULL, batch_id text NOT NULL REFERENCES batches, employee_id text NOT NULL REFERENCES employees,
 good bigint NOT NULL CHECK(good>=0), bad bigint NOT NULL CHECK(bad>=0),
 raw_us bigint NOT NULL CHECK(raw_us>=0), effective_us bigint NOT NULL CHECK(effective_us>=0), accounted_us bigint NOT NULL CHECK(accounted_us>=0),
 cents bigint NOT NULL CHECK(cents>=0), ready boolean NOT NULL, reason text NOT NULL,
 UNIQUE(batch_id,employee_id)
);
CREATE TABLE IF NOT EXISTS audits (
 id text PRIMARY KEY, actor text NOT NULL, action text NOT NULL, target text NOT NULL,
 at bigint NOT NULL, payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
 id text PRIMARY KEY, actor text NOT NULL, fingerprint text NOT NULL, result text NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token text PRIMARY KEY, employee_id text NOT NULL, role text NOT NULL,
 csrf text NOT NULL, expires bigint NOT NULL, last_seen bigint NOT NULL
);

ALTER TABLE versions ADD COLUMN IF NOT EXISTS name text NOT NULL DEFAULT '';
ALTER TABLE versions ADD COLUMN IF NOT EXISTS seq bigint NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS resolutions (
 id text PRIMARY KEY, batch_id text NOT NULL REFERENCES batches,
 standard bigint NOT NULL CHECK(standard BETWEEN 1 AND 86400000000),
 reason text NOT NULL CHECK(reason<>''), actor text NOT NULL, at bigint NOT NULL
);

ALTER TABLE allocations ADD COLUMN IF NOT EXISTS effective_us bigint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS trims (
 id text PRIMARY KEY, batch_id text NOT NULL REFERENCES batches,
 employee_id text NOT NULL REFERENCES employees, start_us bigint NOT NULL, end_us bigint NOT NULL,
 reason text NOT NULL CHECK(reason<>''), actor text NOT NULL, at bigint NOT NULL,
 CHECK(end_us>start_us)
);

ALTER TABLE allocations ADD COLUMN IF NOT EXISTS algorithm text NOT NULL DEFAULT 'interval-v1';
