"""Tests for mag/cli/oauth.py."""

import contextlib
import io
import json
import threading
import time
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

import pytest

from mag.lib import myob_client
from mag.cli import oauth


REDIRECT_URI = "https://mag.example.test/callback"


class TestBuildAuthUrl:
    def test_includes_required_params(self):
        url = oauth.build_auth_url("cid", "state123", REDIRECT_URI)
        query = parse_qs(urlparse(url).query)

        assert query["client_id"] == ["cid"]
        assert query["redirect_uri"] == [REDIRECT_URI]
        assert query["response_type"] == ["code"]
        assert query["state"] == ["state123"]
        assert query["prompt"] == ["consent"]
        assert query["scope"] == [oauth.SCOPE]


class TestExchangeCode:
    def test_posts_expected_grant_and_returns_json(self, mocker):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps({"access_token": "AT"}).encode()
        mock_urlopen = mocker.patch("mag.cli.oauth.urlopen", return_value=resp)

        result = oauth.exchange_code("cid", "csecret", "authcode", REDIRECT_URI)

        assert result == {"access_token": "AT"}
        sent = mock_urlopen.call_args[0][0]
        body = sent.data.decode()
        assert "grant_type=authorization_code" in body
        assert "code=authcode" in body
        assert f"redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}" in body

    def test_http_error_exits(self, mocker):
        mocker.patch("mag.cli.oauth.urlopen", side_effect=HTTPError("u", 400, "bad", {}, io.BytesIO(b"nope")))
        with pytest.raises(SystemExit):
            oauth.exchange_code("cid", "csecret", "authcode", REDIRECT_URI)


class TestMainEnvValidation:
    def test_missing_myob_creds_exits_before_binding_a_socket(self, monkeypatch, mocker):
        monkeypatch.delenv("MYOB_CLIENT_ID", raising=False)
        monkeypatch.delenv("MYOB_CLIENT_SECRET", raising=False)
        monkeypatch.setenv("MAG_DOMAIN", "mag.example.test")
        mock_server_cls = mocker.patch("mag.cli.oauth.HTTPServer")

        with pytest.raises(SystemExit):
            oauth.main()
        mock_server_cls.assert_not_called()

    def test_missing_mag_domain_exits_before_binding_a_socket(self, monkeypatch, mocker):
        monkeypatch.setenv("MYOB_CLIENT_ID", "cid")
        monkeypatch.setenv("MYOB_CLIENT_SECRET", "csecret")
        monkeypatch.delenv("MAG_DOMAIN", raising=False)
        mock_server_cls = mocker.patch("mag.cli.oauth.HTTPServer")

        with pytest.raises(SystemExit):
            oauth.main()
        mock_server_cls.assert_not_called()


STATE = "fixed-test-state"


@pytest.fixture
def oauth_server(tmp_path, monkeypatch):
    """Sets up mag/cli/oauth.py's real one-shot HTTPServer to run safely in
    tests: myob_client.TOKENS_FILE redirected to a scratch file (oauth.py
    saves via myob_client.save_myob_tokens(), so that's the module whose
    TOKENS_FILE actually governs where the write lands), a fixed known
    "state" so callback requests can be crafted without racing stdout, and
    MYOB_CLIENT_ID/SECRET set so main() doesn't refuse to start.

    oauth.py hardcodes LISTEN_PORT (8787) and never explicitly closes its
    server socket (it relies on process exit / GC), which is fine for a
    real one-shot invocation but unsafe to reuse across many in-process
    tests: a still-open socket from a previous test could collide with, or
    be mistaken for, the next test's server. So HTTPServer is patched here
    to always bind port 0 (an OS-assigned free port) and every instance it
    creates is captured and force-closed on teardown, regardless of
    whether the background thread finished cleanly.
    """
    tokens_path = tmp_path / "tokens.json"
    servers = []
    real_http_server = oauth.HTTPServer

    def capturing_http_server(address, handler_cls):
        host, _requested_port = address
        server = real_http_server((host, 0), handler_cls)
        servers.append(server)
        return server

    monkeypatch.setattr(myob_client, "TOKENS_FILE", str(tokens_path))
    monkeypatch.setattr(oauth.secrets, "token_urlsafe", lambda *a, **k: STATE)
    monkeypatch.setenv("MYOB_CLIENT_ID", "cid")
    monkeypatch.setenv("MYOB_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MAG_DOMAIN", "mag.example.test")
    monkeypatch.setattr(oauth, "HTTPServer", capturing_http_server)

    yield tokens_path, servers

    for server in servers:
        try:
            server.server_close()
        except OSError:
            pass


