#!/usr/bin/env bash
# Generates synthetic patients with Synthea (https://github.com/synthetichealth/synthea).
# Every patient Chartwise uses comes from here. Never load real patient data.
#   ./generate.sh [population] [seed]
set -euo pipefail
cd "$(dirname "$0")"
VERSION=v4.0.0
POP="${1:-25}"
SEED="${2:-42}"

if [[ ! -f synthea-with-dependencies.jar ]]; then
  curl -sSL -o synthea-with-dependencies.jar \
    "https://github.com/synthetichealth/synthea/releases/download/${VERSION}/synthea-with-dependencies.jar"
fi

rm -rf output
java -jar synthea-with-dependencies.jar \
  -p "$POP" -s "$SEED" -cs "$SEED" -a 30-85 \
  --exporter.baseDirectory ./output \
  --exporter.years_of_history 3 \
  --exporter.fhir.export true \
  --exporter.fhir.transaction_bundle true \
  --exporter.hospital.fhir.export true \
  --exporter.practitioner.fhir.export true \
  --exporter.csv.export false \
  --exporter.ccda.export false \
  --generate.only_alive_patients true \
  Massachusetts
ls output/fhir | wc -l | xargs echo "bundles written:"
