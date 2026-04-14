from __future__ import annotations

import asyncio
import logging
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.live import Live

from .base import BaseChannel

logger = logging.getLogger(__name__)

console = Console()


class StreamingPanel:
    """带流式内容更新的 Rich Panel（协程安全）"""

    def __init__(self, title: str = "Agent"):
        self._title = title
        self._lines: list[str] = []
        self._current_line = ""
        self._done = False

    def append(self, token: str) -> None:
        """追加 token，自动处理换行"""
        self._current_line += token
        while "\n" in self._current_line:
            line, self._current_line = self._current_line.split("\n", 1)
            self._lines.append(line)

    def finish(self) -> None:
        """标记完成，追加剩余内容"""
        if self._current_line:
            self._lines.append(self._current_line)
            self._current_line = ""
        self._done = True

    def render(self) -> Panel:
        """渲染当前内容"""
        all_lines = self._lines + ([self._current_line] if self._current_line else [])
        content = "\n".join(all_lines)
        status = "✓ 完成" if self._done else "▌ 正在输入..."
        title = f"{self._title} [{status}]"
        if not content:
            content = " "
        return Panel(
            Text(content, style="cyan"),
            title=title,
            style="dim",
            width=console.width,
        )


class TUIChannel(BaseChannel):
    """
    Terminal UI Channel：直接在终端与 Supervisor 对话。

    特性：
    - 流式输出（LLM token 逐字实时显示）
    - Rich Markdown 渲染
    - 多轮对话上下文保持
    - 命令：/help, /status, /clear, /history, /quit
    """

    name = "tui"

    def __init__(self, supervisor_agent=None):
        self._supervisor = supervisor_agent
        self._running = False
        self._user_id = "tui_user"
        self._task: Optional[asyncio.Task] = None
        self._conv_history: list[dict] = []
        self._streaming_panel: Optional[StreamingPanel] = None
        self._streaming_task: Optional[asyncio.Task] = None
        self._use_redis = supervisor_agent is None

    async def send_message(self, text: str) -> None:
        """向终端用户发送消息（Rich Markdown 渲染）"""
        if not text:
            return
        try:
            md = Markdown(text)
            console.print(md)
        except Exception:
            console.print(text)

    async def send_photo(self, photo_bytes: bytes, caption: str = "") -> None:
        console.print(f"[dim][图片] {caption}[/]" if caption else "[dim][图片][/]")

    async def start(self) -> None:
        console.clear()
        self._print_banner()
        console.print("[dim]输入 /help 查看命令，/quit 退出\n[/]")
        self._running = True
        self._task = asyncio.create_task(self._input_loop())
        await self._task

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        console.print("[dim]TUI 已关闭[/]")
        logger.info("[TUIChannel] stopped")

    def _print_banner(self) -> None:
        banner = Text()
        banner.append(
            "╔══════════════════════════════════════════╗\n", style="bold cyan"
        )
        banner.append("║      TradeAgent TUI Channel          ║\n", style="bold cyan")
        banner.append("║      直接与 Supervisor 对话           ║\n", style="dim cyan")
        banner.append("╚══════════════════════════════════════════╝", style="bold cyan")
        console.print(Panel(banner, style="cyan"))

    # ------------------------------------------------------------------ #
    #  主循环                                                            #
    # ------------------------------------------------------------------ #

    async def _input_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                raw = await loop.run_in_executor(
                    None, lambda: Prompt.ask("[bold green]»[/] ")
                )
                text = raw.strip()
                if not text:
                    continue

                cmd = text.lower()
                if cmd in ("/quit", "/exit", "/q"):
                    await self.stop()
                    break
                if cmd == "/help":
                    self._print_help()
                    continue
                if cmd == "/status":
                    self._print_status()
                    continue
                if cmd == "/clear":
                    console.clear()
                    self._print_banner()
                    continue
                if cmd == "/history":
                    self._print_history()
                    continue

                await self._handle_user_input(text)

            except (EOFError, KeyboardInterrupt):
                await self.stop()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TUI input error: {e}")
                console.print(f"[red]错误: {e}[/]")

    async def _handle_user_input(self, text: str) -> None:
        """处理用户输入，带流式显示"""
        separator = Text("─" * 50, style="dim")
        console.print(separator)

        # 保存用户输入
        self._conv_history.append({"role": "user", "text": text})

        # 启动流式渲染
        self._streaming_panel = StreamingPanel(title="🤖 Agent")
        panel_task = asyncio.create_task(self._render_stream())
        panel_task.add_done_callback(lambda _: None)

        try:
            # 带超时调用 Agent
            result = await asyncio.wait_for(
                self._stream_agent_response(text),
                timeout=360.0,
            )
        except asyncio.TimeoutError:
            console.print("\n[yellow]⏱ Agent 处理超时（360s）[/]")
            result = None
        except Exception as e:
            console.print(f"\n[red]✗ Agent 错误: {e}[/]")
            result = None

        # 完成流式渲染
        if self._streaming_panel:
            self._streaming_panel.finish()
        await panel_task

        # 渲染最终 Markdown
        if result:
            self._conv_history.append({"role": "assistant", "text": result})
            if len(self._conv_history) > 40:
                self._conv_history = self._conv_history[-40:]
            console.print()
            try:
                md = Markdown(result)
                console.print(md)
            except Exception:
                console.print(result)
            self._persist_history(text, result)

        console.print()

    async def _render_stream(self) -> None:
        """渲染流式输出面板（异步刷新）"""
        if not self._streaming_panel:
            return
        try:
            with Live(
                self._streaming_panel.render(),
                console=console,
                refresh_per_second=15,
                transient=False,
            ) as live:
                while not self._streaming_panel._done:
                    await asyncio.sleep(0.05)  # noqa: F841 used for yield
                    live.update(self._streaming_panel.render())
                # 最后刷新一次
                live.update(self._streaming_panel.render())
        except Exception:
            pass

    async def _stream_agent_response(self, text: str) -> Optional[str]:
        """调用 Agent 并返回响应（支持进程内调用和 Redis 通信）"""
        if self._use_redis:
            return await self._call_agent_via_redis(text)
        return await self._call_agent_direct(text)

    async def _call_agent_via_redis(self, text: str) -> Optional[str]:
        """通过 Redis Streams 与已运行的 SupervisorAgent 通信"""
        from apps.agent.bus import publish, build_agent_task, wait_reply, AGENT_TASKS
        import json

        msg = build_agent_task(user_id=self._user_id, payload={"text": text})
        await publish(AGENT_TASKS, msg)

        reply = await wait_reply(msg["task_id"], timeout=360)
        if reply is None:
            return None

        # 提取 content（与 TelegramChannel 逻辑一致）
        try:
            if isinstance(reply, str) and reply.strip().startswith("{"):
                parsed = json.loads(reply)
                if isinstance(parsed, dict) and "content" in parsed:
                    return parsed["content"]
                return reply
            elif isinstance(reply, dict) and "content" in reply:
                return reply["content"]
            return str(reply)
        except (json.JSONDecodeError, TypeError):
            return str(reply)

    async def _call_agent_direct(self, text: str) -> Optional[str]:
        """进程内直接调用 SupervisorAgent（保留向后兼容）"""
        from apps.agent.llm_client import LLMClient
        from apps.agent.prompt_loader import PromptLoader

        llm = LLMClient.get_instance()
        system_prompt = PromptLoader.load("supervisor")
        user_prompt = self._build_prompt_with_history(text)

        def on_chunk(token: str) -> None:
            if self._streaming_panel:
                self._streaming_panel.append(token)

        try:
            full_text = await llm.chat_stream(
                system=system_prompt,
                user=user_prompt,
                on_chunk=on_chunk,
                max_tokens=2048,
            )
        except Exception as e:
            logger.warning(f"流式调用失败，降级为同步: {e}")
            console.print("[dim]（降级为同步模式）[/]")
            result = await llm.chat(
                system=system_prompt,
                user=user_prompt,
                max_tokens=2048,
            )
            if self._streaming_panel:
                self._streaming_panel.append(result)
            return result

        return full_text

    def _build_prompt_with_history(self, text: str) -> str:
        if not self._conv_history:
            return text
        history_block = "\n".join(
            f"{t['role']}: {t['text']}" for t in self._conv_history[-10:]
        )
        return f"[对话历史]\n{history_block}\n\n[当前消息]\n{text}"

    def _persist_history(self, user_text: str, agent_text: str) -> None:
        """持久化对话历史到 L1"""
        try:
            from apps.memory.manager import MemoryManager

            mm = MemoryManager(agent_type="supervisor", user_id=self._user_id)
            updated = mm._l1.get("conv_history", [])[-18:] + [
                {"role": "user", "text": user_text[:200], "ts": 0},
                {"role": "assistant", "text": agent_text[:200], "ts": 0},
            ]
            mm.write_l1("conv_history", updated)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  命令                                                              #
    # ------------------------------------------------------------------ #

    def _print_help(self) -> None:
        help_md = """## TUI 命令

| 命令 | 说明 |
|------|------|
| `/quit` 或 `/exit` | 退出 TUI |
| `/help` | 显示本帮助 |
| `/status` | 查看框架运行状态 |
| `/clear` | 清屏 |
| `/history` | 查看本次会话历史 |

直接输入消息即可与 Agent 对话，支持流式输出。"""
        console.print(Markdown(help_md))

    def _print_status(self) -> None:
        try:
            from apps.agent.frame_manager import FrameManager

            fm = FrameManager.get_instance()
            status = fm.status()
            console.print(
                Panel(Text(status, style="cyan"), title="框架状态", style="green")
            )
        except Exception as e:
            console.print(f"[red]获取状态失败: {e}[/]")

    def _print_history(self) -> None:
        if not self._conv_history:
            console.print("[dim]暂无对话历史[/]")
            return
        for entry in self._conv_history[-20:]:
            role = entry.get("role", "user")
            content = entry.get("text", "")
            style = "cyan" if role == "user" else "green"
            prefix = "👤" if role == "user" else "🤖"
            console.print(f"[{style}]{prefix} {role}:[/] {content}")
