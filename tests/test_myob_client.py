"""Tests for app/myob_client.py."""

import base64
import email.message
import io
import json
import os
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pytest

import myob_client


def http_error(code, body: bytes = b"", content_type="application/json"):
    hdrs = email.message.Message()
    hdrs["Content-Type"] = content_type
    return HTTPError(url="https://api.myob.com/x", code=code, msg="err", hdrs=hdrs, fp=io.BytesIO(body))


def fake_response(body: bytes, content_type="application/json", status=200):
    """A context-manager-compatible stand-in for urlopen()'s return value."""
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = body
    resp.status = status
    resp.headers.get.return_value = content_type
    return resp


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setattr(myob_client, "TOKENS_FILE", str(path))
    return str(path)


@pytest.fixture
def myob_env(monkeypatch):
    monkeypatch.setenv("MYOB_CLIENT_ID", "cid")
    monkeypatch.setenv("MYOB_CLIENT_SECRET", "csecret")


def write_tokens(**overrides):
    tokens = {
        "access_token": "AT1",
        "refresh_token": "RT1",
        "businessId": "biz-1",
        "businessName": "Test Biz",
    }
    tokens.update(overrides)
    myob_client.save_myob_tokens(tokens)
    return tokens


class TestLoadSaveTokens:
    def test_load_missing_file_exits(self, tokens_file):
        with pytest.raises(SystemExit):
            myob_client.load_myob_tokens()

    def test_save_then_load_roundtrip(self, tokens_file):
        myob_client.save_myob_tokens({"access_token": "AT"})
        assert myob_client.load_myob_tokens() == {"access_token": "AT"}

    def test_save_sets_owner_and_group_permissions(self, tokens_file):
        myob_client.save_myob_tokens({"access_token": "AT"})
        mode = os.stat(tokens_file).st_mode & 0o777
        assert mode == 0o660


class TestRefreshTokens:
    def test_carries_over_business_id_and_name(self, mocker, tokens_file, myob_env):
        mock_urlopen = mocker.patch(
            "myob_client.urlopen",
            return_value=fake_response(json.dumps({"access_token": "AT2", "refresh_token": "RT2"}).encode()),
        )
        old = write_tokens()

        new = myob_client.refresh_myob_tokens(old, "cid", "csecret")

        assert new["access_token"] == "AT2"
        assert new["businessId"] == "biz-1"
        assert new["businessName"] == "Test Biz"
        # And it was persisted.
        assert myob_client.load_myob_tokens()["access_token"] == "AT2"
        mock_urlopen.assert_called_once()

    def test_sends_refresh_token_grant(self, mocker, tokens_file, myob_env):
        mock_urlopen = mocker.patch(
            "myob_client.urlopen", return_value=fake_response(json.dumps({"access_token": "AT2"}).encode())
        )
        write_tokens(refresh_token="RT-old")

        myob_client.refresh_myob_tokens({"refresh_token": "RT-old"}, "cid", "csecret")

        sent_request = mock_urlopen.call_args[0][0]
        sent_body = sent_request.data.decode()
        assert "grant_type=refresh_token" in sent_body
        assert "refresh_token=RT-old" in sent_body

    def test_http_error_exits(self, mocker, tokens_file, myob_env):
        mocker.patch("myob_client.urlopen", side_effect=http_error(401, b"bad"))
        with pytest.raises(SystemExit):
            myob_client.refresh_myob_tokens({"refresh_token": "x"}, "cid", "csecret")

    def test_url_error_exits(self, mocker, tokens_file, myob_env):
        mocker.patch("myob_client.urlopen", side_effect=URLError("no route"))
        with pytest.raises(SystemExit):
            myob_client.refresh_myob_tokens({"refresh_token": "x"}, "cid", "csecret")


class TestRequestHeaders:
    def test_no_cf_creds_by_default(self, monkeypatch):
        monkeypatch.delenv("MYOB_CF_USERNAME", raising=False)
        monkeypatch.delenv("MYOB_CF_PASSWORD", raising=False)
        headers = myob_client._request_headers("AT", "cid")
        assert "x-myobapi-cftoken" not in headers
        assert headers["Authorization"] == "Bearer AT"
        assert headers["x-myobapi-key"] == "cid"

    def test_cf_creds_base64_encoded_when_set(self, monkeypatch):
        monkeypatch.setenv("MYOB_CF_USERNAME", "u")
        monkeypatch.setenv("MYOB_CF_PASSWORD", "p")
        headers = myob_client._request_headers("AT", "cid")
        assert base64.b64decode(headers["x-myobapi-cftoken"]) == b"u:p"


