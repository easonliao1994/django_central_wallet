from django.core.management.base import BaseCommand, CommandError

from blockchain.locks import single_run_lock
from blockchain.models import Blockchain
from blockchain.tasks import LOCK_TIMEOUT
from blockchain.withdrawals import BroadcastError, check_broadcast_withdrawals, process_chain_withdrawals


class Command(BaseCommand):
    help = ('Confirm broadcast withdrawals, rebroadcast stuck ones and send approved ones (synchronously). '
            'Uses the same per-chain lock as the Celery task.')

    def add_arguments(self, parser):
        parser.add_argument('--chain', type=int, required=True, help='Blockchain id.')
        parser.add_argument('--check-only', action='store_true', help='Only check receipts, do not send anything.')

    def handle(self, *args, chain, check_only=False, **options):
        blockchain = Blockchain.objects.filter(pk=chain).first()
        if blockchain is None:
            raise CommandError(f'Blockchain {chain} does not exist')
        with single_run_lock(f'withdrawals:{blockchain.pk}', LOCK_TIMEOUT) as acquired:
            if not acquired:
                raise CommandError('Withdrawal processing is already running for this chain')
            try:
                if check_only:
                    result = check_broadcast_withdrawals(blockchain)
                else:
                    result = process_chain_withdrawals(blockchain)
            except BroadcastError as e:
                raise CommandError(str(e))
        self.stdout.write(f'{blockchain}: {result}')
