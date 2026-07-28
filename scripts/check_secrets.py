#!/usr/bin/env python
"""Fail CI when common credential patterns appear in tracked source files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private key": re.compile(rb"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
    "provider API key": re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}
SKIP_SUFFIXES = {".pkl", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
    )
    return [PROJECT_ROOT / raw.decode("utf-8") for raw in output.split(b"\0") if raw]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(PROJECT_ROOT)
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        for name, pattern in PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{relative}: possible {name}")

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Secret scan passed: no common credential patterns in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
