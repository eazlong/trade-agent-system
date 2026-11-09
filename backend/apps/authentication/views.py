from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User
from .preferences import (
    HEADER_SYMBOLS_MAX,
    HEADER_SYMBOLS_MIN,
    HeaderSymbolsError,
    default_header_symbols,
    ensure_header_symbols_subscribed,
    get_header_symbols,
    normalize_header_symbols,
)
from .serializers import RegisterSerializer, LoginSerializer, UserSerializer


class RefreshRateThrottle(SimpleRateThrottle):
    """令牌刷新的**独立配额**，不与匿名流量共用同一个桶。

    为什么需要它（txy 生产实测，2026-11-05 16:41–16:55 CST）：`token_refresh` 是
    `AllowAny`，因此在全局默认限流下**落进 `AnonRateThrottle` 的 `anon` 桶**——和
    「未登录访客」共用同一个 20/min 配额。而交易页一次挂载会并发发十几个请求，令牌
    过期时它们会同时 401（前端侧已用单飞把并发刷新压到 1 发；这一层是后端侧的对冲）。

    续会话本来是一小时一次的低频动作（`ACCESS_TOKEN_LIFETIME`），让它和匿名流量挤
    同一个桶，等于「同一 IP 上有没有人在翻公开接口」决定用户还能不能续会话。
    独立 scope 之后互不影响。

    仍然保留速率上限：这个端点接受任意字符串做输入（`RefreshToken(...)` 要验签），
    没有上限等于一个免费的 CPU 打点。

    `scope` 对应的速率在 `settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` 的
    `auth-refresh` 键里（见 `core/settings/base.py`）。

    按 **IP** 计数而不是按用户：这个端点恰恰是「访问令牌已经无效」的时候被调用，
    那时拿不到用户身份，按用户键控等于不限流。
    """

    scope = "auth-refresh"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }

    scope = "auth-refresh"


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
@throttle_classes([RefreshRateThrottle])
def token_refresh(request: Request) -> Response:
    """用刷新令牌换新访问令牌。

    限流走**独立配额**（`auth-refresh`），不占 `anon` 桶——理由与故障经过见
    `RefreshRateThrottle` 的文档字符串。
    """
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


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def preferences(request: Request) -> Response:
    """用户级界面偏好：**顶栏行情币种**。`GET` 读、`PUT` 整份替换。

    为什么是 PUT 整份而不是 `POST /symbols/append` 之类的增量：清单是**有序的**，
    顺序就是顶栏的显示顺序。增量接口要么带位置（于是「删两个、插一个、换顺序」变成
    三次请求、中间态还合法），要么不带位置（于是顺序不可编辑）。整份替换让「用户按下
    保存」与「库里那一行」一一对应，校验也就只需要一处。

    `GET` 除了返回清单，还会**顺手把清单补进 WS 订阅**（`ensure_feed_symbols`，幂等
    尽力而为）：ticker 端点只读 WS 内存存储、不做 REST 回退，用户在新机器/新进程里
    登录时若没人补订阅，他配的那些档会永远显示「—」。放在读路径上是刻意的——它必须
    在「用户看见顶栏之前」发生，而写路径只覆盖「刚刚改过」的那一次。

    校验失败回 400 且 `error` 是**给人看的原文**（前端直接显示，不自己翻译状态码）：
    条数越界、重复、写法读不懂，三种都在 `preferences.normalize_header_symbols` 里。
    """
    user: User = request.user  # type: ignore[assignment]

    if request.method == "GET":
        symbols = get_header_symbols(user)
        ensure_header_symbols_subscribed(symbols)
        return Response(_header_symbols_payload(user, symbols))

    raw = request.data.get("header_symbols") if isinstance(request.data, dict) else None
    try:
        symbols = normalize_header_symbols(raw)
    except HeaderSymbolsError as e:
        return Response({"error": str(e)}, status=400)

    user.header_symbols = symbols
    user.save(update_fields=["header_symbols"])

    # 写后补订阅：用户刚把某个品种配进来，下一轮轮询就该有数，而不是等下一次重连。
    ensure_header_symbols_subscribed(symbols)
    return Response(_header_symbols_payload(user, symbols))


def _header_symbols_payload(user: User, symbols: list[str]) -> dict:
    """偏好响应的形状。把边界一并回给前端，让页面上的按钮状态与后端同一份规矩。"""
    return {
        "header_symbols": symbols,
        # 用户从没改过（库里是 null）时为 True —— 前端据此把「恢复默认」按钮置灰
        "is_default": getattr(user, "header_symbols", None) is None,
        "min": HEADER_SYMBOLS_MIN,
        "max": HEADER_SYMBOLS_MAX,
        "default": default_header_symbols(),
    }


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
