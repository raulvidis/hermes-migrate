"""Tests for the migration logic."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_migrate.migrate import (
    OpenClawMigrator,
    HermesInstaller,
    MigrationLogger,
    MigrationResult,
    OPENCLAW_DIR,
    HERMES_DIR,
)


class TestMigrationResult:
    def test_defaults(self):
        r = MigrationResult(success=True, message="test")
        assert r.success is True
        assert r.message == "test"
        assert r.items_migrated == []
        assert r.warnings == []
        assert r.errors == []


class TestGetAvailableAgents:
    def test_multi_agent_list(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        agents = migrator.get_available_agents(sample_openclaw_config)
        assert len(agents) == 3
        assert agents[0]["id"] == "nora"
        assert agents[1]["id"] == "cleo"
        assert agents[2]["id"] == "hank"

    def test_single_agent_defaults(self, single_agent_config):
        migrator = OpenClawMigrator(dry_run=True)
        agents = migrator.get_available_agents(single_agent_config)
        assert len(agents) == 1
        assert agents[0]["id"] == "main"
        assert agents[0]["model"] == "anthropic/claude-sonnet-4-20250514"

    def test_empty_config(self):
        migrator = OpenClawMigrator(dry_run=True)
        agents = migrator.get_available_agents({})
        assert agents == []


class TestGetAgentBindings:
    def test_agent_with_bindings(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        bindings = migrator.get_agent_bindings(sample_openclaw_config, "cleo")
        assert len(bindings) == 1
        assert bindings[0]["channel"] == "telegram"
        assert bindings[0]["account_id"] == "cleo"

    def test_agent_without_bindings(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        bindings = migrator.get_agent_bindings(sample_openclaw_config, "nonexistent")
        assert bindings == []


class TestGetAgentChannels:
    def test_agent_with_channels(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        channels = migrator.get_agent_channels(sample_openclaw_config, "cleo")
        assert "telegram" in channels
        assert channels["telegram"]["enabled"] is True
        assert channels["telegram"]["account"] == "cleo"

    def test_agent_no_channels(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        channels = migrator.get_agent_channels(sample_openclaw_config, "nonexistent")
        assert channels == {}


class TestMigrateSoul:
    def test_migrate_soul_dry_run(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is True
        assert "SOUL.md" in result.items_migrated
        # Dry run should not create the file
        assert not (tmp_hermes / "SOUL.md").exists()

    def test_migrate_soul_writes_file(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is True
        content = (tmp_hermes / "SOUL.md").read_text()
        assert "You are a helpful assistant named TestBot." in content
        assert "Migrated from OpenClaw" in content

    def test_migrate_soul_missing(self, tmp_openclaw, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", tmp_openclaw)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is False
        assert "not found" in result.warnings[0]


class TestMigrateMemory:
    def test_migrate_all_memory(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_memory()
        assert result.success is True
        assert "MEMORY.md" in result.items_migrated
        assert "USER.md" in result.items_migrated

        mem_dir = tmp_hermes / "memories"
        assert (mem_dir / "MEMORY.md").exists()
        assert (mem_dir / "USER.md").exists()
        assert "User prefers concise answers" in (mem_dir / "MEMORY.md").read_text()

    def test_migrate_daily_memories(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_memory()
        archive = tmp_hermes / "memories" / "openclaw_archive"
        assert archive.exists()
        assert (archive / "2026-03-01.md").exists()
        assert (archive / "2026-03-02.md").exists()
        assert "2 daily memory files" in result.items_migrated


class TestMigrateWorkspaceFiles:
    def test_copies_workspace_files(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_workspace_files()
        assert result.success is True
        assert "IDENTITY.md" in result.items_migrated
        assert "AGENTS.md" in result.items_migrated
        assert "TOOLS.md" in result.items_migrated

        mem_dir = tmp_hermes / "memories"
        assert (mem_dir / "identity.md").exists()
        assert (mem_dir / "agents_config.md").exists()
        assert (mem_dir / "tools_config.md").exists()


class TestMigrateChannels:
    def test_migrate_agent_channels(self, sample_openclaw_config, monkeypatch):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_channels(sample_openclaw_config, hermes_config)
        assert result.success is True
        assert "telegram" in result.items_migrated
        assert "platform_toolsets" in hermes_config
        assert hermes_config["platform_toolsets"]["telegram"] == ["hermes-telegram"]

    def test_no_channels_for_agent(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="nonexistent")
        hermes_config = {}
        result = migrator.migrate_channels(sample_openclaw_config, hermes_config)
        assert result.success is False

    def test_channel_mapping(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="nora")
        hermes_config = {}
        result = migrator.migrate_channels(sample_openclaw_config, hermes_config)
        assert "slack" in result.items_migrated
        assert hermes_config["platform_toolsets"]["slack"] == ["hermes-slack"]

    def test_warns_about_reauthentication(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_channels(sample_openclaw_config, hermes_config)
        assert any("re-authenticate" in w.lower() or "token" in w.lower() for w in result.warnings)


class TestMigrateModels:
    def test_migrate_agent_model(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_models(sample_openclaw_config, hermes_config)
        assert result.success is True
        assert hermes_config["model"]["default"] == "anthropic/claude-haiku-4-5"

    def test_fallback_to_defaults(self, single_agent_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="main")
        hermes_config = {}
        result = migrator.migrate_models(single_agent_config, hermes_config)
        assert result.success is True
        assert hermes_config["model"]["default"] == "anthropic/claude-sonnet-4-20250514"

    def test_custom_provider_warning(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_models(sample_openclaw_config, hermes_config)
        assert any("custom-llm" in w.lower() for w in result.warnings)


class TestMigrateAgents:
    def test_documents_multi_agent(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_agents(sample_openclaw_config)
        assert result.success is True

        doc_path = tmp_hermes / "memories" / "openclaw_agents.md"
        assert doc_path.exists()
        content = doc_path.read_text()
        assert "nora" in content
        assert "cleo" in content
        assert "hank" in content
        assert "SELECTED" in content

    def test_documents_acp(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_agents(sample_openclaw_config)
        content = (tmp_hermes / "memories" / "openclaw_agents.md").read_text()
        assert "ACP" in content
        assert "docker" in content

    def test_no_agents_no_doc(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.migrate_agents({})
        assert result.success is False


class TestHermesInstaller:
    def test_hermes_not_found(self):
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert installer.is_hermes_installed() is False

    def test_hermes_dir_exists(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        assert installer.is_hermes_dir_exists() is True

    def test_hermes_dir_not_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path / "nonexistent")
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        assert installer.is_hermes_dir_exists() is False

    def test_ensure_returns_false_without_auto_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path / "nonexistent")
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert installer.ensure_hermes_installed(auto_install=False) is False

    def test_has_official_install_url(self):
        assert "NousResearch" in HermesInstaller.HERMES_INSTALL_URL
        assert "install.sh" in HermesInstaller.HERMES_INSTALL_URL


class TestBackupHermes:
    def test_creates_backup(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)
        (tmp_hermes / "config.yaml").write_text("model:\n  default: gpt-4\n")
        (tmp_hermes / "SOUL.md").write_text("Test persona")

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        backup = migrator._backup_hermes()
        assert backup is not None
        assert backup.exists()
        assert (backup / "config.yaml").exists()
        assert (backup / "SOUL.md").exists()

    def test_no_backup_if_no_hermes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path / "nonexistent")
        migrator = OpenClawMigrator(dry_run=False)
        assert migrator._backup_hermes() is None


class TestMigrateHeartbeat:
    def test_migrate_heartbeat(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_heartbeat()
        assert result.success is True
        assert "HEARTBEAT.md" in result.items_migrated
        content = (tmp_hermes / "memories" / "HEARTBEAT.md").read_text()
        assert "Check server status" in content
        assert "Migrated from OpenClaw" in content

    def test_heartbeat_dry_run(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.migrate_heartbeat()
        assert result.success is True
        assert not (tmp_hermes / "memories" / "HEARTBEAT.md").exists()

    def test_heartbeat_missing(self, tmp_openclaw, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", tmp_openclaw)
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_heartbeat()
        assert result.success is False
        assert "not found" in result.warnings[0]


class TestMigrateEnvTemplate:
    def test_creates_env_openclaw(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_env_template(sample_openclaw_config)
        assert result.success is True
        env_path = tmp_hermes / ".env.openclaw"
        assert env_path.exists()
        content = env_path.read_text()
        # Should have telegram placeholders
        assert "TELEGRAM_BOT_TOKEN" in content
        # Should have slack placeholders
        assert "SLACK_BOT_TOKEN" in content
        # Should NOT have actual tokens
        assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in content
        assert "xoxb-fake-slack-token" not in content

    def test_env_dry_run(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        result = migrator.migrate_env_template(sample_openclaw_config)
        assert result.success is True
        assert not (tmp_hermes / ".env.openclaw").exists()

    def test_env_includes_web_search(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_env_template(sample_openclaw_config)
        content = (tmp_hermes / ".env.openclaw").read_text()
        assert "FIRECRAWL_API_KEY" in content

    def test_env_includes_custom_providers(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_env_template(sample_openclaw_config)
        content = (tmp_hermes / ".env.openclaw").read_text()
        assert "custom-llm" in content

    def test_env_includes_allowed_users(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_env_template(sample_openclaw_config)
        content = (tmp_hermes / ".env.openclaw").read_text()
        assert "TELEGRAM_ALLOWED_USERS" in content
        assert "5594479851" in content

    def test_empty_channels(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_env_template({"channels": {}, "tools": {}})
        assert result.success is False


class TestParseDuration:
    def test_hours(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("1h") == 60

    def test_minutes(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("30m") == 30

    def test_combined(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("2h30m") == 150

    def test_seconds(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("90s") == 1

    def test_bare_number(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("60") == 60

    def test_invalid(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes("abc") is None

    def test_none(self):
        migrator = OpenClawMigrator(dry_run=True)
        assert migrator._parse_duration_to_minutes(None) is None


class TestMigrateAdvancedConfig:
    def test_compaction_maps_to_compression(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        assert result.success is True
        assert hermes_config.get("compression", {}).get("enabled") is True

    def test_max_concurrent(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        assert "code_execution" in hermes_config
        assert hermes_config["code_execution"]["max_tool_calls"] == 40  # 4 * 10

    def test_subagent_delegation(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        assert "delegation" in hermes_config
        assert hermes_config["delegation"]["max_iterations"] == 48  # 8 * 6

    def test_session_retention(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        assert hermes_config.get("session_reset", {}).get("idle_minutes") == 60

    def test_setdefault_preserves_existing(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {"compression": {"enabled": False}}
        migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        # Should NOT override existing value
        assert hermes_config["compression"]["enabled"] is False

    def test_warns_about_unmapped_fields(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        hermes_config = {}
        result = migrator.migrate_advanced_config(sample_openclaw_config, hermes_config)
        warning_text = " ".join(result.warnings)
        assert "dmScope" in warning_text
        assert "heartbeat" in warning_text


class TestMigrateChannelDetails:
    def test_creates_channel_docs(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_channel_details(sample_openclaw_config)
        assert result.success is True
        doc = (tmp_hermes / "memories" / "openclaw_channels.md").read_text()
        assert "Telegram" in doc
        assert "Slack" in doc

    def test_redacts_secrets(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_channel_details(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_channels.md").read_text()
        assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in doc
        assert "xoxb-fake-slack-token" not in doc

    def test_marks_selected_agent(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_channel_details(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_channels.md").read_text()
        assert "SELECTED" in doc

    def test_dry_run(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        result = migrator.migrate_channel_details(sample_openclaw_config)
        assert result.success is True
        assert not (tmp_hermes / "memories" / "openclaw_channels.md").exists()

    def test_no_channels(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_channel_details({"channels": {}})
        assert result.success is False


class TestMigrateInfrastructure:
    def test_creates_infrastructure_docs(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        result = migrator.migrate_infrastructure(sample_openclaw_config)
        assert result.success is True
        doc = (tmp_hermes / "memories" / "openclaw_infrastructure.md").read_text()
        assert "Gateway" in doc
        assert "Hooks" in doc
        assert "Plugins" in doc
        assert "Commands" in doc
        assert "Tools" in doc

    def test_documents_custom_providers(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_infrastructure(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_infrastructure.md").read_text()
        assert "custom-llm" in doc
        assert "my-llm.example.com" in doc

    def test_redacts_gateway_secrets(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_infrastructure(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_infrastructure.md").read_text()
        assert "fake-gateway-token" not in doc

    def test_documents_hooks(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_infrastructure(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_infrastructure.md").read_text()
        assert "boot-md" in doc
        assert "session-memory" in doc

    def test_documents_cron(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_infrastructure(sample_openclaw_config)
        doc = (tmp_hermes / "memories" / "openclaw_infrastructure.md").read_text()
        assert "Session retention" in doc or "session" in doc.lower()

    def test_dry_run(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        result = migrator.migrate_infrastructure(sample_openclaw_config)
        assert result.success is True
        assert not (tmp_hermes / "memories" / "openclaw_infrastructure.md").exists()

    def test_empty_config(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.migrate_infrastructure({})
        assert result.success is False


class TestStopOpenClaw:
    def test_dry_run_skips_stop(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.stop_openclaw(sample_openclaw_config)
        assert result.success is True
        assert "dry run" in result.items_migrated[0].lower()

    def test_no_processes_found(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = migrator.stop_openclaw(sample_openclaw_config)
        assert result.success is True

    def test_pgrep_not_found(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = migrator.stop_openclaw(sample_openclaw_config)
        assert result.success is True
        assert any("port" in w.lower() for w in result.warnings)


class TestMigrateCredentials:
    def test_extracts_telegram_token_for_agent(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert result.success is True
        assert "Telegram bot token" in result.items_migrated
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" in env_content

    def test_extracts_correct_agent_telegram_token(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="hank")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "987654321:ZYXwvuTSRqpoNMLkjiHGFedcba" in env_content
        assert "123456789:" not in env_content

    def test_extracts_slack_token_from_accounts(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="nora")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert result.success is True
        assert "Slack bot token" in result.items_migrated
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "xoxb-fake-slack-token-12345" in env_content

    def test_extracts_telegram_allowed_users(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            migrator.migrate_credentials(sample_openclaw_config)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "TELEGRAM_ALLOWED_USERS=" in env_content
        assert "5594479851" in env_content

    def test_extracts_web_search_api_key(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert "Web search API key" in result.items_migrated
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "FIRECRAWL_API_KEY=fake-search-key" in env_content

    def test_dry_run_no_file_written(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert result.success is True
        assert not (tmp_path / ".env").exists()

    def test_no_credentials_returns_success(self, tmp_path):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        empty_config = {"channels": {}, "tools": {}, "models": {}}
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(empty_config)
        assert result.success is True
        assert not result.items_migrated

    def test_appends_to_existing_env(self, tmp_path, sample_openclaw_config):
        existing_env = tmp_path / ".env"
        existing_env.write_text("EXISTING_VAR=hello\n", encoding="utf-8")
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            migrator.migrate_credentials(sample_openclaw_config)
        env_content = existing_env.read_text(encoding="utf-8")
        assert "EXISTING_VAR=hello" in env_content
        assert "OpenClaw Migration" in env_content
        assert "TELEGRAM_BOT_TOKEN=" in env_content

    def test_extracts_custom_provider_base_url(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            migrator.migrate_credentials(sample_openclaw_config)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "CUSTOM_LLM_BASE_URL=https://my-llm.example.com/v1" in env_content

    def test_extracts_memory_search_api_key(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert any("GEMINI" in item for item in result.items_migrated)
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "GEMINI_API_KEY=fake-gemini-key" in env_content

    def test_extracts_gateway_auth_token(self, tmp_path, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        with patch("hermes_migrate.migrate.HERMES_DIR", tmp_path):
            result = migrator.migrate_credentials(sample_openclaw_config)
        assert "Gateway auth token" in result.items_migrated
        env_content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "GATEWAY_AUTH_TOKEN=fake-gateway-token" in env_content


class TestStartHermes:
    def test_dry_run(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.start_hermes()
        assert result.success is True
        assert "dry run" in result.items_migrated[0].lower()

    def test_hermes_not_in_path(self):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = migrator.start_hermes()
        assert result.success is True
        assert any("not found" in w.lower() for w in result.warnings)

    def test_hermes_version_fails(self):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("subprocess.run", return_value=mock_result):
            result = migrator.start_hermes()
        assert result.success is True
        assert any("not in path" in w.lower() for w in result.warnings)

    def test_hermes_starts_successfully(self):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        mock_version = MagicMock()
        mock_version.returncode = 0
        with patch("subprocess.run", return_value=mock_version), \
             patch("subprocess.Popen") as mock_popen:
            result = migrator.start_hermes()
        assert result.success is True
        assert "Hermes started" in result.items_migrated
        mock_popen.assert_called_once()

    def test_hermes_start_exception(self):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        mock_version = MagicMock()
        mock_version.returncode = 0
        with patch("subprocess.run", return_value=mock_version), \
             patch("subprocess.Popen", side_effect=OSError("permission denied")):
            result = migrator.start_hermes()
        assert result.success is True
        assert any("permission denied" in w.lower() for w in result.warnings)


class TestAutoStart:
    def test_auto_start_default(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        assert migrator.auto_start is True

    def test_auto_start_disabled(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test", auto_start=False)
        assert migrator.auto_start is False
