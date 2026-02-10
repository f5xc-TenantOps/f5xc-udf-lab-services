"""Tests for the consolidated tops-lab service."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock petname before importing app so module-level code doesn't fail
sys.modules["petname"] = MagicMock()

import app as app_module
from app import (
    app,
    fetch_global_config,
    load_deployment_state,
    poll_backend_state,
    save_deployment_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level globals before each test."""
    app_module._backend_state = None
    app_module._deployment_state = None
    app_module._ce_status = None
    app_module._ce_registration_started = False
    yield
    app_module._backend_state = None
    app_module._deployment_state = None
    app_module._ce_status = None
    app_module._ce_registration_started = False


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_running(self, client):
        """GET /health returns running status."""
        response = client.get("/health")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "running"


# ---------------------------------------------------------------------------
# /status/json endpoint
# ---------------------------------------------------------------------------
class TestStatusJson:
    """Tests for /status/json endpoint."""

    def test_status_json_returns_backend_state(self, client):
        """GET /status/json returns backend state when available."""
        app_module._backend_state = {
            "status": "IN_PROGRESS",
            "petname": "fuzzy-cat",
            "steps": {"namespace": {"status": "SUCCESS"}},
        }

        response = client.get("/status/json")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "IN_PROGRESS"
        assert data["petname"] == "fuzzy-cat"

    def test_status_json_returns_waiting_when_no_state(self, client):
        """GET /status/json returns WAITING when no backend state."""
        response = client.get("/status/json")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "WAITING"
        assert "message" in data


# ---------------------------------------------------------------------------
# /metadata endpoint
# ---------------------------------------------------------------------------
class TestMetadataEndpoint:
    """Tests for /metadata endpoint."""

    def test_metadata_returns_deployment_metadata(self, client):
        """GET /metadata returns deployment metadata when available."""
        app_module._deployment_state = {
            "dep_id": "dep-123",
            "lab_id": "lab-456",
            "email": "test@example.com",
            "petname": "fuzzy-cat",
            "config": {"sqsURL": "https://sqs.example.com"},
        }

        response = client.get("/metadata")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["dep_id"] == "dep-123"
        assert data["lab_id"] == "lab-456"
        assert data["email"] == "test@example.com"
        assert data["petname"] == "fuzzy-cat"

    def test_metadata_returns_404_when_no_state(self, client):
        """GET /metadata returns 404 when no deployment state."""
        response = client.get("/metadata")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# /petname endpoint
# ---------------------------------------------------------------------------
class TestPetnameEndpoint:
    """Tests for /petname endpoint."""

    def test_petname_returns_petname(self, client):
        """GET /petname returns the deployment petname."""
        app_module._deployment_state = {
            "dep_id": "dep-123",
            "lab_id": "lab-456",
            "email": "test@example.com",
            "petname": "fuzzy-cat",
        }

        response = client.get("/petname")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["petname"] == "fuzzy-cat"

    def test_petname_returns_404_when_no_state(self, client):
        """GET /petname returns 404 when no deployment state."""
        response = client.get("/petname")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# /outputs endpoints
