# OpenClaw to Hermes Migration Tool

One-click migration from [OpenClaw](https://github.com/openclaw/openclaw) to [Hermes](https://github.com/nousresearch/hermes).

## Features

- **Agent Selection**: Interactive prompt to select which agent to migrate from multi-agent setups
- **Auto-Install Hermes**: Detects and optionally installs Hermes if not present
- **Channel Binding Migration**: Migrates channels specific to the selected agent
- **Model Configuration**: Preserves agent-specific model settings
- **Security-First**: Never copies tokens, API keys, or sensitive credentials

## What Gets Migrated

| OpenClaw | Hermes |
|----------|--------|
| `workspace/SOUL.md` | `~/.hermes/SOUL.md` |
| `workspace/MEMORY.md` | `~/.hermes/memories/MEMORY.md` |
| `workspace/USER.md` | `~/.hermes/memories/USER.md` |
| `workspace/memory/*.md` | `~/.hermes/memories/openclaw_archive/` |
| Agent's channel bindings | `config.yaml` platform_toolsets |
| Agent's model config | `config.yaml` model settings |
| Multi-agent setup | Documented in `memories/openclaw_agents.md` |

## Installation

```bash
pip install openclaw-to-hermes
```

Or with pipx:

```bash
pipx install openclaw-to-hermes
```

## Usage

```bash
# Interactive migration (prompts to select agent)
openclaw-to-hermes

# Migrate specific agent
openclaw-to-hermes --agent cleo

# Preview changes without writing
openclaw-to-hermes --dry-run

# Verbose output
openclaw-to-hermes -v

# Install Hermes if not present
openclaw-to-hermes --install-hermes
```

## Multi-Agent Setups

OpenClaw supports multiple agents with different models, channels, and configs. When you run the migration:

1. **Agent Selection**: The tool lists all available agents with their models and channel bindings
2. **Select One**: Choose which agent to migrate to this Hermes instance
3. **Targeted Migration**: Only that agent's config, channels, and model are migrated

Example output:
```
  Available OpenClaw agents:

    [1] nora
        Model: openai-codex/gpt-5.4
        Channels: no bindings

    [2] cleo
        Model: anthropic/claude-haiku-4-5
        Channels: telegram:cleo

    [3] hank
        Model: zai/glm-5
        Channels: telegram:hank

  Select agent to migrate [1-3]: 
```

### Migrating Multiple Agents

To migrate multiple agents, you'll need separate Hermes instances:

```bash
# Migrate agent 'cleo' to default Hermes
openclaw-to-hermes --agent cleo

# For another agent, use a different Hermes home
HERMES_HOME=~/.hermes-hank openclaw-to-hermes --agent hank
```

## What's NOT Migrated (Security)

For security, the following are **never** copied:
- API keys and tokens
- Bot tokens (Telegram, Slack, etc.)
- OAuth credentials
- User IDs / phone numbers
- Gateway auth tokens
- Any field matching sensitive patterns

You'll need to re-authenticate with channels after migration.

## Post-Migration Steps

1. Review `~/.hermes/SOUL.md` (your persona)
2. Check `~/.hermes/memories/` for migrated memories
3. Re-authenticate with channels (Slack, Discord, etc.)
4. Verify model settings in `~/.hermes/config.yaml`
5. Review `openclaw_agents.md` for multi-agent documentation
6. Run `hermes` to test the migrated setup

## Development

```bash
git clone https://github.com/nousresearch/openclaw-to-hermes
cd openclaw-to-hermes
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black openclaw_to_hermes/
```

## License

MIT
