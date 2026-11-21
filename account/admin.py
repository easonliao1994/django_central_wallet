from django.contrib import admin
from blockchain.admin import CryptoAddressInline
from django.contrib.auth.admin import UserAdmin
from blockchain.models import *
from django.http import HttpResponseRedirect
from .models import *

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    inlines = [CryptoAddressInline]
    list_display = ('id', 'email', 'is_active', 'is_verified', 'date_joined', 'last_login')
    search_fields = ['email']
    ordering = ('-id',)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(UserAdmin, self).get_inline_instances(request, obj)
    
    @admin.display(description='Generate Address')
    def generate_address(modeladmin, request, queryset):        
        for user in queryset:
            address, private_key = generate_address()
            crypto_address = CryptoAddress(user=user, address=address)
            crypto_address.set_private_key(private_key)
            crypto_address.save()
        return HttpResponseRedirect(request.get_full_path())
    
    actions = [generate_address]