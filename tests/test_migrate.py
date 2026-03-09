"""Tests for the migration logic."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from openclaw_to_hermes.migrate import (
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
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is True
        assert "SOUL.md" in result.items_migrated
        # Dry run should not create the file
        assert not (tmp_hermes / "SOUL.md").exists()

    def test_migrate_soul_writes_file(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is True
        content = (tmp_hermes / "SOUL.md").read_text()
        assert "You are a helpful assistant named TestBot." in content
        assert "Migrated from OpenClaw" in content

    def test_migrate_soul_missing(self, tmp_openclaw, tmp_hermes, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", tmp_openclaw)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_soul()
        assert result.success is False
        assert "not found" in result.warnings[0]


class TestMigrateMemory:
    def test_migrate_all_memory(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

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
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        result = migrator.migrate_memory()
        archive = tmp_hermes / "memories" / "openclaw_archive"
        assert archive.exists()
        assert (archive / "2026-03-01.md").exists()
        assert (archive / "2026-03-02.md").exists()
        assert "2 daily memory files" in result.items_migrated


class TestMigrateWorkspaceFiles:
    def test_copies_workspace_files(self, openclaw_with_files, tmp_hermes, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.OPENCLAW_DIR", openclaw_with_files)
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

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
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

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
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)

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
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        assert installer.is_hermes_dir_exists() is True

    def test_hermes_dir_not_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_path / "nonexistent")
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        assert installer.is_hermes_dir_exists() is False

    def test_ensure_returns_false_without_auto_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_path / "nonexistent")
        logger = MigrationLogger()
        installer = HermesInstaller(logger)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert installer.ensure_hermes_installed(auto_install=False) is False

    def test_has_official_install_url(self):
        assert "NousResearch" in HermesInstaller.HERMES_INSTALL_URL
        assert "install.sh" in HermesInstaller.HERMES_INSTALL_URL


class TestBackupHermes:
    def test_creates_backup(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_hermes)
        (tmp_hermes / "config.yaml").write_text("model:\n  default: gpt-4\n")
        (tmp_hermes / "SOUL.md").write_text("Test persona")

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        backup = migrator._backup_hermes()
        assert backup is not None
        assert backup.exists()
        assert (backup / "config.yaml").exists()
        assert (backup / "SOUL.md").exists()

    def test_no_backup_if_no_hermes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openclaw_to_hermes.migrate.HERMES_DIR", tmp_path / "nonexistent")
        migrator = OpenClawMigrator(dry_run=False)
        assert migrator._backup_hermes() is None
