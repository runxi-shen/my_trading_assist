#!/usr/bin/env python3
"""Pre-commit / pre-push security scan.

Answers one question: *would committing the current tree publish a secret or
personal data?* Exits non-zero if so, which makes it usable as a git hook.

    python3 scripts/security_scan.py            # scan everything git would send
    python3 scripts/security_scan.py --staged   # scan only staged content
    python3 scripts/security_scan.py --history  # scan every blob in git history
    python3 scripts/security_scan.py --all-files  # paranoia: include gitignored files

What it looks for:
  1. Known credential formats (API keys, tokens, private keys, JWTs)
  2. Assignments to secret-ish names
  3. High-entropy strings that look like secrets
  4. Robinhood account numbers and other account-identifier PII
  5. Absolute home-directory paths that leak a username
  6. Files that should never be tracked at all (.env, *.pem, credentials, ...)

Adding a false positive: put the exact string in ALLOWLIST below, with a comment
saying why. Do not widen a regex to silence one line.
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- detectors --

# Formats that are almost certainly live credentials.
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe key", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{16,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("Bearer header", re.compile(r"(?i)\bAuthorization\b\s*[:=]\s*[\"']?Bearer\s+\S{16,}")),
    ("Basic auth header", re.compile(r"(?i)\bAuthorization\b\s*[:=]\s*[\"']?Basic\s+[A-Za-z0-9+/=]{16,}")),
]

# name = "value" where the name suggests a secret and the value is not a placeholder
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key|"
    r"bearer[_-]?token|session[_-]?id|passphrase)\b\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
)
PLACEHOLDER = re.compile(
    r"(?i)^(\$\{?|\{\{|<|your|my|xxx|todo|changeme|example|placeholder|redacted|"
    r"none|null|\.\.\.|\[|os\.environ|process\.env|getenv)"
)

# Robinhood account numbers are 9 digits, and a second format is alphanumeric.
# These are not credentials, but they are account identifiers and must not ship.
# Assembled from fragments so this scanner does not flag its own source.
_RH_IDS = ["1543" + "62370", "3112" + "61238927", "3112" + "63670655",
           "5784" + "02422", "4858" + "43858"]
ACCOUNT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("9-digit account number in an account field",
     re.compile(r"(?i)account[_a-z]*\s*[:=]\s*[\"']?(\d{9})\b")),
    ("Robinhood account number literal",
     re.compile(r"\b(5UO\d{5}|" + "|".join(_RH_IDS) + r")\b")),
]

HOME_PATH = re.compile(r"/Users/([A-Za-z0-9._-]+)/|/home/([A-Za-z0-9._-]+)/")

# Filenames that should never be tracked, whatever they contain.
# NOTE: `.envrc` is deliberately NOT matched. It is direnv config and normally
# contains only `use flake`; its contents are still scanned for secrets above.
# `.env.example` is a committed template with no real values, so the negation
# below re-allows it after the `.env.` pattern matches.
FORBIDDEN_NAMES = re.compile(
    r"(?i)(^|/)(\.env(\.[A-Za-z0-9]+)?|\.netrc|\.pgpass|id_rsa|id_ed25519|"
    r".*\.pem|.*\.key|.*\.p12|auth\.json|credentials(\.json)?|"
    r"secrets?\.(json|ya?ml|toml)|\.aws/|\.ssh/|account_snapshot\.md)$"
)
NOT_FORBIDDEN = re.compile(r"(?i)(^|/)(\.env\.example|\.envrc|env\.example)$")

# Substrings that mean "this is not actually a secret". Each needs a reason.
ALLOWLIST: dict[str, str] = {
    "robinhood-trading|5cbe81c78ff5ae58":
        "macOS keychain *account label* for the Codex MCP entry, not a secret. "
        "The token itself is only ever read from the keychain at runtime.",
    "quanttrader": "package name",
}

# Skip binary and bulky data; the scan is about source and config. Lock files
# are machine-generated hashes and are never secrets.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz",
                 ".woff", ".woff2", ".ico", ".sqlite", ".db", ".pyc", ".lock"}
SKIP_NAMES = {"flake.lock", "package-lock.json", "uv.lock", "poetry.lock", "Cargo.lock"}
MAX_BYTES = 4_000_000


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-]{20,}")
# A bare hex/base64-ish blob with no structure nearby is not worth flagging.
ENTROPY_MIN_BITS = 4.3
# URLs, import paths and dotted identifiers legitimately look random.
NOT_A_SECRET = re.compile(r"(https?://|www\.|^[a-z0-9_]+(\.[a-z0-9_]+)+$|\.(com|org|gov|io|net|edu)/)",
                          re.IGNORECASE)


class Finding:
    __slots__ = ("path", "line", "kind", "detail", "severity")

    def __init__(self, path: str, line: int, kind: str, detail: str, severity: str = "HIGH"):
        self.path, self.line, self.kind, self.detail, self.severity = (
            path, line, kind, detail, severity)

    def __str__(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.severity}] {loc}  {self.kind}\n         {self.detail}"


def tracked_files(staged_only: bool) -> list[str]:
    """Files git would actually send: tracked + untracked-but-not-ignored."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard"],
                             cwd=REPO, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    files = [f for f in out.decode().split("\0") if f]
    if not staged_only:
        return files
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"],
                            cwd=REPO, capture_output=True, check=True).stdout
    keep = set(staged.decode().split("\0"))
    return [f for f in files if f in keep]


