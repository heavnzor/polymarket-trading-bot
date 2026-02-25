from django.urls import path

from core.consumers import BotEventsConsumer

websocket_urlpatterns = [
    path("ws/control-plane/", BotEventsConsumer.as_asgi()),
]
