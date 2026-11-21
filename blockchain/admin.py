from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.shortcuts import render

from .models import *


class RescanBlockForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    block_number = forms.IntegerField(min_value=0)

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
        from .wallet import ensure_balances
        for address in queryset:
            ensure_balances(address)
    
    actions = [generate_balance]


class BlockchainAdmin(admin.ModelAdmin):
    list_display = ('name', 'chain_id', 'get_symbol', 'confirm_times', 'last_block', 'last_sync_time', 'is_auto_sync', 'scan_native')

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
    
    @admin.display(description='Scan new blocks now for selected blockchains')
    def scan_now(modeladmin, request, queryset):
        from .scanner import ChainScanner, ScanError
        for b in queryset:
            try:
                credited = ChainScanner(b).run()
                modeladmin.message_user(request, f'{b}: scanned up to block {b.last_block}, {credited} new deposit(s).')
            except ScanError as e:
                modeladmin.message_user(request, f'{b}: {e}', level=messages.ERROR)

    @admin.display(description='Rescan a specific block for selected blockchains')
    def rescan_block(modeladmin, request, queryset):
        from .scanner import ChainScanner, ScanError
        if 'apply' in request.POST:
            form = RescanBlockForm(request.POST)
            if form.is_valid():
                number = form.cleaned_data['block_number']
                for b in queryset:
                    try:
                        credited = ChainScanner(b).rescan(number)
                        modeladmin.message_user(request, f'{b}: rescanned block {number}, {credited} new deposit(s).')
                    except ScanError as e:
                        modeladmin.message_user(request, f'{b}: {e}', level=messages.ERROR)
                return None
        else:
            form = RescanBlockForm(initial={'_selected_action': request.POST.getlist(ACTION_CHECKBOX_NAME)})
        return render(request, 'admin/blockchain/rescan_block.html', {
            **modeladmin.admin_site.each_context(request),
            'title': 'Rescan block',
            'form': form,
            'queryset': queryset,
            'opts': modeladmin.model._meta,
        })

    @admin.display(description='Reconcile on-chain balances for selected blockchains')
    def reconcile_balances(modeladmin, request, queryset):
        from .reconciliation import find_discrepancies
        from .scanner import ScanError
        for b in queryset:
            try:
                found = find_discrepancies(b)
            except ScanError as e:
                modeladmin.message_user(request, f'{b}: {e}', level=messages.ERROR)
                continue
            if not found:
                modeladmin.message_user(request, f'{b}: all balances match.')
            for d in found:
                modeladmin.message_user(
                    request,
                    f'{b}: {d.address} {d.symbol} on-chain={d.on_chain} expected={d.expected} (diff {d.difference})',
                    level=messages.WARNING)

    actions = [update_latest_block_number, scan_now, rescan_block, reconcile_balances]

# Register your models here.
admin.site.register(Coin)
admin.site.register(CoinInfo)
admin.site.register(CoinInfoBalance)
@admin.register(TransactionLog)
class TransactionLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'blockchain', 'coin_info', 'direction', 'status', 'user', 'amount', 'block_number', 'tx_id', 'created_at')
    list_filter = ('direction', 'status', 'blockchain')
    search_fields = ('tx_id', 'sender_address', 'recipient_address', 'user__email')
    raw_id_fields = ('user',)

admin.site.register(Blockchain, BlockchainAdmin)
admin.site.register(CryptoAddress, CryptoAddressAdmin)