#!/bin/bash
# Manage allowed emails in Firestore for OAuth access control.
# Usage:
#   ./manage-users.sh add user@example.com
#   ./manage-users.sh remove user@example.com
#   ./manage-users.sh list

set -euo pipefail

PROJECT="${GCP_PROJECT:-krai-project-490112}"
COLLECTION="allowed_emails"

case "${1:-}" in
  add)
    EMAIL="${2:?Usage: $0 add <email>}"
    gcloud firestore documents create \
      --project="$PROJECT" \
      --database="(default)" \
      --collection="$COLLECTION" \
      --document-id="$EMAIL" \
      --data='{"email":"'"$EMAIL"'","added_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}'
    echo "Added: $EMAIL"
    ;;
  remove)
    EMAIL="${2:?Usage: $0 remove <email>}"
    gcloud firestore documents delete \
      --project="$PROJECT" \
      --database="(default)" \
      "projects/$PROJECT/databases/(default)/documents/$COLLECTION/$EMAIL"
    echo "Removed: $EMAIL"
    ;;
  list)
    gcloud firestore documents list \
      --project="$PROJECT" \
      --database="(default)" \
      --collection="$COLLECTION" \
      --format="table(name.basename())"
    ;;
  *)
    echo "Usage: $0 {add|remove|list} [email]"
    exit 1
    ;;
esac
