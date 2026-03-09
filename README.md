# hermes-migrate

One-click migration from [OpenClaw](https://github.com/openclaw/openclaw) to [Hermes](https://github.com/nousresearch/hermes).

Stop OpenClaw, migrate everything, start Hermes — your channels keep working.

## Install

```bash
pip install hermes-migrate
```

Or with [pipx](https://pypa.github.io/pipx/):

```bash
pipx install hermes-migrate
```

### From source (no pip/venv needed)

For locked-down systems (Debian/Ubuntu with externally-managed Python):

```bash
git clone https://github.com/raulvidis/hermes-migrate.git
cd hermes-migrate
./install.sh
hermes-migrate --dry-run -v   # preview
hermes-migrate -v             # full migration
```

Only requires Python 3.9+ and PyYAML (usually pre-installed).

## Quick Start

```bash
# Full end-to-end migration
hermes-migrate

# Preview what would happen (no changes)
hermes-migrate --dry-run

# Migrate a specific agent from a multi-agent setup
hermes-migrate --agent cleo

# Verbose output
hermes-migrate -v
```

That's it. The tool will:

1. Stop OpenClaw processes
2. Auto-install Hermes if not present
3. Migrate your persona, memories, and workspace files
4. Write working credentials to `~/.hermes/.env`
5. Generate `config.yaml` with model and channel settings
6. Start Hermes in the background

After it finishes, your existing channels (Telegram, Slack, Discord, WhatsApp) should just work.

## What Gets Migrated

### Files

| OpenClaw | Hermes |
|----------|--------|
| `workspace/SOUL.md` | `~/.hermes/SOUL.md` |
| `workspace/MEMORY.md` | `~/.hermes/memories/MEMORY.md` |
| `workspace/USER.md` | `~/.hermes/memories/USER.md` |
| `workspace/HEARTBEAT.md` | `~/.hermes/memories/HEARTBEAT.md` |
| `workspace/IDENTITY.md` | `~/.hermes/memories/identity.md` |
| `workspace/AGENTS.md` | `~/.hermes/memories/agents_config.md` |
| `workspace/TOOLS.md` | `~/.hermes/memories/tools_config.md` |
| `workspace/memory/*.md` | `~/.hermes/memories/openclaw_archive/` |

### Configuration

| OpenClaw | Hermes |
|----------|--------|
| Agent model (primary + fallbacks) | `config.yaml` model settings |
| Channel bindings | `config.yaml` platform_toolsets |
| Compaction mode | compression settings |
| maxConcurrent | code_execution.max_tool_calls |
| subagents.maxConcurrent | delegation.max_iterations |
| Session retention / cron | session_reset settings |

### Credentials

Bot tokens, API keys, and auth tokens are copied to `~/.hermes/.env` (chmod 600):

- Telegram bot tokens (per-agent in multi-agent setups)
- Slack bot/app tokens
- Discord bot tokens
- WhatsApp config
- Embedding/memory search API keys (e.g., Gemini)
- Gateway auth tokens
- Custom model provider API keys and base URLs
- Allowed user lists (Telegram, Slack)

### Documentation

The tool also generates reference docs in `~/.hermes/memories/`:

- `openclaw_agents.md` — multi-agent setup documentation
- `openclaw_channels.md` — per-channel account details (tokens redacted)
- `openclaw_infrastructure.md` — gateway, hooks, plugins, custom providers

## Multi-Agent Setups

OpenClaw supports multiple agents with different models and channel bindings. When you run the migration:

1. The tool lists all agents with their models and channels
2. You pick which one to migrate to this Hermes instance
3. Only that agent's config, channels, and credentials are migrated

```
  Available OpenClaw agents:

    [1] nora
        Model: openai/gpt-5
        Channels: slack:nora

    [2] cleo
        Model: anthropic/claude-haiku-4-5
        Channels: telegram:cleo

    [3] hank
        Model: zai/glm-5
        Channels: telegram:hank

  Select agent to migrate [1-3]:
```

To migrate multiple agents, use separate Hermes instances:

```bash
hermes-migrate --agent cleo
HERMES_HOME=~/.hermes-hank hermes-migrate --agent hank
```

## CLI Reference

```
usage: hermes-migrate [-h] [--dry-run] [-v] [-a AGENT_ID] [--no-install]
                          [--no-start] [--version]

options:
  --dry-run          Preview changes without writing files
  -v, --verbose      Enable verbose output
  -a, --agent ID     Specify agent to migrate (skips interactive prompt)
  --no-install       Skip automatic Hermes installation
  --no-start         Don't start Hermes after migration
  --version          Show version
```

## Post-Migration

If everything went smoothly, Hermes is already running and your channels are live. To verify:

1. Send a message through your old channel (Telegram, Slack, etc.)
2. Check `~/.hermes/.env` has the right credentials
3. Check `~/.hermes/config.yaml` for model and channel settings
4. Review `~/.hermes/SOUL.md` to adjust your persona
5. Browse `~/.hermes/memories/` for migrated memories and reference docs

If Hermes didn't auto-start (e.g., CLI not in PATH after fresh install):

```bash
source ~/.bashrc
hermes
```

## Development

```bash
git clone https://github.com/raulvidis/hermes-migrate
cd hermes-migrate
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v

# Format
black hermes_migrate/ tests/

# Lint
ruff check .
```

### Project Structure

```
hermes_migrate/
  __init__.py        # Package version
  cli.py             # CLI entry point
  migrate.py         # All migration logic
tests/
  conftest.py        # Shared fixtures
  test_cli.py        # CLI argument parsing tests
  test_migrate.py    # Migration logic tests (90+ tests)
  test_security.py   # Redaction and security tests
```

## Requirements

- Python 3.9+
- PyYAML (installed automatically)
- Works on Linux, macOS, and Windows

## License

MIT
