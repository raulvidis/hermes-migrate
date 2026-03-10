"""
Migration logic for OpenClaw to Hermes conversion.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Paths
OPENCLAW_DIR = Path.home() / ".openclaw"
HERMES_DIR = Path.home() / ".hermes"

# Sensitive field patterns to NEVER copy
SENSITIVE_PATTERNS = [
    r"token",
    r"api[_-]?key",
    r"secret",
    r"password",
    r"credential",
    r"auth",
    r"bot[_-]?token",
    r"access[_-]?token",
    r"private[_-]?key",
]

# Fields that match sensitive patterns but are NOT secrets
SAFE_FIELD_ALLOWLIST = {
    "maxtokens",
    "max_tokens",
    "contexttokens",
    "context_tokens",
    "totaltokens",
    "total_tokens",
    "contextwindow",
    "context_window",
    "tokencount",
    "token_count",
    "token_usage",
    "tokenusage",
    "tokensused",
    "tokens_used",
}

# Sensitive values to redact in logs
REDACT_VALUE = "***REDACTED***"

# Seconds to wait after SIGTERM before SIGKILL
GRACEFUL_SHUTDOWN_WAIT = 2

# Providers known to be supported by Hermes
HERMES_SUPPORTED_PROVIDERS = {
    "openai",
    "openrouter",
    "nous",
    "nousportal",
    "nous_portal",
    "z.ai",
    "zai",
    "glm",
    "kimi",
    "moonshot",
    "minimax",
}

# Model name prefixes that indicate an unsupported provider
UNSUPPORTED_MODEL_PREFIXES = {
    "claude": "Anthropic",
    "anthropic": "Anthropic",
    "gemini": "Google",
    "palm": "Google",
    "command": "Cohere",
    "mistral": "Mistral (use via OpenRouter)",
    "llama": "Meta (use via OpenRouter)",
}


@dataclass
class MigrationResult:
    """Result of a migration operation."""

    success: bool
    message: str
    items_migrated: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class MigrationLogger:
    """Logger with sensitive data redaction."""

    def __init__(self, verbose: bool = False, quiet: bool = False):
        self.verbose = verbose
        self.quiet = quiet
        self.messages: List[Tuple[str, str]] = []  # (level, message)

    def _redact(self, msg: str) -> str:
        """Redact sensitive values from message."""
        patterns = [
            (r'(botToken["\s:=]+)["\']?[\w\-:]+', r"\1" + REDACT_VALUE),
            (r'(apiKey["\s:=]+)["\']?[\w\-]+', r"\1" + REDACT_VALUE),
            (r'(token["\s:=]+)["\']?[\w\-]+', r"\1" + REDACT_VALUE),
            (r"(\d{10,}:[\w\-]+)", REDACT_VALUE),  # Telegram bot tokens
            (r"(AIza[\w\-]+)", REDACT_VALUE),  # Google API keys
            (r"(sk-[\w\-]+)", REDACT_VALUE),  # OpenAI keys
            (r"(xox[baprs]-[\w\-]+)", REDACT_VALUE),  # Slack tokens (all types)
            (r"(AKIA[\w]{16,})", REDACT_VALUE),  # AWS access keys
            (r"(ghp_[\w]+)", REDACT_VALUE),  # GitHub personal tokens
            (r"(gho_[\w]+)", REDACT_VALUE),  # GitHub OAuth tokens
            (r"(sk-ant-[\w\-]+)", REDACT_VALUE),  # Anthropic API keys
            (r"(sk_live_[\w]+)", REDACT_VALUE),  # Stripe secret keys
            (r"(rk_live_[\w]+)", REDACT_VALUE),  # Stripe restricted keys
            (r"(AC[a-fA-F0-9]{32})", REDACT_VALUE),  # Twilio account SIDs
            (r"(SK[a-fA-F0-9]{32})", REDACT_VALUE),  # Twilio API keys
            (r"(Bearer\s+[\w\-.]+)", REDACT_VALUE),  # Bearer tokens in headers
        ]
        for pattern, replacement in patterns:
            msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
        return msg

    def info(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("INFO", msg))
        if not self.quiet:
            print(f"  {msg}")

    def success(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("SUCCESS", msg))
        if not self.quiet:
            print(f"  {msg}")

    def warn(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("WARN", msg))
        if not self.quiet:
            print(f"  {msg}")

    def error(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("ERROR", msg))
        print(f"  {msg}")  # Errors always print

    def debug(self, msg: str):
        if self.verbose:
            msg = self._redact(msg)
            self.messages.append(("DEBUG", msg))
            print(f"  [DEBUG] {msg}")


def is_sensitive_field(field_name: str) -> bool:
    """Check if a field name contains sensitive patterns."""
    field_lower = field_name.lower()
    if field_lower in SAFE_FIELD_ALLOWLIST:
        return False
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, field_lower):
            return True
    return False


def redact_sensitive_fields(data: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    """Recursively redact sensitive fields from a dict."""
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        full_path = f"{path}.{key}" if path else key

        if is_sensitive_field(key):
            result[key] = REDACT_VALUE
        elif isinstance(value, dict):
            result[key] = redact_sensitive_fields(value, full_path)
        elif isinstance(value, list):
            result[key] = [
                (
                    redact_sensitive_fields(item, f"{full_path}[{i}]")
                    if isinstance(item, dict)
                    else item
                )
                for i, item in enumerate(value)
            ]
        else:
            result[key] = value

    return result


class HermesInstaller:
    """Handles Hermes installation and detection."""

    HERMES_INSTALL_URL = os.environ.get(
        "HERMES_INSTALL_URL",
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh",
    )

    def __init__(self, logger: MigrationLogger):
        self.logger = logger

    def is_hermes_installed(self) -> bool:
        """Check if Hermes CLI is available."""
        try:
            result = subprocess.run(
                ["hermes", "--version"], capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def is_hermes_dir_exists(self) -> bool:
        """Check if Hermes directory exists."""
        return HERMES_DIR.exists()

    def install_hermes(self, interactive: bool = False) -> bool:
        """Install Hermes using the official installer.

        Args:
            interactive: If True, let user handle all prompts.
                         If False, auto-skip prompts with defaults.
        """
        if interactive:
            self.logger.info("Installing Hermes (interactive setup)...\n")
            cmd = f"curl -fsSL {self.HERMES_INSTALL_URL} | bash"
        else:
            self.logger.info("Installing Hermes (skipping setup wizard)...")
            # The official installer supports --skip-setup to bypass the TUI wizard.
            # Download script first so we can pass the flag to bash.
            cmd = (
                f"tmpf=$(mktemp) && curl -fsSL {self.HERMES_INSTALL_URL} -o $tmpf"
                f" && bash $tmpf --skip-setup; rm -f $tmpf"
            )

        try:
            returncode = subprocess.call(cmd, shell=True, timeout=600)

            if returncode == 0:
                self.logger.success("Hermes installed successfully!")
                return True
            else:
                self.logger.error(f"Installation failed (exit code {returncode})")
                return False

        except subprocess.TimeoutExpired:
            self.logger.error("Installation timed out")
            return False
        except Exception as e:
            self.logger.error(f"Installation error: {e}")
            return False

    def ensure_hermes_installed(self, auto_install: bool = False) -> bool:
        """Ensure Hermes is installed, installing if necessary."""
        if self.is_hermes_installed():
            self.logger.success("Hermes is installed")
            return True

        if self.is_hermes_dir_exists():
            self.logger.info("Hermes directory exists but CLI not in PATH")
            self.logger.info("Run 'source ~/.bashrc' or restart your shell")
            # Still proceed - migration can work without CLI
            return True

        if auto_install:
            return self.install_hermes()

        return False


class OpenClawMigrator:
    """Main migration class."""

    def __init__(
        self,
        dry_run: bool = False,
        verbose: bool = False,
        agent_id: Optional[str] = None,
        auto_start: bool = True,
        force: bool = False,
    ):
        self.dry_run = dry_run
        self.verbose = verbose
        self.agent_id = agent_id
        self.auto_start = auto_start
        self.force = force
        self.logger = MigrationLogger(verbose)
        self.results: List[MigrationResult] = []
        self.backup_dir: Optional[Path] = None
        self.selected_agent_config: Dict[str, Any] = {}

    def _ensure_hermes_dir(self):
        """Ensure Hermes directory exists."""
        if not self.dry_run:
            HERMES_DIR.mkdir(parents=True, exist_ok=True)
            (HERMES_DIR / "memories").mkdir(parents=True, exist_ok=True)

    def _backup_hermes(self) -> Optional[Path]:
        """Create backup of current Hermes config."""
        if not HERMES_DIR.exists():
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = HERMES_DIR / f"backup_{timestamp}"

        if self.dry_run:
            self.logger.debug(f"Would create backup at {backup_dir}")
            return backup_dir

        backup_dir.mkdir(parents=True, exist_ok=True)

        for f in ["config.yaml", "SOUL.md", ".env"]:
            src = HERMES_DIR / f
            if src.exists():
                shutil.copy2(src, backup_dir / f)

        mem_dir = HERMES_DIR / "memories"
        if mem_dir.exists():
            shutil.copytree(mem_dir, backup_dir / "memories")

        self.logger.info(f"Created backup at {backup_dir}")
        self.backup_dir = backup_dir
        return backup_dir

    def _rollback(self):
        """Restore Hermes directory from backup after a failed migration."""
        if not self.backup_dir or not self.backup_dir.exists():
            self.logger.warn("No backup available for rollback")
            return

        self.logger.info("Rolling back to pre-migration state...")

        for f in ["config.yaml", "SOUL.md", ".env"]:
            backup_file = self.backup_dir / f
            dst = HERMES_DIR / f
            if backup_file.exists():
                shutil.copy2(backup_file, dst)

        backup_mem = self.backup_dir / "memories"
        hermes_mem = HERMES_DIR / "memories"
        if backup_mem.exists():
            if hermes_mem.exists():
                shutil.rmtree(hermes_mem)
            shutil.copytree(backup_mem, hermes_mem)

        self.logger.success("Rolled back to pre-migration state")

    def _cleanup_old_backups(self, keep: int = 3):
        """Remove all but the N most recent backup_* directories in HERMES_DIR."""
        if not HERMES_DIR.exists():
            return
        backups = sorted(
            [d for d in HERMES_DIR.iterdir() if d.is_dir() and d.name.startswith("backup_")],
            key=lambda d: d.name,
        )
        if len(backups) <= keep:
            return
        for old_backup in backups[:-keep]:
            try:
                shutil.rmtree(old_backup)
                self.logger.debug(f"Removed old backup: {old_backup.name}")
            except OSError as e:
                self.logger.warn(f"Could not remove old backup {old_backup.name}: {e}")

    def _load_openclaw_config(self) -> Optional[Dict[str, Any]]:
        """Load OpenClaw configuration."""
        config_path = OPENCLAW_DIR / "openclaw.json"
        if not config_path.exists():
            self.logger.error(f"OpenClaw config not found: {config_path}")
            return None

        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.error(f"Invalid OpenClaw config at {config_path}: {e}")
            return None

        if not isinstance(config, dict):
            self.logger.error(f"OpenClaw config is not a JSON object: {config_path}")
            return None

        self.logger.debug(f"Loaded OpenClaw config with keys: {list(config.keys())}")
        return config

    @staticmethod
    def _yaml_serialize(data: Any, indent: int = 0) -> str:
        """Minimal YAML serializer for when PyYAML is not available."""
        lines = []
        prefix = "  " * indent

        if isinstance(data, dict):
            if not data:
                return "{}"
            for key, value in data.items():
                if isinstance(value, (dict, list)) and value:
                    lines.append(f"{prefix}{key}:")
                    lines.append(OpenClawMigrator._yaml_serialize(value, indent + 1))
                else:
                    serialized = OpenClawMigrator._yaml_scalar(value)
                    lines.append(f"{prefix}{key}: {serialized}")
        elif isinstance(data, list):
            if not data:
                return f"{prefix}[]"
            for item in data:
                if isinstance(item, dict):
                    lines.append(f"{prefix}-")
                    lines.append(OpenClawMigrator._yaml_serialize(item, indent + 1))
                else:
                    serialized = OpenClawMigrator._yaml_scalar(item)
                    lines.append(f"{prefix}- {serialized}")
        else:
            return f"{prefix}{OpenClawMigrator._yaml_scalar(data)}"

        return "\n".join(lines)

    @staticmethod
    def _yaml_scalar(value: Any) -> str:
        """Serialize a scalar value to YAML."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            # Quote strings that could be misinterpreted
            if not value or value in ("true", "false", "null", "yes", "no", "on", "off"):
                return f"'{value}'"
            if any(c in value for c in ":{}[]#&*!|>'\"%@`"):
                return f"'{value}'"
            if value.startswith(("-", " ")) or value.endswith(" "):
                return f"'{value}'"
            return value
        return str(value)

    def _load_hermes_config(self) -> Dict[str, Any]:
        """Load Hermes configuration."""
        config_path = HERMES_DIR / "config.yaml"
        if not config_path.exists():
            return {}

        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            # Fallback: try basic YAML parsing for simple configs
            self.logger.debug("PyYAML not installed, attempting basic config load")
            try:
                return self._basic_yaml_load(config_path)
            except Exception:
                self.logger.debug("Basic YAML load failed, starting fresh")
                return {}

    @staticmethod
    def _basic_yaml_load(path: Path) -> Dict[str, Any]:
        """Basic YAML loader that handles top-level keys and one level of nesting.

        Preserves existing config structure so migration doesn't destroy user settings.
        For deeply nested YAML (3+ levels), inner values are stored as raw strings.
        """
        result: Dict[str, Any] = {}
        current_key: Optional[str] = None

        with open(path, encoding="utf-8") as f:
            for line in f:
                # Skip empty lines and comments
                stripped = line.rstrip("\n\r")
                if not stripped or stripped.lstrip().startswith("#"):
                    continue

                # Calculate indentation level
                indent = len(stripped) - len(stripped.lstrip())

                if indent == 0:
                    # Top-level key
                    if ":" in stripped:
                        key, _, value = stripped.partition(":")
                        value = value.strip()
                        key = key.strip()
                        if value:
                            result[key] = OpenClawMigrator._parse_yaml_value(value)
                            current_key = None
                        else:
                            # Start of a nested block
                            current_key = key
                            result[current_key] = {}
                elif indent > 0 and current_key is not None:
                    # Nested under current_key
                    content = stripped.lstrip()
                    if ":" in content:
                        key, _, value = content.partition(":")
                        value = value.strip()
                        key = key.strip()
                        if isinstance(result[current_key], dict):
                            result[current_key][key] = (
                                OpenClawMigrator._parse_yaml_value(value) if value else {}
                            )

        return result

    @staticmethod
    def _parse_yaml_value(value: str) -> Any:
        """Parse a YAML scalar value string."""
        if value == "true":
            return True
        if value == "false":
            return False
        if value == "null":
            return None
        if value.isdigit():
            return int(value)
        try:
            return float(value)
        except ValueError:
            pass
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
            return value[1:-1]
        return value

    def _save_hermes_config(self, config: Dict[str, Any]):
        """Save Hermes configuration."""
        if self.dry_run:
            self.logger.debug("Would save Hermes config")
            return

        config_path = HERMES_DIR / "config.yaml"
        try:
            import yaml

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        except ImportError:
            # Fallback: use built-in serializer
            yaml_str = self._yaml_serialize(config)
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(yaml_str + "\n")
        self.logger.success("Saved Hermes config.yaml")

    def get_available_agents(self, oc_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of available agents from OpenClaw config."""
        agents = []

        agent_list = oc_config.get("agents", {}).get("list", [])
        for agent in agent_list:
            agents.append(
                {
                    "id": agent.get("id", "unknown"),
                    "model": agent.get("model", "default"),
                    "workspace": agent.get("workspace", ""),
                    "source": "agents.list",
                }
            )

        if not agents:
            defaults = oc_config.get("agents", {}).get("defaults", {})
            if defaults:
                agents.append(
                    {
                        "id": "main",
                        "model": defaults.get("model", {}).get("primary", "default"),
                        "workspace": defaults.get("workspace", ""),
                        "source": "agents.defaults",
                    }
                )

        return agents

    def get_agent_bindings(self, oc_config: Dict[str, Any], agent_id: str) -> List[Dict[str, Any]]:
        """Get channel bindings for a specific agent."""
        bindings = oc_config.get("bindings", [])
        agent_bindings = []

        for binding in bindings:
            if binding.get("agentId") == agent_id:
                match = binding.get("match", {})
                agent_bindings.append(
                    {
                        "channel": match.get("channel", "unknown"),
                        "account_id": match.get("accountId", "default"),
                    }
                )

        return agent_bindings

    def get_agent_channels(self, oc_config: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
        """Get channel configs specific to this agent's bindings."""
        bindings = self.get_agent_bindings(oc_config, agent_id)
        channels = oc_config.get("channels", {})
        agent_channels = {}

        if bindings:
            for binding in bindings:
                channel = binding["channel"]
                account_id = binding["account_id"]

                if channel in channels:
                    channel_config = channels[channel]

                    if "accounts" in channel_config and channel_config["accounts"]:
                        accounts = channel_config.get("accounts", {})
                        if account_id in accounts:
                            agent_channels[channel] = {
                                "enabled": True,
                                "account": account_id,
                            }
                    else:
                        agent_channels[channel] = {
                            "enabled": channel_config.get("enabled", True),
                        }
        else:
            # No bindings — fall back to all enabled channels if agent exists
            agent_list = oc_config.get("agents", {}).get("list", [])
            agent_exists = any(a.get("id") == agent_id for a in agent_list)
            if agent_exists:
                for name, config in channels.items():
                    if config.get("enabled", False):
                        agent_channels[name] = {"enabled": True}

        return agent_channels

    def select_agent(self, oc_config: Dict[str, Any]) -> Optional[str]:
        """Prompt user to select an agent to migrate."""
        agents = self.get_available_agents(oc_config)

        if not agents:
            self.logger.warn("No agents found in OpenClaw config")
            return None

        if len(agents) > 1 and not sys.stdin.isatty():
            self.logger.error(
                "Multiple agents found but no TTY available. "
                "Use --agent <id> to specify which agent to migrate."
            )
            return None

        if len(agents) == 1:
            agent = agents[0]
            self.logger.info(f"Found single agent: {agent['id']}")
            return agent["id"]

        print("\n  Available OpenClaw agents:\n")
        for i, agent in enumerate(agents, 1):
            bindings = self.get_agent_bindings(oc_config, agent["id"])
            binding_str = (
                ", ".join([f"{b['channel']}:{b['account_id']}" for b in bindings]) or "no bindings"
            )
            print(f"    [{i}] {agent['id']}")
            print(f"        Model: {agent['model']}")
            print(f"        Channels: {binding_str}")
            print()

        while True:
            try:
                choice = input("  Select agent to migrate [1-{}]: ".format(len(agents)))
                idx = int(choice) - 1
                if 0 <= idx < len(agents):
                    selected = agents[idx]["id"]
                    print()
                    return selected
                print(f"  Please enter a number between 1 and {len(agents)}")
            except (ValueError, KeyboardInterrupt):
                print("\n  Cancelled.")
                return None
            except EOFError:
                self.logger.error(
                    "No TTY available. Use --agent <id> to specify which agent to migrate."
                )
                return None

    def migrate_soul(self) -> MigrationResult:
        """Migrate SOUL.md (persona)."""
        result = MigrationResult(success=False, message="SOUL.md migration")
        src = OPENCLAW_DIR / "workspace" / "SOUL.md"
        dst = HERMES_DIR / "SOUL.md"

        if not src.exists():
            result.message = "No SOUL.md found in OpenClaw workspace"
            result.warnings.append("SOUL.md not found")
            return result

        with open(src, encoding="utf-8") as f:
            content = f.read()

        # Replace OpenClaw references with Hermes
        content = content.replace("OpenClaw", "Hermes")
        content = content.replace("openclaw", "hermes")
        content = content.replace("OPENCLAW", "HERMES")

        migrated = f"""# Hermes Agent Persona

<!--
Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Source: ~/.openclaw/workspace/SOUL.md
Agent: {self.agent_id or 'default'}
-->

{content}
"""

        if self.dry_run:
            self.logger.debug(f"Would migrate SOUL.md to {dst}")
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(migrated)
            self.logger.success("Migrated SOUL.md (persona)")

        result.success = True
        result.items_migrated.append("SOUL.md")
        return result

    def migrate_memory(self) -> MigrationResult:
        """Migrate MEMORY.md and USER.md."""
        result = MigrationResult(success=False, message="Memory migration")
        mem_dir = HERMES_DIR / "memories"

        # MEMORY.md
        src = OPENCLAW_DIR / "workspace" / "MEMORY.md"
        if src.exists():
            with open(src, encoding="utf-8") as f:
                content = f.read()

            # Replace OpenClaw references
            content = content.replace("OpenClaw", "Hermes")
            content = content.replace("openclaw", "hermes")
            content = content.replace("OPENCLAW", "HERMES")

            dst = mem_dir / "MEMORY.md"
            migrated = f"""Migrated from Hermes on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Source: ~/.openclaw/workspace/MEMORY.md
Agent: {self.agent_id or 'default'}

{content}
"""
            if self.dry_run:
                self.logger.debug(f"Would migrate MEMORY.md to {dst}")
            else:
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(migrated)
                self.logger.success("Migrated MEMORY.md")
            result.items_migrated.append("MEMORY.md")

        # USER.md
        user_src = OPENCLAW_DIR / "workspace" / "USER.md"
        if user_src.exists():
            with open(user_src) as f:
                content = f.read()

            dst = mem_dir / "USER.md"
            migrated = f"""Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Agent: {self.agent_id or 'default'}

{content}
"""
            if self.dry_run:
                self.logger.debug(f"Would migrate USER.md to {dst}")
            else:
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(migrated)
                self.logger.success("Migrated USER.md")
            result.items_migrated.append("USER.md")

        # Archive daily memory files
        daily_dir = OPENCLAW_DIR / "workspace" / "memory"
        if daily_dir.exists():
            archive_dir = mem_dir / "openclaw_archive"
            count = 0
            for mem_file in daily_dir.glob("*.md"):
                if not self.dry_run:
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(mem_file, archive_dir / mem_file.name)
                count += 1

            if count > 0:
                self.logger.success(f"Archived {count} daily memory files")
                result.items_migrated.append(f"{count} daily memory files")

        result.success = bool(result.items_migrated)
        return result

    def migrate_workspace_files(self) -> MigrationResult:
        """Migrate other workspace files (IDENTITY, AGENTS, TOOLS)."""
        result = MigrationResult(success=False, message="Workspace files migration")
        workspace = OPENCLAW_DIR / "workspace"
        mem_dir = HERMES_DIR / "memories"

        files = {
            "IDENTITY.md": "identity.md",
            "AGENTS.md": "agents_config.md",
            "TOOLS.md": "tools_config.md",
        }

        for src_name, dst_name in files.items():
            src = workspace / src_name
            if src.exists():
                if not self.dry_run:
                    shutil.copy2(src, mem_dir / dst_name)
                result.items_migrated.append(src_name)

        if result.items_migrated:
            self.logger.success(f"Migrated {len(result.items_migrated)} workspace files")
            result.success = True

        return result

    def migrate_channels(
        self, oc_config: Dict[str, Any], hermes_config: Dict[str, Any]
    ) -> MigrationResult:
        """Migrate channel configuration for selected agent."""
        result = MigrationResult(success=False, message="Channel migration")

        if self.agent_id:
            agent_channels = self.get_agent_channels(oc_config, self.agent_id)
            bindings = self.get_agent_bindings(oc_config, self.agent_id)
        else:
            channels = oc_config.get("channels", {})
            agent_channels = {k: v for k, v in channels.items() if v.get("enabled", False)}
            bindings = []

        if not agent_channels:
            result.message = "No channels configured for this agent"
            return result

        channel_map = {
            "slack": "hermes-slack",
            "discord": "hermes-discord",
            "telegram": "hermes-telegram",
            "whatsapp": "hermes-whatsapp",
            "signal": "hermes-signal",
            "matrix": "hermes-matrix",
        }

        enabled_channels = []
        for channel in agent_channels.keys():
            enabled_channels.append(channel)
            binding_info = ""
            if bindings:
                agent_binding = [b for b in bindings if b["channel"] == channel]
                if agent_binding:
                    binding_info = f" (account: {agent_binding[0]['account_id']})"
            self.logger.info(f"Detected channel: {channel}{binding_info}")

        if not enabled_channels:
            result.message = "No enabled channels found"
            return result

        if "platform_toolsets" not in hermes_config:
            hermes_config["platform_toolsets"] = {}

        for ch in enabled_channels:
            if ch in channel_map:
                hermes_config["platform_toolsets"][ch] = [channel_map[ch]]
            else:
                result.warnings.append(
                    f"Unknown channel '{ch}' has no Hermes mapping - manual setup required"
                )

        result.items_migrated = enabled_channels
        result.warnings.append("Verify channel tokens were migrated correctly to ~/.hermes/.env")
        self.logger.success(
            f"Enabled {len(enabled_channels)} channel toolsets for agent '{self.agent_id}'"
        )
        self.logger.warn("NOTE: Verify channel tokens in ~/.hermes/.env after migration")

        result.success = True
        return result

    def migrate_models(
        self, oc_config: Dict[str, Any], hermes_config: Dict[str, Any]
    ) -> MigrationResult:
        """Migrate model configuration for selected agent."""
        result = MigrationResult(success=False, message="Model migration")

        model_config = {}

        if self.agent_id:
            agent_list = oc_config.get("agents", {}).get("list", [])
            for agent in agent_list:
                if agent.get("id") == self.agent_id:
                    model_config = {"primary": agent.get("model", "")}
                    break

            if not model_config.get("primary"):
                defaults = oc_config.get("agents", {}).get("defaults", {})
                model_config = defaults.get("model", {})
        else:
            defaults = oc_config.get("agents", {}).get("defaults", {})
            model_config = defaults.get("model", {})

        if model_config.get("primary"):
            primary = model_config["primary"]

            # Check if the model is from an unsupported provider
            primary_lower = primary.lower()
            for prefix, provider_name in UNSUPPORTED_MODEL_PREFIXES.items():
                if primary_lower.startswith(prefix):
                    self.logger.warn(
                        f"Model '{primary}' ({provider_name}) is not"
                        f" directly supported by Hermes.\n"
                        f"         Hermes supports: OpenAI, OpenRouter,"
                        f" Nous Portal, z.ai/GLM, Kimi, MiniMax.\n"
                        f"         Tip: Use this model via OpenRouter."
                    )
                    result.warnings.append(
                        f"Model '{primary}' ({provider_name}) not directly supported by Hermes"
                    )
                    break

            if "model" not in hermes_config or not isinstance(hermes_config["model"], dict):
                hermes_config["model"] = {}

            # Strip provider prefix from model name (e.g. zai/glm-5 -> glm-5).
            # Hermes routes to providers via env vars (ZAI_API_KEY, etc.),
            # so only the bare model name is needed in config.yaml.
            if "/" in primary:
                primary = primary.split("/", 1)[1]
                self.logger.debug(f"Stripped provider prefix: {primary}")

            hermes_config["model"]["default"] = primary

            # Remove OpenRouter base_url default — Hermes auto-detects
            # the correct endpoint from provider env vars (ZAI_API_KEY, etc.)
            hermes_config["model"].pop("base_url", None)
            hermes_config["model"].pop("provider", None)

            # Filter fallbacks to only include supported providers
            if model_config.get("fallbacks"):
                supported = []
                for fb in model_config["fallbacks"]:
                    fb_lower = fb.lower()
                    is_unsupported = any(fb_lower.startswith(p) for p in UNSUPPORTED_MODEL_PREFIXES)
                    if is_unsupported:
                        self.logger.debug(f"Skipping unsupported fallback: {fb}")
                    else:
                        supported.append(fb)
                if supported:
                    hermes_config["model"]["fallbacks"] = supported

            self.logger.success(f"Set model for '{self.agent_id}': {primary}")
            result.items_migrated.append(f"model: {primary}")

        providers = oc_config.get("models", {}).get("providers", {})
        for name, provider in providers.items():
            name_lower = name.lower()
            self.logger.debug(f"Found custom provider: {name} ({provider.get('api', 'unknown')})")
            if name_lower not in HERMES_SUPPORTED_PROVIDERS:
                result.warnings.append(
                    f"Provider '{name}' may not be supported by Hermes - manual config needed"
                )
            else:
                result.warnings.append(
                    f"Custom provider '{name}' noted - manual config may be needed"
                )

        result.success = bool(result.items_migrated)
        return result

    def migrate_agents(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Document multi-agent setup."""
        result = MigrationResult(success=False, message="Multi-agent documentation")

        agents_list = oc_config.get("agents", {}).get("list", [])
        bindings = oc_config.get("bindings", [])
        acp_config = oc_config.get("acp", {})

        if not agents_list and not bindings and not acp_config:
            result.message = "No multi-agent setup found"
            return result

        doc = f"""# OpenClaw Multi-Agent Setup

Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Selected Agent: {self.agent_id or 'default'}

Hermes doesn't have a direct equivalent to OpenClaw's multi-agent routing.
This file documents your setup for reference.

"""

        if agents_list:
            doc += "## All Agents\n\n"
            for agent in agents_list:
                marker = " (SELECTED)" if agent.get("id") == self.agent_id else ""
                doc += f"- **{agent.get('id', 'unknown')}**{marker}\n"
                doc += f"  - Model: {agent.get('model', 'default')}\n"
                doc += f"  - Workspace: {agent.get('workspace', 'default')}\n"
                doc += "\n"

        if bindings:
            doc += "## Channel Bindings\n\n"
            for binding in bindings:
                match = binding.get("match", {})
                marker = " (SELECTED)" if binding.get("agentId") == self.agent_id else ""
                doc += f"- Agent `{binding.get('agentId', 'unknown')}`{marker} <- "
                doc += f"{match.get('channel', 'unknown')}/{match.get('accountId', 'default')}\n"
            doc += "\n"

        if acp_config:
            doc += "## ACP / Coding Agents\n\n"
            doc += f"- Enabled: {acp_config.get('enabled', False)}\n"
            doc += f"- Backend: {acp_config.get('backend', 'unknown')}\n"
            doc += f"- Default Agent: {acp_config.get('defaultAgent', 'unknown')}\n"
            doc += f"- Allowed Agents: {acp_config.get('allowedAgents', [])}\n"
            doc += "\n"

        if not self.dry_run:
            mem_dir = HERMES_DIR / "memories"
            with open(mem_dir / "openclaw_agents.md", "w", encoding="utf-8") as f:
                f.write(doc)

        self.logger.success(f"Documented {len(agents_list)} agents, {len(bindings)} bindings")
        result.items_migrated.append("Multi-agent documentation")
        result.success = True
        return result

    def migrate_heartbeat(self) -> MigrationResult:
        """Migrate HEARTBEAT.md."""
        result = MigrationResult(success=False, message="HEARTBEAT.md migration")
        src = OPENCLAW_DIR / "workspace" / "HEARTBEAT.md"
        dst = HERMES_DIR / "memories" / "HEARTBEAT.md"

        if not src.exists():
            result.warnings.append("HEARTBEAT.md not found")
            return result

        with open(src, encoding="utf-8") as f:
            content = f.read()

        # Skip if file is empty or only comments
        stripped = "\n".join(
            line
            for line in content.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and not line.strip().startswith("<!--")
        )

        migrated = f"""# Heartbeat Tasks
<!--
Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Agent: {self.agent_id or 'default'}
-->

{content}
"""
        if self.dry_run:
            self.logger.debug(f"Would migrate HEARTBEAT.md to {dst}")
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(migrated)
            self.logger.success("Migrated HEARTBEAT.md")

        result.success = True
        result.items_migrated.append("HEARTBEAT.md")
        if not stripped:
            result.warnings.append("HEARTBEAT.md was empty/comments-only")
        return result

    def migrate_env_template(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Generate .env.openclaw with credential placeholders for detected services."""
        result = MigrationResult(success=False, message="Environment template")
        lines = [
            "# OpenClaw Migration - Credential Placeholders",
            f"# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Agent: {self.agent_id or 'default'}",
            "#",
            "# Fill in values below then copy relevant lines to ~/.hermes/.env",
            "# Lines are commented out to prevent accidental sourcing",
            "",
        ]

        channels = oc_config.get("channels", {})
        items = []

        # Telegram
        tg = channels.get("telegram", {})
        if tg.get("enabled") or tg.get("accounts"):
            lines.append("# --Telegram--")
            accounts = tg.get("accounts", {})
            if accounts:
                # If agent has a specific account, highlight it
                if self.agent_id and self.agent_id in accounts:
                    lines.append(f"# TELEGRAM_BOT_TOKEN=  # For agent '{self.agent_id}'")
                else:
                    for acct in accounts:
                        lines.append(f"# TELEGRAM_BOT_TOKEN=  # Account: {acct}")
            else:
                lines.append("# TELEGRAM_BOT_TOKEN=")
            # Allowed users
            allow_from = oc_config.get("commands", {}).get("allowFrom", {}).get("telegram", [])
            if allow_from:
                lines.append(f"# TELEGRAM_ALLOWED_USERS={','.join(str(u) for u in allow_from)}")
            lines.append("")
            items.append("Telegram credentials")

        # Slack
        sl = channels.get("slack", {})
        if sl.get("enabled"):
            lines.append("# --Slack--")
            lines.append("# SLACK_BOT_TOKEN=")
            lines.append("# SLACK_APP_TOKEN=")
            allow_from = oc_config.get("commands", {}).get("allowFrom", {}).get("slack", [])
            if allow_from:
                lines.append(f"# SLACK_ALLOWED_USERS={','.join(str(u) for u in allow_from)}")
            lines.append("")
            items.append("Slack credentials")

        # WhatsApp
        wa = channels.get("whatsapp", {})
        if wa.get("enabled"):
            lines.append("# --WhatsApp--")
            lines.append("# WHATSAPP_ENABLED=true")
            allow_from = wa.get("groupAllowFrom", [])
            if allow_from:
                lines.append(f"# WHATSAPP_ALLOWED_USERS={','.join(str(u) for u in allow_from)}")
            lines.append("")
            items.append("WhatsApp credentials")

        # Discord
        dc = channels.get("discord", {})
        if dc.get("enabled"):
            lines.append("# --Discord--")
            lines.append("# DISCORD_BOT_TOKEN=")
            lines.append("")
            items.append("Discord credentials")

        # Web search / tools
        tools = oc_config.get("tools", {})
        web = tools.get("web", {})
        if web.get("search", {}).get("enabled") or web.get("search", {}).get("apiKey"):
            lines.append("# --Web Tools--")
            lines.append("# FIRECRAWL_API_KEY=  # Or equivalent web search API key")
            lines.append("")
            items.append("Web search API key")

        # Memory search (embedding)
        mem_search = oc_config.get("agents", {}).get("defaults", {}).get("memorySearch", {})
        if mem_search.get("provider") == "gemini":
            lines.append("# --Embedding / Memory Search--")
            lines.append("# Note: OpenClaw used Gemini embedding (gemini-embedding-001)")
            lines.append("# Hermes has built-in memory - this is for reference only")
            lines.append("")
            items.append("Memory search reference")

        # Custom providers
        providers = oc_config.get("models", {}).get("providers", {})
        if providers:
            lines.append("# --Custom Model Providers--")
            for name, prov in providers.items():
                base_url = prov.get("baseUrl", "")
                lines.append(f"# Provider '{name}': {base_url}")
            lines.append("# Set API keys for custom providers as needed")
            lines.append("")
            items.append("Custom provider references")

        if not items:
            result.message = "No credentials detected to template"
            return result

        env_content = "\n".join(lines) + "\n"
        dst = HERMES_DIR / ".env.openclaw"

        if self.dry_run:
            self.logger.debug(f"Would write env template to {dst}")
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(env_content)
            self.logger.success("Created credential template: .env.openclaw")

        result.success = True
        result.items_migrated = items
        result.warnings.append("Fill in credentials in .env.openclaw, then copy to .env")
        return result

    def migrate_credentials(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Copy actual credentials from OpenClaw to Hermes .env for working channels."""
        result = MigrationResult(success=False, message="Credentials")
        env_lines = [
            f"# Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Agent: {self.agent_id or 'default'}",
            "",
        ]
        items = []

        channels = oc_config.get("channels", {})

        # Telegram - get the selected agent's bot token
        tg = channels.get("telegram", {})
        if tg.get("enabled") or tg.get("accounts"):
            accounts = tg.get("accounts", {})
            bot_token = None

            # Priority: selected agent's account, then "default" account
            if self.agent_id and self.agent_id in accounts:
                bot_token = accounts[self.agent_id].get("botToken")
            elif "default" in accounts:
                bot_token = accounts["default"].get("botToken")
            elif accounts:
                # Take the first one
                first_acct = next(iter(accounts.values()))
                bot_token = first_acct.get("botToken")

            # Also check top-level botToken (flat config)
            if not bot_token:
                bot_token = tg.get("botToken")

            if bot_token:
                env_lines.append(f"TELEGRAM_BOT_TOKEN={bot_token}")
                items.append("Telegram bot token")

            # Allowed users
            allow_tg = oc_config.get("commands", {}).get("allowFrom", {}).get("telegram", [])
            group_allow = tg.get("groupAllowFrom", [])
            all_users = list(set(str(u) for u in (allow_tg + group_allow)))
            if all_users:
                env_lines.append(f"TELEGRAM_ALLOWED_USERS={','.join(all_users)}")
                items.append("Telegram allowed users")

            env_lines.append("")

        # Slack
        sl = channels.get("slack", {})
        if sl.get("enabled") or sl.get("accounts"):
            bot_token = sl.get("botToken")
            app_token = sl.get("appToken")

            # Check accounts structure (like Telegram)
            sl_accounts = sl.get("accounts", {})
            if not bot_token and sl_accounts:
                if self.agent_id and self.agent_id in sl_accounts:
                    bot_token = sl_accounts[self.agent_id].get("accessToken") or sl_accounts[
                        self.agent_id
                    ].get("botToken")
                elif "default" in sl_accounts:
                    bot_token = sl_accounts["default"].get("accessToken") or sl_accounts[
                        "default"
                    ].get("botToken")
                elif sl_accounts:
                    first_acct = next(iter(sl_accounts.values()))
                    bot_token = first_acct.get("accessToken") or first_acct.get("botToken")

            if bot_token:
                env_lines.append(f"SLACK_BOT_TOKEN={bot_token}")
                items.append("Slack bot token")
            if app_token:
                env_lines.append(f"SLACK_APP_TOKEN={app_token}")
                items.append("Slack app token")

            # Check multiple sources for allowed Slack users
            allow_sl = oc_config.get("commands", {}).get("allowFrom", {}).get("slack", [])
            if not allow_sl:
                # Check credentials/slack-*-allowFrom.json files
                cred_dir = OPENCLAW_DIR / "credentials"
                if cred_dir.exists():
                    for af in cred_dir.glob("slack-*-allowFrom.json"):
                        try:
                            with open(af, encoding="utf-8") as f:
                                af_data = json.load(f)
                            allow_sl = af_data.get("allowFrom", [])
                            if allow_sl:
                                break
                        except (json.JSONDecodeError, OSError):
                            pass
            if allow_sl:
                env_lines.append(f"SLACK_ALLOWED_USERS={','.join(str(u) for u in allow_sl)}")
                items.append("Slack allowed users")

            env_lines.append("")

        # WhatsApp
        wa = channels.get("whatsapp", {})
        if wa.get("enabled"):
            env_lines.append("WHATSAPP_ENABLED=true")
            allow_wa = wa.get("groupAllowFrom", [])
            if allow_wa:
                env_lines.append(f"WHATSAPP_ALLOWED_USERS={','.join(str(u) for u in allow_wa)}")
                items.append("WhatsApp config")
            env_lines.append("")

        # Discord
        dc = channels.get("discord", {})
        if dc.get("enabled"):
            dc_token = dc.get("botToken")
            if dc_token:
                env_lines.append(f"DISCORD_BOT_TOKEN={dc_token}")
                items.append("Discord bot token")
            env_lines.append("")

        # Web search API key
        tools = oc_config.get("tools", {})
        web_search = tools.get("web", {}).get("search", {})
        if isinstance(web_search, dict) and web_search.get("apiKey"):
            env_lines.append(f"FIRECRAWL_API_KEY={web_search['apiKey']}")
            items.append("Web search API key")
            env_lines.append("")

        # Memory search API key (e.g., Gemini embedding)
        mem_search = oc_config.get("agents", {}).get("defaults", {}).get("memorySearch", {})
        mem_api_key = mem_search.get("remote", {}).get("apiKey")
        if mem_api_key:
            provider_name = mem_search.get("provider", "embedding").upper()
            env_lines.append("# Memory search / embedding provider")
            env_lines.append(f"{provider_name}_API_KEY={mem_api_key}")
            items.append(f"{provider_name} API key (memory search)")
            env_lines.append("")

        # Load auth profiles for API keys stored outside openclaw.json
        auth_profiles = {}
        agent_dir = self.agent_id or "main"
        auth_path = OPENCLAW_DIR / "agents" / agent_dir / "agent" / "auth-profiles.json"
        if auth_path.exists():
            try:
                with open(auth_path, encoding="utf-8") as f:
                    auth_data = json.load(f)
                auth_profiles = auth_data.get("profiles", {})
                self.logger.debug(f"Loaded {len(auth_profiles)} auth profiles")
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warn(f"Could not read auth profiles: {e}")

        # Also load per-agent models.json for apiKey fields
        models_path = OPENCLAW_DIR / "agents" / agent_dir / "agent" / "models.json"
        agent_models = {}
        if models_path.exists():
            try:
                with open(models_path, encoding="utf-8") as f:
                    agent_models = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # Discover Hermes env var names from the .env template
        # This ensures we use the correct variable names regardless of provider
        provider_env_map = {}
        hermes_env_path = HERMES_DIR / ".env"
        if hermes_env_path.exists():
            try:
                env_text = hermes_env_path.read_text(encoding="utf-8")
                # Find all KEY_API_KEY= and KEY_BASE_URL= patterns
                # (both set and commented-out template lines)
                for env_line in env_text.splitlines():
                    stripped = env_line.lstrip("# ").strip()
                    if "_API_KEY=" in stripped or "_BASE_URL=" in stripped:
                        var_name = stripped.split("=", 1)[0].strip()
                        # Extract prefix (e.g. GLM from GLM_API_KEY)
                        if var_name.endswith("_API_KEY"):
                            prefix = var_name[: -len("_API_KEY")]
                        elif var_name.endswith("_BASE_URL"):
                            prefix = var_name[: -len("_BASE_URL")]
                        else:
                            continue
                        # Map lowercase prefix to the template prefix
                        if prefix:
                            provider_env_map[prefix.lower()] = prefix
            except OSError:
                pass

        # Add known OpenClaw-to-Hermes name mappings as fallbacks
        provider_env_map.setdefault("zai", "GLM")
        provider_env_map.setdefault("z.ai", "GLM")

        # Custom model provider API keys (from auth profiles, models.json, or config)
        providers = oc_config.get("models", {}).get("providers", {})
        for name, prov in providers.items():
            base_url = prov.get("baseUrl", "")
            # Use Hermes env var name if known, otherwise derive from provider name
            env_prefix = provider_env_map.get(
                name.lower(),
                name.upper().replace("-", "_").replace(".", "_"),
            )

            # Find API key from multiple sources
            api_key = prov.get("apiKey")
            if not api_key:
                # Check per-agent models.json
                agent_prov = agent_models.get("providers", {}).get(name, {})
                api_key = agent_prov.get("apiKey")
            if not api_key:
                # Check auth profiles (try common naming patterns)
                for profile_key, profile in auth_profiles.items():
                    if profile.get("provider") == name:
                        api_key = profile.get("key") or profile.get("token")
                        break

            if base_url or api_key:
                env_lines.append(f"# Custom provider: {name}")
                if base_url:
                    env_lines.append(f"{env_prefix}_BASE_URL={base_url}")
                if api_key:
                    env_lines.append(f"{env_prefix}_API_KEY={api_key}")
                    items.append(f"{name} API key")
                env_lines.append("")

        if not items:
            result.message = "No credentials found to migrate"
            result.success = True
            return result

        env_content = "\n".join(env_lines) + "\n"
        dst = HERMES_DIR / ".env"

        # Determine the primary model name for LLM_MODEL env var
        primary_model = ""
        if self.agent_id:
            agent_list = oc_config.get("agents", {}).get("list", [])
            for agent in agent_list:
                if agent.get("id") == self.agent_id:
                    primary_model = agent.get("model", "")
                    break
        if not primary_model:
            defaults = oc_config.get("agents", {}).get("defaults", {})
            primary_model = defaults.get("model", {}).get("primary", "")
        # Strip provider prefix (e.g. zai/glm-5 -> glm-5)
        if "/" in primary_model:
            primary_model = primary_model.split("/", 1)[1]

        if self.dry_run:
            self.logger.debug(f"Would write {len(items)} credentials to .env")
        else:
            # If .env already exists (from Hermes installer), patch template
            # values in-place before appending our migration section
            if dst.exists():
                existing = dst.read_text(encoding="utf-8")

                # Build replacements for template values
                replacements = {}
                if primary_model:
                    replacements["LLM_MODEL"] = primary_model

                # Collect provider keys/urls to patch in the template
                for name, prov in providers.items():
                    env_prefix = provider_env_map.get(
                        name.lower(),
                        name.upper().replace("-", "_").replace(".", "_"),
                    )
                    # Find API key
                    api_key = prov.get("apiKey")
                    if not api_key:
                        agent_prov = agent_models.get("providers", {}).get(name, {})
                        api_key = agent_prov.get("apiKey")
                    if not api_key:
                        for pk, profile in auth_profiles.items():
                            if profile.get("provider") == name:
                                api_key = profile.get("key") or profile.get("token")
                                break
                    base_url = prov.get("baseUrl", "")
                    if api_key:
                        replacements[f"{env_prefix}_API_KEY"] = api_key
                    if base_url:
                        replacements[f"{env_prefix}_BASE_URL"] = base_url

                # Apply replacements to existing template lines
                patched_lines = []
                for line in existing.splitlines():
                    patched = False
                    for key, value in replacements.items():
                        # Match "KEY=" or "# KEY=" (commented template)
                        if line.startswith(f"{key}="):
                            patched_lines.append(f"{key}={value}")
                            patched = True
                            break
                        elif line.startswith(f"# {key}="):
                            patched_lines.append(f"{key}={value}")
                            patched = True
                            break
                    if not patched:
                        patched_lines.append(line)

                existing = "\n".join(patched_lines) + "\n"
                env_content = existing + "\n# --- OpenClaw Migration ---\n" + env_content
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(env_content)
            else:
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(env_content)

            # Set restrictive permissions (owner-only)
            try:
                os.chmod(dst, 0o600)
            except OSError:
                result.warnings.append(
                    "Could not set .env file permissions to 600 (Windows). "
                    "Ensure the file is not world-readable."
                )

            self.logger.success(f"Wrote {len(items)} credentials to .env")

        result.success = True
        result.items_migrated = items
        return result

    def start_hermes(self) -> MigrationResult:
        """Start Hermes after migration."""
        result = MigrationResult(success=False, message="Start Hermes")

        if self.dry_run:
            self.logger.debug("Would start Hermes")
            result.success = True
            result.items_migrated.append("Hermes start (dry run)")
            return result

        # Check if hermes command is available
        try:
            version_check = subprocess.run(
                ["hermes", "--version"], capture_output=True, text=True, timeout=10
            )
            if version_check.returncode != 0:
                result.warnings.append("Hermes CLI not in PATH. Start manually: hermes")
                result.success = True
                return result
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result.warnings.append(
                "Hermes CLI not found. Start manually after sourcing shell: "
                "source ~/.bashrc && hermes"
            )
            result.success = True
            return result

        # Start Hermes in the background
        try:
            self.logger.info("Starting Hermes...")
            popen_kwargs: Dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                # Windows: use CREATE_NEW_PROCESS_GROUP
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(["hermes"], **popen_kwargs)
            self.logger.success("Hermes started in background")
            result.success = True
            result.items_migrated.append("Hermes started")
        except Exception as e:
            self.logger.error(f"Failed to start Hermes: {e}")
            result.warnings.append(f"Could not start Hermes: {e}. Start manually: hermes")
            result.success = True

        return result

    def _parse_duration_to_minutes(self, duration_str: str) -> Optional[int]:
        """Parse duration strings like '1h', '30m', '90s', '2h30m' to minutes."""
        if not duration_str or not isinstance(duration_str, str):
            return None

        total_seconds = 0
        matches = re.findall(r"(\d+)\s*([dhms])", duration_str.lower())
        if not matches:
            # Try bare number (assume minutes)
            try:
                return int(duration_str)
            except ValueError:
                return None

        for value, unit in matches:
            v = int(value)
            if unit == "d":
                total_seconds += v * 86400
            elif unit == "h":
                total_seconds += v * 3600
            elif unit == "m":
                total_seconds += v * 60
            elif unit == "s":
                total_seconds += v

        return max(1, total_seconds // 60)

    def migrate_advanced_config(
        self, oc_config: Dict[str, Any], hermes_config: Dict[str, Any]
    ) -> MigrationResult:
        """Map advanced OpenClaw settings to Hermes config.yaml."""
        result = MigrationResult(success=False, message="Advanced config")
        defaults = oc_config.get("agents", {}).get("defaults", {})

        # Compaction → Compression
        compaction = defaults.get("compaction", {})
        if compaction.get("mode"):
            hermes_config.setdefault("compression", {})
            hermes_config["compression"].setdefault("enabled", True)
            result.items_migrated.append(f"compression (from compaction.{compaction['mode']})")
            self.logger.debug(
                f"Mapped compaction.mode={compaction['mode']} -> compression.enabled=true"
            )

        # Context pruning → Compression threshold
        pruning = defaults.get("contextPruning", {})
        if pruning.get("mode") == "cache-ttl":
            hermes_config.setdefault("compression", {})
            hermes_config["compression"].setdefault("enabled", True)
            ttl = pruning.get("ttl", "")
            if ttl:
                result.items_migrated.append(f"context pruning (ttl: {ttl})")

        # maxConcurrent → code_execution.max_tool_calls
        # OpenClaw maxConcurrent is parallel agent count; Hermes max_tool_calls is
        # total tool calls per turn. Multiply by 10 (typical tools per concurrent agent).
        max_concurrent = defaults.get("maxConcurrent")
        if max_concurrent:
            hermes_config.setdefault("code_execution", {})
            hermes_config["code_execution"].setdefault("max_tool_calls", max_concurrent * 10)
            result.items_migrated.append(f"code_execution.max_tool_calls={max_concurrent * 10}")

        # subagents.maxConcurrent → delegation.max_iterations
        # OpenClaw subagent concurrency maps to Hermes delegation iterations.
        # Multiply by 6 (typical iterations per concurrent subagent).
        sub_max = defaults.get("subagents", {}).get("maxConcurrent")
        if sub_max:
            hermes_config.setdefault("delegation", {})
            hermes_config["delegation"].setdefault("max_iterations", sub_max * 6)
            result.items_migrated.append(f"delegation.max_iterations={sub_max * 6}")

        # Session retention → session_reset
        retention = oc_config.get("cron", {}).get("sessionRetention")
        if retention:
            minutes = self._parse_duration_to_minutes(retention)
            if minutes:
                hermes_config.setdefault("session_reset", {})
                hermes_config["session_reset"].setdefault("mode", "idle")
                hermes_config["session_reset"].setdefault("idle_minutes", minutes)
                result.items_migrated.append(f"session_reset.idle_minutes={minutes}")

        # Toolset enablement based on detected tools
        tools = oc_config.get("tools", {})
        if tools.get("web", {}).get("search") or tools.get("web", {}).get("fetch"):
            hermes_config.setdefault("toolsets", ["all"])
            result.items_migrated.append("web toolset detected")
        if tools.get("agentToAgent", {}).get("enabled"):
            hermes_config.setdefault("toolsets", ["all"])
            result.items_migrated.append("delegation toolset detected")

        # Document-only fields (no direct Hermes equivalent)
        dm_scope = oc_config.get("session", {}).get("dmScope")
        if dm_scope:
            result.warnings.append(f"session.dmScope='{dm_scope}' has no direct Hermes equivalent")

        heartbeat = defaults.get("heartbeat", {}).get("every")
        if heartbeat:
            result.warnings.append(f"heartbeat.every='{heartbeat}' has no direct Hermes equivalent")

        maintenance = oc_config.get("session", {}).get("maintenance", {}).get("mode")
        if maintenance:
            result.warnings.append(
                f"session.maintenance.mode='{maintenance}' has no Hermes equivalent"
            )

        messages = oc_config.get("messages", {})
        if messages.get("ackReactionScope"):
            result.warnings.append(
                f"messages.ackReactionScope='{messages['ackReactionScope']}' is platform-specific"
            )

        result.success = bool(result.items_migrated)
        if result.items_migrated:
            self.logger.success(f"Mapped {len(result.items_migrated)} advanced settings")
        return result

    def migrate_channel_details(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Create detailed channel migration documentation."""
        result = MigrationResult(success=False, message="Channel details")
        channels = oc_config.get("channels", {})

        if not channels:
            return result

        doc = f"""# OpenClaw Channel Configuration Reference

Migrated on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Agent: {self.agent_id or 'default'}

This documents your OpenClaw channel setup for manual reconfiguration in Hermes.
Credentials have been redacted - see .env.openclaw for placeholder list.

"""

        for channel_name, channel_config in channels.items():
            safe_config = redact_sensitive_fields(channel_config)
            doc += f"## {channel_name.title()}\n\n"
            doc += f"- Enabled: {safe_config.get('enabled', 'unknown')}\n"

            # Channel-specific details
            if channel_name == "telegram":
                doc += f"- Streaming: {safe_config.get('streaming', 'default')}\n"
                doc += f"- DM Policy: {safe_config.get('dmPolicy', 'default')}\n"
                doc += f"- Group Policy: {safe_config.get('groupPolicy', 'default')}\n"

                accounts = safe_config.get("accounts", {})
                if accounts:
                    doc += f"\n### Accounts ({len(accounts)})\n\n"
                    for acct_name, acct_config in accounts.items():
                        is_selected = acct_name == self.agent_id
                        marker = " **(SELECTED)**" if is_selected else ""
                        doc += f"#### `{acct_name}`{marker}\n\n"
                        doc += f"- DM Policy: {acct_config.get('dmPolicy', 'default')}\n"
                        doc += f"- Streaming: {acct_config.get('streaming', 'default')}\n"
                        doc += f"- Group Policy: {acct_config.get('groupPolicy', 'default')}\n"

                        capabilities = acct_config.get("capabilities", {})
                        if capabilities:
                            doc += f"- Capabilities: {capabilities}\n"

                        groups = acct_config.get("groups", {})
                        if groups:
                            doc += "- Groups:\n"
                            for gid, gconfig in groups.items():
                                doc += f"  - `{gid}`: enabled={gconfig.get('enabled', True)}"
                                doc += f", requireMention={gconfig.get('requireMention', True)}"
                                doc += f", policy={gconfig.get('groupPolicy', 'default')}\n"

                                topics = gconfig.get("topics", {})
                                if topics:
                                    for tid, tconfig in topics.items():
                                        enabled = tconfig.get("enabled", True)
                                        mention = tconfig.get("requireMention", True)
                                        doc += f"    - Topic `{tid}`: enabled={enabled}"
                                        doc += f", requireMention={mention}"
                                        doc += f", policy={tconfig.get('groupPolicy', 'default')}\n"
                        doc += "\n"

            elif channel_name == "slack":
                doc += f"- Mode: {safe_config.get('mode', 'default')}\n"
                doc += f"- Block Streaming: {safe_config.get('blockStreaming', False)}\n"
                doc += "\n"

            elif channel_name == "whatsapp":
                doc += f"- DM Policy: {safe_config.get('dmPolicy', 'default')}\n"
                doc += f"- Self Chat Mode: {safe_config.get('selfChatMode', False)}\n"
                doc += f"- Group Policy: {safe_config.get('groupPolicy', 'default')}\n"
                doc += f"- Debounce (ms): {safe_config.get('debounceMs', 0)}\n"
                doc += f"- Max Media Size (MB): {safe_config.get('mediaMaxMb', 'default')}\n"
                doc += "\n"

            elif channel_name == "discord":
                doc += "\n"

            else:
                # Generic channel - document all non-sensitive fields
                for key, val in safe_config.items():
                    if key not in ("enabled",) and val != REDACT_VALUE:
                        doc += f"- {key}: {val}\n"
                doc += "\n"

        doc += """## Manual Reconfiguration Required

After migration, you need to:
1. Set up bot tokens in ~/.hermes/.env (see .env.openclaw for reference)
2. Configure group/topic routing in Hermes platform settings
3. Re-pair with channels using `hermes setup`
4. Test each channel independently
"""

        if not self.dry_run:
            mem_dir = HERMES_DIR / "memories"
            with open(mem_dir / "openclaw_channels.md", "w", encoding="utf-8") as f:
                f.write(doc)

        self.logger.success(f"Documented {len(channels)} channel configurations")
        result.success = True
        result.items_migrated.append(f"{len(channels)} channel configs documented")
        return result

    def migrate_infrastructure(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Document OpenClaw infrastructure config for reference."""
        result = MigrationResult(success=False, message="Infrastructure docs")

        doc = f"""# OpenClaw Infrastructure Reference

Migrated on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Agent: {self.agent_id or 'default'}

This documents your OpenClaw infrastructure settings for reference.
Some have Hermes equivalents (noted), others are OpenClaw-specific.

"""
        sections = []

        # Gateway
        gateway = oc_config.get("gateway", {})
        if gateway:
            safe_gw = redact_sensitive_fields(gateway)
            doc += "## Gateway\n\n"
            doc += "Hermes has its own gateway - this is for reference.\n\n"
            doc += f"- Port: {safe_gw.get('port', 'default')}\n"
            doc += f"- Mode: {safe_gw.get('mode', 'default')}\n"
            doc += f"- Bind: {safe_gw.get('bind', 'default')}\n"
            ts = safe_gw.get("tailscale", {})
            if ts:
                doc += f"- Tailscale: mode={ts.get('mode', 'off')}\n"
            deny = safe_gw.get("nodes", {}).get("denyCommands", [])
            if deny:
                doc += f"- Denied Commands: {deny}\n"
            doc += "\n"
            sections.append("Gateway")

        # Custom Model Providers
        providers = oc_config.get("models", {}).get("providers", {})
        if providers:
            doc += "## Custom Model Providers\n\n"
            doc += "These may need manual setup in Hermes (.env or config.yaml).\n\n"
            for name, prov in providers.items():
                safe_prov = redact_sensitive_fields(prov)
                doc += f"### {name}\n\n"
                doc += f"- Base URL: {safe_prov.get('baseUrl', 'unknown')}\n"
                doc += f"- API Type: {safe_prov.get('api', 'unknown')}\n"
                models = safe_prov.get("models", [])
                if models:
                    doc += "- Models:\n"
                    for m in models:
                        doc += f"  - `{m.get('id', '?')}` ({m.get('name', '?')})"
                        ctx = m.get("contextWindow", "?")
                        maxtok = m.get("maxTokens", "?")
                        doc += f" - context: {ctx}, max tokens: {maxtok}"
                        if m.get("reasoning"):
                            doc += " [reasoning]"
                        doc += "\n"
                doc += "\n"
            sections.append("Custom providers")

        # Hooks
        hooks = oc_config.get("hooks", {})
        if hooks:
            doc += "## Hooks\n\n"
            internal = hooks.get("internal", {})
            if internal.get("enabled"):
                doc += "Internal hooks were enabled:\n\n"
                entries = internal.get("entries", {})
                for hook_name, hook_config in entries.items():
                    status = "enabled" if hook_config.get("enabled") else "disabled"
                    doc += f"- `{hook_name}`: {status}\n"
                doc += "\n"
                doc += "Hermes equivalent: Place hook scripts in ~/.hermes/hooks/\n\n"
            sections.append("Hooks")

        # Plugins
        plugins = oc_config.get("plugins", {})
        if plugins:
            entries = plugins.get("entries", {})
            if entries:
                doc += "## Plugins\n\n"
                for plugin_name, plugin_config in entries.items():
                    safe_pc = redact_sensitive_fields(plugin_config)
                    status = "enabled" if safe_pc.get("enabled") else "disabled"
                    doc += f"- `{plugin_name}`: {status}\n"
                    config = safe_pc.get("config", {})
                    if config:
                        for k, v in config.items():
                            doc += f"  - {k}: {v}\n"
                doc += "\n"
                sections.append("Plugins")

        # Commands
        commands = oc_config.get("commands", {})
        if commands:
            doc += "## Commands Configuration\n\n"
            doc += f"- Native commands: {commands.get('native', 'auto')}\n"
            doc += f"- Native skills: {commands.get('nativeSkills', 'auto')}\n"
            doc += f"- Restart allowed: {commands.get('restart', False)}\n"
            doc += f"- Owner display: {commands.get('ownerDisplay', 'default')}\n"
            allow_from = commands.get("allowFrom", {})
            if allow_from:
                doc += "- Access control:\n"
                for platform, users in allow_from.items():
                    doc += f"  - {platform}: {users}\n"
            doc += "\n"
            sections.append("Commands")

        # Tools
        tools = oc_config.get("tools", {})
        if tools:
            doc += "## Tools Configuration\n\n"
            web = tools.get("web", {})
            if web:
                doc += f"- Web search: {bool(web.get('search'))}\n"
                doc += f"- Web fetch: {bool(web.get('fetch'))}\n"
            sessions = tools.get("sessions", {})
            if sessions:
                doc += f"- Sessions visibility: {sessions.get('visibility', 'default')}\n"
            a2a = tools.get("agentToAgent", {})
            if a2a:
                doc += f"- Agent-to-agent: {a2a.get('enabled', False)}\n"
            doc += "\nHermes equivalent: Configure toolsets in config.yaml\n\n"
            sections.append("Tools")

        # Cron
        cron = oc_config.get("cron", {})
        if cron:
            doc += "## Cron / Scheduled Tasks\n\n"
            doc += f"- Session retention: {cron.get('sessionRetention', 'default')}\n"
            jobs = cron.get("jobs", [])
            if jobs:
                doc += "- Scheduled jobs:\n"
                for job in jobs:
                    doc += f"  - {job}\n"
            doc += "\nHermes equivalent: Place cron YAML files in ~/.hermes/cron/\n\n"
            sections.append("Cron")

        # Skills install config
        skills = oc_config.get("skills", {})
        if skills:
            doc += "## Skills Configuration\n\n"
            doc += f"- Node manager: {skills.get('install', {}).get('nodeManager', 'default')}\n"
            doc += "\n"
            sections.append("Skills")

        # Update preferences
        update = oc_config.get("update", {})
        if update:
            doc += "## Update Preferences\n\n"
            doc += f"- Channel: {update.get('channel', 'stable')}\n"
            auto = update.get("auto", {})
            if auto:
                doc += f"- Auto-update: {auto.get('enabled', False)}\n"
            doc += "\n"
            sections.append("Update prefs")

        # Session config
        session = oc_config.get("session", {})
        if session:
            doc += "## Session Configuration\n\n"
            doc += f"- DM scope: {session.get('dmScope', 'default')}\n"
            maint = session.get("maintenance", {})
            if maint:
                doc += f"- Maintenance mode: {maint.get('mode', 'off')}\n"
            doc += "\n"
            sections.append("Session")

        # Messages config
        messages = oc_config.get("messages", {})
        if messages:
            doc += "## Messages Configuration\n\n"
            for k, v in messages.items():
                doc += f"- {k}: {v}\n"
            doc += "\n"
            sections.append("Messages")

        if not sections:
            result.message = "No infrastructure config found"
            return result

        if not self.dry_run:
            mem_dir = HERMES_DIR / "memories"
            with open(mem_dir / "openclaw_infrastructure.md", "w", encoding="utf-8") as f:
                f.write(doc)

        self.logger.success(f"Documented {len(sections)} infrastructure sections")
        result.success = True
        result.items_migrated = sections
        return result

    def stop_openclaw(self, oc_config: Dict[str, Any]) -> MigrationResult:
        """Stop OpenClaw gateway and main process."""
        result = MigrationResult(success=False, message="Stop OpenClaw")

        if self.dry_run:
            self.logger.debug("Would stop OpenClaw processes")
            result.success = True
            result.items_migrated.append("OpenClaw stop (dry run)")
            return result

        stopped = []

        # Stop systemd user service if it exists (prevents respawning)
        try:
            svc_check = subprocess.run(
                ["systemctl", "--user", "is-active", "openclaw-gateway"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if svc_check.stdout.strip() == "active":
                self.logger.info("Stopping openclaw-gateway systemd service...")
                subprocess.run(
                    ["systemctl", "--user", "stop", "openclaw-gateway"],
                    capture_output=True,
                    timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "disable", "openclaw-gateway"],
                    capture_output=True,
                    timeout=10,
                )
                stopped.append("systemd:openclaw-gateway")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try graceful stop via gateway API
        gateway = oc_config.get("gateway", {})
        port = gateway.get("port")
        if port:
            self.logger.info(f"Stopping OpenClaw gateway on port {port}...")

        # Find and kill openclaw processes
        try:
            # Collect PIDs for all openclaw-related processes.
            # Strategy 1: exact binary name matches — covers both 'openclaw' (main)
            # and 'openclaw-gateway' (gateway daemon).  We must check both separately
            # because pgrep -x requires an exact name and won't match 'openclaw-gateway'
            # when searching for 'openclaw'.
            all_pids_raw: List[str] = []
            for binary_name in ("openclaw", "openclaw-gateway"):
                r = subprocess.run(
                    ["pgrep", "-x", binary_name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    all_pids_raw.extend(r.stdout.strip().split("\n"))

            # Strategy 2: fallback command-line pattern — used only when the exact-name
            # searches found nothing (e.g. non-standard process names or future renames).
            if not all_pids_raw:
                r = subprocess.run(
                    ["pgrep", "-f", "openclaw.*(serve|start|gateway)"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    all_pids_raw.extend(r.stdout.strip().split("\n"))

            # Deduplicate while preserving order
            seen: set = set()
            deduped: List[str] = []
            for p in all_pids_raw:
                p = p.strip()
                if p and p not in seen:
                    seen.add(p)
                    deduped.append(p)

            if deduped:
                pids = deduped
            else:
                pids = []

            if pids:
                # Filter out our own migration process and parent shell
                our_pid = str(os.getpid())
                parent_pid = str(os.getppid())
                pids = [p for p in pids if p not in (our_pid, parent_pid)]

                if pids:
                    # SIGTERM first (graceful)
                    for pid in pids:
                        try:
                            os.kill(int(pid), 15)  # SIGTERM
                            stopped.append(pid)
                        except (ProcessLookupError, PermissionError):
                            pass

                    if stopped:
                        self.logger.info(f"Sent SIGTERM to {len(stopped)} OpenClaw process(es)")

                        # Wait briefly for graceful shutdown
                        import time

                        time.sleep(GRACEFUL_SHUTDOWN_WAIT)

                        # Check if any are still running
                        still_running = []
                        for pid in stopped:
                            try:
                                os.kill(int(pid), 0)  # Check if alive
                                still_running.append(pid)
                            except ProcessLookupError:
                                pass

                        if still_running:
                            self.logger.warn(
                                f"{len(still_running)} process(es) still running, sending SIGKILL"
                            )
                            for pid in still_running:
                                try:
                                    os.kill(int(pid), 9)  # SIGKILL
                                except (ProcessLookupError, PermissionError):
                                    pass
                else:
                    self.logger.info("No OpenClaw processes found")
            else:
                self.logger.info("No OpenClaw processes found")

        except FileNotFoundError:
            # pgrep not available (e.g. Windows) - try port-based detection
            if port:
                self.logger.warn(
                    f"Cannot detect processes (pgrep not found). "
                    f"Manually stop OpenClaw on port {port}"
                )
                result.warnings.append(
                    f"Could not auto-stop OpenClaw. Stop it manually (port {port})"
                )
                result.success = True
                return result
        except Exception as e:
            self.logger.warn(f"Error stopping OpenClaw: {e}")
            result.warnings.append(f"Could not auto-stop OpenClaw: {e}")

        if stopped:
            self.logger.success(f"Stopped {len(stopped)} OpenClaw process(es)")
            result.items_migrated.append(f"{len(stopped)} process(es) stopped")
        else:
            result.items_migrated.append("No processes to stop")

        result.success = True
        return result

    def _check_previous_migration(self) -> bool:
        """Check if a previous migration exists. Returns True if safe to proceed."""
        found_marker = False

        marker = HERMES_DIR / ".env"
        if marker.exists():
            content = marker.read_text(encoding="utf-8")
            if "OpenClaw Migration" in content:
                found_marker = True

        soul = HERMES_DIR / "SOUL.md"
        if soul.exists():
            content = soul.read_text(encoding="utf-8")
            if "Migrated from OpenClaw" in content:
                found_marker = True

        memories = HERMES_DIR / "memories"
        if memories.exists() and memories.is_dir():
            for child in memories.iterdir():
                if child.name.startswith("openclaw_"):
                    found_marker = True
                    break

        if found_marker:
            self.logger.warn("Previous migration detected in ~/.hermes/")
            self.logger.warn(
                "Re-running may create duplicate entries. "
                "Use --force to overwrite, or clean up manually."
            )
            if not self.force:
                return False
        return True

    def _prompt_step(self, name: str, description: str) -> bool:
        """Ask user whether to run a migration step. Returns True to proceed."""
        if self.dry_run or self.logger.quiet:
            return True
        try:
            answer = input(f"\n  Migrate {name}? ({description}) [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
            print("")
        # Default is yes (empty or y/yes)
        return answer not in ("n", "no")

    def run(self) -> bool:
        """Run the full migration."""
        print("\n  OpenClaw  Hermes Migration Tool")
        print("  " + "-" * 38)

        # Check OpenClaw exists
        if not OPENCLAW_DIR.exists():
            self.logger.error(f"OpenClaw directory not found: {OPENCLAW_DIR}")
            return False

        # Check Hermes status (don't auto-install — CLI handles that)
        installer = HermesInstaller(self.logger)
        if installer.is_hermes_installed():
            self.logger.success("Hermes is installed")
        elif installer.is_hermes_dir_exists():
            self.logger.info("Hermes directory exists (CLI not in PATH yet)")
        else:
            self.logger.info("Hermes not installed — migrating config only")

        # Setup Hermes directory
        self._ensure_hermes_dir()

        # Idempotency check
        if not self._check_previous_migration():
            return False

        if not self.dry_run:
            self._backup_hermes()

        # Load configs
        oc_config = self._load_openclaw_config()
        if not oc_config:
            return False

        # Select agent if not specified
        if not self.agent_id:
            self.agent_id = self.select_agent(oc_config)
            if not self.agent_id:
                return False

        # Stop OpenClaw before migrating
        self.results.append(self.stop_openclaw(oc_config))

        hermes_config = self._load_hermes_config()

        # Define migration steps grouped by category
        migration_steps = [
            (
                "Persona & Memory",
                "Migrate SOUL.md, MEMORY.md, and workspace files",
                [
                    lambda: self.migrate_soul(),
                    lambda: self.migrate_memory(),
                    lambda: self.migrate_workspace_files(),
                    lambda: self.migrate_heartbeat(),
                ],
            ),
            (
                "Channels",
                "Migrate channel bindings (Telegram, Slack, Discord, etc.)",
                [
                    lambda: self.migrate_channels(oc_config, hermes_config),
                ],
            ),
            (
                "Models",
                "Migrate model and provider configuration",
                [
                    lambda: self.migrate_models(oc_config, hermes_config),
                ],
            ),
            (
                "Advanced Config",
                "Migrate compaction, concurrency, session settings",
                [
                    lambda: self.migrate_advanced_config(oc_config, hermes_config),
                ],
            ),
            (
                "Credentials & Keys",
                "Copy API keys and tokens to Hermes .env",
                [
                    lambda: self.migrate_env_template(oc_config),
                    lambda: self.migrate_credentials(oc_config),
                ],
            ),
            (
                "Documentation",
                "Generate reference docs for agents, channels, and infrastructure",
                [
                    lambda: self.migrate_agents(oc_config),
                    lambda: self.migrate_channel_details(oc_config),
                    lambda: self.migrate_infrastructure(oc_config),
                ],
            ),
        ]

        try:
            config_changed = False
            for name, description, steps in migration_steps:
                if not self._prompt_step(name, description):
                    self.logger.debug(f"Skipped: {name}")
                    continue
                for step in steps:
                    self.results.append(step())
                if name in ("Channels", "Models", "Advanced Config"):
                    config_changed = True

            # Save config incrementally after config mutations
            if not self.dry_run and config_changed:
                self._save_hermes_config(hermes_config)

        except Exception as e:
            self.logger.error(f"Migration failed: {e}")
            if not self.dry_run:
                self._rollback()
            return False

        # Print summary
        self._print_summary()

        # Clean up old backups
        if not self.dry_run:
            self._cleanup_old_backups()

        # Start Hermes
        if self.auto_start:
            self.results.append(self.start_hermes())

        success = all(r.success or not r.items_migrated for r in self.results)
        if not success and not self.dry_run:
            self._rollback()
        return success

    def _print_summary(self):
        """Print migration summary."""
        print("\n  " + "=" * 50)
        print("  MIGRATION SUMMARY")
        print("  " + "=" * 50)
        print(f"  Agent: {self.agent_id}")
        print()

        for result in self.results:
            if result.items_migrated:
                icon = "[OK]" if result.success else "[!!]"
                items = ", ".join(result.items_migrated)
                print(f"    {icon} {result.message}: {items}")

        warnings = [w for r in self.results for w in r.warnings]
        if warnings:
            print("\n  WARNINGS:")
            for w in warnings:
                print(f"     {w}")

        print("\n  " + "-" * 50)
        print("  NEXT STEPS:")
        print("  " + "-" * 50)
        print("    1. Hermes should be starting automatically")
        print("    2. Test your channels (Telegram, Slack, etc.)")
        print("    3. Review ~/.hermes/SOUL.md if you want to adjust persona")
        print("    4. Check ~/.hermes/memories/ for migrated files")
        print("    5. Verify ~/.hermes/config.yaml and ~/.hermes/.env")
        print("    6. See memories/openclaw_channels.md for channel details")
        print("  " + "=" * 50 + "\n")
