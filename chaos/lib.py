"""Shared helpers for the load and chaos experiments. Standard library only (+ matplotlib for charts)."""

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "http://localhost:8080"
PROM = "http://localhost:9090"
GATEWAY_ADMIN = "http://localhost:8081"
ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results"
VISITS = ROOT / "tools" / "transcripts" / "out" / "visits.json"


def http(method: str, url: str, body=None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def login(user: str = "scribe", password: str = "chartwise-dev") -> str:
    return http("POST", f"{API}/api/auth/login", {"username": user, "password": password})["token"]


def visits() -> list[dict]:
    return json.loads(VISITS.read_text())


def create_encounter(token: str, visit: dict) -> str:
    return http("POST", f"{API}/api/encounters", visit, token)["id"]


def prom(query: str) -> float | None:
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    result = http("GET", url)["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def prom_increase(metric: str, seconds: float) -> int:
    """Counter increase over the last `seconds`; unlike a before/after subtraction, this survives
    counter resets when a process restarts."""
    return round(prom(f"sum(increase({metric}[{max(int(seconds), 10)}s]))") or 0)


def prom_range(query: str, start: float, end: float, step: str = "5s") -> list[tuple[float, float]]:
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    result = http("GET", url)["data"]["result"]
    return [(float(t), float(v)) for t, v in result[0]["values"]] if result else []


def chaos(mode: str, **kwargs) -> dict:
    return http("POST", f"{GATEWAY_ADMIN}/admin/chaos", {"mode": mode, **kwargs})


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "chartwise", "-d", "chartwise",
                          "-At", "-F", "\t", "-c", sql], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.strip().splitlines() if line]


def topic_messages(topic: str) -> int:
    """Total messages ever written to a topic (sum of partition end offsets)."""
    out = subprocess.run(["docker", "compose", "exec", "-T", "kafka", "/opt/kafka/bin/kafka-get-offsets.sh",
                          "--bootstrap-server", "localhost:9092", "--topic", topic],
                         cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return sum(int(line.rsplit(":", 1)[1]) for line in out.strip().splitlines() if line.count(":") >= 2)


def encounter_report(ids: list[str]) -> dict:
    """Per-run integrity check straight from the database: lost, duplicated, failed."""
    id_list = ",".join(f"'{i}'" for i in ids)
    rows = psql(f"""
        select e.status, count(v.id) filter (where v.author_type = 'AI') as ai_versions
        from encounter e left join note_version v on v.encounter_id = e.id
        where e.id in ({id_list})
        group by e.id, e.status""")
    statuses: dict[str, int] = {}
    ai_counts: dict[str, int] = {}
    for status, ai in rows:
        statuses[status] = statuses.get(status, 0) + 1
        ai_counts[ai] = ai_counts.get(ai, 0) + 1
    return {
        "encounters_created": len(ids),
        "encounters_found": len(rows),
        "status_counts": statuses,
        "ai_versions_per_encounter": ai_counts,
        "lost": len(ids) - sum(1 for s, ai in rows if int(ai) == 1),
        "duplicated": sum(1 for _, ai in rows if int(ai) > 1),
    }


def wait_until(predicate, timeout: float, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def drafting_count(ids: list[str]) -> int:
    id_list = ",".join(f"'{i}'" for i in ids)
    return int(psql(f"select count(*) from encounter where status = 'DRAFTING' and id in ({id_list})")[0][0])


def save(name: str, data: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path
