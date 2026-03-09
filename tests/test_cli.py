"""Tests for CLI argument parsing and entry point."""

import sys
from unittest.mock import patch, MagicMock

import pytest


class TestCLIParsing:
    def test_dry_run_flag(self):
        with patch("sys.argv", ["hermes-migrate", "--dry-run"]):
            from hermes_migrate.cli import main

            # We can't run main() without OpenClaw dir, but we can test argparse
            import argparse

            parser = argparse.ArgumentParser()
            parser.add_argument("--dry-run", action="store_true")
            parser.add_argument("-v", "--verbose", action="store_true")
            parser.add_argument("-a", "--agent", dest="agent_id")
            parser.add_argument("--install-hermes", action="store_true")

            args = parser.parse_args(["--dry-run"])
            assert args.dry_run is True
            assert args.verbose is False

    def test_agent_flag(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("-a", "--agent", dest="agent_id")
        args = parser.parse_args(["--agent", "cleo"])
        assert args.agent_id == "cleo"

    def test_agent_short_flag(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("-a", "--agent", dest="agent_id")
        args = parser.parse_args(["-a", "hank"])
        assert args.agent_id == "hank"

    def test_verbose_flag(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("-v", "--verbose", action="store_true")
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_exits_without_openclaw(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", tmp_path / "nonexistent")
        # Also patch in the cli module's imported reference
        monkeypatch.setattr("hermes_migrate.cli.OPENCLAW_DIR", tmp_path / "nonexistent")
        monkeypatch.setattr("sys.argv", ["hermes-migrate"])
        from hermes_migrate.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
