"""诊断 WebSocket 握手 403 的 JWT 根因(签名/过期/用户缺失)。

为何用:握手被拒时 uvicorn 只打印 "connection rejected (403 Forbidden)",
本脚本按顺序给出确切结论:
  1. 载荷 claims(user_id / iat / exp / 有效期)
  2. 仅验签(忽略过期)—— 排除 SECRET_KEY 不匹配
  3. simplejwt AccessToken 完整校验(与 ChatConsumer 握手路径一致)
  4. 用户存在性与 is_active

用法:
  DJANGO_SETTINGS_MODULE=core.settings.dev python scripts/diagnose_ws_token.py <token>

退出码: 0=用户有效  1=token 无效(签名/过期/格式)  2=用户不存在或禁用
"""

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")
# 保证脚本从任何 cwd 运行都能 import backend/ (core 包)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django  # noqa: E402

django.setup()

import jwt  # noqa: E402
from django.conf import settings  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from apps.notify.middleware import _token_failure_reason  # noqa: E402


def main(token: str) -> int:
    # 1) 载荷(不验签)
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:
        print(f"FAIL: 无法解析 token 载荷: {exc!r}")
        return 1

    user_id = payload.get("user_id")
    iat = payload.get("iat")
    exp = payload.get("exp")
    print(f"claims: user_id={user_id}")
    print(
        "claims: iat="
        + (datetime.fromtimestamp(iat, timezone.utc).isoformat() if iat else None)
    )
    print(
        "claims: exp="
        + (datetime.fromtimestamp(exp, timezone.utc).isoformat() if exp else None)
    )
    if iat and exp:
        ttl = exp - iat
        now = int(datetime.now(timezone.utc).timestamp())
        remaining = exp - now
        print(f"claims: ttl(exp-iat)={ttl}s, 剩余={remaining}s (≈{remaining / 60:.1f} 分钟)")
        print(
            "  注: ttl!=3600 通常只是 refresh 签发时继承了 refresh 的 iat,"
            "以剩余时间是否≈60分钟为准"
        )

    # 2) 仅验签(忽略过期),排除 SECRET_KEY 不匹配
    try:
        jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
        print("signature: OK (当前 SECRET_KEY 可验签)")
    except jwt.InvalidSignatureError:
        print("结论: 签名校验失败 —— SECRET_KEY 与签发时不一致,或 token 被改动")
        return 1
    except Exception as exc:
        print(f"结论: 签名校验异常: {exc!r}")
        return 1

    # 3) simplejwt 完整校验(与握手路径一致)
    try:
        AccessToken(token)
        print("simplejwt: AccessToken 校验通过")
    except Exception as exc:
        print(f"simplejwt: AccessToken 校验失败 —— {_token_failure_reason(exc)}")

    # 4) 用户存在性
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        print(f"user: {user.username} <{user.email}> is_active={user.is_active}")
        return 0 if user.is_active else 2
    except User.DoesNotExist:
        print(f"user: {user_id} 不存在于当前数据库")
        return 2
    except Exception as exc:
        print(f"user: 查询异常 {exc!r}")
        return 2


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(3)
    sys.exit(main(sys.argv[1]))