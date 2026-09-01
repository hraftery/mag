"""Tests for mag/cli/__main__.py, the single dispatching entry point.

These test the dispatcher's own routing responsibility in isolation. Eg.
does 'oauth' route to the oauth module; does 'issue' route to tokens".
Mocks importlib.import_module, rather than exercising what each
routed-to command actually does (covered by test_cli_oauth.py and
test_cli_tokens.py).
"""

import sys
import pytest
from mag.cli import __main__ as mag


def run(mocker, capsys, *argv):
    # patch.object (not the string form) - the string form resolves "sys" via
    # importlib.import_module(), which several tests below patch to a Mock,
    # so it would silently set argv on a decoy object instead of real sys.
    mocker.patch.object(sys, "argv", ["mag", *argv])
    mag.main()
    return capsys.readouterr().out


class TestDispatch:
    def test_oauth_routes_to_oauth_module(self, mocker, capsys):
        mock_import_module = mocker.patch("mag.cli.__main__.importlib.import_module")
        mock_module = mock_import_module.return_value

        run(mocker, capsys, "oauth")

        mock_import_module.assert_called_once_with("mag.cli.oauth")
        mock_module.main.assert_called_once_with()

    @pytest.mark.parametrize("command", ["issue", "list", "edit", "revoke"])
    def test_token_subcommands_route_to_tokens_module(self, mocker, capsys, command):
        mock_import_module = mocker.patch("mag.cli.__main__.importlib.import_module")
        mock_module = mock_import_module.return_value

        run(mocker, capsys, command)

        mock_import_module.assert_called_once_with("mag.cli.tokens")
        mock_module.main.assert_called_once_with()

    def test_token_subcommand_args_left_intact_for_tokens_py_to_parse(self, mocker, capsys):
        # issue/list/edit/revoke are tokens.py's *own* subcommands, so the
        # dispatcher must not strip the command name before delegating -
        # tokens.py's own argparse needs to see it.
        mocker.patch("mag.cli.__main__.importlib.import_module")
        mocker.patch.object(sys, "argv", ["mag", "issue", "--name", "x"])

        mag.main()

        assert sys.argv == ["mag", "issue", "--name", "x"]


class TestNonRoutingBehavior:
    def test_no_args_prints_usage_and_exits_1(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys)
        assert excinfo.value.code == 1

    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_help_flag_prints_usage_and_returns_normally(self, mocker, capsys, flag):
        output = run(mocker, capsys, flag)
        assert "Usage:" in output
        assert "Commands:" in output

    def test_unknown_command_exits_with_message(self, mocker, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run(mocker, capsys, "bogus")
        assert "Unknown command 'bogus'" in str(excinfo.value)
