from django.core.management.base import BaseCommand, CommandError

from blockchain.models import Blockchain
from blockchain.reconciliation import find_discrepancies
from blockchain.scanner import ScanError


class Command(BaseCommand):
    help = 'Compare on-chain balances of deposit addresses with recorded deposits and sweeps.'

    def add_arguments(self, parser):
        parser.add_argument('--chain', type=int, required=True, help='Blockchain id.')

    def handle(self, *args, chain, **options):
        blockchain = Blockchain.objects.filter(pk=chain).first()
        if blockchain is None:
            raise CommandError(f'Blockchain {chain} does not exist')
        try:
            found = find_discrepancies(blockchain)
        except ScanError as e:
            raise CommandError(str(e))
        if not found:
            self.stdout.write(self.style.SUCCESS('All balances match.'))
        for d in found:
            self.stdout.write(self.style.WARNING(
                f'{d.address} {d.symbol}: on-chain={d.on_chain} expected={d.expected} diff={d.difference}'))
