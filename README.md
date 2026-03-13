# krai — Long-Running Export/Import API

Async export/import API built with FastAPI + React frontend, deployed on GKE with Pub/Sub, GCS, and Firestore.

## Architecture

### Export Flow

```mermaid
sequenceDiagram
    participant UI as React Frontend
    participant API as FastAPI (GKE)
    participant FS as Firestore
    participant PS as Pub/Sub
    participant W as Worker (GKE)
    participant GCS as GCS

    UI->>+API: POST /api/v1/exports (x-api-key)
    API->>FS: Create job (PENDING)
    API->>PS: Publish message
    API-->>-UI: 202 {job_id}

    PS->>W: Pull message
    W->>GCS: Generate & upload data
    W->>FS: Update job (COMPLETED + signed URL)

    UI->>+API: GET /api/v1/jobs/{id}
    API->>FS: Read job
    API-->>-UI: 200 {status, progress}

    UI->>+API: GET /api/v1/jobs/{id}/result
    API->>FS: Read job
    API-->>-UI: 200 {download_url}

    UI->>GCS: Download via signed URL
```

### Import Flow

```mermaid
sequenceDiagram
    participant UI as React Frontend
    participant API as FastAPI (GKE)
    participant FS as Firestore
    participant PS as Pub/Sub
    participant W as Worker (GKE)

    UI->>+API: POST /api/v1/imports (x-api-key)
    API->>FS: Create job (PENDING)
    API->>PS: Publish message
    API-->>-UI: 202 {job_id}

    PS->>W: Pull message
    W->>W: Fetch & process data from source
    W->>FS: Update job (COMPLETED + records_processed)

    UI->>+API: GET /api/v1/jobs/{id}
    API->>FS: Read job
    API-->>-UI: 200 {status, progress}

    UI->>+API: GET /api/v1/jobs/{id}/result
    API->>FS: Read job
    API-->>-UI: 200 {records_processed}
```

## Key Design Decisions

