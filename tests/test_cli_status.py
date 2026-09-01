"""Tests for mag/cli/status.py."""

import subprocess

import pytest

from mag.cli import status
from mag.lib import token_store


@pytest.fixture
def tokens_files(tmp_path, monkeypatch):
    """Redirects every file status.py reads to scratch paths, so tests never
    touch real credentials/logs. Returns (myob_tokens_path, api_tokens_path,
    audit_log_path) - none created by default; tests create what they need."""
    myob_tokens_path = tmp_path / "tokens.json"
    api_tokens_path = tmp_path / "api_tokens.json"
    audit_log_path = tmp_path / "proxy_audit.log"
    monkeypatch.setattr(status, "MYOB_TOKENS_FILE", str(myob_tokens_path))
    monkeypatch.setattr(status, "AUDIT_LOG", str(audit_log_path))
    monkeypatch.setattr(token_store, "API_TOKENS_FILE", str(api_tokens_path))
    return myob_tokens_path, api_tokens_path, audit_log_path


class TestRun:
    def test_missing_command_returns_none(self):
        assert status._run(["definitely-not-a-real-command-xyz"]) is None

    def test_returns_combined_stdout_and_stderr(self, mocker):
        mocker.patch.object(
            status.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="out\n", stderr="err\n"),
        )
        assert status._run(["echo", "hi"]) == "out\nerr"


class TestMain:
    def test_systemctl_and_journalctl_unavailable_reported_not_crashed(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        output = capsys.readouterr().out
        assert "systemctl not found" in output
        assert "journalctl not found" in output

    def test_systemctl_and_journalctl_output_printed(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", side_effect=["service is active", "some log lines"])

        status.main()

        output = capsys.readouterr().out
        assert "service is active" in output
        assert "some log lines" in output

    def test_not_authorized_yet_hint_when_myob_tokens_missing(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        output = capsys.readouterr().out
        assert "Not authorized yet" in output
        assert "mag oauth" in output

    def test_authorized_when_myob_tokens_present(self, mocker, capsys, tokens_files):
        myob_tokens_path, _, _ = tokens_files
        myob_tokens_path.write_text("{}")
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        output = capsys.readouterr().out
        assert "tokens.json present" in output
        assert "Not authorized yet" not in output

    def test_token_counts_active_and_revoked(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", return_value=None)
        token_store.issue("a", ["Contact:GET"])
        _, revoked_record = token_store.issue("b", ["Contact:GET"])
        token_store.revoke(revoked_record["id"])

        status.main()

        assert "1 active, 1 revoked" in capsys.readouterr().out

    def test_no_tokens_issued_yet(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        assert "0 active, 0 revoked" in capsys.readouterr().out

    def test_no_requests_logged_yet_when_audit_log_absent(self, mocker, capsys, tokens_files):
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        assert "No requests logged yet." in capsys.readouterr().out

    def test_tails_last_n_lines_of_audit_log(self, mocker, capsys, tokens_files):
        _, _, audit_log_path = tokens_files
        lines = [f"line {i}\n" for i in range(status.AUDIT_LOG_TAIL + 5)]
        audit_log_path.write_text("".join(lines))
        mocker.patch.object(status, "_run", return_value=None)

        status.main()

        output = capsys.readouterr().out
        assert "line 0" not in output  # older lines dropped
        assert f"line {status.AUDIT_LOG_TAIL + 4}" in output  # last line kept
