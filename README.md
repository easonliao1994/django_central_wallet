# django_central_wallet

`django_central_wallet` is a centralized wallet project based on the Django framework. It provides a web interface for managing cryptocurrencies, allowing users to deposit, withdraw, and transfer their digital assets.

## Run with Docker

Docker Compose starts PostgreSQL, Redis, a one-shot migration job, the web app (gunicorn), a Celery worker, Celery Beat and the frontend:

```
cp .env.default .env
# fill in SECRET_KEY, WALLET_ENCRYPTION_KEYS and POSTGRES_PASSWORD; for production also
# set DEBUG=False, ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

The user web app is on port 8080 (`FRONTEND_PORT`); the Django app (API, admin at `/admin/`, Swagger at `/`) is on port 8000 (`WEB_PORT`). Data lives in the `postgres-data` and `redis-data` volumes. Put a TLS-terminating reverse proxy (nginx, Caddy, a load balancer) in front of the frontend in production.

Logs: `docker compose logs -f worker beat web`. One-off commands: `docker compose exec web python manage.py <command>`.

`docker compose ps` shows each service's health: web calls `GET /health/`, the worker answers `celery inspect ping` for its own node, and beat must have touched its heartbeat file within the last 6 minutes. Beat starts once the worker is healthy. Docker only reports health; it does not restart unhealthy containers by itself.

## Frontend

`frontend/` is a React + TypeScript single-page app (Vite) for users: sign up and log in (with the 2FA step), balances, deposit address with QR code, withdrawals, transfers, transaction history, 2FA setup, saved withdrawal addresses, whitelist mode, profile and password. The interface is in Traditional Chinese (default) and English, follows the system light/dark theme, and works on phones.

It talks to the REST API on the same origin with token authentication, so it needs no CORS settings:

- **Docker**: the `frontend` service builds the app and serves it with nginx, which proxies `/account/`, `/blockchain/`, `/admin/`, `/static/` and `/health/` to the web service (`frontend/nginx.conf`) and sends a strict Content-Security-Policy with the app.
- **Development**: `cd frontend && npm install && npm run dev` serves it on http://localhost:5173 and proxies the API to `VITE_API_PROXY` (default `http://localhost:8000`). `npm test` runs the unit tests, `npm run lint` the linter, `npm run build` type-checks and builds.

For production, point `SITE_URL` at the frontend's public URL and set `PASSWORD_RESET_URL=<frontend>/reset-password?uid={uid}&token={token}` so reset emails open the app. Publish only the frontend port, with `NUM_PROXIES=1` so throttling sees real client IPs, and add the frontend origin to `CSRF_TRUSTED_ORIGINS` to use the admin through it. The API token is kept in the browser's `localStorage`; the CSP blocks third-party scripts, which is the main protection for it.

## Installation (without Docker)

Python 3.10 to 3.13 (the project runs on Django 5.2 LTS, supported until April 2028). Docker and CI use Python 3.12.

```
pip install -r requirements.txt
cp .env.default .env
cp django_central_wallet/settings.py.default django_central_wallet/settings.py
```

Edit `.env`:

