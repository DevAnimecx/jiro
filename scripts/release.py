#!/usr/bin/env python3
"""Release script for Jiro v0.2.1.

Usage:
    python scripts/release.py --dry-run    # Preview what would happen
    python scripts/release.py --build      # Build distribution packages
    python scripts/release.py --publish    # Build and publish to PyPI
    python scripts/release.py --github     # Create GitHub release
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)
    return result


def get_version() -> str:
    """Get version from pyproject.toml."""
    pyproject = Path("pyproject.toml").read_text()
    for line in pyproject.split("\n"):
        if line.startswith("version"):
            return line.split('"')[1]
    raise ValueError("Version not found in pyproject.toml")


def clean_build():
    """Clean build artifacts."""
    print("\n[1/6] Cleaning build artifacts...")
    for dir_name in ["build", "dist", "*.egg-info"]:
        for p in Path(".").glob(dir_name):
            if p.is_dir():
                shutil.rmtree(p)
                print(f"  Removed {p}")


def build_distribution():
    """Build sdist and wheel."""
    print("\n[2/6] Building distribution packages...")
    run([sys.executable, "-m", "build"])
    print("  Built packages in dist/")


def check_distribution():
    """Check the built distribution."""
    print("\n[3/6] Checking distribution...")
    run([sys.executable, "-m", "twine", "check", "dist/*"])


def create_git_tag(version: str, dry_run: bool = False):
    """Create git tag for the release."""
    print(f"\n[4/6] Creating git tag v{version}...")
    if dry_run:
        print(f"  Would create tag: v{version}")
        return
    
    # Check if tag already exists
    result = run(["git", "tag", "-l", f"v{version}"], check=False)
    if f"v{version}" in result.stdout:
        print(f"  Tag v{version} already exists, skipping")
        return
    
    run(["git", "tag", "-a", f"v{version}", "-m", f"Release v{version}"])
    print(f"  Created tag v{version}")


def publish_to_pypi(dry_run: bool = False):
    """Publish to PyPI."""
    print("\n[5/6] Publishing to PyPI...")
    if dry_run:
        print("  Would publish to PyPI")
        return
    
    run([sys.executable, "-m", "twine", "upload", "dist/*"])


def create_github_release(version: str, dry_run: bool = False):
    """Create GitHub release using gh CLI."""
    print(f"\n[6/6] Creating GitHub release v{version}...")
    if dry_run:
        print(f"  Would create GitHub release v{version}")
        return
    
    # Check if gh is available
    result = run(["gh", "--version"], check=False)
    if result.returncode != 0:
        print("  WARNING: gh CLI not found, skipping GitHub release")
        print("  Install: https://cli.github.com/")
        return
    
    # Create release
    run([
        "gh", "release", "create",
        f"v{version}",
        "--title", f"v{version}",
        "--notes-file", "CHANGELOG.md",
        "dist/*",
    ])
    print(f"  Created GitHub release v{version}")


def main():
    parser = argparse.ArgumentParser(description="Release Jiro")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--build", action="store_true", help="Build distribution packages")
    parser.add_argument("--publish", action="store_true", help="Build and publish to PyPI")
    parser.add_argument("--github", action="store_true", help="Create GitHub release")
    parser.add_argument("--all", action="store_true", help="Do everything")
    args = parser.parse_args()
    
    if not any([args.build, args.publish, args.github, args.all]):
        parser.print_help()
        return
    
    version = get_version()
    print(f"Jiro Release v{version}")
    print("=" * 40)
    
    if args.all or args.build or args.publish:
        clean_build()
        build_distribution()
        check_distribution()
    
    if args.all or args.publish:
        create_git_tag(version, args.dry_run)
        publish_to_pypi(args.dry_run)
    
    if args.all or args.github:
        create_github_release(version, args.dry_run)
    
    print("\n" + "=" * 40)
    print(f"Release v{version} complete!")


if __name__ == "__main__":
    main()