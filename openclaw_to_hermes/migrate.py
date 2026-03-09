"""
Migration logic for OpenClaw to Hermes conversion.
"""

import os
import re
import json
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field


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

# Sensitive values to redact in logs
REDACT_VALUE = "***REDACTED***"


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
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.messages: List[Tuple[str, str]] = []  # (level, message)
    
    def _redact(self, msg: str) -> str:
        """Redact sensitive values from message."""
        patterns = [
            (r'(botToken["\s:=]+)["\']?[\w\-:]+', r'\1' + REDACT_VALUE),
            (r'(apiKey["\s:=]+)["\']?[\w\-]+', r'\1' + REDACT_VALUE),
            (r'(token["\s:=]+)["\']?[\w\-]+', r'\1' + REDACT_VALUE),
            (r'(\d{10,}:[\w\-]+)', REDACT_VALUE),  # Telegram bot tokens
            (r'(AIza[\w\-]+)', REDACT_VALUE),  # Google API keys
            (r'(sk-[\w\-]+)', REDACT_VALUE),  # OpenAI keys
            (r'(xox[baprs]-[\w\-]+)', REDACT_VALUE),  # Slack tokens
        ]
        for pattern, replacement in patterns:
            msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
        return msg
    
    def info(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("INFO", msg))
        print(f"  {msg}")
    
    def success(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("SUCCESS", msg))
        print(f"  {msg}")
    
    def warn(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("WARN", msg))
        print(f"  {msg}")
    
    def error(self, msg: str):
        msg = self._redact(msg)
        self.messages.append(("ERROR", msg))
        print(f"  {msg}")
    
    def debug(self, msg: str):
        if self.verbose:
            msg = self._redact(msg)
            self.messages.append(("DEBUG", msg))
            print(f"  [DEBUG] {msg}")


def is_sensitive_field(field_name: str) -> bool:
    """Check if a field name contains sensitive patterns."""
    field_lower = field_name.lower()
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
                redact_sensitive_fields(item, f"{full_path}[{i}]")
                if isinstance(item, dict) else item
                for i, item in enumerate(value)
            ]
        else:
            result[key] = value
    
    return result


