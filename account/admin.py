from django.contrib import admin
from blockchain.admin import CryptoAddressInline
from django.contrib.auth.admin import UserAdmin
from blockchain.models import *
from django.http import HttpResponseRedirect

class CustomUserAdmin(UserAdmin):
    inlines = [CryptoAddressInline]
    list_display = ('id', 'username', 'email', 'is_active', 'date_joined', 'last_login')
    search_fields = ['username', 'email']
    ordering = ('-id',)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(CustomUserAdmin, self).get_inline_instances(request, obj)
    
    @admin.display(description='Generate Address')
    def generate_address(modeladmin, request, queryset):
        coin_infos = CoinInfo.objects.all()
        
        for user in queryset:
            address, private_key = generate_address()
            crypto_address = CryptoAddress.objects.create(user=user, address=address, private_key=private_key)
        return HttpResponseRedirect(request.get_full_path())
    
    actions = [generate_address]

# Register your models here.
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