class TestApiGet:
    def test_missing_env_vars_exits(self, tokens_file, monkeypatch):
        monkeypatch.delenv("MYOB_CLIENT_ID", raising=False)
        monkeypatch.delenv("MYOB_CLIENT_SECRET", raising=False)
        with pytest.raises(SystemExit):
            myob_client.api_get("/Sale/Invoice")

    def test_missing_business_id_exits(self, tokens_file, myob_env):
        write_tokens(businessId=None)
        with pytest.raises(SystemExit):
            myob_client.api_get("/Sale/Invoice")

    def test_success_returns_parsed_json(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch("myob_client.urlopen", return_value=fake_response(json.dumps({"Items": [1, 2]}).encode()))

        result = myob_client.api_get("/Sale/Invoice")

        assert result == {"Items": [1, 2]}

    def test_401_triggers_refresh_then_retry(self, mocker, tokens_file, myob_env):
        write_tokens()
        mock_urlopen = mocker.patch(
            "myob_client.urlopen",
            side_effect=[
                http_error(401, b"expired"),
                fake_response(json.dumps({"access_token": "AT2"}).encode()),  # refresh_tokens' urlopen call
                fake_response(json.dumps({"Items": []}).encode()),  # retried api call
            ],
        )

        result = myob_client.api_get("/Sale/Invoice")

        assert result == {"Items": []}
        assert mock_urlopen.call_count == 3
        assert myob_client.load_myob_tokens()["access_token"] == "AT2"

    def test_non_401_http_error_exits(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch("myob_client.urlopen", side_effect=http_error(500, b"boom"))
        with pytest.raises(SystemExit):
            myob_client.api_get("/Sale/Invoice")

    def test_url_error_exits(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch("myob_client.urlopen", side_effect=URLError("dns fail"))
        with pytest.raises(SystemExit):
            myob_client.api_get("/Sale/Invoice")


class TestRawRequest:
    def test_success_relays_status_and_body_unmodified(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch(
            "myob_client.urlopen",
            return_value=fake_response(b'{"ok":true}', content_type="application/json", status=201),
        )

        status, ctype, data = myob_client.raw_request("POST", "/Sale/Invoice")

        assert status == 201
        assert ctype == "application/json"
        assert data == b'{"ok":true}'

    def test_myob_http_error_is_returned_not_raised(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch("myob_client.urlopen", side_effect=http_error(422, b'{"error":"bad"}'))

        status, ctype, data = myob_client.raw_request("POST", "/Sale/Invoice")

        assert status == 422
        assert data == b'{"error":"bad"}'

    def test_401_refreshes_once_then_retries(self, mocker, tokens_file, myob_env):
        write_tokens()
        mocker.patch(
            "myob_client.urlopen",
            side_effect=[
                http_error(401, b""),
                fake_response(json.dumps({"access_token": "AT2"}).encode()),  # refresh
                fake_response(b'{"ok":true}', status=200),  # retry
            ],
        )

        status, _, data = myob_client.raw_request("GET", "/Sale/Invoice")

        assert status == 200
        assert data == b'{"ok":true}'

    def test_unreachable_upstream_raises(self, mocker, tokens_file, myob_env):
        mocker.patch("myob_client.urlopen", side_effect=URLError("unreachable"))
        write_tokens()
        with pytest.raises(myob_client.UpstreamUnreachable):
            myob_client.raw_request("GET", "/Sale/Invoice")

    def test_body_and_content_type_forwarded(self, mocker, tokens_file, myob_env):
        write_tokens()
        mock_urlopen = mocker.patch("myob_client.urlopen", return_value=fake_response(b"{}", status=201))

        myob_client.raw_request("POST", "/Sale/Invoice", body=b'{"x":1}', content_type="application/json")

        sent_request = mock_urlopen.call_args[0][0]
        assert sent_request.data == b'{"x":1}'
        assert sent_request.get_header("Content-type") == "application/json"


class TestApiGetAll:
    def test_pages_until_short_page(self, mocker):
        # api_get_all mutates and reuses the same params dict across calls, so
        # capture a *copy* of params on each call rather than relying on
        # call_args_list (which would show every call with the final,
        # already-mutated dict).
        pages = [{"Items": list(range(2))}, {"Items": list(range(1))}]  # 2nd page shorter -> stop
        seen_params = []

        def fake_api_get(path, params=None):
            seen_params.append(dict(params))
            return pages.pop(0)

        mocker.patch("myob_client.api_get", side_effect=fake_api_get)

        result = myob_client.api_get_all("/Sale/Invoice", page_size=2)

        assert result == [0, 1, 0]
        assert len(seen_params) == 2
        assert (seen_params[0]["$top"], seen_params[0]["$skip"]) == (2, 0)
        assert (seen_params[1]["$top"], seen_params[1]["$skip"]) == (2, 2)

    def test_single_short_page_no_extra_call(self, mocker):
        mock_api_get = mocker.patch("myob_client.api_get", return_value={"Items": [1]})

        result = myob_client.api_get_all("/Sale/Invoice", page_size=1000)

        assert result == [1]
        assert mock_api_get.call_count == 1

    def test_handles_bare_list_response(self, mocker):
        mocker.patch("myob_client.api_get", return_value=[1, 2, 3])

        result = myob_client.api_get_all("/Sale/Invoice", page_size=10)

        assert result == [1, 2, 3]
