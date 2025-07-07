from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/ride/updates/$', consumers.RideUpdatesConsumer.as_asgi()),
    re_path(r'ws/driver/updates/$', consumers.DriverUpdatesConsumer.as_asgi()),
]