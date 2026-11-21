# django_central_wallet

`django_central_wallet` is a centralized wallet project based on the Django framework. It provides a web interface for managing cryptocurrencies, allowing users to deposit, withdraw, and transfer their digital assets.

## Installation

Python 3.10 is recommended (web3.py 5.x does not support newer Python versions without patches).

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

Then run `python manage.py migrate`.

Use PostgreSQL in production: balance and nonce handling rely on row locks (`select_for_update`), which SQLite ignores.

## Usage

```
python manage.py runserver
```

Visit `http://localhost:8000/admin` for the admin site and `http://localhost:8000/` for the API docs (Swagger).

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
python manage.py rotate_wallet_keys
```

### Setting up a chain

1. In the admin, add a `Coin` for the native coin and each token, then a `Blockchain` (RPC URLs one per line, `chain_id`, `confirm_times`) and a `CoinInfo` per coin on that chain (`contract` empty for the native coin).
2. Tick `is_auto_sync` to let the scanner pick the chain up. A new chain starts scanning from the current confirmed block.
3. For withdrawals, add a `HotWallet` for the chain (the key is generated and encrypted automatically), fund its address, and set the withdrawal limits on each `CoinInfo`. By default every withdrawal needs admin approval.

## How it works

- **Deposits**: ERC20 deposits are detected from `Transfer` event logs (`eth_getLogs`), so reverted transfers are never credited and `transferFrom`/multisend deposits are included. Native coin deposits are found by reading blocks and are credited only when the receipt status is 1. Only blocks with `confirm_times` confirmations (or the `finalized` tag) are processed, and a unique constraint on `(blockchain, tx_id, log_index)` makes rescans safe.
- **Withdrawals**: the balance is debited when requested; approved withdrawals are signed by the chain's hot wallet and confirmed by polling the transaction receipt. A withdrawal keeps its nonce once signed, so it can be paid at most once even when the RPC node returns errors; stuck ones are rebroadcast with the same nonce. Reverted withdrawals, and ones rejected before signing, are refunded.
- **Reconciliation**: a daily job compares on-chain balances with recorded deposits and sweeps and logs mismatches.

## Features

- [x] Generate Ethereum addresses for users.
- [x] Support user deposits, including ERC20 tokens.
- [x] Support user withdrawals, including ERC20 tokens.
- [ ] Internal transfers between users.
- [ ] Sweep deposits to the hot wallet.

## Contributing

Contributions to this project are welcome! To get started, fork the repository and create a new branch for your feature or bug fix. Once you've made your changes, submit a pull request for review.

## License

[MIT License](LICENSE)
