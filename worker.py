"""
krai worker - Pub/Sub pull subscriber for processing export/import jobs.
Runs as a separate deployment in GKE alongside the API server.
"""

import json
import os
import signal
import sys
import time

from dotenv import load_dotenv
from google.cloud import firestore, pubsub_v1, storage

load_dotenv()

GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
PUBSUB_SUBSCRIPTION = os.getenv("PUBSUB_SUBSCRIPTION", "krai-jobs-sub")
GCS_BUCKET = os.getenv("GCS_BUCKET", "")

if not GOOGLE_CLOUD_PROJECT:
    print("[worker] GOOGLE_CLOUD_PROJECT not set. Worker requires GCP credentials.")
    print("[worker] For local development, the API server handles jobs inline.")
    sys.exit(1)

storage_client = storage.Client()
bucket = storage_client.bucket(GCS_BUCKET)
firestore_client = firestore.Client()
subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(GOOGLE_CLOUD_PROJECT, PUBSUB_SUBSCRIPTION)

running = True


def update_job(job_id: str, fields: dict) -> None:
    from datetime import datetime, timezone
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    firestore_client.collection("jobs").document(job_id).update(fields)


def get_job(job_id: str) -> dict | None:
    doc = firestore_client.collection("jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


def generate_mock_csv(size_mb: float = 1.0) -> bytes:
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


def generate_download_url(blob_name: str) -> str:
    from google.auth.transport import requests as auth_requests

    blob = bucket.blob(blob_name)
    # Workload Identity provides a token, not a private key.
    # Pass SA email + access token so the client uses IAM signBlob API instead.
    credentials = storage_client._credentials
    credentials.refresh(auth_requests.Request())
    return blob.generate_signed_url(
        version="v4",
        expiration=900,
        method="GET",
        service_account_email=credentials.service_account_email,
        access_token=credentials.token,
    )


def process_export(job_id: str) -> None:
    print(f"[worker] Processing export job {job_id}")
    update_job(job_id, {"status": "PROCESSING", "progress": 0})
    job = get_job(job_id)
    if not job:
        print(f"[worker] Job {job_id} not found")
        return

    size_mb = job.get("params", {}).get("size_mb", 1.0)

    for progress in range(10, 100, 10):
        time.sleep(0.5)
        update_job(job_id, {"progress": progress})

    data = generate_mock_csv(size_mb)
    blob_name = f"exports/{job_id}.csv"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type="text/csv")

    download_url = generate_download_url(blob_name)
    update_job(job_id, {
        "status": "COMPLETED",
        "progress": 100,
        "download_url": download_url,
        "file_size_bytes": len(data),
    })
    print(f"[worker] Export job {job_id} completed ({len(data)} bytes)")


def process_import(job_id: str) -> None:
    print(f"[worker] Processing import job {job_id}")
    update_job(job_id, {"status": "PROCESSING", "progress": 0})
    job = get_job(job_id)
    if not job:
        print(f"[worker] Job {job_id} not found")
        return

    # Simulate fetching and processing data from source
    for progress in range(10, 100, 10):
        time.sleep(0.3)
        update_job(job_id, {"progress": progress})

    # Mock: simulate processing 100 records from source
    records_processed = 100
    update_job(job_id, {
        "status": "COMPLETED",
        "progress": 100,
        "records_processed": records_processed,
    })
    print(f"[worker] Import job {job_id} completed ({records_processed} records)")


def callback(message):
    try:
        data = json.loads(message.data.decode("utf-8"))
        job_id = data.get("job_id")
        job_type = data.get("type")

        if not job_id or not job_type:
            print(f"[worker] Invalid message: {data}")
            message.ack()
            return

        if job_type == "export":
            process_export(job_id)
        elif job_type == "import":
            process_import(job_id)
        else:
            print(f"[worker] Unknown job type: {job_type}")

        message.ack()
    except Exception as e:
        print(f"[worker] Error processing message: {e}")
        message.nack()


def shutdown(_signum, _frame):
    global running
    print("[worker] Shutting down gracefully...")
    running = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    print(f"[worker] Starting Pub/Sub subscriber on {subscription_path}")
    streaming_pull = subscriber.subscribe(subscription_path, callback=callback)

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        streaming_pull.cancel()
        streaming_pull.result(timeout=10)
        print("[worker] Stopped")
