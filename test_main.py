"""Tests for krai API server."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Force local mock mode
import os
os.environ["API_KEY"] = "test-key"
os.environ["GCS_BUCKET"] = ""
os.environ["GOOGLE_CLOUD_PROJECT"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["PORT"] = "8099"

from main import app, jobs_store, mock_files


@pytest.fixture(autouse=True)
def clear_stores():
    jobs_store.clear()
    mock_files.clear()
    yield
    jobs_store.clear()
    mock_files.clear()


client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_healthz(self):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    def test_no_auth(self):
        resp = client.post("/api/v1/exports", json={"dataset": "test"})
        assert resp.status_code == 401

    def test_invalid_api_key(self):
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test"},
            headers={"x-api-key": "wrong-key"},
        )
        assert resp.status_code == 403

    def test_invalid_bearer_format(self):
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test"},
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    def test_bearer_without_client_id(self):
        """Bearer token fails when GOOGLE_CLIENT_ID is not configured."""
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test"},
            headers={"Authorization": "Bearer some-token"},
        )
        assert resp.status_code == 500
        assert "not configured" in resp.json()["detail"]

    @patch("main.GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    @patch("main.google_id_token")
    def test_valid_bearer_token(self, mock_id_token):
        mock_id_token.verify_oauth2_token.return_value = {
            "sub": "12345",
            "email": "user@example.com",
        }
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test"},
            headers={"Authorization": "Bearer valid-google-token"},
        )
        assert resp.status_code == 202

    @patch("main.GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    @patch("main.google_id_token")
    def test_invalid_bearer_token(self, mock_id_token):
        mock_id_token.verify_oauth2_token.side_effect = ValueError("Invalid token")
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test"},
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Export flow
# ---------------------------------------------------------------------------
class TestExport:
    def test_create_export(self):
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "users", "format": "csv", "size_mb": 0.001},
            headers=HEADERS,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "PENDING"

    def test_export_completes(self):
        """Test that the local mock worker processes the export."""
        import time

        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test", "size_mb": 0.001},
            headers=HEADERS,
        )
        job_id = resp.json()["job_id"]

        # Wait for the background thread to complete
        for _ in range(20):
            time.sleep(0.5)
            resp = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            if resp.json()["status"] == "COMPLETED":
                break

        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["progress"] == 100

        # Check result endpoint
        resp = client.get(f"/api/v1/jobs/{job_id}/result", headers=HEADERS)
        assert resp.status_code == 200
        result = resp.json()
        assert result["download_url"] is not None
        assert result["file_size_bytes"] > 0


# ---------------------------------------------------------------------------
# Import flow
# ---------------------------------------------------------------------------
class TestImport:
    def test_create_import(self):
        resp = client.post(
            "/api/v1/imports",
            json={"source": "salesforce", "dataset": "contacts"},
            headers=HEADERS,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "PENDING"

    def test_import_completes(self):
        """Test that the local mock worker processes the import."""
        import time

        resp = client.post(
            "/api/v1/imports",
            json={"source": "default", "dataset": "contacts"},
            headers=HEADERS,
        )
        job_id = resp.json()["job_id"]

        for _ in range(20):
            time.sleep(0.5)
            resp = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
            if resp.json()["status"] == "COMPLETED":
                break

        data = resp.json()
        assert data["status"] == "COMPLETED"

        # Check result endpoint
        resp = client.get(f"/api/v1/jobs/{job_id}/result", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["records_processed"] == 100


# ---------------------------------------------------------------------------
# Unified job endpoints
# ---------------------------------------------------------------------------
class TestJobs:
    def test_job_not_found(self):
        resp = client.get("/api/v1/jobs/nonexistent", headers=HEADERS)
        assert resp.status_code == 404

    def test_result_not_completed(self):
        resp = client.post(
            "/api/v1/exports",
            json={"dataset": "test", "size_mb": 0.001},
            headers=HEADERS,
        )
        job_id = resp.json()["job_id"]

        # Immediately request result before job completes
        resp = client.get(f"/api/v1/jobs/{job_id}/result", headers=HEADERS)
        assert resp.status_code == 400

    def test_result_not_found(self):
        resp = client.get("/api/v1/jobs/nonexistent/result", headers=HEADERS)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_export_default_values(self):
        resp = client.post("/api/v1/exports", json={}, headers=HEADERS)
        assert resp.status_code == 202

    def test_import_default_values(self):
        resp = client.post("/api/v1/imports", json={}, headers=HEADERS)
        assert resp.status_code == 202
