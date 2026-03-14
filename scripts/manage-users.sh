#!/bin/bash
# Manage allowed emails in Firestore for OAuth access control.
# Usage:
#   ./manage-users.sh add user@example.com
#   ./manage-users.sh remove user@example.com
#   ./manage-users.sh list

set -euo pipefail

PROJECT="${GCP_PROJECT:?Set GCP_PROJECT environment variable}"
COLLECTION="allowed_emails"
BASE="https://firestore.googleapis.com/v1/projects/$PROJECT/databases/(default)/documents"
TOKEN=$(gcloud auth print-access-token)

case "${1:-}" in
  add)
    EMAIL="${2:?Usage: $0 add <email>}"
    curl -s -X PATCH \
      "$BASE/$COLLECTION/$EMAIL" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "fields": {
          "email": {"stringValue": "'"$EMAIL"'"},
          "added_at": {"stringValue": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}
        }
      }' | python3 -c "import sys,json; d=json.load(sys.stdin); print('Added:', d['fields']['email']['stringValue'])"
    ;;
  remove)
    EMAIL="${2:?Usage: $0 remove <email>}"
    curl -s -X DELETE \
      "$BASE/$COLLECTION/$EMAIL" \
      -H "Authorization: Bearer $TOKEN"
    echo "Removed: $EMAIL"
    ;;
  list)
    curl -s "$BASE/$COLLECTION" \
      -H "Authorization: Bearer $TOKEN" \
      | python3 -c "
import sys, json
data = json.load(sys.stdin)
for doc in data.get('documents', []):
    print(doc['fields']['email']['stringValue'])
"
    ;;
  *)
    echo "Usage: $0 {add|remove|list} [email]"
    exit 1
    ;;
esac
