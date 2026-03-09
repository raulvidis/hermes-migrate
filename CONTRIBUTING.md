# Contributing

Thanks for your interest in hermes-migrate!

## Setup

```bash
git clone https://github.com/raulvidis/hermes-migrate
cd hermes-migrate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

All tests must pass before submitting a PR. The test suite covers migration logic, CLI parsing, and security/redaction.

## Code Style

```bash
black openclaw_to_hermes/ tests/
ruff check .
```

## Submitting Changes

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add tests for new functionality
4. Run `pytest` and `black`/`ruff`
5. Open a PR with a clear description of the change

## Adding Support for New Channels

If OpenClaw or Hermes adds a new channel (e.g., Matrix, Signal):

1. Add the channel mapping in `migrate_channels()` (`channel_map` dict)
2. Add credential extraction in `migrate_credentials()`
3. Add a test in `tests/test_migrate.py`
4. Update the sample config fixture in `tests/conftest.py`

## Adding Support for New Config Fields

1. Map the field in `migrate_advanced_config()` or the relevant migration method
2. Add a test case
3. If the field contains secrets, ensure it's handled by `migrate_credentials()` and redacted in documentation methods

## Security

- Credentials go to `.env` only (never to config.yaml or markdown docs)
- All documentation/log output must use `redact_sensitive_fields()` or `MigrationLogger._redact()`
- Test any new sensitive field patterns in `tests/test_security.py`
