#!/usr/bin/env bash
set -euo pipefail

# End-to-end API test script
# Usage: ./scripts/test-api.sh [base-url] [api-key]

BASE_URL="${1:-http://localhost:8080}"
API_KEY="${2:-demo-key-change-me}"

echo "=== krai API E2E Test ==="
echo "Base URL: ${BASE_URL}"
echo ""

# Health check
echo "--- Health Check ---"
curl -s "${BASE_URL}/healthz" | python3 -m json.tool
echo ""

# Create export job
echo "--- Create Export Job ---"
EXPORT_RESP=$(curl -s -X POST "${BASE_URL}/api/v1/exports" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"dataset": "users", "format": "csv", "size_mb": 0.01}')
echo "${EXPORT_RESP}" | python3 -m json.tool

EXPORT_ID=$(echo "${EXPORT_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Export Job ID: ${EXPORT_ID}"
echo ""

# Poll export status
echo "--- Polling Export Status ---"
for i in $(seq 1 30); do
  STATUS_RESP=$(curl -s "${BASE_URL}/api/v1/jobs/${EXPORT_ID}" \
    -H "x-api-key: ${API_KEY}")
  STATUS=$(echo "${STATUS_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  PROGRESS=$(echo "${STATUS_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('progress', 0))")
  echo "  Poll ${i}: status=${STATUS} progress=${PROGRESS}%"

  if [ "${STATUS}" = "COMPLETED" ]; then
    echo ""
    echo "Export completed!"
    echo "${STATUS_RESP}" | python3 -m json.tool

    # Get result with download URL
    echo ""
    echo "--- Export Result ---"
    RESULT_RESP=$(curl -s "${BASE_URL}/api/v1/jobs/${EXPORT_ID}/result" \
      -H "x-api-key: ${API_KEY}")
    echo "${RESULT_RESP}" | python3 -m json.tool
    DOWNLOAD_URL=$(echo "${RESULT_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['download_url'])")
    echo ""
    echo "Download URL: ${DOWNLOAD_URL}"
    echo "First 5 lines of downloaded file:"
    curl -s "${DOWNLOAD_URL}" | head -5
    echo ""
    break
  fi
  sleep 2
done
echo ""

# Create import job
echo "--- Create Import Job ---"
IMPORT_RESP=$(curl -s -X POST "${BASE_URL}/api/v1/imports" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"source": "salesforce", "dataset": "contacts", "description": "test import"}')
echo "${IMPORT_RESP}" | python3 -m json.tool

IMPORT_ID=$(echo "${IMPORT_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Import Job ID: ${IMPORT_ID}"
echo ""

# Poll import status
echo "--- Polling Import Status ---"
for i in $(seq 1 30); do
  STATUS_RESP=$(curl -s "${BASE_URL}/api/v1/jobs/${IMPORT_ID}" \
    -H "x-api-key: ${API_KEY}")
  STATUS=$(echo "${STATUS_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  PROGRESS=$(echo "${STATUS_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('progress', 0))")
  echo "  Poll ${i}: status=${STATUS} progress=${PROGRESS}%"

  if [ "${STATUS}" = "COMPLETED" ]; then
    echo ""
    echo "Import completed!"

    # Get result
    echo ""
    echo "--- Import Result ---"
    curl -s "${BASE_URL}/api/v1/jobs/${IMPORT_ID}/result" \
      -H "x-api-key: ${API_KEY}" | python3 -m json.tool
    break
  fi
  sleep 2
done

echo ""
echo "=== E2E Test Complete ==="
