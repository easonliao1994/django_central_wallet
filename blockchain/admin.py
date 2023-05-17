from django.contrib import admin
from .models import *

class CryptoAddressInline(admin.TabularInline):
    model = CryptoAddress
    can_delete = False
    list_display = ('address', 'get_qrcode')
    readonly_fields = list_display
    fields = list_display
    extra = 0

    @admin.display(description='QR Code')
    def get_qrcode(self, instance: CryptoAddress) -> str:
        from django.utils.safestring import mark_safe
        data = instance.as_svg_qrcode()
        return mark_safe(data)

class CryptoAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address')
    readonly_fields = list_display
    fields = list_display

    @admin.display(description='Generate Balance')
    def generate_balance(modeladmin, request, queryset):
        coin_infos = CoinInfo.objects.filter(blockchain__isnull=False, coin__isnull=False)
        for address in queryset:
            for coin_info in coin_infos:
                CoinInfoBalance.objects.get_or_create(crypto_address=address, coin_info=coin_info)
    
    actions = [generate_balance]


class BlockchainAdmin(admin.ModelAdmin):
    list_display = ('name', 'chain_id', 'get_symbol', 'confirm_times', 'last_block', 'last_sync_time', 'is_auto_sync')

    list_filter = (
        ('is_auto_sync'),
    )
    search_fields = ['name', 'coin__symbol']
    
    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions
    
    def get_symbol(self, instance: CoinInfo):
        if instance.coin is None:
            return None
        return instance.coin.symbol

    @admin.display(description='Update latest block number for selected blockchains')
    def update_latest_block_number(modeladmin, request, queryset):
        for b in queryset:
            w3 = b.get_web3(True)
            if w3 is None:
                continue
            confirm_latest_block_number = b.get_confirm_latest_block_number(w3)
            b.last_block = confirm_latest_block_number
            b.save()
    
    actions = [update_latest_block_number]

# Register your models here.
admin.site.register(Coin)
admin.site.register(CoinInfo)
admin.site.register(CoinInfoBalance)
admin.site.register(TransactionLog)
admin.site.register(Blockchain, BlockchainAdmin)
admin.site.register(CryptoAddress, CryptoAddressAdmin)