from django.conf import settings
from rest_framework.permissions import BasePermission


class IsBridgeClient(BasePermission):
    message = "Bridge token missing or invalid."

    def has_permission(self, request, view) -> bool:
        token = request.headers.get("X-Bridge-Token")
        return bool(token and token == settings.BRIDGE_SHARED_TOKEN)