class HermesInstaller:
    """Handles Hermes installation and detection."""
    
    HERMES_INSTALL_URL = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh"
    
    def __init__(self, logger: MigrationLogger):
        self.logger = logger
    
    def is_hermes_installed(self) -> bool:
        """Check if Hermes CLI is available."""
        try:
            result = subprocess.run(
                ["hermes", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def is_hermes_dir_exists(self) -> bool:
        """Check if Hermes directory exists."""
        return HERMES_DIR.exists()
    
    def install_hermes(self) -> bool:
        """Install Hermes using the official installer."""
        self.logger.info("Hermes not found. Starting installation...")
        self.logger.info(f"Using official installer from NousResearch/hermes-agent")
        
        # Use the official installer
        try:
            self.logger.info("Running: curl -fsSL ... | bash")
            result = subprocess.run(
                f"curl -fsSL {self.HERMES_INSTALL_URL} | bash",
                shell=True,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes
            )
            
            if result.returncode == 0:
                self.logger.success("Hermes installed successfully!")
                self.logger.info("Run 'source ~/.bashrc' or restart your shell, then 'hermes setup'")
                return True
            else:
                self.logger.error(f"Installation failed: {result.stderr}")
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
    
    def __init__(self, dry_run: bool = False, verbose: bool = False, agent_id: Optional[str] = None):
        self.dry_run = dry_run
        self.verbose = verbose
        self.agent_id = agent_id
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
        
        for f in ["config.yaml", "SOUL.md"]:
            src = HERMES_DIR / f
            if src.exists():
                shutil.copy2(src, backup_dir / f)
        
        mem_dir = HERMES_DIR / "memories"
        if mem_dir.exists():
            shutil.copytree(mem_dir, backup_dir / "memories")
        
        self.logger.info(f"Created backup at {backup_dir}")
        return backup_dir
    
    def _load_openclaw_config(self) -> Optional[Dict[str, Any]]:
        """Load OpenClaw configuration."""
        config_path = OPENCLAW_DIR / "openclaw.json"
        if not config_path.exists():
            self.logger.error(f"OpenClaw config not found: {config_path}")
            return None
        
        with open(config_path) as f:
            config = json.load(f)
        
        self.logger.debug(f"Loaded OpenClaw config with keys: {list(config.keys())}")
        return config
    
    def _load_hermes_config(self) -> Dict[str, Any]:
        """Load Hermes configuration."""
        config_path = HERMES_DIR / "config.yaml"
        if not config_path.exists():
            return {}
        
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            self.logger.debug("PyYAML not installed, skipping config load")
            return {}
    
    def _save_hermes_config(self, config: Dict[str, Any]):
        """Save Hermes configuration."""
        if self.dry_run:
            self.logger.debug("Would save Hermes config")
            return
        
        try:
            import yaml
            config_path = HERMES_DIR / "config.yaml"
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            self.logger.debug("Saved Hermes config")
        except ImportError:
            self.logger.warn("PyYAML not installed, config not saved")
    
    def get_available_agents(self, oc_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get list of available agents from OpenClaw config."""
        agents = []
        
        agent_list = oc_config.get("agents", {}).get("list", [])
        for agent in agent_list:
            agents.append({
                "id": agent.get("id", "unknown"),
                "model": agent.get("model", "default"),
                "workspace": agent.get("workspace", ""),
                "source": "agents.list"
            })
        
        if not agents:
            defaults = oc_config.get("agents", {}).get("defaults", {})
            if defaults:
                agents.append({
                    "id": "main",
                    "model": defaults.get("model", {}).get("primary", "default"),
                    "workspace": defaults.get("workspace", ""),
                    "source": "agents.defaults"
                })
        
        return agents
    
    def get_agent_bindings(self, oc_config: Dict[str, Any], agent_id: str) -> List[Dict[str, Any]]:
        """Get channel bindings for a specific agent."""
        bindings = oc_config.get("bindings", [])
        agent_bindings = []
        
        for binding in bindings:
            if binding.get("agentId") == agent_id:
                match = binding.get("match", {})
                agent_bindings.append({
                    "channel": match.get("channel", "unknown"),
                    "account_id": match.get("accountId", "default"),
                })
        
        return agent_bindings
    
    def get_agent_channels(self, oc_config: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
        """Get channel configs specific to this agent's bindings."""
        bindings = self.get_agent_bindings(oc_config, agent_id)
        channels = oc_config.get("channels", {})
        agent_channels = {}
        
        for binding in bindings:
            channel = binding["channel"]
            account_id = binding["account_id"]
            
            if channel in channels:
                channel_config = channels[channel]
                
                if "accounts" in channel_config:
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
        
        return agent_channels
    
    def select_agent(self, oc_config: Dict[str, Any]) -> Optional[str]:
        """Prompt user to select an agent to migrate."""
        agents = self.get_available_agents(oc_config)
        
        if not agents:
            self.logger.warn("No agents found in OpenClaw config")
            return None
        
        if len(agents) == 1:
            agent = agents[0]
            self.logger.info(f"Found single agent: {agent['id']}")
            return agent["id"]
        
        print("\n  Available OpenClaw agents:\n")
        for i, agent in enumerate(agents, 1):
            bindings = self.get_agent_bindings(oc_config, agent["id"])
            binding_str = ", ".join([f"{b['channel']}:{b['account_id']}" for b in bindings]) or "no bindings"
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
    
    def migrate_soul(self) -> MigrationResult:
        """Migrate SOUL.md (persona)."""
        result = MigrationResult(success=False, message="SOUL.md migration")
        src = OPENCLAW_DIR / "workspace" / "SOUL.md"
        dst = HERMES_DIR / "SOUL.md"
        
        if not src.exists():
            result.message = "No SOUL.md found in OpenClaw workspace"
            result.warnings.append("SOUL.md not found")
            return result
        
        with open(src) as f:
            content = f.read()
        
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
            with open(dst, 'w') as f:
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
            with open(src) as f:
                content = f.read()
            
            dst = mem_dir / "MEMORY.md"
            migrated = f"""Migrated from OpenClaw on {datetime.now().strftime('%Y-%m-%d %H:%M')}
Source: ~/.openclaw/workspace/MEMORY.md
Agent: {self.agent_id or 'default'}

{content}
"""
            if self.dry_run:
                self.logger.debug(f"Would migrate MEMORY.md to {dst}")
            else:
                with open(dst, 'w') as f:
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
                with open(dst, 'w') as f:
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
            "TOOLS.md": "tools_config.md"
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
    
    def migrate_channels(self, oc_config: Dict[str, Any], hermes_config: Dict[str, Any]) -> MigrationResult:
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
        
        result.items_migrated = enabled_channels
        result.warnings.append("You'll need to re-authenticate with each channel (tokens not migrated)")
        self.logger.success(f"Enabled {len(enabled_channels)} channel toolsets for agent '{self.agent_id}'")
        self.logger.warn("NOTE: Re-authenticate with channels (tokens not migrated)")
        
        result.success = True
        return result
    
    def migrate_models(self, oc_config: Dict[str, Any], hermes_config: Dict[str, Any]) -> MigrationResult:
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
            
            if "model" not in hermes_config:
                hermes_config["model"] = {}
            
            hermes_config["model"]["default"] = primary
            
            if model_config.get("fallbacks"):
                hermes_config["model"]["fallbacks"] = model_config["fallbacks"]
            
            self.logger.success(f"Set model for '{self.agent_id}': {primary}")
            result.items_migrated.append(f"model: {primary}")
        
        providers = oc_config.get("models", {}).get("providers", {})
        for name, provider in providers.items():
            self.logger.debug(f"Found custom provider: {name} ({provider.get('api', 'unknown')})")
            result.warnings.append(f"Custom provider '{name}' noted - manual config may be needed")
        
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
            with open(mem_dir / "openclaw_agents.md", 'w') as f:
                f.write(doc)
        
        self.logger.success(f"Documented {len(agents_list)} agents, {len(bindings)} bindings")
        result.items_migrated.append("Multi-agent documentation")
        result.success = True
        return result
    
    def run(self) -> bool:
        """Run the full migration."""
        print("\n  OpenClaw  Hermes Migration Tool")
        print("  " + "-" * 38)
        
        # Check OpenClaw exists
        if not OPENCLAW_DIR.exists():
            self.logger.error(f"OpenClaw directory not found: {OPENCLAW_DIR}")
            return False
        
        # Ensure Hermes is installed
        installer = HermesInstaller(self.logger)
        if not installer.ensure_hermes_installed():
            self.logger.error("Hermes installation required. Please install Hermes first.")
            return False
        
        # Setup Hermes directory
        self._ensure_hermes_dir()
        
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
        
        hermes_config = self._load_hermes_config()
        
        # Run migrations
        self.results.append(self.migrate_soul())
        self.results.append(self.migrate_memory())
        self.results.append(self.migrate_workspace_files())
        self.results.append(self.migrate_channels(oc_config, hermes_config))
        self.results.append(self.migrate_models(oc_config, hermes_config))
        self.results.append(self.migrate_agents(oc_config))
        
        # Save Hermes config
        if not self.dry_run:
            self._save_hermes_config(hermes_config)
        
        # Print summary
        self._print_summary()
        
        return all(r.success or not r.items_migrated for r in self.results)
    
    def _print_summary(self):
        """Print migration summary."""
        print("\n  " + "=" * 50)
        print("  MIGRATION SUMMARY")
        print("  " + "=" * 50)
        print(f"  Agent: {self.agent_id}")
        print()
        
        for result in self.results:
            if result.items_migrated:
                icon = "" if result.success else ""
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
        print("    1. Review ~/.hermes/SOUL.md (your persona)")
        print("    2. Check ~/.hermes/memories/ for migrated memories")
        print("    3. Re-authenticate with channels (Slack, Discord, etc.)")
        print("    4. Verify model settings in ~/.hermes/config.yaml")
        print("    5. Run 'hermes' to test the migrated setup")
        print("  " + "=" * 50 + "\n")
