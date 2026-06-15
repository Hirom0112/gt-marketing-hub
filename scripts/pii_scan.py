#!/usr/bin/env python3
"""PII / secret scanner — mechanical enforcement of the synthetic-data mandate.

Implements the CI gate from THREAT_MODEL.md §5 (C-SYN-2, C-SEC-3). Walks the
repo and FAILS (exit 1) when it finds, in data / fixture / code files:

  (a) the real-PII cluster signature (C-SYN-2): a personal name +
      ``household_income`` + a ZIP/geo value co-occurring in one data file —
      the exact shape the IDOR finding exposed.
  (b) real-looking SSNs or precise lat/long clusters.
  (c) secret-shaped strings: Supabase anon / service_role JWTs,
      ``sk-ant-...`` Anthropic keys, HubSpot tokens.

Calibration (THREAT_MODEL.md §5 — gate must not cry wolf):
  * Skipped dirs: ``.git``, ``.venv``, ``node_modules`` (+ caches, dist).
  * Skipped files: ``*.lock`` (incl. ``uv.lock``), binaries.
  * ALLOWED files (planning docs + env templates legitimately discuss the
    schema and carry PUBLIC business contact info / placeholders):
    every ``*.md`` and ``.env.example`` (any dir). These are not scanned for
    PII clusters or emails; they ARE still scanned for live secret material,
    because a real key in a doc is still a leak.
  * Synthetic markers (``@example.invalid``, ``<placeholder>``, ``<secret>``,
    angle-bracket placeholders) are recognised and never flagged.

Usage::

    python scripts/pii_scan.py            # scan the working tree; exit 0 clean
    python scripts/pii_scan.py PATH ...   # scan specific paths
    python scripts/pii_scan.py --self-test  # plant a fixture, prove it flags

Exit 0 = clean. Exit 1 = at least one finding (printed to stderr).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

# --------------------------------------------------------------------------- #
# Calibration: what we never walk into, and what is allowed by policy.
# --------------------------------------------------------------------------- #

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".uv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

# Lockfiles and obviously-binary extensions are never PII fixtures.
SKIP_SUFFIXES = {
    ".lock",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".ttf",
    ".woff",
    ".woff2",
    ".otf",
    ".exr",
    ".hdr",
    ".pdf",
    ".zip",
    ".gz",
    ".pyc",
    ".mp3",
    ".wav",
    ".mp4",
}
SKIP_FILENAMES = {"uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}

# Files allowed to discuss the schema / carry public contacts & placeholders.
# These are exempt from PII-cluster + email scanning, but NOT from secret
# scanning (a real key is a leak no matter where it lives).
ALLOWED_FROM_PII = {".env.example"}


def _is_allowed_from_pii(path: Path) -> bool:
    return path.suffix.lower() == ".md" or path.name in ALLOWED_FROM_PII


# The cluster signature (name + household_income + ZIP in ONE file) describes a
# DATA row, not source code. Restricting it to data/config extensions stops the
# scanner (and other source modules that legitimately name these fields) from
# self-flagging on prose, while every fixture/seed format is still covered.
DATA_SUFFIXES = {
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
    ".txt",
    ".xml",
    ".parquet",
}


def _is_data_file(path: Path) -> bool:
    return path.suffix.lower() in DATA_SUFFIXES or path.suffix == ""


# Placeholder / synthetic markers — presence on a line neutralises a match.
PLACEHOLDER_RE = re.compile(
    r"@example\.(invalid|com)\b"
    r"|example\.invalid"
    r"|<[^>]{1,40}>"  # <secret>, <ref>, <public anon key>, etc.
    r"|\bplaceholder\b"
    r"|\bredacted\b"
    r"|\bxxx+\b"
    r"|\bYOUR_[A-Z_]+\b"
    r"|\bchangeme\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Detection patterns.
# --------------------------------------------------------------------------- #

# (a) cluster signature components — evaluated per file.
HOUSEHOLD_INCOME_RE = re.compile(r"household_income", re.IGNORECASE)
# A personal-name signature: a "First Last" capitalised pair. Kept deliberately
# narrow (two capitalised words) so it triggers on data rows, not prose.
NAME_RE = re.compile(r"\b[A-Z][a-z]{1,19}\s+[A-Z][a-z]{1,19}\b")
# A US ZIP (5-digit, optionally +4) or a geo/lat-long marker.
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
GEO_KEY_RE = re.compile(r"\b(zip|zipcode|postal_code|lat|lng|long|latitude|longitude)\b", re.I)

# (b) SSN: 3-2-4 with a hyphen/space separator (avoids matching ZIP+phone runs).
SSN_RE = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
# Precise lat/long pair: two signed decimals with >=4 fractional digits, close
# together (a "cluster"). Coarse 2-3 dp aggregate coords won't trip this.
LATLONG_RE = re.compile(r"[-+]?\d{1,3}\.\d{4,}\s*[,;]\s*[-+]?\d{1,3}\.\d{4,}")

# Real-looking email (not a synthetic marker, not a public business contact).
EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
# Public business contacts are allowed even outside *.md (e.g. seed config).
ALLOWED_EMAIL_DOMAINS = {"gt.school"}

# (c) secret-shaped strings.
# Supabase keys are JWTs: header always starts eyJ... ; require 3 dot-segments.
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
ANTHROPIC_RE = re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")
# HubSpot private-app tokens: pat-<region>-<uuid-ish>.
HUBSPOT_RE = re.compile(r"\bpat-[a-z0-9]{2,4}-[A-Za-z0-9-]{16,}\b")


class Finding:
    __slots__ = ("path", "rule", "detail")

    def __init__(self, path: Path, rule: str, detail: str) -> None:
        self.path = path
        self.rule = rule
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.detail}"


# --------------------------------------------------------------------------- #
# Walking.
# --------------------------------------------------------------------------- #


def iter_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if name in SKIP_FILENAMES:
                    continue
                if p.suffix.lower() in SKIP_SUFFIXES:
                    continue
                yield p


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:  # binary guard
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("latin-1")
        except UnicodeDecodeError:
            return None


# --------------------------------------------------------------------------- #
# Per-file checks.
# --------------------------------------------------------------------------- #


def _scan_secrets(path: Path, text: str, findings: list[Finding]) -> None:
    """Secret scan runs on EVERY file (incl. *.md / .env.example)."""
    for line in text.splitlines():
        if PLACEHOLDER_RE.search(line):
            # Placeholder JWT/key examples are fine; but only neutralise if the
            # placeholder is what stands in for the secret on this line.
            if not (JWT_RE.search(line) or ANTHROPIC_RE.search(line) or HUBSPOT_RE.search(line)):
                continue
        if m := JWT_RE.search(line):
            if not PLACEHOLDER_RE.search(line):
                findings.append(Finding(path, "secret:jwt", _excerpt(m.group(0))))
        if m := ANTHROPIC_RE.search(line):
            if not PLACEHOLDER_RE.search(line):
                findings.append(Finding(path, "secret:anthropic", _excerpt(m.group(0))))
        if m := HUBSPOT_RE.search(line):
            if not PLACEHOLDER_RE.search(line):
                findings.append(Finding(path, "secret:hubspot", _excerpt(m.group(0))))


def _scan_pii(path: Path, text: str, findings: list[Finding]) -> None:
    """PII scan — skipped for allowed (doc / env.example) files."""
    # (b) SSN + precise lat/long, line by line (placeholder-aware).
    for line in text.splitlines():
        if PLACEHOLDER_RE.search(line):
            continue
        if m := SSN_RE.search(line):
            findings.append(Finding(path, "pii:ssn", _excerpt(m.group(0))))
        if m := LATLONG_RE.search(line):
            findings.append(Finding(path, "pii:latlong", _excerpt(m.group(0))))
        if m := EMAIL_RE.search(line):
            addr = m.group(0)
            domain = addr.rsplit("@", 1)[-1].lower()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                findings.append(Finding(path, "pii:email", _excerpt(addr)))

    # (a) cluster signature — whole-file co-occurrence (C-SYN-2). Data files
    # only: the signature describes a leaked DATA row, not source prose.
    if _is_data_file(path) and HOUSEHOLD_INCOME_RE.search(text):
        has_name = bool(NAME_RE.search(text))
        has_geo = bool(ZIP_RE.search(text) or GEO_KEY_RE.search(text))
        if has_name and has_geo:
            findings.append(
                Finding(
                    path,
                    "pii:cluster",
                    "household_income + personal name + ZIP/geo co-occur (C-SYN-2)",
                )
            )


def _excerpt(s: str, n: int = 24) -> str:
    s = s if len(s) <= n else s[:n] + "…"
    # Never echo a full secret/PII value back to logs.
    return s[:6] + "***" if len(s) > 6 else s


def scan(roots: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(roots):
        text = _read_text(path)
        if text is None:
            continue
        _scan_secrets(path, text, findings)
        if not _is_allowed_from_pii(path):
            _scan_pii(path, text, findings)
    return findings


# --------------------------------------------------------------------------- #
# Self-test: plant a real-PII fixture in a temp dir, prove the gate flags it.
# --------------------------------------------------------------------------- #


def self_test() -> int:
    ok = True

    # Fixtures are assembled at runtime from inert parts so this source file
    # itself stays clean (it must pass its own scan).
    seg = "A" * 12
    fake_jwt = ".".join(["eyJ" + seg, "eyJ" + seg, seg])
    fake_ant = "sk-ant-" + "B" * 24
    fake_ssn = "123" + "-" + "45" + "-" + "6789"
    fake_geo = "34.052235" + "," + "-118.243683"

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        # 1. Planted real-PII cluster (C-SYN-2) + SSN + secrets — MUST flag.
        bad = tmp / "leads_seed.csv"
        bad.write_text(
            f"name,household_income,zip,ssn\nJane Doe,84000,90210,{fake_ssn}\n",
            encoding="utf-8",
        )
        secrets = tmp / "config.json"
        secrets.write_text(
            f'{{"anthropic":"{fake_ant}","jwt":"{fake_jwt}"}}\n',
            encoding="utf-8",
        )
        geo = tmp / "precise.txt"
        geo.write_text(f"loc: {fake_geo}\n", encoding="utf-8")

        bad_findings = scan([tmp])
        rules = {f.rule for f in bad_findings}
        for expect in ("pii:cluster", "pii:ssn", "secret:anthropic", "secret:jwt", "pii:latlong"):
            present = expect in rules
            print(f"[self-test] planted {expect:18s} -> {'FLAGGED' if present else 'MISSED'}")
            ok = ok and present

    # 2. Clean / allowed content — MUST NOT flag.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "notes.md").write_text(
            "The leads_new table has name, household_income, zip in ZIP 90210.\n"
            "Contact contact@gt.school. Example key: sk-ant-<secret>.\n",
            encoding="utf-8",
        )
        (tmp / ".env.example").write_text(
            "ANTHROPIC_API_KEY=<secret>\nSUPABASE_ANON_KEY=<public anon key>\n",
            encoding="utf-8",
        )
        (tmp / "synthetic.csv").write_text(
            "name,email\nFamily One,family.one@example.invalid\n",
            encoding="utf-8",
        )
        clean_findings = scan([tmp])
        clean_ok = not clean_findings
        print(
            f"[self-test] clean/allowed tree    -> "
            f"{'NO FALSE POSITIVES' if clean_ok else 'FALSE POSITIVE: ' + str(clean_findings)}"
        )
        ok = ok and clean_ok

    print(f"[self-test] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PII / secret scanner (THREAT_MODEL.md §5).")
    parser.add_argument("paths", nargs="*", help="paths to scan (default: repo root)")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="plant a real-PII fixture and prove the gate flags it (then exit)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.paths:
        roots = [Path(p) for p in args.paths]
    else:
        # Default: the repo root (parent of scripts/).
        roots = [Path(__file__).resolve().parent.parent]

    findings = scan(roots)
    if findings:
        print("PII/secret scan FAILED — findings:", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print(
            f"\n{len(findings)} finding(s). Synthetic-data mandate (THREAT_MODEL.md §5) violated.",
            file=sys.stderr,
        )
        return 1
    print("PII/secret scan passed — clean tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
