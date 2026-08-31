"""Tests for cli/tokens.py (the issue/list/edit/revoke subcommands)."""

import sys

import pytest

import token_store
import tokens


def run(mocker, capsys, *argv):
    """Call tokens.main() with argv, capturing stdout. Raises SystemExit if
    the command does (mirrors real CLI use)."""
    mocker.patch.object(sys, "argv", ["tokens.py", *argv])
    tokens.main()
    return capsys.readouterr().out


@pytest.fixture
def tokens_file(tmp_path, monkeypatch):
    path = tmp_path / "api_tokens.json"
    monkeypatch.setattr(token_store, "TOKENS_FILE", str(path))
    return str(path)


@pytest.mark.usefixtures("tokens_file")
class TestIssue:
    def test_issues_token_and_prints_it_once(self, mocker, capsys):
        output = run(mocker, capsys, "issue", "--name", "laptop-explore", "--scope", "Sale/Invoice:GET,POST")

        assert "Issued token" in output
        assert "laptop-explore" in output
        assert "Sale/Invoice [GET, POST]" in output
        assert "mbt_" in output

        records = token_store.load_records()
        assert len(records) == 1
        assert records[0]["name"] == "laptop-explore"

    def test_repeatable_scope_flag(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "x", "--scope", "Sale/Invoice:GET", "--scope", "Contact:GET")
        assert len(token_store.load_records()[0]["scopes"]) == 2

    def test_missing_required_name_is_argparse_error(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys, "issue", "--scope", "Sale/Invoice:GET")
        assert excinfo.value.code == 2

    def test_invalid_scope_spec_exits_cleanly(self, mocker, capsys):
        with pytest.raises(SystemExit):
            run(mocker, capsys, "issue", "--name", "x", "--scope", "no-colon")


@pytest.mark.usefixtures("tokens_file")
class TestList:
    def test_no_tokens_yet(self, mocker, capsys):
        assert "No tokens issued yet." in run(mocker, capsys, "list")

    def test_lists_issued_tokens_with_status_and_scopes(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "laptop-explore", "--scope", "Sale/Invoice:GET")
        output = run(mocker, capsys, "list")

        assert "laptop-explore" in output
        assert "[active]" in output
        assert "Sale/Invoice:GET" in output
        assert "never" in output  # last used


@pytest.mark.usefixtures("tokens_file")
class TestEdit:
    def test_adds_scope_without_touching_others(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "x", "--scope", "Sale/Invoice:GET")
        token_id = token_store.load_records()[0]["id"]

        output = run(mocker, capsys, "edit", token_id, "--add-scope", "Contact:GET")

        assert f"Added scope Contact:GET to {token_id}" in output
        scopes = token_store.find_by_id(token_id)["scopes"]
        assert len(scopes) == 2

    def test_unknown_id_exits(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys, "edit", "deadbeef", "--add-scope", "Contact:GET")
        assert "No token with id deadbeef" in str(excinfo.value)

    def test_revoked_token_refuses_edit(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "x", "--scope", "Sale/Invoice:GET")
        token_id = token_store.load_records()[0]["id"]
        run(mocker, capsys, "revoke", token_id)

        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys, "edit", token_id, "--add-scope", "Contact:GET")
        assert "is revoked" in str(excinfo.value)


@pytest.mark.usefixtures("tokens_file")
class TestRevoke:
    def test_revokes_active_token(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "x", "--scope", "Sale/Invoice:GET")
        token_id = token_store.load_records()[0]["id"]

        output = run(mocker, capsys, "revoke", token_id)

        assert f"Revoked {token_id}" in output
        assert token_store.find_by_id(token_id)["revoked"]

    def test_revoking_already_revoked_is_a_no_op_message_not_an_error(self, mocker, capsys):
        run(mocker, capsys, "issue", "--name", "x", "--scope", "Sale/Invoice:GET")
        token_id = token_store.load_records()[0]["id"]
        run(mocker, capsys, "revoke", token_id)

        output = run(mocker, capsys, "revoke", token_id)

        assert "already revoked" in output

    def test_unknown_id_exits(self, mocker, capsys):
        with pytest.raises(SystemExit):
            run(mocker, capsys, "revoke", "deadbeef")


@pytest.mark.usefixtures("tokens_file")
class TestArgparseWiring:
    def test_no_subcommand_is_an_error(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys)
        assert excinfo.value.code == 2

    def test_unknown_subcommand_is_an_error(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys, "bogus")
        assert excinfo.value.code == 2