def _wait_for_server(servers, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if servers:
            return servers[0]
        time.sleep(0.005)
    raise TimeoutError("oauth.main() never constructed its HTTPServer")


def _get_with_retries(url, attempts=50, delay=0.02):
    last_error = None
    for _ in range(attempts):
        try:
            with urlopen(url, timeout=2) as resp:
                return resp.status, resp.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()
        except OSError as e:
            last_error = e
            time.sleep(delay)
    raise last_error


def run_main_and_hit(mocker, servers, path_and_query, exchange_return=None):
    """Runs oauth.main() in a background thread against its real,
    freshly-bound port, and fires one GET request at path_and_query.

    main() sys.exit()s in every rejection path (state mismatch, missing
    code, MYOB error param, or an unexpected path) as well as on success -
    that's expected, not a test failure, so it's caught here rather than
    left to surface as an unhandled-thread-exception warning. Returns
    (status, body, mock_exchange, system_exit_or_None).
    """
    caught = []

    def target():
        try:
            oauth.main()
        except SystemExit as e:
            caught.append(e)

    mock_exchange = mocker.patch.object(oauth, "exchange_code", return_value=exchange_return)
    thread = threading.Thread(target=target, daemon=True)
    with contextlib.redirect_stdout(io.StringIO()):
        thread.start()
        server = _wait_for_server(servers)
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}{path_and_query}"
        status, body = _get_with_retries(url)
        thread.join(timeout=5)
    assert not thread.is_alive(), "oauth.main() did not exit after handling one request"
    return status, body, mock_exchange, (caught[0] if caught else None)


def run_main_and_hit_callback(mocker, servers, query: dict, exchange_return=None):
    return run_main_and_hit(mocker, servers, f"/callback?{urlencode(query)}", exchange_return=exchange_return)


class TestOauthCallbackHappyPath:
    def test_saves_tokens_with_business_id_and_name_merged_in(self, oauth_server, mocker):
        tokens_path, servers = oauth_server
        status, body, mock_exchange, system_exit = run_main_and_hit_callback(
            mocker,
            servers,
            {"state": STATE, "code": "AUTHCODE", "businessId": "biz-1", "businessName": "Test Biz"},
            exchange_return={"access_token": "AT", "refresh_token": "RT"},
        )

        assert status == 200
        assert "Authorization complete" in body
        assert system_exit is None  # the happy path returns normally, no sys.exit()
        mock_exchange.assert_called_once_with("cid", "csecret", "AUTHCODE", "https://mag.example.test/callback")

        with open(tokens_path) as f:
            saved = json.load(f)
        assert saved["access_token"] == "AT"
        assert saved["businessId"] == "biz-1"
        assert saved["businessName"] == "Test Biz"

    def test_saved_tokens_file_is_owner_and_group_only(self, oauth_server, mocker):
        tokens_path, servers = oauth_server
        run_main_and_hit_callback(
            mocker, servers, {"state": STATE, "code": "AUTHCODE"}, exchange_return={"access_token": "AT"}
        )
        mode = tokens_path.stat().st_mode & 0o777
        assert mode == 0o660


class TestOauthCallbackErrorPaths:
    def test_state_mismatch_rejected_and_no_tokens_written(self, oauth_server, mocker):
        tokens_path, servers = oauth_server
        status, body, mock_exchange, system_exit = run_main_and_hit_callback(
            mocker, servers, {"state": "wrong-state", "code": "AUTHCODE"}
        )

        assert status == 400
        assert "State mismatch" in body
        assert "state_mismatch" in str(system_exit)
        mock_exchange.assert_not_called()
        assert not tokens_path.exists()

    def test_missing_code_rejected(self, oauth_server, mocker):
        _, servers = oauth_server
        status, body, mock_exchange, system_exit = run_main_and_hit_callback(mocker, servers, {"state": STATE})

        assert status == 400
        assert "No authorization code" in body
        assert "missing_code" in str(system_exit)
        mock_exchange.assert_not_called()

    def test_myob_error_param_rejected(self, oauth_server, mocker):
        tokens_path, servers = oauth_server
        status, body, mock_exchange, system_exit = run_main_and_hit_callback(
            mocker, servers, {"state": STATE, "error": "access_denied"}
        )

        assert status == 400
        assert "access_denied" in body
        assert "access_denied" in str(system_exit)
        mock_exchange.assert_not_called()
        assert not tokens_path.exists()

    def test_wrong_path_is_404_and_main_exits_cleanly_not_a_crash(self, oauth_server, mocker):
        # A stray request (browser prefetch, health check, favicon.ico...)
        # consumes the one-shot listener - main() must exit cleanly with a
        # clear message, not crash with a KeyError trying to save "tokens"
        # that were never obtained.
        tokens_path, servers = oauth_server
        status, _, mock_exchange, system_exit = run_main_and_hit(mocker, servers, "/wrong-path")

        mock_exchange.assert_not_called()
        assert status == 404
        assert system_exit is not None
        assert "wrong-path" in str(system_exit)
        assert not tokens_path.exists()
