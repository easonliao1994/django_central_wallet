"""Balance reconciliation: compare on-chain balances with what the platform recorded.

The scanner cannot see everything (e.g. native coin sent from inside a contract),
and RPC providers occasionally miss data. This check reports, per address and
coin, the difference between the on-chain balance and the expected balance
(confirmed deposits minus confirmed sweeps). It never credits anything by itself;
mismatches are surfaced to admins to investigate.

Note: native coin balances also drop by the gas paid when sweeping tokens, so a
small negative difference on the native coin is expected once sweeps exist.
"""
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List

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


def _expected_balance(blockchain, coin_info, address: str) -> int:
    def total(**filters):
        value = TransactionLog.objects.filter(
            blockchain=blockchain, coin_info=coin_info,
            status=TransactionLog.Status.CONFIRMED, **filters,
        ).aggregate(s=Sum('amount'))['s']
        return int(value or Decimal(0))

    deposits = total(direction=TransactionLog.Direction.DEPOSIT, recipient_address__iexact=address)
    sweeps = total(direction=TransactionLog.Direction.SWEEP, sender_address__iexact=address)
    return deposits - sweeps


def find_discrepancies(blockchain: Blockchain, w3=None) -> List[Discrepancy]:
    from web3 import Web3

    from django_central_wallet.utils.global_method import get_erc20_contract

    w3 = w3 or blockchain.get_web3(middleware_inject=True)
    if w3 is None:
        raise ScanError(f'No reachable RPC endpoint for {blockchain}')

    coin_infos = list(CoinInfo.objects.filter(blockchain=blockchain, coin__isnull=False).select_related('coin'))
    result = []
    for crypto_address in CryptoAddress.objects.all():
        checksum = Web3.toChecksumAddress(crypto_address.address)
        for coin_info in coin_infos:
            if coin_info.contract:
                contract = get_erc20_contract(w3, Web3.toChecksumAddress(coin_info.contract))
                on_chain = int(contract.functions.balanceOf(checksum).call())
            else:
                on_chain = int(w3.eth.get_balance(checksum))
            expected = _expected_balance(blockchain, coin_info, crypto_address.address)
            if on_chain != expected:
                d = Discrepancy(crypto_address.address, coin_info.pk, coin_info.coin.symbol, on_chain, expected)
                logger.warning('%s: balance mismatch %s %s on-chain=%s expected=%s',
                               blockchain, d.address, d.symbol, d.on_chain, d.expected)
                result.append(d)
    return result
