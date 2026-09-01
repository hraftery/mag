"""Integration tests for mag/proxy/proxy.py.

Spins the real ThreadingHTTPServer on an OS-assigned port and drives it with
real HTTP requests, mocking only token_store.authorize / myob_client.raw_request
(and redirecting the audit log to a scratch file) so tests never touch real
MYOB credentials or the real proxy_audit.log.
"""

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mag.lib import myob_client, token_store
from mag.proxy import proxy


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "AUDIT_LOG", str(tmp_path / "proxy_audit.log"))

    server = proxy.ThreadingHTTPServer(("127.0.0.1", 0), proxy.ProxyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def do_request(base_url, method, path, headers=None, body=None):
    req = Request(f"{base_url}{path}", method=method, headers=headers or {}, data=body)
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read()


def audit_log_lines():
    # audit() runs *after* the response is already sent back to the client
    # (see _handle()), so a client that has just read its response can
    # briefly race the server thread's own log write. Poll rather than
    # reading once.
    deadline = time.monotonic() + 2
    lines = []
    while time.monotonic() < deadline:
        try:
            with open(proxy.AUDIT_LOG) as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = []
        if lines:
            return lines
        time.sleep(0.01)
    return lines


class TestAuthAndRouting:
    def test_missing_authorization_header_401(self, running_server):
        status, _, body = do_request(running_server, "GET", "/proxy/Sale/Invoice")
        assert status == 401
        assert json.loads(body) == {"error": "missing bearer token"}
        assert "- GET /Sale/Invoice -> 401" in audit_log_lines()[-1]

    def test_non_bearer_authorization_401(self, running_server):
        status, _, _ = do_request(
            running_server, "GET", "/proxy/Sale/Invoice", headers={"Authorization": "Basic xyz"}
        )
        assert status == 401

    def test_unmatched_path_prefix_404(self, running_server):
        status, _, body = do_request(running_server, "GET", "/not-proxy/x")
        assert status == 404
        assert json.loads(body) == {"error": "not found"}

    def test_invalid_or_unscoped_token_403(self, running_server, mocker):
        mocker.patch.object(token_store, "authorize", return_value=None)
        status, _, body = do_request(
            running_server, "GET", "/proxy/Sale/Invoice", headers={"Authorization": "Bearer bogus"}
        )
        assert status == 403
        assert json.loads(body) == {"error": "forbidden"}
        assert "invalid-or-unscoped GET /Sale/Invoice -> 403" in audit_log_lines()[-1]

    def test_authorize_called_with_method_and_myob_path(self, running_server, mocker):
        mock_authorize = mocker.patch.object(token_store, "authorize", return_value=None)
        do_request(running_server, "GET", "/proxy/Sale/Invoice/Item", headers={"Authorization": "Bearer t"})
        mock_authorize.assert_called_once_with("t", "GET", "/Sale/Invoice/Item")


@pytest.fixture
def authorized(running_server, mocker):
    mocker.patch.object(token_store, "authorize", return_value={"name": "laptop-explore"})
    return running_server


class TestForwarding:
    def test_success_relays_status_body_and_content_type_verbatim(self, authorized, mocker):
        mocker.patch.object(myob_client, "raw_request", return_value=(201, "application/json", b'{"UID":"abc"}'))

        status, headers, body = do_request(
            authorized, "POST", "/proxy/Sale/Invoice", headers={"Authorization": "Bearer t"}, body=b'{"x":1}'
        )

        assert status == 201
        assert headers["Content-Type"] == "application/json"
        assert body == b'{"UID":"abc"}'
        assert "laptop-explore POST /Sale/Invoice -> 201" in audit_log_lines()[-1]

    def test_body_and_content_type_forwarded_to_myob_client(self, authorized, mocker):
        mock_raw_request = mocker.patch.object(myob_client, "raw_request", return_value=(200, "application/json", b"{}"))

        do_request(
            authorized,
            "POST",
            "/proxy/Sale/Invoice",
            headers={"Authorization": "Bearer t", "Content-Type": "application/json"},
            body=b'{"a":1}',
        )

        _, kwargs = mock_raw_request.call_args
        assert mock_raw_request.call_args[0][:2] == ("POST", "/Sale/Invoice")
        assert kwargs["body"] == b'{"a":1}'
        assert kwargs["content_type"] == "application/json"

    def test_query_string_forwarded_as_params(self, authorized, mocker):
        mock_raw_request = mocker.patch.object(myob_client, "raw_request", return_value=(200, "application/json", b"[]"))

        do_request(
            authorized,
            "GET",
            "/proxy/Sale/Invoice?$top=5&$orderby=Date+desc",
            headers={"Authorization": "Bearer t"},
        )

        _, kwargs = mock_raw_request.call_args
        assert kwargs["params"] == {"$top": "5", "$orderby": "Date desc"}

    def test_no_query_string_gives_none_params(self, authorized, mocker):
        mock_raw_request = mocker.patch.object(myob_client, "raw_request", return_value=(200, "application/json", b"[]"))

        do_request(authorized, "GET", "/proxy/Sale/Invoice", headers={"Authorization": "Bearer t"})

        assert mock_raw_request.call_args.kwargs["params"] is None

    def test_upstream_unreachable_502(self, authorized, mocker):
        mocker.patch.object(myob_client, "raw_request", side_effect=myob_client.UpstreamUnreachable("timed out"))

        status, _, body = do_request(authorized, "GET", "/proxy/Sale/Invoice", headers={"Authorization": "Bearer t"})

        assert status == 502
        assert "MYOB unreachable" in json.loads(body)["error"]
        assert "laptop-explore GET /Sale/Invoice -> 502" in audit_log_lines()[-1]

    def test_myob_error_response_relayed_verbatim(self, authorized, mocker):
        # MYOB's own error bodies pass through unmodified - the proxy must
        # not reshape or swallow them.
        mocker.patch.object(
            myob_client, "raw_request", return_value=(422, "application/json", b'{"Errors":[{"Message":"bad"}]}')
        )

        status, _, body = do_request(
            authorized, "POST", "/proxy/Sale/Invoice", headers={"Authorization": "Bearer t"}, body=b"{}"
        )

        assert status == 422
        assert json.loads(body) == {"Errors": [{"Message": "bad"}]}

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_all_four_methods_dispatch(self, authorized, mocker, method):
        mock_raw_request = mocker.patch.object(myob_client, "raw_request", return_value=(200, "application/json", b"{}"))

        status, _, _ = do_request(authorized, method, "/proxy/Sale/Invoice", headers={"Authorization": "Bearer t"})

        assert status == 200
        assert mock_raw_request.call_args[0][0] == method
