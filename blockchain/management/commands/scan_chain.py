from django.core.management.base import BaseCommand, CommandError

from blockchain.models import Blockchain
from blockchain.scanner import ChainScanner, ScanError


class Command(BaseCommand):
    help = 'Scan a blockchain for deposits once (synchronously, without Celery).'

    def add_arguments(self, parser):
        parser.add_argument('--chain', type=int, help='Blockchain id. Defaults to every chain with is_auto_sync.')
        parser.add_argument('--rescan-block', type=int, help='Rescan only this block number; the cursor is not moved.')

    def handle(self, *args, chain=None, rescan_block=None, **options):
        if chain is not None:
            chains = Blockchain.objects.filter(pk=chain)
            if not chains.exists():
                raise CommandError(f'Blockchain {chain} does not exist')
        else:
            if rescan_block is not None:
                raise CommandError('--rescan-block requires --chain')
            chains = Blockchain.objects.filter(is_auto_sync=True)

        for blockchain in chains:
            scanner = ChainScanner(blockchain)
            try:
                if rescan_block is not None:
                    credited = scanner.rescan(rescan_block)
                    self.stdout.write(f'{blockchain}: rescanned block {rescan_block}, {credited} new deposit(s)')
                else:
                    credited = scanner.run()
                    self.stdout.write(f'{blockchain}: at block {blockchain.last_block}, {credited} new deposit(s)')
            except ScanError as e:
                self.stderr.write(f'{blockchain}: {e}')
