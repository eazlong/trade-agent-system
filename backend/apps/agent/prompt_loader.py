from __future__ import annotations

from datetime import datetime
from pathlib import Path

PROMPT_BASE = Path(__file__).parent.parent.parent / 'prompts'

_cache: dict[str, str] = {}


class PromptLoader:
    @classmethod
    def load(cls, name: str, version: str = 'v1') -> str:
        """
        加载指定版本的Prompt文件。
        变更必须新建版本目录（ADR-004），可追溯可回滚。
        动态注入当前时间，防止LLM使用过期信息。
        """
        cache_key = f'{version}/{name}'
        if cache_key not in _cache:
            file_path = PROMPT_BASE / version / f'{name}.txt'
            if not file_path.exists():
                raise FileNotFoundError(f'Prompt not found: {file_path}')
            _cache[cache_key] = file_path.read_text(encoding='utf-8')

        prompt = _cache[cache_key]
        # 动态注入当前时间，替换占位符
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        prompt = prompt.replace('{{CURRENT_DATETIME}}', current_date)
        return prompt

    @classmethod
    def clear_cache(cls) -> None:
        """测试或热更新时使用"""
        _cache.clear()
