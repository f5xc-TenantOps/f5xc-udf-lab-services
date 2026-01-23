"""Tests for tops-info service."""

import json
import sys
from unittest.mock import patch, MagicMock

import pytest

# Set up module-level globals before importing app
import app as app_module
app_module.metadata = {"petname": "test-pet", "email": "test@example.com"}
app_module.lab_info = {"sqsURL": "https://sqs.example.com"}
app_module.petname = "test-pet"


@pytest.fixture
def client():
    """Flask test client."""
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as client:
        yield client


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_running(self, client):
        """GET /health returns running status."""
        response = client.get('/health')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "running"


class TestStatusEndpoints:
    """Tests for /status endpoints."""

    def test_status_json_returns_backend_state(self, client):
        """GET /status/json returns backend state when available."""
        mock_state = {
            "status": "IN_PROGRESS",
            "petname": "fuzzy-cat",
            "steps": {"namespace": {"status": "SUCCESS"}}
        }

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/status/json')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "IN_PROGRESS"
            assert data["petname"] == "fuzzy-cat"

    def test_status_json_returns_waiting_when_no_state(self, client):
        """GET /status/json returns waiting status when no backend state."""
        with patch.object(app_module, 'load_backend_state', return_value=None):
            response = client.get('/status/json')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["status"] == "WAITING"

    def test_status_page_renders_html(self, client):
        """GET /status returns HTML page."""
        mock_state = {
            "status": "COMPLETED",
            "petname": "fuzzy-cat",
            "email": "user@test.com",
            "steps": {"namespace": {"status": "SUCCESS"}}
        }

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/status')

            assert response.status_code == 200
            assert b'fuzzy-cat' in response.data
            assert b'COMPLETED' in response.data


class TestOutputsEndpoints:
    """Tests for /outputs endpoints."""

    def test_outputs_returns_all_outputs(self, client):
        """GET /outputs returns all outputs when available."""
        mock_state = {
            "status": "COMPLETED",
            "outputs": {
                "site_token": "abc123",
                "lb_hostname": "app.example.com"
            }
        }

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/outputs')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["site_token"] == "abc123"
            assert data["lb_hostname"] == "app.example.com"

    def test_outputs_returns_404_when_no_outputs(self, client):
        """GET /outputs returns 404 when no outputs available."""
        with patch.object(app_module, 'load_backend_state', return_value=None):
            response = client.get('/outputs')

            assert response.status_code == 404

    def test_outputs_returns_404_when_empty_outputs(self, client):
        """GET /outputs returns 404 when outputs dict is empty."""
        mock_state = {"status": "IN_PROGRESS", "outputs": {}}

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/outputs')

            assert response.status_code == 404

    def test_output_key_returns_specific_output(self, client):
        """GET /outputs/<key> returns specific output value."""
        mock_state = {
            "status": "COMPLETED",
            "outputs": {"site_token": "abc123", "lb_hostname": "app.example.com"}
        }

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/outputs/site_token')

            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["site_token"] == "abc123"

    def test_output_key_returns_404_when_key_not_found(self, client):
        """GET /outputs/<key> returns 404 when key doesn't exist."""
        mock_state = {
            "status": "IN_PROGRESS",
            "outputs": {"other_key": "value"}
        }

        with patch.object(app_module, 'load_backend_state', return_value=mock_state):
            response = client.get('/outputs/site_token')

            assert response.status_code == 404
            data = json.loads(response.data)
            assert "not available" in data["error"]

    def test_output_key_returns_404_when_no_state(self, client):
        """GET /outputs/<key> returns 404 when no backend state."""
        with patch.object(app_module, 'load_backend_state', return_value=None):
            response = client.get('/outputs/site_token')

            assert response.status_code == 404


class TestLoadBackendState:
    """Tests for load_backend_state function."""

    def test_load_returns_state_when_file_exists(self, tmp_path):
        """Returns state dict when file exists."""
        state_file = tmp_path / "backend_state.json"
        state_file.write_text(json.dumps({"status": "COMPLETED"}))

        original_file = app_module.BACKEND_STATE_FILE
        try:
            app_module.BACKEND_STATE_FILE = str(state_file)
            result = app_module.load_backend_state()
            assert result["status"] == "COMPLETED"
        finally:
            app_module.BACKEND_STATE_FILE = original_file

    def test_load_returns_none_when_file_missing(self):
        """Returns None when file doesn't exist."""
        original_file = app_module.BACKEND_STATE_FILE
        try:
            app_module.BACKEND_STATE_FILE = "/nonexistent/path/state.json"
            result = app_module.load_backend_state()
            assert result is None
        finally:
            app_module.BACKEND_STATE_FILE = original_file
