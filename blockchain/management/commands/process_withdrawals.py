from django.core.management.base import BaseCommand, CommandError

from blockchain.models import Blockchain
from blockchain.withdrawals import BroadcastError, broadcast_approved, check_broadcast_withdrawals


class Command(BaseCommand):
    help = 'Broadcast approved withdrawals and check receipts of broadcast ones (synchronously).'

    def add_arguments(self, parser):
        parser.add_argument('--chain', type=int, required=True, help='Blockchain id.')
        parser.add_argument('--check-only', action='store_true', help='Only check receipts, do not broadcast.')

    def handle(self, *args, chain, check_only=False, **options):
        blockchain = Blockchain.objects.filter(pk=chain).first()
        if blockchain is None:
            raise CommandError(f'Blockchain {chain} does not exist')
        try:
            w3 = blockchain.get_web3(middleware_inject=True)
            if w3 is None:
                raise BroadcastError(f'No reachable RPC endpoint for {blockchain}')
            if not check_only:
                self.stdout.write(f'{blockchain}: broadcast {broadcast_approved(blockchain, w3)} withdrawal(s)')
            self.stdout.write(f'{blockchain}: {check_broadcast_withdrawals(blockchain, w3)}')
        except BroadcastError as e:
            raise CommandError(str(e))
