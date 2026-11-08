#!/usr/bin/env python3
"""Replay WB-6 HTTP measurements on two local binaries sharing an ISOLATED demo DB.

Use --seed only on a disposable database; never use the member's 55432/8080 service.
Optional --postgres-log counts logged SQL calls (log_statement=all).
"""

import argparse
import http.cookiejar
import json
import re
import statistics
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database-url", required=True)
parser.add_argument("--baseline-url", required=True)
parser.add_argument("--candidate-url", required=True)
parser.add_argument("--postgres-log", type=Path)
parser.add_argument("--seed", action="store_true")
parser.add_argument("--samples", type=int, default=25)
parser.add_argument("--out", type=Path, default=Path("artifacts/wb6/measurements.json"))
args = parser.parse_args()
for value in (args.database_url, args.baseline_url, args.candidate_url):
    address = urllib.parse.urlsplit(value)
    assert address.hostname in ("localhost", "127.0.0.1") and address.port not in (
        55432,
        8080,
    ), "Use isolated local ports"


def sql(statement):
    return subprocess.check_output(
        ["psql", args.database_url, "-XAt", "-v", "ON_ERROR_STOP=1", "-c", statement],
        text=True,
    )


if args.seed:
    sql(Path(__file__).with_name("wb6-history.sql").read_text())


class Client:
    def __init__(self, base, identity=None):
        self.base = base.rstrip("/")
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        page = self.get("/login")
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        values = (
            {"mode": "employee", "identity": identity, "csrf": self.csrf}
            if identity
            else {"mode": "admin", "password": "WorkshopDemo!2026", "csrf": self.csrf}
        )
        page = self.post("/login", values)
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def get(self, path):
        with self.http.open(self.base + path, timeout=60) as response:
            return response.read().decode()

    def post(self, path, values, accept=None):
        headers = {"Accept": accept} if accept else {}
        request = urllib.request.Request(
            self.base + path,
            data=urllib.parse.urlencode(values, doseq=True).encode(),
            headers=headers,
        )
        with self.http.open(request, timeout=60) as response:
            return response.read().decode()

    def action(self, action, values):
        return json.loads(
            self.post(
                "/action/" + action,
                dict(values, csrf=self.csrf, request_id=uuid.uuid4().hex),
                "application/json",
            )
        )


def log_position():
    return args.postgres_log.stat().st_size if args.postgres_log else 0


def query_count(position):
    if not args.postgres_log:
        return None
    with args.postgres_log.open("rb") as log:
        log.seek(position)
        return len(re.findall(rb"LOG:  (?:execute [^\n]*?:|statement:)", log.read()))


def measure(fn, samples):
    fn()  # warm connections, prepared statements and buffers
    elapsed, queries, sizes = [], [], []
    for _ in range(samples):
        position = log_position()
        start = time.perf_counter_ns()
        result = fn()
        elapsed.append((time.perf_counter_ns() - start) / 1e6)
        queries.append(query_count(position))
        sizes.append(len(result.encode()))
    ordered = sorted(elapsed)
    return {
        "samples": samples,
        "median_ms": round(statistics.median(elapsed), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3),
        "sql_min": min(queries) if queries[0] is not None else None,
        "sql_max": max(queries) if queries[0] is not None else None,
        "response_bytes_median": statistics.median(sizes),
    }


clients = {"before": Client(args.baseline_url), "after": Client(args.candidate_url)}
report = {
    "dataset": {
        "employees": 40,
        "orders": 100,
        "dispatches": 400,
        "batches": 4000,
        "spans": 4000,
        "allocations": 4000,
        "audits": 4000,
        "requests": 4000,
    },
    "reads": {},
    "note": (
        "Loopback, same isolated PostgreSQL dataset; "
        "warm repeated HTTP samples. Not production throughput."
    ),
}
report["actual_totals_at_start"] = json.loads(
    sql(
        "SELECT json_build_object("
        "'employees',(SELECT count(*) FROM employees),"
        "'orders',(SELECT count(*) FROM orders),"
        "'dispatches',(SELECT count(*) FROM dispatches),"
        "'batches',(SELECT count(*) FROM batches),"
        "'allocations',(SELECT count(*) FROM allocations))"
    )
)
for path in (
    "/stats?all=1",
    "/stats?all=1&employee=wb6-employee-001",
    "/terminal?task=wb6-task-001",
):
    report["reads"][path] = {
        name: measure(lambda client=client, path=path: client.get(path), args.samples)
        for name, client in clients.items()
    }
    print(path, json.dumps(report["reads"][path]), flush=True)

# Complete interaction: old POST + redirected GET versus new POST + card.
candidate = clients["after"]
suffix = str(time.time_ns())
candidate.action(
    "import",
    {
        "csv": (
            "external_id,product_code,center_code,quantity,status,source\n"
            f"WB6-LIVE-{suffix},P001,WC01,100,open,WB6 benchmark"
        )
    },
)
order = sql("SELECT id FROM orders WHERE external_id='WB6-LIVE-" + suffix + "'").strip()
op = sql("SELECT id FROM operations WHERE seq=10 LIMIT 1").strip()
candidate.action(
    "dispatch",
    {
        "order_id": order,
        "operation_id": op,
        "quantity": "100",
        "members": ["wb6-employee-040"],
    },
)
task = sql(
    "SELECT d.id FROM dispatches d JOIN snapshots s ON s.id=d.snapshot_id "
    "WHERE s.order_id='" + order + "'"
).strip()
candidate.action("start", {"id": task})


def report_once(client):
    result = json.loads(
        client.post(
            "/action/report",
            {
                "csrf": client.csrf,
                "request_id": uuid.uuid4().hex,
                "return": "/terminal",
                "id": task,
                "good": "1",
                "bad": "0",
            },
            "application/json",
        )
    )
    return result["html"] if "html" in result else client.get(result["redirect"])


workers = {name: Client(client.base, "WB6-CARD-40") for name, client in clients.items()}
report["continuous_report"] = {
    name: measure(lambda client=client: report_once(client), 5)
    for name, client in workers.items()
}
report["continuous_report"]["requests"] = {"before": 2, "after": 1}
candidate.action("pause", {"id": task, "reason": "测量结束"})
candidate.action("finish", {"id": task, "good": "1", "bad": "0"})
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report["continuous_report"], ensure_ascii=False), flush=True)
