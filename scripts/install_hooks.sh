#!/usr/bin/env sh
# Point git at the committed hooks so the protection travels with the repo
# instead of living untracked in .git/hooks.
set -e
cd "$(dirname "$0")/.."
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
echo "installed hooks from .githooks (core.hooksPath=.githooks)"
echo "  pre-commit -> scripts/security_scan.py --staged"
echo "  pre-push   -> scripts/security_scan.py --history + everything git would send"
