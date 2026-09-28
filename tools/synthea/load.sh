#!/usr/bin/env bash
# Loads the generated Synthea bundles into HAPI FHIR.
# Organizations and practitioners go first because patient bundles reference them.
#   FHIR_BASE_URL=http://localhost:8090/fhir ./load.sh
set -euo pipefail
cd "$(dirname "$0")"
FHIR="${FHIR_BASE_URL:-http://localhost:8090/fhir}"

echo "waiting for $FHIR ..."
for _ in $(seq 1 120); do
  curl -fs "$FHIR/metadata" >/dev/null 2>&1 && break
  sleep 2
done

post() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$FHIR" \
    -H 'Content-Type: application/fhir+json' --data-binary @"$1")
  echo "$code $(basename "$1")"
  [[ "$code" == 200 ]]
}

shopt -s nullglob
for f in output/fhir/hospitalInformation*.json output/fhir/practitionerInformation*.json; do post "$f"; done
for f in output/fhir/*.json; do
  case "$(basename "$f")" in hospitalInformation*|practitionerInformation*) continue ;; esac
  post "$f"
done
curl -s "$FHIR/Patient?_summary=count" | python3 -c 'import json,sys; print("patients in FHIR:", json.load(sys.stdin)["total"])'
