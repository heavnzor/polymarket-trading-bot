from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from core.services import EVENT_GROUP


class BotEventsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        allow_anon = settings.ALLOW_ANONYMOUS_WEBSOCKET
        user = self.scope.get("user")
        if not allow_anon and (not user or not user.is_authenticated):
            await self.close(code=4401)
            return

        await self.channel_layer.group_add(EVENT_GROUP, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connection_ready", "group": EVENT_GROUP})

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(EVENT_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def realtime_event(self, event):
        await self.send_json(
            {
                "type": "event",
                "event_type": event.get("event_type"),
                "payload": event.get("payload", {}),
                "emitted_at": event.get("emitted_at"),
            }
        )