- `SECRET_KEY`: Django secret key
- `WALLET_ENCRYPTION_KEYS`: Fernet key used to encrypt wallet private keys at rest. Generate one with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Never commit it, and back it up: without it the private keys cannot be decrypted.
- `REDIS_URL`: Redis for Celery and task locks (default `redis://localhost:6379/0`)
- `POSTGRES_HOST` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD`: PostgreSQL. With `POSTGRES_HOST` empty, SQLite is used.
- `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
- `ADMINS`, `ALERT_WEBHOOK_URL` and the other `ALERT_*` settings: where alerts go (see [Alerts and health checks](#alerts-and-health-checks))

Then run `python manage.py migrate`.

Use PostgreSQL in production: balance and nonce handling rely on row locks (`select_for_update`), which SQLite ignores, and Django reads SQLite decimals with only 15 significant digits, so large amounts in wei are rounded there.

## Usage

```
python manage.py runserver
```

Visit `http://localhost:8000/admin` for the admin site and `http://localhost:8000/` for the API docs (Swagger).

### User API (token or session auth)

| Endpoint | Description |
|---|---|
| `POST /account/register/` | Sign up; a verification email is sent |
| `POST /account/resend-verification/` | Send the verification email again |
| `POST /account/login/` | Email + password -> API token (`Authorization: Token <key>`) |
| `POST /account/logout/` | Revoke the current token |
| `POST /account/password/forgot/` / `POST /account/password/reset/` | Email a reset link / set a new password with its `uid` and `token` (signs out every session) |
| `GET/PATCH /account/me/`, `POST /account/me/password/` | My profile (name only) / change my password |
| `GET /blockchain/deposit-address/` | My deposit address (verified users) |
| `GET /blockchain/balances/` | My balance per chain and coin. `balance` is rounded down to the coin's `scale`; `balance_raw` is exact. Filters: `blockchain`, `symbol` |
| `GET /blockchain/transactions/` | My deposits, withdrawals and internal transfers, newest first, paginated (`page`, `page_size`). Filters: `blockchain`, `coin_info`, `symbol`, `direction` (comma separated), `status`, `created_after`, `created_before` (ISO date or datetime) |
| `GET/POST /blockchain/withdrawals/` | Request and track withdrawals. A withdrawal to another user's deposit address becomes an internal transfer |
| `GET/POST /blockchain/transfers/` | Send coins to another user off-chain (`to` = email or platform address), list transfers I sent or received |
| `POST /account/2fa/setup/`, `/account/2fa/enable/`, `/account/2fa/disable/` | Two-factor authentication with an authenticator app: setup (password) returns a secret, `otpauth://` URI and QR code; enable with the first code; disable with password + code |
| `GET/POST /account/withdrawal-addresses/`, `DELETE /account/withdrawal-addresses/<id>/` | Saved withdrawal addresses (adding one needs a 2FA code; usable after a cooldown) |
| `POST /account/withdrawal-whitelist/` | Whitelist mode on (immediate) or off (2FA code; takes effect after the cooldown) |

Login, verification and password emails are throttled per IP (`AUTH_THROTTLE_RATE`, default `20/hour`) and per target email (`AUTH_EMAIL_THROTTLE_RATE`, `10/hour`); behind a reverse proxy set `NUM_PROXIES=1` so the real client IP is used. These endpoints answer the same way whether or not an account exists, and emails are sent by the Celery worker. Set `SITE_URL` in production: links in emails are built from it rather than from the request's Host header. Configure SMTP with `EMAIL_HOST` and friends (see `.env.default`).

### Protecting outgoing funds

- **2FA is required for withdrawals and transfers** (`REQUIRE_2FA_FOR_WITHDRAWAL`, default `True`): both take an `otp_code`, and accounts without 2FA get `two_factor_required`. Once 2FA is on, login also needs `otp_code`. Codes are single use, a code from the previous or next 30-second step is accepted for clock drift, and after `OTP_MAX_FAILURES` wrong codes within `OTP_FAILURE_WINDOW` seconds every code is refused (`too_many_otp_attempts`). The secret is encrypted with `WALLET_ENCRYPTION_KEYS` (and re-encrypted by `rotate_wallet_keys`). A failed withdrawal does not use up the code.
- **Whitelist mode** (optional, per user, needs 2FA): withdrawals only go to saved addresses whose cooldown (`WITHDRAWAL_ADDRESS_COOLDOWN_HOURS`, default 24) has passed. Transfers are checked too: the recipient's deposit address must be saved. Turning whitelist mode off takes the same cooldown, so a stolen session cannot switch it off and withdraw at once.
- **Security cooldown**: after a password reset, or after 2FA is turned off, withdrawals and transfers pause for `SECURITY_CHANGE_COOLDOWN_HOURS` (default 24); the account shows `withdrawals_locked_until`.
- **Notices**: the user gets an email for every withdrawal and transfer they send, new saved addresses, 2FA changes, password resets and whitelist switch-off requests.
- **Lost device**: an admin can use the *Reset 2FA* action on the user; it turns 2FA off and starts the security cooldown.

Users only see their own data. Superusers can add `user=<id>`, or `user=all` (platform totals for balances; every record, including sweeps and gas top-ups, for transactions).

### Background jobs

Deposit scanning, withdrawal broadcasting/confirmation and balance reconciliation run on Celery (Redis broker). Start each in its own terminal:

```
celery -A django_central_wallet worker -l info
celery -A django_central_wallet beat -l info
```

On Windows add `-P solo` to the worker command.

The same jobs can be run once by hand (useful for debugging):

```
python manage.py scan_chain [--chain <id>] [--rescan-block <number>]
python manage.py process_withdrawals --chain <id> [--check-only]
python manage.py reconcile_balances --chain <id>
python manage.py sweep_deposits --chain <id> [--dry-run]
python manage.py rotate_wallet_keys
```

### Alerts and health checks

Problems are reported to operators by email to `ADMINS` and/or a webhook (`blockchain/alerts.py`), and every alert is also logged at ERROR level:

| Alert | When |
|---|---|
| Balance mismatch | The daily reconciliation finds a deposit address whose on-chain balance differs from the transaction log |
| Hot wallet low | The hot wallet holds less of a coin than its `hot_wallet_alert_threshold` (set per `CoinInfo` in the admin, smallest unit, 0 = off), or less than the withdrawals of that coin pending review or approved |
| Withdrawal not confirmed | Approved more than `ALERT_STUCK_MINUTES` ago (default 30) and still waiting to broadcast, broadcast or stuck. Counted from approval, so rebroadcasts do not reset it |
| Withdrawal failed | Reverted on chain (and refunded) |
| Sweep / gas top-up not confirmed | A sweep open longer than `ALERT_STUCK_MINUTES`, twice that when it needed a gas top-up |
| Sweep / gas top-up failed | Failed, or cancelled after repeated send errors |
| Scanner lag | The deposit scanner is more than `ALERT_SCAN_LAG_BLOCKS` (default 100) behind the confirmed head |
| RPC unreachable | None of a chain's RPC URLs answers (auto-sync chains and chains with a hot wallet) |
| Task failed | Any Celery task raises an unhandled exception (including time limits) |

Reconciliation alerts come from the daily job and task failures from the worker as they happen; the other checks run every 5 minutes (`blockchain.run_monitoring_checks`, `blockchain/monitoring.py`). They only read the database and the chain.

- **Channels**: `ADMINS` (comma separated emails; needs SMTP, see `EMAIL_HOST`; sent from `SERVER_EMAIL`) and `ALERT_WEBHOOK_URL`, which receives `POST {"text": "..."}`: a Slack incoming webhook, a Discord webhook with `/slack` appended, or any receiver that accepts that JSON (10 second timeout, `ALERT_WEBHOOK_TIMEOUT`). Treat the webhook URL as a secret; it is never logged. URLs are removed from alert texts, so RPC API keys in error messages do not end up in chat. Setting `ADMINS` also turns on Django's emails for server errors (HTTP 500) when `DEBUG=False`.
- **De-duplication**: the same alert (same chain, coin, withdrawal, sweep or task + error) is sent at most once per `ALERT_DEDUP_SECONDS` (default 3600) while the problem lasts. Each failure is reported once. Failures older than 3 days are not reported, so a first deploy does not report old ones.
- **Never in the way**: an alert that cannot be sent (SMTP down, webhook timeout) is logged, and the job that raised it carries on.

`GET /health/` (no authentication) checks the database (`SELECT 1`) and Redis (`PING` on `REDIS_URL`) and answers `200 {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`, or `503` with `"status": "failing"` and the failing component. Nothing else is disclosed. It is answered by the first middleware, before the `ALLOWED_HOSTS` check and any HTTPS redirect, so container and load balancer probes can use `http://<ip>:8000/health/` with production settings. Failing probes are not reported as server errors, so they do not trigger error emails.

### Setting up a chain

1. In the admin, add a `Coin` for the native coin and each token, then a `Blockchain` (RPC URLs one per line, `chain_id`, `confirm_times`) and a `CoinInfo` per coin on that chain (`contract` empty for the native coin).
2. Tick `is_auto_sync` to let the scanner pick the chain up. A new chain starts scanning from the current confirmed block.
3. For withdrawals, add a `HotWallet` for the chain (the key is generated and encrypted automatically), fund its address, and set the withdrawal limits on each `CoinInfo`. By default every withdrawal needs admin approval.
4. To sweep deposits into the hot wallet, set `sweep_threshold` on each `CoinInfo` (0 = never sweep). Optionally set the chain's `cold_wallet_address` and a coin's `hot_wallet_limit`: once the hot wallet holds that much, sweeps go to the cold wallet instead. Keep some native coin in the hot wallet: it pays the gas top-ups for token sweeps.

Example for the Sepolia testnet: RPC URLs `https://ethereum-sepolia-rpc.publicnode.com` and `https://sepolia.gateway.tenderly.co`, `chain_id` 11155111, explorer `https://sepolia.etherscan.io`. Use an RPC provider whose `eth_getLogs` accepts at least `scan_batch_size` blocks (some public endpoints limit it to 50).

## Architecture

```
            users (REST API, token auth)                 admins (Django admin)
                         |                                         |
   account/  register, login, password reset, profile              |
   blockchain/views.py  balances, history, deposit address, withdrawals, transfers
                         |                                         |
                         v                                         v
              PostgreSQL: users, CoinInfoBalance (internal + external), TransactionLog,
                          Withdrawal, Sweep, InternalTransfer, encrypted keys (Fernet)
                         ^
                         |  Celery worker, scheduled by Celery Beat (Redis broker + locks)
   dispatch_chain_jobs (every minute) -----> scan_chain            blockchain/scanner.py
                                     \----> process_withdrawals   blockchain/withdrawals.py
                                              (+ in-flight sweeps) blockchain/sweeps.py
   sweep_deposits (every 10 minutes) -------> start new sweeps     blockchain/sweeps.py
   reconcile_balances (daily) --------------> compare with chain   blockchain/reconciliation.py
   run_monitoring_checks (every 5 minutes) -> alerts (email / webhook)  blockchain/monitoring.py, alerts.py
                         |
                         v
              EVM chain via JSON-RPC (web3.py 7): deposits in, withdrawals and sweeps out
```

- **Balances** live in the database: `external_amount` changes with on-chain deposits and withdrawals, `internal_amount` with off-chain transfers. User funds sit on per-user deposit addresses until swept to the hot wallet, which pays withdrawals.
- **Keys**: user deposit keys and hot wallet keys are encrypted at rest with `WALLET_ENCRYPTION_KEYS` (`blockchain/key_vault.py`); the cold wallet key is never stored.
- **Per-chain lock**: withdrawals, gas top-ups and sweeps of a chain run under one Redis lock, because they share the hot wallet nonce.
- **Code map**: `blockchain/scanner.py` (deposits), `withdrawals.py`, `sweeps.py`, `transfers.py`, `reconciliation.py`, `monitoring.py` + `alerts.py` (operator alerts), `tasks.py` (Celery), `wallet.py` (addresses and balances), `management/commands/` (manual runs); `django_central_wallet/health.py` (`/health/`), `beat.py` (beat heartbeat).

## How it works

- **Deposits**: ERC20 deposits are detected from `Transfer` event logs (`eth_getLogs`), so reverted transfers are never credited and `transferFrom`/multisend deposits are included. Native coin deposits are found by reading blocks and are credited only when the receipt status is 1. Only blocks with `confirm_times` confirmations (or the `finalized` tag) are processed, and a unique constraint on `(blockchain, tx_id, log_index)` makes rescans safe.
- **Withdrawals**: the balance is debited when requested; approved withdrawals are signed by the chain's hot wallet and confirmed by polling the transaction receipt. The gas limit is estimated per withdrawal (`eth_estimateGas` + 20%, never below the chain's `gas_limit`), because a fixed 21000 is not enough everywhere: on Sepolia, sending ETH to a never-used address costs about 207k gas. A withdrawal keeps its nonce once signed, so it can be paid at most once even when the RPC node returns errors; stuck ones are rebroadcast with the same nonce. Reverted withdrawals, and ones rejected before signing, are refunded.
- **Sweeps**: every 10 minutes, deposit addresses holding at least a coin's `sweep_threshold` are swept to the hot wallet (or the cold wallet once the hot wallet is above `hot_wallet_limit`). Native coin is swept minus the gas fee. A token sweep needs native coin for gas, so if the address has none the hot wallet first sends a gas top-up, and the sweep is signed once that is mined. Both transactions keep their nonce and are rebroadcast with the same nonce if they get stuck. A top-up is only sent when the hot wallet can pay it, so it never leaves a nonce gap that would block withdrawals. Gas top-ups are recognised by transaction hash and never credited as deposits, and sweeps never change users' platform balances. Not supported yet: chains with an extra L1 data fee (OP-stack L2s), since the native sweep amount does not account for it.
- **Internal transfers**: off-chain; only `CoinInfoBalance.internal_amount` changes (the sender's may go negative, the checked figure is `internal + external`). The balance rows involved are locked in primary-key order in one transaction, so balances cannot be overdrawn by concurrent requests, and both users get a `TransactionLog` (`direction=internal`, with an `in`/`out` `flow`).
- **Reconciliation**: a daily job compares each deposit address's on-chain balance with the transaction log (deposits + gas top-ups received - sweeps - gas fees paid), logs mismatches and alerts the admins.

## Features

- [x] Generate Ethereum addresses for users.
- [x] Support user deposits, including ERC20 tokens.
- [x] Support user withdrawals, including ERC20 tokens.
- [x] Internal transfers between users (off-chain, instant, no fee).
- [x] Sweep deposits to the hot wallet (with gas top-ups and an optional cold wallet).
- [x] Two-factor authentication for withdrawals and transfers, withdrawal address whitelist.
- [x] Web frontend for users (React, Traditional Chinese and English).

## Contributing

Contributions to this project are welcome! To get started, fork the repository and create a new branch for your feature or bug fix. Once you've made your changes, submit a pull request for review.

## License

[MIT License](LICENSE)
