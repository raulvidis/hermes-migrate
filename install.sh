#!/usr/bin/env bash
# Install hermes-migrate from a git clone (no pip required).
# Usage: git clone https://github.com/raulvidis/hermes-migrate && cd hermes-migrate && ./install.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"

# Ensure wrapper is executable
chmod +x "$REPO_DIR/hermes-migrate"

# Create ~/.local/bin and symlink
mkdir -p "$BIN_DIR"
ln -sf "$REPO_DIR/hermes-migrate" "$BIN_DIR/hermes-migrate"

# Add to PATH if not already there
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    SHELL_RC=""
    if [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -f "$HOME/.profile" ]; then
        SHELL_RC="$HOME/.profile"
    fi

    if [ -n "$SHELL_RC" ]; then
        if ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            echo "  Added ~/.local/bin to PATH in $(basename "$SHELL_RC")"
        fi
    fi
    export PATH="$BIN_DIR:$PATH"
fi

echo "  hermes-migrate installed: $(hermes-migrate --version 2>/dev/null || $BIN_DIR/hermes-migrate --version)"
echo "  Run 'hermes-migrate --dry-run -v' to preview migration"
echo ""
echo "  If the command is not found, run:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
