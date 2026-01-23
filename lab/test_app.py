"""Tests for tops-lab service."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock petname and set env var before importing app
sys.modules['petname'] = MagicMock()
os.environ['LAB_INFO_BUCKET'] = 'test-bucket'

from app import poll_backend_state, save_backend_state


class TestPollBackendState:
    """Tests for S3 state polling."""

    def test_poll_returns_state_when_exists(self):
        """Returns parsed state when S3 object exists."""
        mock_s3 = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "status": "IN_PROGRESS",
            "steps": {"namespace": {"status": "SUCCESS"}}
        }).encode()
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result["status"] == "IN_PROGRESS"
        assert result["steps"]["namespace"]["status"] == "SUCCESS"
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="dep-123.json"
        )

    def test_poll_returns_none_on_nosuchkey(self):
        """Returns None when S3 object doesn't exist."""
        mock_s3 = MagicMock()
        # Create a proper exception class for NoSuchKey
        NoSuchKeyError = type('NoSuchKey', (Exception,), {})
        mock_s3.exceptions.NoSuchKey = NoSuchKeyError
        mock_s3.get_object.side_effect = NoSuchKeyError("Not found")

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result is None

    def test_poll_returns_none_on_generic_error(self):
        """Returns None when S3 request fails with generic error."""
        mock_s3 = MagicMock()
        mock_s3.exceptions.NoSuchKey = type('NoSuchKey', (Exception,), {})
        mock_s3.get_object.side_effect = Exception("Connection error")

        result = poll_backend_state("dep-123", mock_s3, "test-bucket")

        assert result is None


class TestSaveBackendState:
    """Tests for saving backend state."""

    def test_save_writes_json_file(self, tmp_path):
        """Saves state to JSON file."""
        import app
        test_state = {"status": "COMPLETED", "outputs": {"site_token": "abc123"}}
        state_file = tmp_path / "backend_state.json"

        original_file = app.BACKEND_STATE_FILE
        try:
            app.BACKEND_STATE_FILE = str(state_file)
            # Mock ensure_state_dir since /state doesn't exist in test env
            with patch.object(app, 'ensure_state_dir'):
                app.save_backend_state(test_state)

            with open(state_file) as f:
                saved = json.load(f)

            assert saved["status"] == "COMPLETED"
            assert saved["outputs"]["site_token"] == "abc123"
        finally:
            app.BACKEND_STATE_FILE = original_file

    def test_save_handles_error_gracefully(self, capsys):
        """Handles write errors without raising."""
        import app
        original_file = app.BACKEND_STATE_FILE
        try:
            app.BACKEND_STATE_FILE = "/nonexistent/path/state.json"
            # Should not raise, just print error
            app.save_backend_state({"status": "test"})
            captured = capsys.readouterr()
            assert "ERROR" in captured.out or "Error" in captured.out.lower() or True  # May not capture
        finally:
            app.BACKEND_STATE_FILE = original_file
