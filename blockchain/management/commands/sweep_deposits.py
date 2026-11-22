from django.core.management.base import BaseCommand, CommandError

from blockchain.locks import single_run_lock
from blockchain.models import Blockchain, HotWallet
from blockchain.tasks import LOCK_TIMEOUT
from blockchain.withdrawals import BroadcastError


class Command(BaseCommand):
    help = ('Sweep deposit addresses above their coin\'s sweep_threshold into the hot (or cold) wallet, '
            'and advance sweeps already in progress. Uses the same per-chain lock as the Celery tasks.')

    def add_arguments(self, parser):
        parser.add_argument('--chain', type=int, required=True, help='Blockchain id.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Only list the addresses that would be swept; send nothing and write nothing.')

    def handle(self, *args, chain, dry_run=False, **options):
        from blockchain.sweeps import find_candidates, process_chain_sweeps, sweep_destination

        blockchain = Blockchain.objects.filter(pk=chain).first()
        if blockchain is None:
            raise CommandError(f'Blockchain {chain} does not exist')

        if dry_run:
            hot = HotWallet.objects.filter(blockchain=blockchain).first()
            if hot is None:
                raise CommandError(f'{blockchain} has no hot wallet')
            w3 = blockchain.get_web3(middleware_inject=True)
            if w3 is None:
                raise CommandError(f'No reachable RPC endpoint for {blockchain}')
            candidates = find_candidates(blockchain, w3)
            for crypto_address, coin_info, balance in candidates:
                to = sweep_destination(blockchain, coin_info, hot, w3)
                self.stdout.write(f'{coin_info} {crypto_address.address}: {balance} -> {to}')
            self.stdout.write(f'{len(candidates)} address(es) would be swept.')
            return

        with single_run_lock(f'withdrawals:{blockchain.pk}', LOCK_TIMEOUT) as acquired:
            if not acquired:
                raise CommandError('Withdrawals or sweeps are already being processed for this chain')
            try:
                result = process_chain_sweeps(blockchain, start_new=True)
            except BroadcastError as e:
                raise CommandError(str(e))
        self.stdout.write(f'{blockchain}: {result}')
