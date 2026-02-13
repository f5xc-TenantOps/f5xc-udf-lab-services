"""Tests for CE registration client."""

from unittest.mock import MagicMock, patch

import pytest

import ce_client


# ---------------------------------------------------------------------------
# discover_ce_ip
# ---------------------------------------------------------------------------
class TestDiscoverCeIp:
    """Tests for discover_ce_ip."""

    @patch("ce_client.http_requests")
    def test_returns_mgmt_ip(self, mock_requests):
        """Returns mgmtIp from first tagged instance."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"mgmtIp": "10.1.1.5"}]
        mock_requests.get.return_value = mock_resp

        result = ce_client.discover_ce_ip()

        assert result == "10.1.1.5"

    @patch("ce_client.http_requests")
    def test_raises_on_400(self, mock_requests):
        """Raises RuntimeError when no role tag exists."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_requests.get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="CE not found"):
            ce_client.discover_ce_ip()

    @patch("ce_client.http_requests")
    def test_raises_on_empty_results(self, mock_requests):
        """Raises RuntimeError when no instances are tagged."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = []
        mock_requests.get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="CE not found"):
            ce_client.discover_ce_ip()

    @patch("ce_client.http_requests")
    def test_raises_on_network_error(self, mock_requests):
        """Raises RuntimeError on network failure."""
        mock_requests.get.side_effect = ConnectionError("no route")

        with pytest.raises(RuntimeError, match="CE discovery failed"):
            ce_client.discover_ce_ip()


# ---------------------------------------------------------------------------
# register_ce
# ---------------------------------------------------------------------------
class TestRegisterCe:
    """Tests for register_ce."""

    @patch("ce_client.http_requests")
    def test_posts_token(self, mock_requests):
        """Posts site token to CE config endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}
        mock_requests.post.return_value = mock_resp

        result = ce_client.register_ce("10.1.1.5", "jwt-token")

        assert result == {"result": "ok"}
        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args
        assert call_kwargs[1]["json"] == {"token": "jwt-token"}

    @patch("ce_client.http_requests")
    def test_raises_on_failure(self, mock_requests):
        """Raises RuntimeError on POST failure."""
        mock_requests.post.side_effect = ConnectionError("refused")

        with pytest.raises(RuntimeError, match="CE registration failed"):
            ce_client.register_ce("10.1.1.5", "jwt-token")