| Decision | Why |
|----------|-----|
| **Signed URLs** | API server never buffers 1-100MB files |
| **Pub/Sub** | Decouples API from workers, natural backpressure |
| **Firestore** | Serverless job tracking, no schema migrations |
| **GKE + HPA** | Auto-scales API pods on CPU |
| **KEDA** | Scales worker pods based on Pub/Sub queue depth (messages per worker) instead of CPU |
| **Workload Identity** | No static credentials (GCP's IRSA equivalent) |
| **Separate namespaces** | `krai-backend` and `krai-frontend` deploy and scale independently |

### Worker Autoscaling (KEDA)

API pods scale on CPU via standard HPA — CPU correlates well with HTTP request load. Worker pods use [KEDA](https://keda.sh/) to scale on Pub/Sub queue depth instead, because a worker could be idle-polling at low CPU while messages pile up.

KEDA checks the `krai-jobs-sub` subscription every 15s and calculates: `desired workers = undelivered messages / messagesPerWorker (5)`.

| Messages in queue | Workers | Reason |
|---|---|---|
| 0 | 1 | `minReplicaCount` keeps at least 1 worker running |
| 5 | 1 | 5 / 5 = 1 worker needed |
| 12 | 3 | 12 / 5 = 2.4, rounds up to 3 |
| 20+ | 3 | Capped at `maxReplicaCount: 3` |
| 0 (after burst) | 1 | Scales down after 60s `cooldownPeriod` |

## Quick Start (Local)

```bash
# Backend (mock mode — no GCP credentials needed)
cd krai
pip install -r requirements.txt
python main.py

# In another terminal — E2E test
bash scripts/test-api.sh

# Frontend
cd krai-frontend
npm install
npm start
```

## API Reference

All endpoints (except `/healthz`) require `x-api-key` header.

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| GET | `/healthz` | Health check | 200 |
| POST | `/api/v1/exports` | Create export job | 202 `{job_id, status}` |
| POST | `/api/v1/imports` | Create import job | 202 `{job_id, status}` |
| GET | `/api/v1/jobs/{id}` | Poll job status | 200 `{status, progress}` |
| GET | `/api/v1/jobs/{id}/result` | Get result (when completed) | 200 `{download_url}` or `{records_processed}` |

## Deploy to GKE

```bash
# 1. Provision infrastructure
cd krai-terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT"

# 2. ArgoCD auto-syncs all Helm charts from krai-gitops
#    - keda                 → keda namespace (KEDA operator)
#    - krai-helm-chart      → krai-backend namespace
#    - krai-frontend-chart  → krai-frontend namespace
```

## Multi-Repo Layout

| Repo | Purpose |
|------|---------|
| **krai** | Backend API + worker (FastAPI, Python) |
| **krai-frontend** | React frontend |
| **krai-gitops** | Helm charts + ArgoCD manifests (backend, frontend, KEDA) |
| **krai-terraform** | Terraform IaC (GKE, VPC, IAM, GCS, Pub/Sub, Firestore, Artifact Registry, GitHub OIDC) |

## GKE Namespace Layout

```
keda namespace:           KEDA operator + metrics server
krai-backend namespace:   API pods + Worker pods (KEDA-scaled) + LoadBalancer Service
krai-frontend namespace:  React pods + LoadBalancer Service
```

## CI/CD Pipeline

Each repo has its own GitHub Actions workflows:

| Repo | CI (`test.yaml`) | CD (`publish.yaml`) |
|------|-------------------|---------------------|
| **krai-backend** | Lint (ruff) → Test (pytest) → Grype scan | Docker build → Push to Artifact Registry → Update image tag in krai-gitops |
| **krai-frontend** | Build → Grype scan | Docker build → Push to Artifact Registry → Update image tag in krai-gitops |
| **krai-terraform** | Checkov IaC security scan | — |
| **krai-gitops** | — (ArgoCD auto-syncs on push) | — |

GitHub Actions authenticates to GCP via **Workload Identity Federation** (OIDC) — no static credentials. Terraform provisions the identity pool, provider, and a dedicated `github-actions` service account with `artifactregistry.writer` role only.

### GitHub Secrets Required

Set these on both `krai-backend` and `krai-frontend` repos:

| Secret | Source |
|--------|--------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `terraform output gcp_workload_identity_provider` |
| `GCP_SERVICE_ACCOUNT` | `terraform output github_actions_service_account` |
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GITOPS_PAT` | GitHub PAT with `repo` scope for krai-gitops |

### Image Tagging Strategy

Each push to `main` builds a Docker image tagged with the **short git SHA** and `latest`. The CD pipeline updates the Helm values in krai-gitops with the SHA tag. ArgoCD detects the commit and deploys the new image. Using SHA (not `latest`) ensures Kubernetes always pulls the correct version and provides an audit trail for rollbacks.

## Testing

```bash
pip install -r requirements-dev.txt
pytest test_main.py -v
ruff check .
```

## Security

- API key authentication on all endpoints
- Rate limiting (100 req/15 min)
- Signed URLs with 15-min TTL
- Non-root container, read-only filesystem, drop all capabilities
- Workload Identity for GKE pods (no static GCP credentials)
- Workload Identity Federation for CI/CD (GitHub OIDC, no static GCP credentials)
- Separate service accounts: `krai-app` (application) and `github-actions` (CI/CD, `artifactregistry.writer` only)
- Private GCS bucket with uniform access control

## Production Hardening

This is a demo project. To keep costs low, the GKE cluster runs in a **single zone** (`us-central1-a`) with **Spot instances** (`e2-medium`). For a production deployment, the following changes would be made:

| Area | Current (Demo) | Production |
|------|----------------|------------|
| **Networking** | L4 LoadBalancer Service, plain HTTP | GKE Ingress (L7) with TLS termination, static IP via Terraform, Google-managed SSL certificate |
| **WAF / DDoS** | In-app rate limiting only | Cloud Armor policy attached to the Ingress for L7 filtering and DDoS protection |
| **DNS** | Clients hit raw IP | Cloud DNS record pointing to the static Ingress IP |
| **Auth** | Single shared API key in env var | Per-client API keys stored in Secret Manager, or OAuth 2.0 / JWT |
| **Secrets** | `API_KEY` in Helm values (plaintext) | External Secrets Operator syncing from GCP Secret Manager |
| **GKE cluster** | Public control plane, no authorized networks | Private cluster with authorized networks, Binary Authorization |
| **Observability** | Stdout logs only | Structured logging → Cloud Logging, metrics → Cloud Monitoring / Prometheus, distributed tracing via OpenTelemetry |
| **CI/CD** | Grype scan only | Add SAST (Semgrep), container signing (Cosign), SBOM generation, policy-as-code (OPA/Gatekeeper) |
| **Data** | Single-region Firestore + GCS | Multi-region Firestore, dual-region GCS, cross-region GKE for HA |
| **GKE topology** | Zonal cluster, Spot `e2-medium`, single shared node pool | Regional cluster for HA, on-demand nodes for API pods, Spot for workers, separate node pools per workload |
# krai
