# 测试标准

## 覆盖率要求

- **最低覆盖率**: 80%
- **关键模块覆盖率**: 95%（apps/trading/, apps/risk/, apps/agent/）

## 测试类型

### 1. 单元测试
- 测试单个函数/方法
- 使用 Mock 隔离外部依赖
- 文件位置: `apps/*/tests/test_*.py`

### 2. 集成测试
- 测试 API 端点
- 测试数据库操作
- 文件位置: `apps/*/tests/integration/`

### 3. E2E 测试
- 测试完整用户流程
- 使用 Playwright
- 文件位置: `tests/e2e/`

### 4. DST 仿真测试
- 确定性仿真测试
- 故障注入测试
- 文件位置: `apps/*/tests/dst/`

## 测试命令

```bash
# 单元测试
pytest apps/ --cov=apps --cov-report=term-missing

# 集成测试
pytest apps/*/tests/integration/ -v

# DST 仿真测试
SIMULATION_MODE=True pytest apps/*/tests/dst/ -v

# 全量测试
pytest --cov=apps --cov-report=html
```

## Mock 标准

### 交易所 API Mock
```python
@pytest.fixture
def mock_exchange():
    with patch('ccxt.binance') as mock:
        mock.return_value.create_order.return_value = {'id': '123'}
        yield mock
```

### Redis Mock
```python
@pytest.fixture
def mock_redis():
    with patch('redis.Redis') as mock:
        yield mock
```

### Celery 任务 Mock
```python
@pytest.fixture
def mock_celery():
    with patch('celery.shared_task') as mock:
        yield mock
```

## 禁止事项

- ❌ 在测试中使用 `time.sleep()`（使用 `freezegun` 或 Mock）
- ❌ 跳过失败的测试（应修复实现）
- ❌ 修改测试以匹配错误的实现
- ❌ 在测试中硬编码敏感信息