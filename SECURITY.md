# Security

This repository is **public**. Treat every commit as a publication that cannot be
taken back: GitHub caches, forks, and archives. Deleting a secret in a later
commit does not unpublish it.

## Before you commit or push

The scan runs automatically once hooks are installed:

```sh
./scripts/install_hooks.sh      # once per clone
```

Installing the hooks points `core.hooksPath` at `.githooks/`, so the protection is
committed and travels with the repository rather than sitting untracked in
`.git/hooks/`.

| Hook | Runs | Blocks on |
|---|---|---|
| `pre-commit` | `security_scan.py --staged` | any finding in staged content |
| `pre-push` | `security_scan.py --history`, then `--tree` | any finding anywhere in history or the tree |

Run it by hand any time:

```sh
python3 scripts/security_scan.py --tree       # working tree, ignoring .gitignore
python3 scripts/security_scan.py --staged     # exactly what would be committed
python3 scripts/security_scan.py --history    # every blob in every commit
```

Do not use `git commit --no-verify`. If the scan is wrong, add the exact string
to `ALLOWLIST` in `scripts/security_scan.py` with a comment explaining why —
never widen a regex to silence one line.

## What the scan looks for

1. Known credential formats — API keys, GitHub/AWS/Slack/Stripe/Google tokens,
   private-key blocks, JWTs, `Authorization:` headers
2. Assignments to secret-like names with a non-placeholder value
3. High-entropy strings that look like secrets
4. Robinhood account numbers and account-identifier fields
5. Absolute home-directory paths that leak a local username
6. Filenames that must never be tracked (`.env`, `*.pem`, `auth.json`, …)

## What is deliberately kept out of git

| Category | Where it lives instead |
|---|---|
| Robinhood account number | `$QUANTTRADER_ACCOUNT` env var; `Policy.require_account()` fails closed if unset |
| MCP OAuth token | macOS keychain, service `Codex MCP Credentials`, refreshed by `codex mcp login` |
| Position sizes, balances, cost basis | `local/account_snapshot.md` (gitignored) |
| Broker OHLCV bars | not redistributed; re-fetch via `scripts/fetch_data.py` and verify the SHA-256 in `BACKTEST_REPORT.md` |
| Generated study output | regenerable: `scripts/run_backtest.py` |

The MCP token is never written to disk by this project. `feed.py` shells out to
`security find-generic-password` at call time and holds the value only in memory.
The one string that appears in source, `robinhood-trading|5cbe81c78ff5ae58`, is the
keychain **account label** used to look the entry up — not the secret itself. It is
in the scan allowlist with that reason recorded.

## If a secret is ever committed

1. **Rotate it first.** Assume it is already compromised; cleaning the repo does
   not un-leak it.
2. Rewrite history (`git filter-repo`) and force-push, accepting that forks and
   caches may retain copies.
3. Add the string to `ALLOWLIST` only if it is genuinely dead and inert.

## Threat model, briefly

The realistic risks here are not exotic: a broker token pasted into a config
file, an account number in a README, or a `.env` swept in by `git add -A`. The
scanner and `.gitignore` are aimed squarely at those. The other risk is
operational — an unguarded code path placing a real order — which is handled
separately by `risk.RiskManager`, the inert `RobinhoodBroker`, and
`live_orders_enabled = false` in `config/policy.toml`.
