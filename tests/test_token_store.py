"""Tests for mag/lib/token_store.py."""

import os
import pytest
from mag.lib import token_store


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    """Redirects token_store.MAG_TOKENS_FILE to a scratch file for the test,
    so tests never touch the real mag_tokens.json."""
    path = tmp_path / "mag_tokens.json"
    monkeypatch.setattr(token_store, "MAG_TOKENS_FILE", str(path))
    return str(path)


class TestParseScope:
    def test_basic(self):
        assert token_store.parse_scope("Sale/Invoice:GET,POST") == {
            "prefix": "Sale/Invoice",
            "methods": ["GET", "POST"],
        }

    def test_strips_leading_trailing_slashes_from_prefix(self):
        assert token_store.parse_scope("/Sale/Invoice/:GET")["prefix"] == "Sale/Invoice"

    def test_empty_prefix_allowed(self):
        assert token_store.parse_scope(":GET")["prefix"] == ""

    def test_methods_uppercased_and_trimmed(self):
        assert token_store.parse_scope("Contact: get , post ")["methods"] == ["GET", "POST"]

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError):
            token_store.parse_scope("Sale/Invoice")

    def test_no_methods_raises(self):
        with pytest.raises(ValueError):
            token_store.parse_scope("Sale/Invoice:")


@pytest.mark.usefixtures("tokens_file")
class TestIssueFindRevokeEditRoundtrip:
    def test_issue_returns_raw_token_and_record(self):
        raw_token, record = token_store.issue("laptop-explore", ["Sale/Invoice:GET"])

        assert raw_token.startswith(token_store.TOKEN_PREFIX)
        assert record["name"] == "laptop-explore"
        assert record["scopes"] == [{"prefix": "Sale/Invoice", "methods": ["GET"]}]
        assert not record["revoked"]
        assert record["last_used_at"] is None
        # Only the hash is persisted - the raw token itself never is.
        assert raw_token not in str(token_store.load_records())
        assert record["token_hash"] == token_store._hash(raw_token)

    def test_issue_persists_across_loads(self):
        _, record = token_store.issue("a", ["Contact:GET"])
        records = token_store.load_records()
        assert len(records) == 1
        assert records[0]["id"] == record["id"]

    def test_load_records_empty_when_file_absent(self):
        assert token_store.load_records() == []

    def test_find_by_id_hit_and_miss(self):
        _, record = token_store.issue("a", ["Contact:GET"])
        assert token_store.find_by_id(record["id"])["name"] == "a"
        assert token_store.find_by_id("nonexistent") is None

    def test_revoke_flips_flag(self):
        _, record = token_store.issue("a", ["Contact:GET"])
        assert token_store.revoke(record["id"])
        assert token_store.find_by_id(record["id"])["revoked"]

    def test_revoke_unknown_id_returns_false(self):
        assert not token_store.revoke("nonexistent")

    def test_add_scope_appends(self):
        _, record = token_store.issue("a", ["Contact:GET"])
        assert token_store.add_scope(record["id"], "Sale/Invoice:POST")
        scopes = token_store.find_by_id(record["id"])["scopes"]
        assert len(scopes) == 2
        assert {"prefix": "Sale/Invoice", "methods": ["POST"]} in scopes

    def test_add_scope_unknown_id_returns_false(self):
        assert not token_store.add_scope("nonexistent", "Contact:GET")

    def test_add_scope_invalid_spec_raises(self):
        _, record = token_store.issue("a", ["Contact:GET"])
        with pytest.raises(ValueError):
            token_store.add_scope(record["id"], "no-colon-here")

    def test_saved_file_permissions_owner_and_group(self):
        token_store.issue("a", ["Contact:GET"])
        mode = os.stat(token_store.MAG_TOKENS_FILE).st_mode & 0o777
        assert mode == 0o660


class TestPathMatches:
    def test_exact_match(self):
        assert token_store._path_matches("Sale/Invoice", "Sale/Invoice")

    def test_sub_path_matches(self):
        assert token_store._path_matches("Sale/Invoice/Item", "Sale/Invoice")

    def test_sibling_with_shared_string_prefix_does_not_match(self):
        # The documented gotcha: naive startswith() would wrongly match this.
        assert not token_store._path_matches("Sale/InvoiceTemplate", "Sale/Invoice")

    def test_empty_prefix_matches_everything(self):
        assert token_store._path_matches("anything/at/all", "")
        assert token_store._path_matches("anything/at/all", "/")

    def test_leading_slashes_ignored(self):
        assert token_store._path_matches("/Sale/Invoice", "Sale/Invoice")


@pytest.mark.usefixtures("tokens_file")
class TestAuthorize:
    @pytest.fixture
    def issued(self):
        return token_store.issue("laptop-explore", ["Sale/Invoice:GET,POST", "Contact:GET"])

    def test_valid_token_matching_scope_authorizes(self, issued):
        raw_token, record = issued
        result = token_store.authorize(raw_token, "GET", "Sale/Invoice")
        assert result is not None
        assert result["id"] == record["id"]

    def test_method_check_is_case_insensitive(self, issued):
        raw_token, _ = issued
        assert token_store.authorize(raw_token, "get", "Sale/Invoice") is not None

    def test_records_last_used_at(self, issued):
        raw_token, record = issued
        assert token_store.find_by_id(record["id"])["last_used_at"] is None
        token_store.authorize(raw_token, "GET", "Sale/Invoice")
        assert token_store.find_by_id(record["id"])["last_used_at"] is not None

    def test_unknown_token_rejected(self, issued):
        assert token_store.authorize("mbt_bogus", "GET", "Sale/Invoice") is None

    def test_wrong_method_rejected(self, issued):
        raw_token, _ = issued
        assert token_store.authorize(raw_token, "DELETE", "Sale/Invoice") is None

    def test_wrong_path_rejected(self, issued):
        raw_token, _ = issued
        assert token_store.authorize(raw_token, "GET", "Banking/SpendMoneyTxn") is None

    def test_revoked_token_rejected_even_with_matching_scope(self, issued):
        raw_token, record = issued
        token_store.revoke(record["id"])
        assert token_store.authorize(raw_token, "GET", "Sale/Invoice") is None
