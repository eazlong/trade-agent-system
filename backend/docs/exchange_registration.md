# 交易所账户注册指南

## 方法1: 通过API接口

### 添加新的交易所账户

发送POST请求到 `/api/exchange/accounts/` 端点：

```json
{
  "exchange": "binance",
  "label": "我的币安账户",
  "api_key": "YOUR_API_KEY_HERE",
  "api_secret": "YOUR_API_SECRET_HERE"
}
```

**参数说明:**
- `exchange`: 交易所名称 (如 "binance", "okx", "bybit")
- `label`: 账户标签/描述 (可选)
- `api_key`: 交易所API密钥
- `api_secret`: 交易所API密钥密钥
- `is_active`: 账户是否激活 (默认为true)

> **注意**: API密钥会自动使用FERNET_KEY进行加密存储，不会以明文形式保存在数据库中。

## 方法2: 使用Django管理命令

运行以下命令来安全地添加交易所账户：

```bash
cd backend
DJANGO_SETTINGS_MODULE=core.settings.dev python manage.py add_exchange_account --exchange=binance --label="我的币安账户"
```

命令将提示您输入API密钥和密钥，不会在屏幕上显示输入内容。

## 配置要求

确保在 `.env` 文件中设置了 `FERNET_KEY`：

```bash
FERNET_KEY=your_fernet_encryption_key_here
```

生成Fernet密钥的方法：
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 支持的交易所

目前支持的交易所包括：
- Binance
- OKX
- Bybit

更多交易所可以通过扩展系统轻松添加。