# ---------------------------------------------------------------------------
# get_ce_status
# ---------------------------------------------------------------------------
class TestGetCeStatus:
    """Tests for get_ce_status."""

    @patch("ce_client.http_requests")
    def test_returns_status_dict(self, mock_requests):
        """Returns parsed status from health endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "state": "ONLINE",
            "hostname": "ce-node-1",
            "os_version": "9.2024.30",
            "public_ip": "203.0.113.1",
        }
        mock_requests.get.return_value = mock_resp

        result = ce_client.get_ce_status("10.1.1.5")

        assert result["state"] == "ONLINE"
        assert result["hostname"] == "ce-node-1"

    @patch("ce_client.http_requests")
    def test_raises_on_unreachable(self, mock_requests):
        """Raises RuntimeError when CE is unreachable."""
        mock_requests.get.side_effect = ConnectionError("timeout")

        with pytest.raises(RuntimeError, match="CE is not responding"):
            ce_client.get_ce_status("10.1.1.5")


# ---------------------------------------------------------------------------
# poll_ce_until_online
# ---------------------------------------------------------------------------
class TestPollCeUntilOnline:
    """Tests for dual-timeout polling logic."""

    @patch("ce_client.time.sleep")
    @patch("ce_client.get_ce_status")
    def test_poll_returns_registered_on_online(self, mock_status, mock_sleep):
        """CE returns ONLINE on first poll — returns REGISTERED immediately."""
        mock_status.return_value = {
            "state": "ONLINE",
            "hostname": "ce-1",
            "os_version": "9.2024.30",
            "public_ip": "203.0.113.1",
        }

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "REGISTERED"
        assert result["ce_ip"] == "10.1.1.5"
        assert result["state"] == "ONLINE"
        mock_sleep.assert_not_called()

    @patch("ce_client.time.sleep")
    @patch("ce_client.get_ce_status")
    def test_poll_returns_registered_on_provisioned(self, mock_status, mock_sleep):
        """CE returns PROVISIONED — treated as success like ONLINE."""
        mock_status.return_value = {
            "state": "PROVISIONED",
            "hostname": "ce-1",
            "os_version": "9.2024.30",
            "public_ip": "203.0.113.1",
        }

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "REGISTERED"
        assert result["state"] == "PROVISIONED"
        mock_sleep.assert_not_called()

    @patch("ce_client.time.sleep")
    @patch("ce_client.time.time")
    @patch("ce_client.get_ce_status")
    def test_poll_resets_silence_timeout_on_valid_response(
        self, mock_status, mock_time, mock_sleep
    ):
        """CE returns PROVISIONING repeatedly — silence timer resets, does not timeout."""
        # time.time() calls: start, last_contact init,
        # then per iteration: last_contact reset + now (2 calls each)
        # Third iteration returns ONLINE so only last_contact is called before return
        times = iter([
            0,      # start
            0,      # last_contact init
            30,     # last_contact = 30 (valid response)
            30,     # now = 30 (checks ok)
            60,     # last_contact = 60 (valid response)
            60,     # now = 60 (checks ok)
            90,     # last_contact = 90 (ONLINE — returns immediately)
        ])
        mock_time.side_effect = lambda: next(times)

        call_count = [0]
        def status_side_effect(ip):
            call_count[0] += 1
            if call_count[0] < 3:
                return {"state": "PROVISIONING", "hostname": "", "os_version": "", "public_ip": ""}
            return {"state": "ONLINE", "hostname": "ce-1", "os_version": "", "public_ip": ""}

        mock_status.side_effect = status_side_effect

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "REGISTERED"
        assert mock_sleep.call_count == 2

    @patch("ce_client.time.sleep")
    @patch("ce_client.time.time")
    @patch("ce_client.get_ce_status")
    def test_poll_silence_timeout_when_ce_goes_dark(
        self, mock_status, mock_time, mock_sleep
    ):
        """CE responds once then goes dark — hits silence timeout."""
        # Iteration 1: valid response → last_contact reset + now (2 calls)
        # Iteration 2: RuntimeError → no last_contact reset, just now (1 call)
        # Iteration 3: RuntimeError → just now (1 call) → silence timeout
        times = iter([
            0,      # start
            0,      # last_contact init
            10,     # last_contact = 10 (valid response)
            10,     # now = 10 (silence: 0, overall: 10, ok)
            25,     # now = 25 (RuntimeError, no last_contact reset; silence: 15, ok)
            620,    # now = 620 (RuntimeError; silence: 620-10=610 > 600)
        ])
        mock_time.side_effect = lambda: next(times)

        call_count = [0]
        def status_side_effect(ip):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"state": "PROVISIONING", "hostname": "", "os_version": "", "public_ip": ""}
            raise RuntimeError("CE not responding")

        mock_status.side_effect = status_side_effect

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "TIMEOUT"
        assert result["reason"] == "silence"
        assert "stopped responding" in result["error"]

    @patch("ce_client.time.sleep")
    @patch("ce_client.time.time")
    @patch("ce_client.get_ce_status")
    def test_poll_overall_timeout_even_with_responses(
        self, mock_status, mock_time, mock_sleep
    ):
        """CE keeps responding PROVISIONING past overall timeout."""
        # time.time() is called: start, last_contact init,
        # then per iteration: last_contact reset + now (2 calls)
        times = iter([
            0,      # start
            0,      # last_contact init
            100,    # last_contact = 100 (valid response)
            100,    # now = 100 (silence: 0, overall: 100, ok)
            800,    # last_contact = 800 (valid response)
            800,    # now = 800 (silence: 0, overall: 800, ok)
            1501,   # last_contact = 1501 (valid response)
            1501,   # now = 1501 (silence: 0, overall: 1501 > 1500)
        ])
        mock_time.side_effect = lambda: next(times)

        mock_status.return_value = {
            "state": "PROVISIONING", "hostname": "", "os_version": "", "public_ip": "",
        }

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "TIMEOUT"
        assert result["reason"] == "overall"
        assert "25 minutes" in result["error"]

    @patch("ce_client.time.sleep")
    @patch("ce_client.time.time")
    @patch("ce_client.get_ce_status")
    def test_poll_unreachable_does_not_reset_silence_timer(
        self, mock_status, mock_time, mock_sleep
    ):
        """RuntimeError from get_ce_status does not reset last_contact."""
        # RuntimeError path: no last_contact reset, just now (1 call per iteration)
        times = iter([
            0,      # start
            0,      # last_contact init
            10,     # now = 10 (RuntimeError; silence: 10-0=10, ok)
            601,    # now = 601 (RuntimeError; silence: 601-0=601 > 600)
        ])
        mock_time.side_effect = lambda: next(times)

        mock_status.side_effect = RuntimeError("CE not responding")

        result = ce_client.poll_ce_until_online("10.1.1.5")

        assert result["status"] == "TIMEOUT"
        assert result["reason"] == "silence"
        # last_contact was never updated from 0


# ---------------------------------------------------------------------------
# get_ce_config
# ---------------------------------------------------------------------------
class TestGetCeConfig:
    """Tests for get_ce_config."""

    @patch("ce_client.http_requests")
    def test_returns_config_with_token(self, mock_requests):
        """Returns parsed config dict including Token field."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"Token": "jwt-abc-123", "Cluster": "test"}
        mock_requests.get.return_value = mock_resp

        result = ce_client.get_ce_config("10.1.1.5")

        assert result["Token"] == "jwt-abc-123"
        mock_requests.get.assert_called_once()
        call_url = mock_requests.get.call_args[0][0]
        assert ce_client.CE_CONFIG_READ_PATH in call_url

    @patch("ce_client.http_requests")
    def test_raises_on_unreachable(self, mock_requests):
        """Raises RuntimeError when CE is unreachable."""
        mock_requests.get.side_effect = ConnectionError("refused")

        with pytest.raises(RuntimeError, match="Cannot read CE configuration"):
            ce_client.get_ce_config("10.1.1.5")
