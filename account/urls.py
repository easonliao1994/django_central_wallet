from django.urls import re_path, path
from django.contrib.auth import views as auth_views

from . import views
from .views import UserViewSet

urlpatterns = [
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('register/', UserViewSet.as_view({'post': 'create'}), name='register'),
    
]