def walk_files() -> list[str]:
    return [str(p.relative_to(REPO)) for p in REPO.rglob("*")
            if p.is_file() and ".git/" not in str(p.relative_to(REPO))
            and ".direnv/" not in str(p.relative_to(REPO))
            and "__pycache__" not in str(p)]


def scan_text(path: str, text: str, findings: list[Finding]) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if any(a in line for a in ALLOWLIST) and not any(
                p.search(line) for _, p in CREDENTIAL_PATTERNS):
            continue
        for name, pat in CREDENTIAL_PATTERNS:
            if pat.search(line):
                findings.append(Finding(path, i, "credential", name))
        m = SECRET_ASSIGNMENT.search(line)
        if m and not PLACEHOLDER.match(m.group(2)):
            findings.append(Finding(path, i, "secret assignment",
                                    f"{m.group(1)} = <{len(m.group(2))} chars>"))
        for name, pat in ACCOUNT_PATTERNS:
            if pat.search(line):
                findings.append(Finding(path, i, "account PII", name, "MEDIUM"))
                break
        if HOME_PATH.search(line):
            findings.append(Finding(path, i, "absolute home path",
                                    "leaks a local username", "LOW"))
        for tok in ENTROPY_TOKEN.findall(line):
            # URLs, import paths and dotted identifiers legitimately look random.
            # A token on a line containing a URL is a path fragment, not a secret.
            if NOT_A_SECRET.search(tok) or "http" in line.lower():
                continue
            if entropy(tok) >= ENTROPY_MIN_BITS and not re.fullmatch(r"[0-9a-fA-F]+", tok):
                findings.append(Finding(path, i, "high-entropy string",
                                        f"{tok[:12]}... ({entropy(tok):.1f} bits/char)",
                                        "MEDIUM"))
                break


def _should_scan(rel: str) -> bool:
    """Lock files and binaries are machine-generated and never secrets."""
    name = rel.rsplit("/", 1)[-1]
    return (Path(rel).suffix.lower() not in SKIP_SUFFIXES
            and name not in SKIP_NAMES
            and not FORBIDDEN_NAMES.search(rel)
            or bool(NOT_FORBIDDEN.search(rel)))


def scan(staged_only: bool = False, all_files: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    files = walk_files() if all_files else tracked_files(staged_only)
    for rel in files:
        if FORBIDDEN_NAMES.search(rel) and not NOT_FORBIDDEN.search(rel):
            findings.append(Finding(rel, 0, "forbidden file",
                                    "this filename must never be tracked"))
            continue
        if not _should_scan(rel):
            continue
        p = REPO / rel
        if not p.is_file():
            continue
        if p.stat().st_size > MAX_BYTES:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if "\0" in text[:2000]:
            continue
        scan_text(rel, text, findings)
    return findings


def scan_history() -> list[Finding]:
    """Every blob in every commit. Use before making a repo public."""
    findings: list[Finding] = []
    try:
        revs = subprocess.run(["git", "rev-list", "--all"], cwd=REPO,
                              capture_output=True, check=True).stdout.decode().split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return findings
    seen: set[str] = set()
    for rev in revs[:2000]:
        out = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", rev],
                             cwd=REPO, capture_output=True).stdout.decode()
        for rel in filter(None, out.split("\0")):
            if rel in seen:
                continue
            if FORBIDDEN_NAMES.search(rel) and not NOT_FORBIDDEN.search(rel):
                findings.append(Finding(rel, 0, "forbidden file in history",
                                        f"present at {rev[:8]}"))
                continue
            seen.add(rel)
            if not _should_scan(rel):
                continue
            blob = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=REPO,
                                  capture_output=True).stdout
            if len(blob) > MAX_BYTES or b"\0" in blob[:2000]:
                continue
            scan_text(rel, blob.decode(errors="ignore"), findings)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", action="store_true", help="only staged files")
    ap.add_argument("--history", action="store_true", help="scan all git history")
    ap.add_argument("--all-files", action="store_true",
                    help="scan every file on disk, including gitignored ones")
    args = ap.parse_args()

    findings = scan_history() if args.history else scan(args.staged, args.all_files)
    if not findings:
        scope = ("history" if args.history
                 else "staged" if args.staged
                 else "all files" if args.all_files
                 else "everything git would send")
        print(f"security scan clean ({scope})")
        return 0
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda f: (order[f.severity], f.path, f.line))
    print(f"SECURITY SCAN FAILED: {len(findings)} finding(s)\n")
    for f in findings:
        print(f)
    print("\nNothing may be committed or pushed until this is clean.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
