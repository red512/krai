"""
krai - Long-running Export/Import API
Single-file FastAPI app with GCP integration (GCS, Firestore, Pub/Sub).
Runs with in-memory mocks when GCP env vars are not set.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY = os.getenv("API_KEY", "demo-key-change-me")
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC", "krai-jobs")
PUBSUB_SUBSCRIPTION = os.getenv("PUBSUB_SUBSCRIPTION", "krai-jobs-sub")
PORT = int(os.getenv("PORT", "8080"))
USE_GCP = bool(GOOGLE_CLOUD_PROJECT and GCS_BUCKET)

# ---------------------------------------------------------------------------
# GCP clients or in-memory mocks
# ---------------------------------------------------------------------------
jobs_store: dict = {}  # in-memory mock for Firestore
mock_files: dict = {}  # in-memory mock for GCS

if USE_GCP:
    from google.cloud import firestore, pubsub_v1, storage

    storage_client = storage.Client()
    bucket = storage_client.bucket(GCS_BUCKET)
    firestore_client = firestore.Client()
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(GOOGLE_CLOUD_PROJECT, PUBSUB_TOPIC)
else:
    storage_client = None
    bucket = None
    firestore_client = None
    publisher = None
    topic_path = None


# ---------------------------------------------------------------------------
# Job store helpers
# ---------------------------------------------------------------------------
def create_job(job: dict) -> dict:
    if USE_GCP:
        doc_ref = firestore_client.collection("jobs").document(job["id"])
        doc_ref.set(job)
    else:
        jobs_store[job["id"]] = job
    return job


def get_job(job_id: str) -> dict | None:
    if USE_GCP:
        doc = firestore_client.collection("jobs").document(job_id).get()
        return doc.to_dict() if doc.exists else None
    return jobs_store.get(job_id)


def update_job(job_id: str, fields: dict) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    if USE_GCP:
        firestore_client.collection("jobs").document(job_id).update(fields)
    else:
        if job_id in jobs_store:
            jobs_store[job_id].update(fields)


# ---------------------------------------------------------------------------
# Pub/Sub helpers
# ---------------------------------------------------------------------------
def publish_message(data: dict) -> None:
    payload = json.dumps(data).encode("utf-8")
    if USE_GCP:
        publisher.publish(topic_path, payload)
    else:
        # In local mode, process inline (simulates async worker)
        from threading import Thread
        Thread(target=_process_job_locally, args=(data,), daemon=True).start()


def _process_job_locally(data: dict):
    """Mock worker for local development."""
    job_id = data["job_id"]
    job_type = data["type"]
    time.sleep(1)  # simulate startup delay

    if job_type == "export":
        _process_export(job_id)
    elif job_type == "import":
        _process_import(job_id)


# ---------------------------------------------------------------------------
# GCS signed URL helpers
# ---------------------------------------------------------------------------
def generate_download_url(blob_name: str) -> str:
    if USE_GCP:
        from google.auth.transport import requests as auth_requests

        blob = bucket.blob(blob_name)
        credentials = storage_client._credentials
        credentials.refresh(auth_requests.Request())
        return blob.generate_signed_url(
            version="v4",
            expiration=900,  # 15 minutes
            method="GET",
            service_account_email=credentials.service_account_email,
            access_token=credentials.token,
        )
    return f"http://localhost:{PORT}/mock-download/{blob_name}"


# ---------------------------------------------------------------------------
# Mock data generator
# ---------------------------------------------------------------------------
def generate_mock_csv(size_mb: float = 1.0) -> bytes:
    """Generate mock CSV data of approximately the given size."""
    header = "id,name,email,amount,date,status\n"
    row_template = "{id},User {id},user{id}@example.com,{amount:.2f},2025-01-{day:02d},active\n"
    rows = []
    i = 0
    current_size = len(header)
    target_size = int(size_mb * 1024 * 1024)

    while current_size < target_size:
        row = row_template.format(id=i, amount=(i * 1.5) % 10000, day=(i % 28) + 1)
        rows.append(row)
        current_size += len(row)
        i += 1

    return (header + "".join(rows)).encode("utf-8")


def generate_mock_import_data() -> dict:
    """Simulate importing data from an external source."""
    return {
        "records": [
            {"id": i, "name": f"Record {i}", "value": i * 10}
            for i in range(100)
        ]
    }


# ---------------------------------------------------------------------------
# Worker functions (called by Pub/Sub worker or inline in local mode)
# ---------------------------------------------------------------------------
def _process_export(job_id: str) -> None:
    update_job(job_id, {"status": "PROCESSING", "progress": 0})
    job = get_job(job_id)
    if not job:
        return

    size_mb = job.get("params", {}).get("size_mb", 1.0)

    # Simulate progress updates
    for progress in range(10, 100, 10):
        time.sleep(0.5)  # simulate work
        update_job(job_id, {"progress": progress})

    # Generate and upload data
    data = generate_mock_csv(size_mb)
    blob_name = f"exports/{job_id}.csv"

    if USE_GCP:
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type="text/csv")
    else:
        mock_files[blob_name] = data

    download_url = generate_download_url(blob_name)
    update_job(job_id, {
        "status": "COMPLETED",
        "progress": 100,
        "download_url": download_url,
        "file_size_bytes": len(data),
    })


def _process_import(job_id: str) -> None:
    update_job(job_id, {"status": "PROCESSING", "progress": 0})
    job = get_job(job_id)
    if not job:
        return

    # Simulate fetching and processing data from source
    for progress in range(10, 100, 10):
        time.sleep(0.3)
        update_job(job_id, {"progress": progress})

    # Simulate records processed from source
    import_data = generate_mock_import_data()
    records_processed = len(import_data["records"])

    update_job(job_id, {
        "status": "COMPLETED",
        "progress": 100,
        "records_processed": records_processed,
    })


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="krai", version="1.0.0", description="Long-running Export/Import API")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_api_key(x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return x_api_key


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------
class ExportRequest(BaseModel):
    dataset: str = "default"
    format: str = "csv"
    size_mb: float = 1.0


class ImportRequest(BaseModel):
    source: str = "default"
    dataset: str = "contacts"
    description: str = ""


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------
@app.post("/api/v1/exports", status_code=202, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/15minutes")
async def create_export(request: Request, body: ExportRequest):
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "type": "export",
        "status": "PENDING",
        "progress": 0,
        "params": {"dataset": body.dataset, "format": body.format, "size_mb": body.size_mb},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    create_job(job)
    publish_message({"job_id": job_id, "type": "export"})

    return {
        "job_id": job_id,
        "status": "PENDING",
    }


# ---------------------------------------------------------------------------
# Import endpoint
# ---------------------------------------------------------------------------
@app.post("/api/v1/imports", status_code=202, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/15minutes")
async def create_import(request: Request, body: ImportRequest):
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "type": "import",
        "status": "PENDING",
        "progress": 0,
        "params": {"source": body.source, "dataset": body.dataset, "description": body.description},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    create_job(job)
    publish_message({"job_id": job_id, "type": "import"})

    return {
        "job_id": job_id,
        "status": "PENDING",
    }


# ---------------------------------------------------------------------------
# Unified job endpoints
# ---------------------------------------------------------------------------
@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(verify_api_key)])
async def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job["id"],
        "type": job["type"],
        "status": job["status"],
        "progress": job.get("progress", 0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


@app.get("/api/v1/jobs/{job_id}/result", dependencies=[Depends(verify_api_key)])
async def get_job_result(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "COMPLETED":
        raise HTTPException(status_code=400, detail=f"Job not completed (status: {job['status']})")

    result = {
        "job_id": job["id"],
        "type": job["type"],
        "status": job["status"],
    }

    if job["type"] == "export":
        result["download_url"] = job.get("download_url")
        result["file_size_bytes"] = job.get("file_size_bytes")
    elif job["type"] == "import":
        result["records_processed"] = job.get("records_processed")

    return result


# ---------------------------------------------------------------------------
# Mock endpoint (local development only)
# ---------------------------------------------------------------------------
@app.get("/mock-download/{blob_name:path}")
async def mock_download(blob_name: str):
    from fastapi.responses import Response
    data = mock_files.get(blob_name, b"")
    if not data:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=data, media_type="text/csv")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print(f"[krai] Starting API server on port {PORT}")
    print(f"[krai] GCP mode: {'enabled' if USE_GCP else 'disabled (using mocks)'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
