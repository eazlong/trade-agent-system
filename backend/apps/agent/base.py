from __future__ import annotations
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import uuid


# 延迟导入避免循环依赖
def _get_tool_registry():
    from apps.agent.tools.base import ToolRegistry

    return ToolRegistry


logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ""  # agent type or 'user'
    recipient: str = ""  # target agent type
    intent: str = ""
    payload: dict = field(default_factory=dict)
    timeout_ms: int = 30000
    user_id: str = "anonymous"
    origin: str = ""  # 下达命令的 gateway: web | api | lark | telegram | tui | scheduler | ""


@dataclass
class AgentResult:
    task_id: str = ""
    success: bool = True
    data: Any = None
    error: str = ""
    token_used: int = 0
    duration_ms: int = 0
    need_reroute: bool = False
    reroute_reason: str = ""
    reroute_suggestion: str = ""


class BaseAgent(ABC):
    """所有Agent的抽象基类（自研，无第三方框架依赖）"""

    name: str = "base"
    # 子类可覆盖：声明该 Agent 可用的工具名称列表
    _agent_tools: list[str] = []
    # 工具调用最大循环轮数
    _max_tool_rounds: int = 32

    def __init__(self):
        self._running = False
        self._skills_loader = None
        self._current_user_id: str = ""

    # ------------------------------------------------------------------ #
    #  工具 & 技能（公共逻辑）                                              #
    # ------------------------------------------------------------------ #

    def _get_skills_loader(self):
        """懒加载 skills_loader。"""
        if self._skills_loader is None:
            from apps.skill.loader import get_skills_loader

            self._skills_loader = get_skills_loader(self.name)
        return self._skills_loader

    def _get_tools_schema(self) -> list[dict]:
        """获取当前 Agent 可用的工具 schema。

        优先使用 _agent_tools 声明列表；未声明则回退到全局所有工具。
        """
        from apps.agent.tools.base import ToolRegistry

        if self._agent_tools:
            schemas = []
            for tool_name in self._agent_tools:
                tool = ToolRegistry.get(tool_name)
                if tool:
                    schemas.append(tool.schema)
                else:
                    logger.warning(
                        "[%s] tool %s not found in registry", self.name, tool_name
                    )
            return schemas
        return ToolRegistry.get_all_schemas()

    def _build_skills_section(self, base_prompt: str) -> str:
        """注入 always 技能 + 非 always 技能摘要到 system prompt。

        子类传入基础 prompt，返回增强后的 prompt。
        """
        loader = self._get_skills_loader()
        always_skills = loader.get_always_skills()
        if always_skills:
            skills_content = loader.load_skills_content(always_skills)
            base_prompt = f"{base_prompt}\n\n### Agent Skills\n{skills_content}"

        skill_summary = loader.build_summary()
        if skill_summary:
            base_prompt = (
                f"{base_prompt}\n\n"
                f"### Skills\n"
                f"以下技能扩展了你的能力, **能用则必须要使用**。使用 load_skill 工具加载完整内容。\n"
                f"如果用户的需求与以下技能描述相关，**一定要**使用 load_skill 工具。\n"
                f"{skill_summary}"
            )

        return base_prompt

    async def _execute_tool_call(self, tc) -> str:
        """执行单个工具调用，返回结果字符串。

        特殊处理 load_skill（传 agent_name）。
        自动注入 user_id（如果工具支持）。
        """
        try:
            # Push tool call notification
            from .task_tracker import tracker_context
            tracker = tracker_context.get(None)
            if tracker is not None:
                tracker.tool_call(tc.name, tc.arguments)

            if tc.name == "load_skill":
                from apps.agent.tools.load_skill import LoadSkillTool

                skill_name = tc.arguments.get("skill_name", "")
                tool = LoadSkillTool(agent_name=self.name)
                logger.info(
                    "[%s] executing tool: %s with args: %s",
                    self.name,
                    tc.name,
                    tc.arguments,
                )
                result = await tool.execute(skill_name=skill_name)
            else:
                # 注入 user_id（如果工具支持）
                if self._current_user_id and not tc.arguments.get("user_id"):
                    tc.arguments["user_id"] = self._current_user_id
                result = await self.run_tool(tc.name, **tc.arguments)

            if result.success:
                return str(result.data)
            return f"Error: {result.error}"
        except Exception as e:
            logger.warning("[%s] tool %s failed: %s", self.name, tc.name, e)
            return f"Error: {e}"

    def _try_parse_json_tool_call(
        self, content: str, tools: list[dict] | None = None
    ) -> dict | None:
        """尝试从 LLM 文本回复中解析 JSON 格式的 tool call（fallback 路径）。

        当 LLM 不支持原生 function calling 时，fallback 会让 LLM 输出
        {"tool": "<name>", "args": {...}} 格式。此方法解析这种格式并返回
        标准化的 tool call dict。

        额外支持两种文本伪 tool call（LLM 偶发把 native function call 写成
        文本块返回在 content 里，而不是原生 tool_calls 字段）：

        1. XML 格式:
           <tool_call><function=NAME><arguments>{...}</arguments></function></tool_call>
        2. DeepSeek DSML 文本格式（含全角/ASCII/退化标记变体）:
           <｜DSML｜function_calls><｜DSML｜invoke name="NAME">...
           <｜DSML｜parameter name="arg" string="true">value</｜DSML｜parameter>
           ...

        识别后转为标准 dict，让 _run_tool_loop 真正执行工具而不是直接 return
        content（否则用户会收到一段原始 XML/标记文本，任务无法继续）。
        """
        if not content or not content.strip():
            return None
        text = content.strip()
        # 去掉 markdown 代码块包裹
        if text.startswith("```"):
            idx = text.find("\n")
            text = text[idx + 1 :] if idx > 0 else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        # 提取 JSON 对象
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            import re

            m = re.search(
                r'\{[^{}]*"tool"\s*:\s*"[^"]+"\s*,\s*"args"\s*:\s*\{[^{}]*\}\s*\}',
                text,
            )
            if not m:
                # XML 伪 tool call: <tool_call>...<function=NAME>...<arguments>{...}</arguments>...</function></tool_call>
                xml = self._try_parse_xml_tool_call(text)
                if xml is not None:
                    return xml
                # DeepSeek DSML 文本伪 tool call（含标记退化变体）
                return self._try_parse_dsml_tool_call(text, tools)
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        if isinstance(data, dict) and "tool" in data and "args" in data:
            return data
        return None

    def _try_parse_xml_tool_call(self, text: str) -> dict | None:
        """Parse XML-formatted pseudo tool calls from LLM content.

        Recognizes:
            <tool_call>
              <function=NAME>
              <arguments>{JSON}</arguments>
              </function>
            </tool_call>

        Returns {"tool": NAME, "args": {...}} on success, None otherwise.
        """
        import re

        m = re.search(
            r"<\s*tool_call\s*>\s*<\s*function\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*>"
            r"(.*?)</\s*function\s*>\s*</\s*tool_call\s*>",
            text,
            re.DOTALL,
        )
        if not m:
            return None
        name = m.group(1).strip()
        inner = m.group(2) or ""
        args_match = re.search(
            r"<\s*arguments\s*>(.*?)</\s*arguments\s*>",
            inner,
            re.DOTALL,
        )
        if args_match is None:
            args: dict = {}
        else:
            raw = args_match.group(1).strip()
            if not raw:
                args = {}
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return None
                if not isinstance(parsed, dict):
                    return None
                args = parsed
        return {"tool": name, "args": args}

    # ------------------------------------------------------------------ #
    #  DeepSeek DSML 文本格式 tool call（fallback 路径）                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_dsml_text(text: str) -> str | None:
        """将 DSML 标记的已知退化形态归一化为 `<|DSML|tag ...>`。

        DeepSeek 官方文档格式使用全角标记 <｜DSML｜tag>；实际输出常退化为
        ASCII 形式：
          <|DSML|tag>            （单竖线）
          <||DSML||tag>          （双竖线，HF#209）
          <| |DSML| |tag>        （竖线间带空格，用户聊天截图形态）
        归一化后统一为 <|DSML|tag ...>，便于正则解析。

        Returns: 归一化后的文本；若文本中没有任何 DSML 标记形态则返回 None。
        """
        import re

        if "DSML" not in text:
            return None
        t = text.replace("｜", "|")

        def _canon(m: re.Match) -> str:
            close = m.group(1)
            tag = m.group(2)
            attrs = m.group(3) or ""
            return f"<{close}|DSML|{tag}{attrs}>"

        # <| |DSML| |tag attrs> / <||DSML||tag> / <｜DSML｜tag> / </| |DSML| |tag>
        return re.sub(
            r"<\s*(/?)\s*\|(?:\s*\|)*\s*DSML\s*\|(?:\s*\|)*\s*"
            r"([A-Za-z_][A-Za-z0-9_]*)([^>]*)>",
            _canon,
            t,
        )

    @staticmethod
    def _parse_dsml_parameters(inner: str) -> dict:
        """解析 DSML parameter 元素为参数字典。

        <|DSML|parameter name="arg" string="true">value</|DSML|parameter>
        string="true"（或缺省）→ 原样字符串；string="false" → 尝试 JSON 字面量。
        """
        import re

        args: dict = {}
        for pm in re.finditer(
            r"<\|DSML\|parameter\s+name=\"([^\"]+)\""
            r"(?:\s+string=\"(true|false)\")?>(.*?)</\|DSML\|parameter>",
            inner,
            re.DOTALL,
        ):
            name = pm.group(1)
            is_string = pm.group(2) != "false"
            raw = pm.group(3).strip()
            if is_string:
                value = raw
            else:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
            args[name] = value
        return args

    @staticmethod
    def _infer_dsml_tool_by_args(args: dict, tools: list[dict]) -> str | None:
        """退化形态缺失 invoke/tool 名时，按参数名匹配工具 schema 推断。

        只有唯一候选时才返回该工具名，否则返回 None（宁可保持原文输出，
        也不盲猜调用错误工具）。
        """
        if not args or not tools:
            return None
        given = set(args.keys())
        candidates: list[str] = []
        for schema in tools:
            fn = schema.get("function") or {}
            params = (fn.get("parameters") or {}).get("properties") or {}
            props = params.keys() if isinstance(params, dict) else []
            if given.issubset(set(props)):
                name = fn.get("name", "")
                if name:
                    candidates.append(name)
        return candidates[0] if len(candidates) == 1 else None

    def _try_parse_dsml_tool_call(
        self, text: str, tools: list[dict] | None = None
    ) -> dict | None:
        """解析 DeepSeek DSML 文本格式的伪 tool call。

        支持两种结构：
        1. 包装形式（官方文档）：
           <|DSML|tool_calls>...<|DSML|invoke name="TOOL">...
           <|DSML|parameter name="a" string="true">v</|DSML|parameter>
           </|DSML|invoke>...</|DSML|tool_calls>
           （root 名可能是 tool_calls / function_calls；标记全角/ASCII/退化均可）
        2. 单调用退化形式（用户截图）：
           <| |DSML| |tool_call>...</| |DSML| |parameter name="a" string="true">v
           </| |DSML| |parameter></| |DSML| |tool_call>
        3. 无标记形式（服务端剥掉 DSML 标记）：
           <function_calls><invoke name="TOOL">...</function_calls>

        Returns: {"tool": NAME, "args": {...}}；NAME 缺失且无法从 tools schema
        推断时返回 None。
        """
        import re

        if not text:
            return None
        normalized = self._normalize_dsml_text(text)

        # --- 1/2. 带 DSML 标记的形态（含退化） ---
        if normalized is not None:
            work = normalized

            # 1. 包装形式：root tool_calls/function_calls > invoke > parameter
            root_m = re.search(
                r"<\|DSML\|(?:tool_calls|function_calls)>(.*?)"
                r"</\|DSML\|(?:tool_calls|function_calls)>",
                work,
                re.DOTALL,
            )
            if root_m:
                invokes = re.findall(
                    r"<\|DSML\|invoke(?:\s+name=\"([A-Za-z_][A-Za-z0-9_]*)\")?>"
                    r"(.*?)</\|DSML\|invoke>",
                    root_m.group(1),
                    re.DOTALL,
                )
                if not invokes:
                    return None
                name, invoke_inner = invokes[0]
                name = name or ""
                args = self._parse_dsml_parameters(invoke_inner)
                if not name and tools:
                    name = self._infer_dsml_tool_by_args(args, tools) or ""
                return {"tool": name, "args": args} if name else None

            # 2. 单调用退化形式：root tool_call（可带 name 属性）> parameter
            single_m = re.search(
                r"<\|DSML\|tool_call(?:\s+name=\"([A-Za-z_][A-Za-z0-9_]*)\")?>"
                r"(.*?)</\|DSML\|tool_call>",
                work,
                re.DOTALL,
            )
            if single_m:
                name = single_m.group(1) or ""
                args = self._parse_dsml_parameters(single_m.group(2))
                if not name and tools:
                    name = self._infer_dsml_tool_by_args(args, tools) or ""
                return {"tool": name, "args": args} if name else None
            return None

        # --- 3. 无标记形式（文本中没有 DSML 标记，服务端可能剥掉了它们） ---
        work = text
        bare_m = re.search(
            r"<function_calls>(.*?)</function_calls>", work, re.DOTALL
        )
        if not bare_m:
            return None
        invokes = re.findall(
            r"<invoke\s+name=\"([A-Za-z_][A-Za-z0-9_]*)\"[^>]*>(.*?)</invoke>",
            bare_m.group(1),
            re.DOTALL,
        )
        if not invokes:
            return None
        name, invoke_inner = invokes[0]
        args: dict = {}
        for pm in re.finditer(
            r"<parameter\s+name=\"([^\"]+)\""
            r"(?:\s+string=\"(true|false)\")?>(.*?)</parameter>",
            invoke_inner,
            re.DOTALL,
        ):
            pname = pm.group(1)
            is_string = pm.group(2) != "false"
            raw = pm.group(3).strip()
            if is_string:
                value = raw
            else:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
            args[pname] = value
        return {"tool": name, "args": args}

    async def _run_tool_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 2048,
        on_tool_result=None,
    ) -> tuple[str, bool]:
        """通用工具调用循环。子类可复用。

        Returns:
            (final_content, is_fallback)
        """
        from .llm_client import LLMClient, is_fallback

        llm = LLMClient.get_instance()
        had_tool_results = False  # tracks whether any tools were executed

        for round_idx in range(self._max_tool_rounds):
            resp = await llm.chat_with_tools(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
            )
            content_len = len(resp.content) if resp.content else 0
            logger.info(
                "[%s] _run_tool_loop round %d/%d: has_tool_calls=%s, "
                "content_len=%d, reasoning=%s",
                self.name,
                round_idx + 1,
                self._max_tool_rounds,
                resp.has_tool_calls,
                content_len,
                bool(resp.reasoning_content),
            )
            if not resp.has_tool_calls:
                content = resp.content
                if is_fallback(content):
                    logger.warning(
                        "[%s] _run_tool_loop round %d: detected fallback marker",
                        self.name, round_idx + 1,
                    )
                    return ("", True)

                # 检查是否是 fallback 路径的 JSON tool call
                json_tc = self._try_parse_json_tool_call(content, tools)
                if json_tc is None:
                    if content and content.strip():
                        return (content, False)
                    # 空内容但之前有工具执行结果，强制 LLM 总结
                    if had_tool_results:
                        logger.warning(
                            "[%s] LLM returned empty content after tool execution, "
                            "forcing summary",
                            self.name,
                        )
                        break
                    # 既无工具也无内容，跳过本轮
                    logger.debug(
                        "[%s] _run_tool_loop round %d: empty content, no tool calls, "
                        "skipping (had_tool_results=%s)",
                        self.name, round_idx + 1, had_tool_results,
                    )
                    continue

                # 将 JSON tool call 转为标准 ToolCallRequest
                tc = type(
                    "ToolCallRequest",
                    (),
                    {
                        "call_id": "json_tc_0",
                        "name": json_tc["tool"],
                        "arguments": json_tc["args"],
                    },
                )()
                tool_calls = [tc]
            else:
                tool_calls = resp.tool_calls

            # 执行工具
            tool_results = []
            for tc in tool_calls:
                result_text = await self._execute_tool_call(tc)
                if on_tool_result:
                    await on_tool_result(tc.name, result_text)
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.call_id,
                        "content": result_text,
                    }
                )
                had_tool_results = True
                # Update last_alive so watchdog knows task is still making progress
                from .task_tracker import tracker_context
                tracker = tracker_context.get(None)
                if tracker is not None:
                    tracker.alive()

            assistant_msg = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            if resp.reasoning_content:
                assistant_msg["reasoning_content"] = resp.reasoning_content
            messages.append(assistant_msg)
            messages.extend(tool_results)

        # 超出轮次或 LLM 返回空内容但有工具结果，让 LLM 总结
        logger.info(
            "[%s] _run_tool_loop exiting loop: had_tool_results=%s, "
            "message_count=%d, requesting summary from LLM",
            self.name, had_tool_results, len(messages),
        )
        history_text = "\n".join(
            f"[{m.get('role', 'user')}]: {m.get('content', '')}" for m in messages
        )
        final = await llm.chat(
            system=system,
            user=f"{history_text}\n\n请根据以上工具调用结果给出最终回答。",
            max_tokens=max_tokens,
        )
        final_len = len(final) if final else 0
        if not final or not final.strip():
            logger.warning(
                "[%s] _run_tool_loop summary returned empty content "
                "(final_len=%d, had_tool_results=%s, messages=%d)",
                self.name, final_len, had_tool_results, len(messages),
            )
        return (final, False)

    async def run_tool(self, tool_name: str, **kwargs) -> Any:
        """执行一个工具，优先从全局ToolRegistry查找"""
        registry = _get_tool_registry()
        tool = registry.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool {tool_name!r} not found in registry")
        result = await tool.execute(**kwargs)
        logger.debug("[%s] tool=%s success=%s", self.name, tool_name, result.success)
        return result

    @abstractmethod
    async def handle(self, message: AgentMessage, on_tool_result=None) -> AgentResult:
        """处理一条消息，返回结果"""

    async def start(self) -> None:
        self._running = True
        logger.info("[%s] started", self.name)

    async def stop(self) -> None:
        self._running = False
        logger.info("[%s] stopped", self.name)
