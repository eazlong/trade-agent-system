"""
Custom ASGI middleware that authenticates WebSocket connections
using JWT token passed via query parameter (?token=xxx).
"""

import logging
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.middleware import BaseMiddleware
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)


def _get_user(token_string):
    """Validate JWT token and return the Django user. Synchronous."""
    try:
        access_token = AccessToken(token_string)
        user_id = access_token["user_id"]
    except (TokenError, KeyError) as e:
        logger.warning(f"[TokenAuthMiddleware] Token decode error: {e}")
        return None

    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        return User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        logger.warning(f"[TokenAuthMiddleware] User {user_id} not found or inactive")
        return None
    except Exception as e:
        logger.warning(f"[TokenAuthMiddleware] User lookup error: {e}")
        return None


class TokenAuthMiddleware(BaseMiddleware):
    """
    ASGI middleware that reads JWT token from query parameter 'token'
    and sets scope['user']. Falls back to AnonymousUser if no token.
    """

    async def resolve_scope(self, scope):
        from django.contrib.auth.models import AnonymousUser

        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_list = query_params.get("token", [])

        user = None
        if token_list:
            token = token_list[0]
            logger.debug(
                f"[TokenAuthMiddleware] Attempting auth with token: {token[:20]}..."
            )
            user = await sync_to_async(_get_user)(token)
            if user:
                logger.info(f"[TokenAuthMiddleware] Authenticated user: {user.id}")
            else:
                logger.warning(
                    f"[TokenAuthMiddleware] Auth failed for token: {token[:20]}..."
                )
        else:
            logger.debug("[TokenAuthMiddleware] No token in query string")

        scope["user"] = user or AnonymousUser()
        return scope
