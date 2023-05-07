from django.contrib import admin
from .models import *
CryptoAddress

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

# Register your models here.
admin.site.register(Coin)
admin.site.register(CoinInfo)
admin.site.register(Blockchain)
admin.site.register(CryptoAddress, CryptoAddressAdmin)