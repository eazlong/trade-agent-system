"""
Custom ASGI middleware that authenticates WebSocket connections
using JWT token passed via query parameter (?token=xxx).
Replaces AuthMiddlewareStack which only supports Django sessions.
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def get_user(token_string):
    """Validate JWT token and return the Django user."""
    try:
        access_token = AccessToken(token_string)
        user_id = access_token["user_id"]
    except (TokenError, KeyError):
        return None

    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        return User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        return None


class TokenAuthMiddleware(BaseMiddleware):
    """
    ASGI middleware that reads JWT token from query parameter 'token'
    and sets scope['user']. Falls back to AnonymousUser if no token.
    """

    async def resolve_scope(self, scope):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get("token", [])

        if token_list:
            token = token_list[0]
            scope["user"] = await get_user(token)
        else:
            from django.contrib.auth.models import AnonymousUser

            scope["user"] = AnonymousUser()

        return scope
