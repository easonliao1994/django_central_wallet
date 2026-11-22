from django.contrib import admin
from blockchain.admin import CryptoAddressInline
from django.contrib.auth.admin import UserAdmin
from blockchain.models import *
from django.http import HttpResponseRedirect
from .models import *

class WithdrawalAddressInline(admin.TabularInline):
    model = WithdrawalAddress
    extra = 0
    fields = ('label', 'address', 'created_at', 'active_from')
    readonly_fields = fields
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    inlines = [CryptoAddressInline, WithdrawalAddressInline]
    list_display = ('id', 'email', 'is_active', 'is_verified', 'two_factor', 'date_joined', 'last_login')
    readonly_fields = ('two_factor_status',)

    @admin.display(boolean=True, description='2FA')
    def two_factor(self, obj):
        return obj.two_factor_enabled

    @admin.display(description='Two-factor authentication')
    def two_factor_status(self, obj):
        return f'on since {obj.totp_enabled_at:%Y-%m-%d %H:%M}' if obj.two_factor_enabled else 'off'

    @admin.action(description='Reset 2FA (lost device): turn it off and pause withdrawals for the security cooldown')
    def reset_two_factor(modeladmin, request, queryset):
        from .two_factor import lock_withdrawals
        for user in queryset:
            user.totp_secret, user.totp_enabled_at, user.totp_last_step = '', None, None
            lock_withdrawals(user, save=False)
            user.save(update_fields=['totp_secret', 'totp_enabled_at', 'totp_last_step', 'withdrawals_locked_until'])
        modeladmin.message_user(request, f'2FA reset for {queryset.count()} user(s).')
    search_fields = ['email']
    ordering = ('-id',)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(UserAdmin, self).get_inline_instances(request, obj)
    
    @admin.display(description='Generate deposit address (if missing)')
    def generate_address(modeladmin, request, queryset):
        from blockchain.wallet import get_or_create_deposit_address
        for user in queryset:
            get_or_create_deposit_address(user)
        return HttpResponseRedirect(request.get_full_path())
    
    actions = [generate_address, reset_two_factor]