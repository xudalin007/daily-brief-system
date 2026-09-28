#!/usr/bin/env python3
"""Portable paths for the daily briefing, shared by the skill and scripts."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path


def hermes_home() -> Path:
    return Path(os.path.expandvars(os.path.expanduser(
        os.environ.get("HERMES_HOME", "~/.hermes")))).resolve()


def report_dir() -> Path:
    configured = os.environ.get("DAILY_BRIEF_REPORT_DIR")
    if not configured:
        config = hermes_home() / "daily_brief.json"
        if config.is_file():
            configured = json.loads(config.read_text(encoding="utf-8")).get("report_dir")
    path = configured or str(hermes_home() / "reports")
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def report_paths(day: str) -> dict[str, str]:
    date.fromisoformat(day)  # Reject accidental placeholders and path traversal.
    root = report_dir()
    stem = f"每日简报_{day}"
    return {"directory": str(root), "markdown": str(root / f"{stem}.md"),
            "html": str(root / f"{stem}.html")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the report paths for a date")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--field", choices=("directory", "markdown", "html"))
    parser.add_argument("--ensure", action="store_true", help="Create the report directory")
    args = parser.parse_args()
    paths = report_paths(args.date)
    if args.ensure:
        Path(paths["directory"]).mkdir(parents=True, exist_ok=True)
    print(paths[args.field] if args.field else json.dumps(paths, ensure_ascii=False))


if __name__ == "__main__":
    main()
