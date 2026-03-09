#!/usr/bin/env python3
"""
CLI entry point for OpenClaw to Hermes migration tool.
"""

import argparse
import sys
from pathlib import Path

from .migrate import OpenClawMigrator, HermesInstaller, MigrationLogger, HERMES_DIR, OPENCLAW_DIR


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="openclaw-to-hermes",
        description="One-click migration from OpenClaw to Hermes AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  openclaw-to-hermes              Run migration (prompts for agent)
  openclaw-to-hermes --agent cleo Migrate specific agent
  openclaw-to-hermes --dry-run    Preview changes without writing
  openclaw-to-hermes -v           Verbose output

For more info: https://github.com/nousresearch/openclaw-to-hermes
        """,
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    
    parser.add_argument(
        "-a", "--agent",
        dest="agent_id",
        help="Specify agent to migrate (skips prompt)",
    )
    
    parser.add_argument(
        "--install-hermes",
        action="store_true",
        help="Install Hermes if not found",
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )
    
    args = parser.parse_args()
    
    # Check for OpenClaw installation
    if not OPENCLAW_DIR.exists():
        print(f"\n  Error: OpenClaw directory not found at {OPENCLAW_DIR}")
        print("  Make sure OpenClaw is installed and configured.\n")
        sys.exit(1)
    
    # Check/install Hermes
    logger = MigrationLogger(args.verbose)
    installer = HermesInstaller(logger)
    
    if not installer.is_hermes_installed() and not installer.is_hermes_dir_exists():
        if args.install_hermes:
            if not installer.install_hermes():
                sys.exit(1)
        else:
            print(f"\n  Hermes not found at {HERMES_DIR}")
            print("  Run with --install-hermes to install automatically,")
            print("  or install manually: pip install hermes-agent\n")
            sys.exit(1)
    
    # Run migration
    migrator = OpenClawMigrator(
        dry_run=args.dry_run,
        verbose=args.verbose,
        agent_id=args.agent_id
    )
    success = migrator.run()
    
    if args.dry_run:
        print("\n  [DRY RUN] No files were modified.\n")
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
