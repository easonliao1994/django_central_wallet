from web3 import Web3, HTTPProvider
import json
from django_central_wallet.utils.custom_exception_handler import *
from decimal import Decimal
import re

# 驗證信箱格式
def isEmailFormatValid(email):
    regex = re.compile(r'([A-Za-z0-9]+[.-_])*[A-Za-z0-9]+@[A-Za-z0-9-]+(\.[A-Z|a-z]{2,})+')
    if re.fullmatch(regex, email) is None:
        raise EmailFormatException

# Web3 相關
def get_web3(blockchain, middleware_inject=False):
    w3 = None
    rpc_urls = blockchain.rpc_url.splitlines()
    for rpc_url in rpc_urls:
        try:
            w3 = Web3(HTTPProvider(rpc_url)) 
            if w3.isConnected():
                break
            w3 = None
        except Exception:
            w3 = None
            continue

    if w3 is None or w3.isConnected() is False:
        return None
    
    from web3.middleware import geth_poa_middleware
    if middleware_inject:
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    return w3

_ERC20_ABI = None

def get_erc20_abi():
    global _ERC20_ABI
    if _ERC20_ABI is None:
        from django.conf import settings
        with open(settings.BASE_DIR / 'abi' / 'erc20.json', 'r') as f:
            _ERC20_ABI = json.load(f)
    return _ERC20_ABI

def get_erc20_contract(w3, contract_address):
    return w3.eth.contract(address=contract_address, abi=get_erc20_abi())

def format_amount(amount, decimal: int, scale=None) -> str:
    """Smallest on-chain unit -> human readable string, e.g. (1500000, 6) -> '1.5'.

    With ``scale``, at most that many decimals are kept, always rounding down so a
    balance is never shown higher than it is.
    """
    from decimal import ROUND_DOWN
    value = Decimal(int(amount)).scaleb(-int(decimal))
    if scale is not None:
        value = value.quantize(Decimal(1).scaleb(-int(scale)), rounding=ROUND_DOWN)
    text = '{:f}'.format(value)
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text if text not in ('', '-0') else '0'


def get_blockchain_real_amount(coin_info, amount: int):
    if None in [coin_info, amount]:
        return None
    try:
        return '{:.32f}'.format(Decimal(amount) / Decimal(10**coin_info.decimal)) \
            .rstrip('0').rstrip('.')
    except Exception as e:
        print(e)
        return None
# IP
def get_client_ip(request):
    elb_user_ip = request.META.get('HTTP_X_FORWARDED_FOR')
    real_ip = request.META.get('HTTP_X_REAL_IP')
    if elb_user_ip:
        ip = elb_user_ip
    elif real_ip:
        ip = real_ip
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip
