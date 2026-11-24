"""PromptLoader 环境契约回归（#60）。

CI runner 没有部署位 ``~/.tradelogx/agents/``，但仓库里有
``backend/prompts/v1/*.txt``。加载器必须按「部署位优先、仓库兜底」取文件，
而不是只认部署位、缺了就抛 FileNotFoundError（99 条 CI 红的根因）。
同款契约的先例见 apps/strategy_engine/tests/test_box_breakout_threshold.py。
"""

from __future__ import annotations

import pytest

from apps.agent import prompt_loader
from apps.agent.prompt_loader import PromptLoader


@pytest.fixture(autouse=True)
def _clear_prompt_cache():
    # _cache 是模块级全局：不清会让别的文件先加载的结果污染本文件断言。
    prompt_loader._cache.clear()
    prompt_loader._meta_cache.clear()
    yield
    prompt_loader._cache.clear()
    prompt_loader._meta_cache.clear()


def test_load_falls_back_to_repo_when_deploy_missing(monkeypatch, tmp_path):
    """部署位缺失时，仓库 backend/prompts/ 兜底（CI 形态）。"""
    monkeypatch.setattr(prompt_loader, "PROMPT_BASE", tmp_path / "absent")
    body = PromptLoader.load("supervisor")
    assert "主控 Agent" in body
    assert "{{CURRENT_DATETIME}}" not in body  # 动态时间注入仍生效


def test_list_agents_falls_back_to_repo_when_deploy_missing(monkeypatch, tmp_path):
    """list_agents 同样兜底：否则 CI 上 Supervisor 路由拿到空子 Agent 表。"""
    monkeypatch.setattr(prompt_loader, "PROMPT_BASE", tmp_path / "absent")
    names = {a.get("name") for a in PromptLoader.list_agents()}
    assert {"analyst", "coach", "quant", "researcher", "risk_advisor"} <= names


def test_deploy_location_wins_when_present(monkeypatch, tmp_path):
    """部署位存在时优先用它——生产跑的是部署位那份，契约必须对它成立。"""
    deploy = tmp_path / "deploy"
    (deploy / "v1").mkdir(parents=True)
    (deploy / "v1" / "supervisor.txt").write_text(
        "---\nname: supervisor\n---\nDEPLOY_SENTINEL\n", encoding="utf-8"
    )
    monkeypatch.setattr(prompt_loader, "PROMPT_BASE", deploy)
    assert "DEPLOY_SENTINEL" in PromptLoader.load("supervisor")


def test_load_missing_everywhere_raises(monkeypatch, tmp_path):
    """两处都没有才抛 FileNotFoundError，报错里带得上名字便于定位。"""
    monkeypatch.setattr(prompt_loader, "PROMPT_BASE", tmp_path / "absent")
    monkeypatch.setattr(prompt_loader, "REPO_PROMPT_BASE", tmp_path / "also_absent")
    with pytest.raises(FileNotFoundError, match="no_such_prompt_xyz"):
        PromptLoader.load("no_such_prompt_xyz")
