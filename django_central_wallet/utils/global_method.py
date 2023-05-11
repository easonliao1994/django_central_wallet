from web3 import Web3, HTTPProvider
import json
from django_central_wallet.utils.custom_exception_handler import *
from decimal import Decimal
import re
from django_central_wallet.utils.custom_exception_handler import *
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

def get_erc20_contract(w3, contract_address):
    try:
        with open('abi/erc20.json', 'r') as f:
            contract_abi = json.load(f)
    except FileNotFoundError:
        print("Error: ABI file not found")
        return None
    except json.JSONDecodeError:
        print("Error: Invalid ABI file format")
        return None
    
    return w3.eth.contract(address=contract_address, abi=contract_abi)

def generate_address():
    import os
    from ethereum.utils import privtoaddr, checksum_encode, sha3
    key = sha3(os.urandom(4096))
    address = checksum_encode(privtoaddr(key))
    private_key = key.hex()
    return address, private_key

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
