#!/usr/bin/env python3
"""Standalone migration script for Omnigent → AgentNexus.

Can be run independently or as part of the AgentNexus CLI.

Usage:
    python migrate_from_omnigent.py
    # or
    agentnexus migrate
"""

import sys
from pathlib import Path

# Add parent directory to path for standalone execution
sys.path.insert(0, str(Path(__file__).parent.parent))

from agentnexus.migration.from_omnigent import (
    get_old_config_dir,
    get_new_config_dir,
    migrate_config_directory,
    should_migrate,
)


def main():
    """Run the migration interactively."""
    print("=" * 60)
    print("  AgentNexus Configuration Migration")
    print("  From: Omnigent → AgentNexus")
    print("=" * 60)
    print()

    old_dir = get_old_config_dir()
    new_dir = get_new_config_dir()

    if not old_dir.exists():
        print(f"ℹ️  No Omnigent configuration found at {old_dir}")
        print("   Nothing to migrate.")
        return 0

    if new_dir.exists():
        print(f"✅ AgentNexus configuration already exists at {new_dir}")
        print(f"   Old configuration still present at {old_dir}")
        print()
        print("   You can manually remove the old directory:")
        print(f"   rm -rf {old_dir}")
        return 0

    if not should_migrate():
        print("ℹ️  No migration needed.")
        return 0

    # Show what will be migrated
    print(f"Found Omnigent configuration at: {old_dir}")
    print(f"Will migrate to: {new_dir}")
    print()

    # Count files to migrate
    try:
        file_count = sum(1 for _ in old_dir.rglob("*") if _.is_file())
        dir_size = sum(f.stat().st_size for f in old_dir.rglob("*") if f.is_file())
        dir_size_mb = dir_size / (1024 * 1024)

        print(f"   Files: {file_count}")
        print(f"   Size: {dir_size_mb:.2f} MB")
        print()
    except Exception:
        pass

    # Confirm
    response = input("Proceed with migration? [Y/n]: ").strip().lower()
    if response and response not in ("y", "yes"):
        print("❌ Migration cancelled.")
        return 1

    print()

    # Perform migration
    success = migrate_config_directory()

    if success:
        print()
        print("=" * 60)
        print("  ✨ Migration Complete!")
        print("=" * 60)
        return 0
    else:
        print()
        print("=" * 60)
        print("  ❌ Migration Failed")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
