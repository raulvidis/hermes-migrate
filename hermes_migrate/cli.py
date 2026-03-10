#!/usr/bin/env python3
"""
CLI entry point for OpenClaw to Hermes migration tool.
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .migrate import HERMES_DIR, OPENCLAW_DIR, HermesInstaller, MigrationLogger, OpenClawMigrator


def _uninstall():
    """Remove hermes-migrate: symlink, pip package, and optionally the repo clone."""
    import subprocess

    removed = []

    # Remove ~/.local/bin symlink
    symlink = Path.home() / ".local" / "bin" / "hermes-migrate"
    if symlink.is_symlink() or symlink.exists():
        symlink.unlink()
        removed.append(str(symlink))

    # Try pip uninstall (for pip-installed users)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "hermes-migrate", "-y"],
            capture_output=True,
            timeout=30,
        )
        removed.append("pip package")
    except Exception:
        pass

    # Offer to remove the git clone (only if running from one)
    repo_dir = Path(__file__).resolve().parent.parent
    if (repo_dir / ".git").exists() and (repo_dir / "hermes_migrate").is_dir():
        try:
            answer = input(f"  Also delete the repo clone at {repo_dir}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            # Use background rm -rf instead of shutil.rmtree to avoid
            # blocking for minutes on large repos with venvs/.git objects
            subprocess.Popen(
                ["rm", "-rf", str(repo_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            removed.append(str(repo_dir))

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
  hermes-migrate --force      Re-run migration (overwrite previous)
  hermes-migrate -q           Quiet mode for CI/scripting
  hermes-migrate -v           Verbose output
  hermes-migrate --restart-openclaw  Re-enable and start OpenClaw gateway

For more info: https://github.com/raulvidis/hermes-migrate
        """,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    parser.add_argument(
        "-a",
        "--agent",
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
        "--force",
        action="store_true",
        help="Overwrite previous migration (skip idempotency check)",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress non-error output (for CI/scripting)",
    )

    parser.add_argument(
        "--restart-openclaw",
        action="store_true",
        help="Re-enable and start the OpenClaw gateway systemd service",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    # Handle --restart-openclaw as a standalone command
    if args.restart_openclaw:
        import subprocess

        try:
            subprocess.run(
                ["systemctl", "--user", "enable", "openclaw-gateway"],
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(
                ["systemctl", "--user", "start", "openclaw-gateway"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                print("  OpenClaw gateway started successfully.")
            else:
                print(f"  Failed to start OpenClaw gateway: {result.stderr.strip()}")
                sys.exit(1)
        except FileNotFoundError:
            print("  systemctl not found — systemd is not available on this system.")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            print("  Timed out waiting for systemctl.")
            sys.exit(1)
        sys.exit(0)

    # Check for OpenClaw installation
    if not OPENCLAW_DIR.exists():
        print(f"\n  Error: OpenClaw directory not found at {OPENCLAW_DIR}")
        print("  Make sure OpenClaw is installed and configured.\n")
        sys.exit(1)

    # Check/install Hermes
    logger = MigrationLogger(verbose=args.verbose if not args.quiet else False, quiet=args.quiet)
    installer = HermesInstaller(logger)

    if not installer.is_hermes_installed() and not installer.is_hermes_dir_exists():
        if args.dry_run:
            logger.info("Hermes not installed (dry run — skipping install)")
        elif args.no_install:
            print(f"\n  Hermes not found at {HERMES_DIR}")
            print("  Remove --no-install to auto-install, or install manually.\n")
            sys.exit(1)
        else:
            print(f"\n  Hermes not found at {HERMES_DIR}")
            print("  Hermes needs to be installed before migration.\n")
            try:
                answer = input("  Do you want to configure Hermes yourself? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
                print("")
            if answer in ("y", "yes"):
                # Run installer interactively — user handles all prompts
                if not installer.install_hermes(interactive=True):
                    sys.exit(1)
            else:
                # Run installer with auto-skip — accept all defaults
                print("  Installing Hermes with default settings...\n")
                if not installer.install_hermes(interactive=False):
                    print("\n  Installation failed. Creating directory for migration only...")
                    HERMES_DIR.mkdir(parents=True, exist_ok=True)
                    (HERMES_DIR / "memories").mkdir(parents=True, exist_ok=True)

    # Run migration
    migrator = OpenClawMigrator(
        dry_run=args.dry_run,
        verbose=args.verbose if not args.quiet else False,
        agent_id=args.agent_id,
        auto_start=not args.no_start,
        force=args.force,
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
        answer = (
            input("  Migration complete. Uninstall hermes-migrate and clean up? [y/N] ")
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        answer = ""
        print("")

    if answer in ("y", "yes"):
        _uninstall()

    sys.exit(0)


if __name__ == "__main__":
    main()