# ---------------------------------------------------------------------------
class TestOutputsEndpoints:
    """Tests for /outputs endpoints."""

    def test_outputs_returns_all_outputs(self, client):
        """GET /outputs returns all outputs when available."""
        app_module._backend_state = {
            "status": "COMPLETED",
            "outputs": {
                "site_token": "abc123",
                "lb_hostname": "app.example.com",
            },
        }

        response = client.get("/outputs")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["site_token"] == "abc123"
        assert data["lb_hostname"] == "app.example.com"

    def test_outputs_returns_404_when_no_outputs(self, client):
        """GET /outputs returns 404 when no backend state."""
        response = client.get("/outputs")

        assert response.status_code == 404

    def test_outputs_returns_404_when_empty_outputs(self, client):
        """GET /outputs returns 404 when outputs dict is empty."""
        app_module._backend_state = {"status": "IN_PROGRESS", "outputs": {}}

        response = client.get("/outputs")

        assert response.status_code == 404

    def test_output_key_returns_specific_output(self, client):
        """GET /outputs/<key> returns specific output value."""
        app_module._backend_state = {
            "status": "COMPLETED",
            "outputs": {
                "site_token": "abc123",
                "lb_hostname": "app.example.com",
            },
        }

        response = client.get("/outputs/site_token")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["site_token"] == "abc123"

    def test_output_key_returns_404_when_key_not_found(self, client):
        """GET /outputs/<key> returns 404 when key doesn't exist."""
        app_module._backend_state = {
            "status": "IN_PROGRESS",
            "outputs": {"other_key": "value"},
        }

        response = client.get("/outputs/site_token")

        assert response.status_code == 404
        data = json.loads(response.data)
        assert "not available" in data["error"]

    def test_output_key_returns_404_when_no_state(self, client):
        """GET /outputs/<key> returns 404 when no backend state."""
        response = client.get("/outputs/site_token")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# /ce/status endpoint
# ---------------------------------------------------------------------------
class TestCEStatusEndpoint:
    """Tests for /ce/status endpoint."""

    def test_ce_status_returns_not_started_when_no_registration(self, client):
        """GET /ce/status returns NOT_STARTED when no registration."""
        response = client.get("/ce/status")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "NOT_STARTED"

    def test_ce_status_returns_status_when_available(self, client):
        """GET /ce/status returns CE status when set."""
        app_module._ce_status = {
            "status": "REGISTERED",
            "ce_ip": "10.1.1.5",
            "state": "ONLINE",
        }

        response = client.get("/ce/status")

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "REGISTERED"
        assert data["ce_ip"] == "10.1.1.5"


# ---------------------------------------------------------------------------
# poll_backend_state
# ---------------------------------------------------------------------------
class TestPollBackendState:
    """Tests for S3 state polling."""

    def test_poll_returns_state_when_exists(self):
        """Returns parsed state when S3 object exists."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "status": "IN_PROGRESS",
            "steps": {"namespace": {"status": "SUCCESS"}},
        }).encode()
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result["status"] == "IN_PROGRESS"
        assert result["steps"]["namespace"]["status"] == "SUCCESS"
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="dep-123.json"
        )

    def test_poll_returns_none_on_nosuchkey(self):
        """Returns None when S3 object doesn't exist."""
        mock_s3 = MagicMock()
        NoSuchKeyError = type("NoSuchKey", (Exception,), {})
        mock_s3.exceptions.NoSuchKey = NoSuchKeyError
        mock_s3.get_object.side_effect = NoSuchKeyError("Not found")

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result is None

    def test_poll_returns_none_on_generic_error(self):
        """Returns None when S3 request fails with a generic error."""
        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        mock_s3.get_object.side_effect = Exception("Connection error")

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result is None


# ---------------------------------------------------------------------------
# save_deployment_state / load_deployment_state
# ---------------------------------------------------------------------------
class TestDeploymentStatePersistence:
    """Tests for save/load deployment state round-trip."""

    def test_save_and_load_round_trip(self, tmp_path):
        """save_deployment_state and load_deployment_state round-trip correctly."""
        state_file = tmp_path / "deployment_state.json"
        original = app_module.STATE_FILE

        try:
            app_module.STATE_FILE = str(state_file)
            saved = save_deployment_state(
                dep_id="dep-123",
                lab_id="lab-456",
                email="user@test.com",
                pet="fuzzy-cat",
                config={"sqsURL": "https://sqs.example.com"},
            )

            loaded = load_deployment_state()

            assert loaded is not None
            assert loaded["dep_id"] == "dep-123"
            assert loaded["lab_id"] == "lab-456"
            assert loaded["email"] == "user@test.com"
            assert loaded["petname"] == "fuzzy-cat"
            assert loaded["config"]["sqsURL"] == "https://sqs.example.com"
            assert loaded == saved
        finally:
            app_module.STATE_FILE = original

    def test_load_returns_none_when_file_missing(self):
        """load_deployment_state returns None when file doesn't exist."""
        original = app_module.STATE_FILE
        try:
            app_module.STATE_FILE = "/nonexistent/path/state.json"
            result = load_deployment_state()
            assert result is None
        finally:
            app_module.STATE_FILE = original


