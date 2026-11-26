"""Offline repository hygiene, sanitization, binary allowlist, and .gitignore guardrails."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_BINARY_EXTS = (
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.pdf",
    "*.zip",
    "*.tar*",
    "*.dylib",
    "*.so",
    "*.a",
    "*.ipa",
    "*.tipa",
    "*.deb",
    "*.bin",
    "*.p12",
    "*.car",
)

_FORBIDDEN_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"Dropbox/"),
    re.compile(r"\b192\.168\.(?!0\.0/16\b)\d+\.\d+\b"),
    re.compile(r"\b10\.(?!0\.0\.0/8\b)\d+\.\d+\.\d+\b"),
    re.compile(r"BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY"),
)


def test_zero_tracked_binaries() -> None:
    res = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", *_BINARY_EXTS],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [line for line in res.stdout.splitlines() if line]
    assert not tracked, f"Unexpected tracked binaries: {tracked}"


def test_sanitized_of_private_paths_ips_and_keys() -> None:
    hits: list[str] = []
    for rel in ("README.md", "README.zh.md", "NOTICE", "SECURITY.md", "CONTRIBUTING.md", "src", "scripts"):
        target = ROOT / rel
        paths = [target] if target.is_file() else sorted(target.rglob("*"))
        for p in paths:
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for pat in _FORBIDDEN_PATTERNS:
                    if pat.search(line):
                        hits.append(f"{p.relative_to(ROOT)}:{lineno}: {pat.pattern}")
    assert not hits, "Sanitization regression:\n" + "\n".join(hits)


def test_gitignore_self_consistent_without_global_excludes() -> None:
    for probe in (".env", ".DS_Store", ".venv/bin/python", ".omp/x", ".agents/x", "sample.ipa"):
        res = subprocess.run(
            ["git", "-C", str(ROOT), "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", probe],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f".gitignore failed to ignore {probe}: {res.stderr}"
