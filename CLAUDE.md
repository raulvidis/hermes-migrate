# CLAUDE.md - Project Guide

## What This Project Is

A CLI migration tool that converts an **OpenClaw** AI agent setup (`~/.openclaw/`) to a **Hermes** AI agent setup (`~/.hermes/`). It's a one-shot utility: run it once, and your agent persona, memories, channel bindings, and model config move over.

## Architecture

```
openclaw_to_hermes/
  __init__.py        # Package init, version = "1.0.0"
  cli.py             # argparse CLI entry point (main())
  migrate.py         # All migration logic (782 lines)
```

**Entry point:** `openclaw_to_hermes.cli:main`

### Key Classes (migrate.py)

- `OpenClawMigrator` - Orchestrates the full migration pipeline
- `HermesInstaller` - Detects/installs Hermes via official NousResearch installer
- `MigrationLogger` - Console logger with automatic secret redaction
- `MigrationResult` - Dataclass tracking success/warnings/errors per step

### Migration Pipeline (sequential)

1. `migrate_soul()` - Copy SOUL.md (persona file)
2. `migrate_memory()` - Copy MEMORY.md, USER.md, archive daily memories
3. `migrate_workspace_files()` - Copy IDENTITY.md, AGENTS.md, TOOLS.md
4. `migrate_channels()` - Map channel bindings to Hermes platform_toolsets
5. `migrate_models()` - Carry over model config (primary + fallbacks)
6. `migrate_agents()` - Document multi-agent setup in markdown

### File Paths

- OpenClaw source: `~/.openclaw/` (config in `openclaw.json`)
- Hermes target: `~/.hermes/` (config in `config.yaml`)
- Backups: `~/.hermes/backup_YYYYMMDD_HHMMSS/`

## Security Model

**Credentials are NEVER copied.** The tool:
- Skips any config field matching: token, api_key, secret, password, credential, auth, bot_token, access_token, private_key
- Redacts sensitive patterns in all log output (Telegram tokens, OpenAI keys, Slack tokens, Google API keys)
- Warns users to re-authenticate channels post-migration

## Dependencies

- `pyyaml>=6.0` - Read/write Hermes config.yaml

## Development Commands

```bash
pip install -e ".[dev]"   # Install with dev deps
pytest                     # Run tests
black openclaw_to_hermes/  # Format code
ruff check .               # Lint
```

## CLI Usage

```bash
openclaw-to-hermes                    # Interactive migration
openclaw-to-hermes --agent cleo       # Migrate specific agent
openclaw-to-hermes --dry-run          # Preview only
openclaw-to-hermes --dry-run -v       # Preview with debug output
openclaw-to-hermes --install-hermes   # Auto-install Hermes if missing
```

## Notes

- Python 3.9+ required
- Tests in `tests/` covering security, migration logic, and CLI
