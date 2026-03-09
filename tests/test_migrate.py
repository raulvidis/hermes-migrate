"""Tests for the migration logic."""

from unittest.mock import MagicMock, patch

from hermes_migrate.migrate import (
    HermesInstaller,
    MigrationLogger,
    MigrationResult,
    OpenClawMigrator,
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
        assert hermes_config["model"]["default"] == "claude-haiku-4-5"

    def test_fallback_to_defaults(self, single_agent_config):
        migrator = OpenClawMigrator(dry_run=True, agent_id="main")
        hermes_config = {}
        result = migrator.migrate_models(single_agent_config, hermes_config)
        assert result.success is True
        assert hermes_config["model"]["default"] == "claude-sonnet-4-20250514"

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
        migrator.migrate_agents(sample_openclaw_config)
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
        migrator.migrate_env_template(sample_openclaw_config)
        content = (tmp_hermes / ".env.openclaw").read_text()
        assert "FIRECRAWL_API_KEY" in content

    def test_env_includes_custom_providers(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_env_template(sample_openclaw_config)
        content = (tmp_hermes / ".env.openclaw").read_text()
        assert "custom-llm" in content

    def test_env_includes_allowed_users(self, sample_openclaw_config, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        migrator.migrate_env_template(sample_openclaw_config)
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
            migrator.migrate_credentials(sample_openclaw_config)
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
        # fmt: off
        with patch("subprocess.run", return_value=mock_version), \
             patch("subprocess.Popen") as mock_popen:
            result = migrator.start_hermes()
        # fmt: on
        assert result.success is True
        assert "Hermes started" in result.items_migrated
        mock_popen.assert_called_once()

    def test_hermes_start_exception(self):
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        mock_version = MagicMock()
        mock_version.returncode = 0
        # fmt: off
        with patch("subprocess.run", return_value=mock_version), \
             patch("subprocess.Popen", side_effect=OSError("permission denied")):
            result = migrator.start_hermes()
        # fmt: on
        assert result.success is True
        assert any("permission denied" in w.lower() for w in result.warnings)


class TestAutoStart:
    def test_auto_start_default(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        assert migrator.auto_start is True

    def test_auto_start_disabled(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id="test", auto_start=False)
        assert migrator.auto_start is False


class TestBasicYamlLoad:
    def test_loads_flat_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model: gpt-4\nenabled: true\ncount: 5\n")
        result = OpenClawMigrator._basic_yaml_load(config_file)
        assert result["model"] == "gpt-4"
        assert result["enabled"] is True
        assert result["count"] == 5

    def test_preserves_nested_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("model:\n  default: gpt-4\n  fallback: gpt-3.5\nenabled: true\n")
        result = OpenClawMigrator._basic_yaml_load(config_file)
        # Should preserve nested structure instead of returning {}
        assert "model" in result
        assert isinstance(result["model"], dict)
        assert result["model"]["default"] == "gpt-4"
        assert result["enabled"] is True

    def test_handles_comments_and_blank_lines(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("# Comment\n\nmodel: gpt-4\n# Another\nverbose: false\n")
        result = OpenClawMigrator._basic_yaml_load(config_file)
        assert result["model"] == "gpt-4"
        assert result["verbose"] is False

    def test_handles_quoted_values(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("name: 'my bot'\nmodel: \"gpt-4\"\n")
        result = OpenClawMigrator._basic_yaml_load(config_file)
        assert result["name"] == "my bot"
        assert result["model"] == "gpt-4"

    def test_empty_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        result = OpenClawMigrator._basic_yaml_load(config_file)
        assert result == {}


class TestRollback:
    def test_rollback_restores_config(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)

        # Create original config
        (tmp_hermes / "config.yaml").write_text("original: true\n")
        (tmp_hermes / "SOUL.md").write_text("Original persona")

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        migrator._backup_hermes()

        # Modify files (simulate migration)
        (tmp_hermes / "config.yaml").write_text("migrated: true\n")
        (tmp_hermes / "SOUL.md").write_text("Migrated persona")

        # Rollback
        migrator._rollback()

        assert (tmp_hermes / "config.yaml").read_text() == "original: true\n"
        assert (tmp_hermes / "SOUL.md").read_text() == "Original persona"

    def test_rollback_without_backup_warns(self, tmp_hermes, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_hermes)
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        # Should not crash
        migrator._rollback()


class TestIdempotency:
    def test_blocks_duplicate_migration(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("# --- OpenClaw Migration ---\nTOKEN=abc\n")

        migrator = OpenClawMigrator(dry_run=False, agent_id="test", force=False)
        assert migrator._check_previous_migration() is False

    def test_force_bypasses_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("# --- OpenClaw Migration ---\nTOKEN=abc\n")

        migrator = OpenClawMigrator(dry_run=False, agent_id="test", force=True)
        assert migrator._check_previous_migration() is True

    def test_no_previous_migration_proceeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path)
        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        assert migrator._check_previous_migration() is True


class TestUnknownChannel:
    def test_warns_on_unknown_channel(self):
        migrator = OpenClawMigrator(dry_run=True, agent_id=None)
        hermes_config = {}
        oc_config = {
            "channels": {"irc": {"enabled": True}},
            "bindings": [],
        }
        result = migrator.migrate_channels(oc_config, hermes_config)
        assert any("Unknown channel" in w and "irc" in w for w in result.warnings)


class TestSelectAgentEOFError:
    def test_eof_returns_none(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        with patch("builtins.input", side_effect=EOFError):
            result = migrator.select_agent(sample_openclaw_config)
        assert result is None


class TestRunMethod:
    def test_run_dry_run_succeeds(
        self, tmp_path, sample_openclaw_config, openclaw_with_files, monkeypatch
    ):
        import json

        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "memories").mkdir()
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", hermes_dir)

        # Write openclaw.json
        config_path = openclaw_with_files / "openclaw.json"
        config_path.write_text(json.dumps(sample_openclaw_config), encoding="utf-8")

        migrator = OpenClawMigrator(dry_run=True, agent_id="cleo")
        # Mock hermes installer
        with patch.object(HermesInstaller, "ensure_hermes_installed", return_value=True):
            result = migrator.run()
        assert result is True

    def test_run_fails_without_openclaw(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", tmp_path / "nonexistent")
        migrator = OpenClawMigrator(dry_run=True, agent_id="test")
        assert migrator.run() is False


class TestQuietMode:
    def test_quiet_suppresses_info(self, capsys):
        logger = MigrationLogger(quiet=True)
        logger.info("should not print")
        logger.success("should not print")
        logger.warn("should not print")
        captured = capsys.readouterr()
        assert "should not print" not in captured.out

    def test_quiet_shows_errors(self, capsys):
        logger = MigrationLogger(quiet=True)
        logger.error("error message")
        captured = capsys.readouterr()
        assert "error message" in captured.out

    def test_quiet_still_stores_messages(self):
        logger = MigrationLogger(quiet=True)
        logger.info("stored")
        assert len(logger.messages) == 1
        assert logger.messages[0] == ("INFO", "stored")


class TestRollbackOnException:
    def test_run_rolls_back_on_crash(
        self, tmp_path, openclaw_with_files, sample_openclaw_config, monkeypatch
    ):
        import json

        monkeypatch.setattr("hermes_migrate.migrate.OPENCLAW_DIR", openclaw_with_files)
        hermes_dir = tmp_path / ".hermes"
        hermes_dir.mkdir()
        (hermes_dir / "memories").mkdir()
        (hermes_dir / "config.yaml").write_text("original: true\n")
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", hermes_dir)
        config_path = openclaw_with_files / "openclaw.json"
        config_path.write_text(json.dumps(sample_openclaw_config), encoding="utf-8")

        migrator = OpenClawMigrator(dry_run=False, agent_id="cleo")
        # Make migrate_channels crash
        with (
            patch.object(HermesInstaller, "ensure_hermes_installed", return_value=True),
            patch.object(
                OpenClawMigrator,
                "migrate_channels",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = migrator.run()
        assert result is False
        # Original config should be restored
        assert "original" in (hermes_dir / "config.yaml").read_text()


class TestParseYamlValue:
    def test_true(self):
        assert OpenClawMigrator._parse_yaml_value("true") is True

    def test_false(self):
        assert OpenClawMigrator._parse_yaml_value("false") is False

    def test_null(self):
        assert OpenClawMigrator._parse_yaml_value("null") is None

    def test_integer(self):
        assert OpenClawMigrator._parse_yaml_value("42") == 42

    def test_float(self):
        assert OpenClawMigrator._parse_yaml_value("3.14") == 3.14

    def test_quoted_string(self):
        assert OpenClawMigrator._parse_yaml_value("'hello world'") == "hello world"

    def test_double_quoted(self):
        assert OpenClawMigrator._parse_yaml_value('"hello"') == "hello"

    def test_plain_string(self):
        assert OpenClawMigrator._parse_yaml_value("gpt-4") == "gpt-4"


class TestSelectAgentNonInteractive:
    def test_requires_agent_flag_without_tty(self, sample_openclaw_config):
        migrator = OpenClawMigrator(dry_run=True)
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = migrator.select_agent(sample_openclaw_config)
        assert result is None


class TestCleanupOldBackups:
    def test_removes_oldest_backups(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path)
        for i in range(5):
            (tmp_path / f"backup_2026030{i}_120000").mkdir()

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        migrator._cleanup_old_backups(keep=3)

        remaining = sorted(d.name for d in tmp_path.iterdir() if d.name.startswith("backup_"))
        assert len(remaining) == 3
        assert "backup_20260300_120000" not in remaining
        assert "backup_20260301_120000" not in remaining

    def test_keeps_all_if_under_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes_migrate.migrate.HERMES_DIR", tmp_path)
        (tmp_path / "backup_20260301_120000").mkdir()

        migrator = OpenClawMigrator(dry_run=False, agent_id="test")
        migrator._cleanup_old_backups(keep=3)

        remaining = [d.name for d in tmp_path.iterdir() if d.name.startswith("backup_")]
        assert len(remaining) == 1
