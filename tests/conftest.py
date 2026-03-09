"""Shared test fixtures for openclaw-to-hermes tests."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_openclaw(tmp_path):
    """Create a temporary OpenClaw directory with sample config."""
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    workspace = oc_dir / "workspace"
    workspace.mkdir()
    return oc_dir


@pytest.fixture
def tmp_hermes(tmp_path):
    """Create a temporary Hermes directory."""
    h_dir = tmp_path / ".hermes"
    h_dir.mkdir()
    (h_dir / "memories").mkdir()
    return h_dir


@pytest.fixture
def sample_openclaw_config():
    """Sample OpenClaw config with multi-agent setup."""
    return {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "anthropic/claude-sonnet-4-20250514",
                    "fallbacks": ["openai/gpt-4o"]
                },
                "workspace": "default"
            },
            "list": [
                {
                    "id": "nora",
                    "model": "openai/gpt-5",
                    "workspace": "default"
                },
                {
                    "id": "cleo",
                    "model": "anthropic/claude-haiku-4-5",
                    "workspace": "default"
                },
                {
                    "id": "hank",
                    "model": "zai/glm-5",
                    "workspace": "default"
                }
            ]
        },
        "channels": {
            "telegram": {
                "enabled": True,
                "accounts": {
                    "cleo": {"botToken": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"},
                    "hank": {"botToken": "987654321:ZYXwvuTSRqpoNMLkjiHGFedcba"}
                }
            },
            "slack": {
                "enabled": True,
                "accounts": {
                    "nora": {"accessToken": "xoxb-fake-slack-token-12345"}
                }
            }
        },
        "bindings": [
            {"agentId": "cleo", "match": {"channel": "telegram", "accountId": "cleo"}},
            {"agentId": "hank", "match": {"channel": "telegram", "accountId": "hank"}},
            {"agentId": "nora", "match": {"channel": "slack", "accountId": "nora"}}
        ],
        "models": {
            "providers": {
                "custom-llm": {
                    "api": "openai-compatible",
                    "baseUrl": "https://my-llm.example.com/v1"
                }
            }
        },
        "acp": {
            "enabled": True,
            "backend": "docker",
            "defaultAgent": "nora",
            "allowedAgents": ["nora", "cleo"]
        }
    }


@pytest.fixture
def single_agent_config():
    """OpenClaw config with a single agent (no list)."""
    return {
        "agents": {
            "defaults": {
                "model": {
                    "primary": "anthropic/claude-sonnet-4-20250514"
                },
                "workspace": "default"
            }
        },
        "channels": {},
        "bindings": []
    }


@pytest.fixture
def openclaw_with_files(tmp_openclaw):
    """OpenClaw dir populated with workspace files."""
    workspace = tmp_openclaw / "workspace"

    (workspace / "SOUL.md").write_text("You are a helpful assistant named TestBot.")
    (workspace / "MEMORY.md").write_text("User prefers concise answers.")
    (workspace / "USER.md").write_text("Name: TestUser\nTimezone: UTC")
    (workspace / "IDENTITY.md").write_text("Identity config here.")
    (workspace / "AGENTS.md").write_text("Agent roles documented here.")
    (workspace / "TOOLS.md").write_text("Available tools listed here.")

    daily = workspace / "memory"
    daily.mkdir()
    (daily / "2026-03-01.md").write_text("Learned about user's project.")
    (daily / "2026-03-02.md").write_text("Discussed deployment strategy.")

    return tmp_openclaw