# ---------------------------------------------------------------------------
# fetch_global_config
# ---------------------------------------------------------------------------
class TestFetchGlobalConfig:
    """Tests for fetch_global_config."""

    def test_fetch_global_config_parses_config(self):
        """fetch_global_config parses config.json from S3 correctly."""
        config_data = {
            "sqsURL": "https://sqs.us-east-1.amazonaws.com/123456/tops-queue",
            "stateBucket": "tops-deployment-state",
        }

        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps(config_data).encode()

        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": mock_body}

        with patch("app.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3
            result = fetch_global_config("AKID", "SECRET")

        assert result is not None
        assert result["sqsURL"] == config_data["sqsURL"]
        assert result["stateBucket"] == config_data["stateBucket"]
        mock_boto3.client.assert_called_once_with(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
        )

    def test_fetch_global_config_returns_none_on_error(self):
        """fetch_global_config returns None when S3 access fails."""
        with patch("app.boto3") as mock_boto3:
            mock_boto3.client.side_effect = Exception("Access denied")
            result = fetch_global_config("AKID", "SECRET")

        assert result is None


# ---------------------------------------------------------------------------
# Contract tests: S3 state schema (backend Lambdas -> tops-lab)
# ---------------------------------------------------------------------------
class TestContractWithBackend:
    """Contract tests ensuring tops-lab correctly consumes the S3 state schema.

    The fixture represents the state format written by backend Lambdas.
    If this test breaks, the schema contract between repos has drifted.
    """

    @pytest.fixture
    def sample_state(self):
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_deployment_state.json")
        with open(fixture_path) as f:
            return json.load(f)

    def test_status_json_serves_full_state(self, client, sample_state):
        app_module._backend_state = sample_state
        response = client.get("/status/json")
        data = json.loads(response.data)
        assert data["status"] == "IN_PROGRESS"
        assert data["dep_id"] == "deployment-12345"
        assert data["petname"] == "fuzzy-dragon"
        assert data["updated_at"] == "2026-02-07T10:30:00Z"

    def test_steps_preserved(self, client, sample_state):
        app_module._backend_state = sample_state
        response = client.get("/status/json")
        data = json.loads(response.data)
        assert "namespace" in data["steps"]
        assert data["steps"]["namespace"]["status"] == "SUCCESS"
        assert data["steps"]["user"]["status"] == "IN_PROGRESS"
        assert data["steps"]["resource_provisioning"]["status"] == "PENDING"

    def test_outputs_served_correctly(self, client, sample_state):
        app_module._backend_state = sample_state
        response = client.get("/outputs")
        data = json.loads(response.data)
        assert data["site_token"] == "jwt-abc123-long-token-value"
        assert data["lb_hostname"] == "fuzzy-dragon.lab-sec.f5demos.com"

    def test_specific_output_key(self, client, sample_state):
        app_module._backend_state = sample_state
        response = client.get("/outputs/site_token")
        data = json.loads(response.data)
        assert data["site_token"] == "jwt-abc123-long-token-value"

    def test_errors_array_empty(self, client, sample_state):
        app_module._backend_state = sample_state
        response = client.get("/status/json")
        data = json.loads(response.data)
        assert data["errors"] == []

    def test_completed_state_with_all_outputs(self, client, sample_state):
        sample_state["status"] = "COMPLETED"
        sample_state["steps"]["user"]["status"] = "SUCCESS"
        sample_state["steps"]["resource_provisioning"]["status"] = "SUCCESS"
        app_module._backend_state = sample_state
        response = client.get("/status/json")
        data = json.loads(response.data)
        assert data["status"] == "COMPLETED"
        assert all(s["status"] == "SUCCESS" for s in data["steps"].values())
