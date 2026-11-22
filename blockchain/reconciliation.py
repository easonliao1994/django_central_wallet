"""Balance reconciliation: compare on-chain balances with what the platform recorded.

The scanner cannot see everything (e.g. native coin sent from inside a contract),
and RPC providers occasionally miss data. This check reports, per address and
coin, the difference between the on-chain balance and the expected balance. It
never credits anything by itself; mismatches are surfaced to admins to investigate.

Expected balance of a deposit address, per coin:
    confirmed deposits + confirmed gas top-ups received
    - confirmed sweeps sent
    - (native coin only) gas fees paid by the address for mined transactions
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

from django.db.models import Sum

from .models import Blockchain, CoinInfo, CryptoAddress, TransactionLog
from .scanner import ScanError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Discrepancy:
    address: str
    coin_info_id: int
    symbol: str
    on_chain: int
    expected: int

    @property
    def difference(self) -> int:
        return self.on_chain - self.expected


def _sum_by(qs, address_field: str, value_field: str = 'amount') -> Dict[str, int]:
    rows = qs.values(address_field).annotate(total=Sum(value_field))
    return {r[address_field].lower(): int(r['total'] or 0) for r in rows if r[address_field]}


def expected_balances(blockchain: Blockchain, coin_info: CoinInfo) -> Dict[str, int]:
    """Expected on-chain balance per address (lowercase) according to the transaction log."""
    D = TransactionLog.Direction
    confirmed = TransactionLog.objects.filter(blockchain=blockchain, coin_info=coin_info,
                                              status=TransactionLog.Status.CONFIRMED)
    balances = defaultdict(int)
    for address, value in _sum_by(confirmed.filter(direction__in=[D.DEPOSIT, D.GAS_TOPUP]), 'recipient_address').items():
        balances[address] += value
    for address, value in _sum_by(confirmed.filter(direction=D.SWEEP), 'sender_address').items():
        balances[address] -= value
    if not coin_info.contract:
        # Gas is paid in the native coin, for token transfers too, and also by reverted transactions.
        mined = TransactionLog.objects.filter(blockchain=blockchain, block_number__isnull=False, fee__gt=0)
        for address, value in _sum_by(mined, 'sender_address', 'fee').items():
            balances[address] -= value
    return dict(balances)


def find_discrepancies(blockchain: Blockchain, w3=None) -> List[Discrepancy]:
    from .sweeps import coin_balance

    w3 = w3 or blockchain.get_web3(middleware_inject=True)
    if w3 is None:
        raise ScanError(f'No reachable RPC endpoint for {blockchain}')

    coin_infos = list(CoinInfo.objects.filter(blockchain=blockchain, coin__isnull=False).select_related('coin'))
    expected_by_coin = {ci.pk: expected_balances(blockchain, ci) for ci in coin_infos}
    result = []
    for crypto_address in CryptoAddress.objects.all():
        for coin_info in coin_infos:
            on_chain = coin_balance(w3, coin_info, crypto_address.address)
            expected = expected_by_coin[coin_info.pk].get(crypto_address.address.lower(), 0)
            if on_chain != expected:
                d = Discrepancy(crypto_address.address, coin_info.pk, coin_info.coin.symbol, on_chain, expected)
                logger.warning('%s: balance mismatch %s %s on-chain=%s expected=%s',
                               blockchain, d.address, d.symbol, d.on_chain, d.expected)
                result.append(d)
    return result
