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

    @admin.display(description='Sweep deposit addresses now for selected blockchains')
    def sweep_now(modeladmin, request, queryset):
        from .locks import single_run_lock
        from .sweeps import process_chain_sweeps
        from .tasks import LOCK_TIMEOUT
        from .withdrawals import BroadcastError
        for b in queryset:
            with single_run_lock(f'withdrawals:{b.pk}', LOCK_TIMEOUT) as acquired:
                if not acquired:
                    modeladmin.message_user(request, f'{b}: withdrawals/sweeps are being processed, try again shortly.',
                                            level=messages.WARNING)
                    continue
                try:
                    modeladmin.message_user(request, f'{b}: {process_chain_sweeps(b, start_new=True)}')
                except BroadcastError as e:
                    modeladmin.message_user(request, f'{b}: {e}', level=messages.ERROR)

    actions = [update_latest_block_number, scan_now, rescan_block, reconcile_balances, sweep_now]

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


@admin.register(HotWallet)
class HotWalletAdmin(admin.ModelAdmin):
    list_display = ('blockchain', 'address', 'next_nonce', 'created_at')
    readonly_fields = ('address', 'next_nonce', 'created_at')
    fields = ('blockchain', 'address', 'next_nonce', 'created_at')

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (('blockchain',) if obj else ())

    def save_model(self, request, obj, form, change):
        if not change:
            from .wallet import generate_keypair
            address, private_key = generate_keypair()
            obj.address = address
            obj.set_private_key(private_key)
        super().save_model(request, obj, form, change)


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'coin_info', 'get_amount', 'to_address', 'status', 'tx_id', 'created_at')
    list_filter = ('status', 'coin_info__blockchain')
    search_fields = ('user__email', 'to_address', 'tx_id')
    raw_id_fields = ('user', 'crypto_address', 'transaction_log', 'reviewed_by')
    readonly_fields = [f.name for f in Withdrawal._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Amount')
    def get_amount(self, obj):
        return obj.amount_str

    def _run(modeladmin, request, queryset, fn, label):
        from .withdrawals import BroadcastError
        done = 0
        for w in queryset:
            try:
                done += int(bool(fn(w)))
            except BroadcastError as e:
                modeladmin.message_user(request, f'#{w.pk}: {e}', level=messages.ERROR)
        modeladmin.message_user(request, f'{label}: {done} of {queryset.count()} withdrawal(s).')

    @admin.display(description='Approve selected withdrawals')
    def approve_selected(modeladmin, request, queryset):
        from .withdrawals import approve
        modeladmin._run(request, queryset, lambda w: approve(w, request.user), 'Approved')

    @admin.display(description='Reject selected withdrawals not yet signed (refund balance)')
    def reject_selected(modeladmin, request, queryset):
        from .withdrawals import reject
        modeladmin._run(request, queryset, lambda w: reject(w, request.user), 'Rejected')

    @admin.display(description='Broadcast selected approved withdrawals now')
    def broadcast_selected(modeladmin, request, queryset):
        from .withdrawals import broadcast
        modeladmin._run(request, queryset, broadcast, 'Broadcast')

    @admin.display(description='Rebroadcast selected stuck withdrawals (same nonce, higher gas)')
    def rebroadcast_selected(modeladmin, request, queryset):
        from .withdrawals import rebroadcast
        modeladmin._run(request, queryset, rebroadcast, 'Rebroadcast')

    @admin.display(description='Check confirmation status for the chains of selected withdrawals')
    def check_selected(modeladmin, request, queryset):
        from .withdrawals import BroadcastError, check_broadcast_withdrawals
        for chain in Blockchain.objects.filter(coininfo__withdrawal__in=queryset).distinct():
            try:
                modeladmin.message_user(request, f'{chain}: {check_broadcast_withdrawals(chain)}')
            except BroadcastError as e:
                modeladmin.message_user(request, f'{chain}: {e}', level=messages.ERROR)

    actions = [approve_selected, reject_selected, broadcast_selected, rebroadcast_selected, check_selected]


@admin.register(Sweep)
class SweepAdmin(admin.ModelAdmin):
    """Read-only: sweeps are created and advanced by the background jobs."""
    list_display = ('id', 'blockchain', 'coin_info', 'crypto_address', 'amount', 'to_address', 'status', 'topup_count', 'tx_id', 'created_at')
    list_filter = ('status', 'blockchain')
    search_fields = ('crypto_address__address', 'to_address', 'tx_id', 'topup_tx_id')
    readonly_fields = [f.name for f in Sweep._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
