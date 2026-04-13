from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .serializers import RegisterSerializer, LoginSerializer, UserSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request: Request) -> Response:
    serializer = RegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)
    user = serializer.save()
    tokens = _get_tokens(user)
    return Response({"user": UserSerializer(user).data, **tokens}, status=201)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request: Request) -> Response:
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)
    user: User = serializer.validated_data["user"]
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])
    tokens = _get_tokens(user)
    return Response({"user": UserSerializer(user).data, **tokens})


@api_view(["POST"])
@permission_classes([AllowAny])
def token_refresh(request: Request) -> Response:
    refresh_token = request.data.get("refresh")
    if not refresh_token:
        return Response({"error": "refresh token required"}, status=400)
    try:
        refresh = RefreshToken(refresh_token)
        return Response({"access": str(refresh.access_token)})
    except Exception as e:
        return Response({"error": str(e)}, status=401)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request: Request) -> Response:
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout(request: Request) -> Response:
    try:
        refresh_token = request.data.get("refresh")
        if refresh_token:
            RefreshToken(refresh_token).blacklist()
    except Exception:
        pass
    return Response({"detail": "logged out"})


def _get_tokens(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}
