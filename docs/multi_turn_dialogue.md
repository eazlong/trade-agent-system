# Agent多轮对话与信息路由系统

## 概述

本系统实现了支持多轮对话的Agent架构，采用混合方案来处理用户与子Agent之间的多轮交互。该方案结合了集中式路由和分布式处理的优点，既保证了安全性和可控性，又提供了良好的用户体验。

## 核心组件

### 1. SessionManager（会话管理器）
- 管理会话状态（IDLE、MULTI_TURN、FRAME_RUNNING）
- 使用Redis存储会话上下文，支持跨进程共享
- 实现会话超时机制，避免长时间占用资源

### 2. SupervisorAgent（主管代理）
- 意图识别和消息路由的核心
- 根据会话状态决定消息处理方式
- 支持多轮对话的开始和结束控制

### 3. 子Agent增强
- 所有基于LLMAgent的子Agent都支持多轮对话协议
- 能够根据内容判断是否需要继续对话
- 返回标准化的响应格式，包含多轮对话控制信息
- **记忆系统增强**：获取并利用最近的对话记录作为上下文

### 4. TelegramChannel（通道增强）
- 支持特殊命令来结束多轮对话
- 无缝集成多轮对话体验
- **响应格式处理**：正确解析并只显示Agent响应的content部分，而不是整个字典
- **长消息处理**：自动分割过长消息，避免Telegram长度限制错误

### 5. AgentTaskConsumer（消费者增强）
- 处理Agent响应格式，只返回content部分
- 保持向后兼容性

## 工作流程

### 多轮对话启动
1. 用户发送初始请求（例如："分析比特币市场"）
2. Supervisor识别意图为"analyze_market"
3. 路由到AnalystAgent
4. AnalystAgent判断需要深入交流，返回`start_multi_turn=true`
5. Supervisor将用户会话状态设为MULTI_TURN，绑定到AnalystAgent

### 多轮对话进行中
1. 用户发送后续消息
2. Supervisor检测到MULTI_TURN状态
3. 直接将消息路由到活动的AnalystAgent
4. AnalystAgent获取最近的对话记录作为上下文
5. AnalystAgent使用上下文与当前消息一起发送给LLM
6. AnalystAgent继续处理并可能再次返回`continue_conversation=true`

### 多轮对话结束
1. 用户发送结束命令（如"结束"、"停止"、"/end"等）或Agent判断对话已完成
2. Supervisor检测到结束信号
3. 清除用户会话状态
4. 恢复到普通意图识别模式

## 响应格式处理

为了解决显示问题，我们实现了响应格式处理机制：

1. **Agent返回标准化格式**：
   ```python
   {
       'content': '实际消息内容',
       'continue_conversation': True/False,
       'start_multi_turn': True/False,
       'agent_name': 'agent_identifier'
   }
   ```

2. **Consumer处理**：只提取content字段返回给Channel

3. **Channel处理**：额外保障机制，如果Consumer未能正确处理，Channel会再次解析响应格式

4. **长消息处理**：自动检测消息长度并按Telegram限制进行分割

## 记忆系统功能

### 对话历史管理
- **最近对话记录获取**：子Agent可以获取最近的对话历史作为上下文
- **上下文传递**：将对话历史与当前消息一同传递给LLM
- **历史更新**：每次交互后自动更新对话历史
- **长度控制**：限制对话历史长度以优化性能

### 实现细节
- `_get_recent_conversation_context()` 方法获取最近的对话记录
- 在LLM调用时将历史记录作为上下文消息传递
- 使用L1缓存（进程内）存储最近的对话历史
- 限制对话历史长度为最近10轮对话（20条消息）

## 实现亮点

### 1. 安全性
- 所有通信仍经过Supervisor审查
- 避免了子Agent绕过中央控制的潜在风险
- 实现了完整的审计轨迹

### 2. 用户体验
- 正确的响应显示：只显示content内容，不显示JSON格式
- 在保持控制的同时提供流畅的多轮对话体验
- 清晰的对话边界，用户可以随时结束对话
- 上下文保持连贯性
- 长消息自动分段，避免长度限制错误

### 3. 智能性
- **上下文感知**：Agent能够利用历史对话上下文
- **连贯对话**：基于历史记录提供更连贯的响应
- **记忆保持**：在多轮对话中记住关键信息

### 4. 可扩展性
- 模块化设计，易于添加新的Agent类型
- 支持多种通道（不仅限于Telegram）
- 会话状态独立管理

## 技术实现详情

### 子Agent模块（sub_agents.py）

导入：
```python
from __future__ import annotations
import logging
import time  # 用于时间戳记录
from .base import BaseAgent, AgentMessage, AgentResult
from .llm_client import LLMClient, is_fallback
from .prompt_loader import PromptLoader
from .registry import AgentRegistry
```

关键方法：
- `_get_recent_conversation_context()` - 获取最近对话记录
- `handle()` - 处理消息，整合上下文信息
- `_should_continue_conversation()` - 判断是否继续对话

### 记忆管理

对话历史存储在L1缓存中，格式：
```python
conv_history = [
    {'role': 'user', 'text': '...', 'ts': timestamp},
    {'role': 'assistant', 'text': '...', 'ts': timestamp},
    ...
]
```

限制最近20条记录（10轮对话），确保性能和上下文的相关性。

## 未来发展方向

1. **会话可视化**：提供管理界面查看正在进行的会话
2. **智能会话转移**：在多轮对话中智能转移到其他Agent
3. **增强的上下文管理**：更好的长期记忆能力
4. **多模态支持**：支持图像、数据表格等多媒体输入

## 故障排除

- **会话状态异常**：检查Redis连接和键过期设置
- **多轮对话无法启动**：验证Agent返回的数据格式
- **路由错误**：确认意图识别配置正确
- **响应显示为JSON**：确认Consumer和Channel的响应格式处理已更新
- **消息太长错误**：确认长消息处理功能已更新
- **上下文不连贯**：检查记忆系统是否正常工作
- **time模块错误**：确认time模块已正确导入

---
*此文档涵盖了Agent多轮对话与信息路由系统的完整实现和使用说明。*

---
*此文档涵盖了Agent多轮对话与信息路由系统的完整实现和使用说明。*