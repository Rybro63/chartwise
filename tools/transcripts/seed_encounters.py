#!/usr/bin/env python3
"""Creates encounters from generated transcripts through the public API (as the scribe user).

    python3 seed_encounters.py --api http://localhost:8080 --count 20
"""

import argparse
import json
import urllib.request
from pathlib import Path


def call(method: str, url: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--visits", default=str(Path(__file__).parent / "out" / "visits.json"))
    ap.add_argument("--user", default="scribe")
    ap.add_argument("--password", default="chartwise-dev")
    args = ap.parse_args()

    token = call("POST", f"{args.api}/api/auth/login", {"username": args.user, "password": args.password})["token"]
    visits = json.loads(Path(args.visits).read_text())[: args.count]
    for v in visits:
        created = call("POST", f"{args.api}/api/encounters", v, token)
        print(created["id"], created["status"])


if __name__ == "__main__":
    main()
