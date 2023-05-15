from django.test import TestCase
from rest_framework.test import APITestCase
from .models import *
# Create your tests here.
class BlockchainTest(APITestCase):
    def setUp(self):
        # create Blockchain Info
        self.coin = Coin.objects.create(symbol='ETH', name='Ethereum')
        self.blockchain = Blockchain.objects.create(name='Ethereum Goerli', rpc_url='https://rpc.ankr.com/eth_goerli', chain_id=5, coin=self.coin, block_explorer_url='https://goerli.etherscan.io')

        self.coin_info = CoinInfo.objects.create(blockchain=self.blockchain, coin=self.coin)

        self.assertEqual(Coin.objects.count(), 1)
        self.assertEqual(Blockchain.objects.count(), 1)
        self.assertEqual(CoinInfo.objects.count(), 1)
    
    def test_get_lastest_block_number(self):
        latest_block_number = self.blockchain.get_confirm_latest_block_number(w3=None)
        self.assertEqual(latest_block_number > 0, True)
