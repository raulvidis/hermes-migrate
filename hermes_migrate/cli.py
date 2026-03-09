#!/usr/bin/env python3
"""
CLI entry point for OpenClaw to Hermes migration tool.
"""

import argparse
import sys
from pathlib import Path

from .migrate import OpenClawMigrator, HermesInstaller, MigrationLogger, HERMES_DIR, OPENCLAW_DIR


def _uninstall():
    """Remove hermes-migrate: symlink, repo clone, and pip package."""
    import shutil
    import subprocess

    removed = []

    # Remove ~/.local/bin symlink
    symlink = Path.home() / ".local" / "bin" / "hermes-migrate"
    if symlink.is_symlink() or symlink.exists():
        symlink.unlink()
        removed.append(str(symlink))

    # Remove the git clone (repo directory)
    # The wrapper script or __file__ tells us where the repo is
    repo_dir = Path(__file__).resolve().parent.parent
    if (repo_dir / ".git").exists() and (repo_dir / "hermes_migrate").is_dir():
        shutil.rmtree(repo_dir, ignore_errors=True)
        removed.append(str(repo_dir))

    # Try pip uninstall (for pip-installed users)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "hermes-migrate", "-y"],
            capture_output=True, timeout=30,
        )
        removed.append("pip package")
    except Exception:
        pass

    if removed:
        print(f"\n  Cleaned up: {', '.join(removed)}")
    print("  hermes-migrate has been removed. Your Hermes installation is untouched.\n")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="hermes-migrate",
        description="One-click migration from OpenClaw to Hermes AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  hermes-migrate              Full migration (installs Hermes, migrates, starts)
  hermes-migrate --agent cleo Migrate specific agent
  hermes-migrate --dry-run    Preview changes without writing
  hermes-migrate --no-start   Migrate but don't auto-start Hermes
  hermes-migrate -v           Verbose output

For more info: https://github.com/raulvidis/hermes-migrate
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
        "--no-install",
        action="store_true",
        help="Skip automatic Hermes installation if not found",
    )

    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Don't start Hermes after migration",
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
        if args.no_install:
            print(f"\n  Hermes not found at {HERMES_DIR}")
            print("  Remove --no-install to auto-install, or install manually.\n")
            sys.exit(1)
        else:
            print("\n  Hermes not found. Installing automatically...")
            if not installer.install_hermes():
                sys.exit(1)

    # Run migration
    migrator = OpenClawMigrator(
        dry_run=args.dry_run,
        verbose=args.verbose,
        agent_id=args.agent_id,
        auto_start=not args.no_start,
    )
    success = migrator.run()
    
    if args.dry_run:
        print("\n  [DRY RUN] No files were modified.\n")
        sys.exit(0)

    if not success:
        sys.exit(1)

    # Offer to uninstall hermes-migrate after successful migration
    print("")
    try:
        answer = input("  Migration complete. Uninstall hermes-migrate and clean up? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
        print("")

    if answer in ("y", "yes"):
        _uninstall()

    sys.exit(0)


if __name__ == "__main__":
    main()
