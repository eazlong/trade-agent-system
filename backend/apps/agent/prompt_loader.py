from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

PROMPT_BASE = Path(__file__).parent.parent.parent / "prompts"

_cache: dict[str, str] = {}
_meta_cache: dict[str, dict[str, Any]] = {}


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter，返回 (metadata, body)。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}, text

    frontmatter = match.group(1)
    body = text[match.end() :]
    meta: dict[str, Any] = {}

    for line in frontmatter.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # 解析列表: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
            meta[key] = [i.strip().strip('"').strip("'") for i in items if i.strip()]
        # 解析布尔
        elif value.lower() == "true":
            meta[key] = True
        elif value.lower() == "false":
            meta[key] = False
        # 解析数字
        elif value.isdigit():
            meta[key] = int(value)
        else:
            meta[key] = value.strip('"').strip("'")

    return meta, body


class PromptLoader:
    @classmethod
    def load(cls, name: str, version: str = "v1") -> str:
        """
        加载指定版本的Prompt文件。
        变更必须新建版本目录（ADR-004），可追溯可回滚。
        动态注入当前时间，防止LLM使用过期信息。
        自动剥离 YAML frontmatter。
        """
        cache_key = f"{version}/{name}"
        if cache_key not in _cache:
            file_path = PROMPT_BASE / version / f"{name}.txt"
            if not file_path.exists():
                raise FileNotFoundError(f"Prompt not found: {file_path}")
            raw_text = file_path.read_text(encoding="utf-8")
            _, body = _parse_frontmatter(raw_text)
            _cache[cache_key] = body

        prompt = _cache[cache_key]
        # 动态注入当前时间，替换占位符
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt = prompt.replace("{{CURRENT_DATETIME}}", current_date)
        return prompt

    @classmethod
    def load_with_meta(
        cls, name: str, version: str = "v1"
    ) -> tuple[str, dict[str, Any]]:
        """加载 Prompt 内容和元数据，返回 (content, metadata)。"""
        cache_key = f"{version}/{name}"
        meta_key = f"{version}/{name}/meta"

        if meta_key not in _meta_cache:
            text = cls.load(name, version)
            meta, body = _parse_frontmatter(text)
            _meta_cache[meta_key] = meta
            _cache[cache_key] = body
            return body, meta

        # 确保 content cache 已更新（不含 frontmatter）
        if cache_key not in _cache:
            text = cls.load(name, version)
            _, body = _parse_frontmatter(text)
            _cache[cache_key] = body

        return _cache[cache_key], _meta_cache[meta_key]

    @classmethod
    def list_agents(cls, version: str = "v1") -> list[dict[str, Any]]:
        """扫描 Prompt 目录，返回所有带 frontmatter 的 Agent 配置。"""
        version_dir = PROMPT_BASE / version
        if not version_dir.exists():
            return []

        agents = []
        for f in sorted(version_dir.glob("*.txt")):
            if f.name == "supervisor.txt":
                continue  # supervisor 不是子 Agent
            text = f.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            if meta and "name" in meta:
                agents.append(meta)
        return agents

    @classmethod
    def clear_cache(cls) -> None:
        """测试或热更新时使用"""
        _cache.clear()
        _meta_cache.clear()
