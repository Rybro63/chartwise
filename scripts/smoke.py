#!/usr/bin/env python3
"""End-to-end smoke test against a running Chartwise (compose, kind or EKS).

Creates a small synthetic patient in FHIR, uploads a transcript, waits for the AI draft,
approves it, and waits for the note to be filed back to FHIR as a DocumentReference.

    python3 scripts/smoke.py --api http://localhost:8080 --fhir http://localhost:8090/fhir
"""

import argparse
import json
import sys
import time
import urllib.request


def call(method, url, body=None, token=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def wait_for(fn, what, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception:  # noqa: BLE001 - services may still be starting
            pass
        time.sleep(2)
    sys.exit(f"FAIL: timed out waiting for {what}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--fhir", default="http://localhost:8090/fhir")
    ap.add_argument("--password", default="chartwise-dev", help="demo users' password (random on EKS)")
    args = ap.parse_args()

    wait_for(lambda: call("GET", f"{args.fhir}/metadata"), "FHIR server", 600)
    patient = call("POST", f"{args.fhir}/Patient", {
        "resourceType": "Patient", "name": [{"family": "Smoketest", "given": ["Synthetic"]}],
        "gender": "female", "birthDate": "1970-01-01"}, content_type="application/fhir+json")
    call("POST", f"{args.fhir}/MedicationRequest", {
        "resourceType": "MedicationRequest", "status": "active", "intent": "order",
        "subject": {"reference": f"Patient/{patient['id']}"},
        "medicationCodeableConcept": {"text": "lisinopril 10 MG Oral Tablet"}}, content_type="application/fhir+json")

    login = lambda u: call("POST", f"{args.api}/api/auth/login", {"username": u, "password": args.password})["token"]  # noqa: E731
    scribe = wait_for(lambda: login("scribe"), "encounter service", 600)
    clinician = login("clinician")

    enc = call("POST", f"{args.api}/api/encounters", {
        "patientId": patient["id"],
        "transcript": "Doctor: How is the blood pressure?\nPatient: Good, I take my lisinopril daily."}, scribe)
    print("created encounter", enc["id"])

    detail = wait_for(lambda: (d := call("GET", f"{args.api}/api/encounters/{enc['id']}", token=clinician))
                      ["summary"]["status"] == "IN_REVIEW" and d, "AI draft")
    print("draft ready, safety flags:", len(detail["draftSafetyFlags"]))
    call("POST", f"{args.api}/api/encounters/{enc['id']}/approve", {"version": detail["latest"]["version"]}, clinician)
    filed = wait_for(lambda: (d := call("GET", f"{args.api}/api/encounters/{enc['id']}", token=clinician))
                     ["summary"]["status"] == "FILED" and d, "FHIR filing")
    doc = call("GET", f"{args.fhir}/DocumentReference/{filed['fhirDocumentId']}")
    assert doc["subject"]["reference"] == f"Patient/{patient['id']}"
    print("PASS: note filed as DocumentReference/" + doc["id"])


if __name__ == "__main__":
    main()
