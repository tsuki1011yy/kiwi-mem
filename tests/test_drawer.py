"""
工具抽屉路由行为测试（无需数据库 / 不联网）。

通过直接设置 tool_drawer 的模块级注册表 + 打桩 config/refresh，验证 route_tools 的三条
关键语义（对应 PR 约定）：

  1. auto 模式 = 纯语义路由：切到 auto 后，手动钉选（mcp_manual_ids）的外部 MCP 工具
     不被强制展开（无语义命中时不出现）。
  2. manual 模式 = 只尊重手动钉选：mcp_manual_ids 命中的外部类别被展开。
  3. 并发刷新安全：route_tools 在锁内对注册表做快照后，即使 live registry 在遍历前被
     并发刷新清空，本轮仍从快照拿到工具，不读半更新状态。

运行：python tests/test_drawer.py   （退出码非 0 表示有用例失败）
"""

import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_drawer as td
import config

_failures = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        _failures.append(name)


# ── 打桩：避免触网 / 触库 ─────────────────────────────────────
async def _noop_refresh():
    return


async def _get_config_float(key, fallback=0.0):
    return fallback


async def _get_config_int(key, fallback=0):
    return fallback


_manual_ids = {"ids": []}


async def _get_config(key, default=None):
    if key == "mcp_manual_ids":
        return json.dumps(_manual_ids["ids"])
    return default


td.refresh_external_drawers = _noop_refresh
config.get_config_float = _get_config_float
config.get_config_int = _get_config_int
config.get_config = _get_config


def setup_registry():
    """重置注册表，只放一个外部类别 weather（含一个工具 + 对齐 [1,0] 的 embedding）。"""
    for d in (td.CATEGORIES, td._external_categories, td.TOOL_SCHEMAS, td._category_embeddings, td._sessions):
        d.clear()
    td.CATEGORIES["weather"] = {
        "label": "Weather", "description": "天气查询", "keywords": ["weather"],
        "tool_names": ["get_weather"], "external": True,
    }
    td._external_categories["weather"] = {
        "label": "Weather", "server_name": "weather",
        "tool_map": {"get_weather": {"type": "external_mcp", "origin_name": "get_weather"}},
    }
    td.TOOL_SCHEMAS["get_weather"] = {
        "type": "function",
        "function": {"name": "get_weather", "description": "", "parameters": {"type": "object", "properties": {}}},
    }
    td._category_embeddings["weather"] = [1.0, 0.0]


def names(openai_tools):
    return {t["function"]["name"] for t in openai_tools if "function" in t}


async def run():
    print("== route_tools：auto / manual 钉选语义 + 并发快照 ==")

    # 1) auto + 钉选 weather + 无语义命中（embedding 正交、消息无关键词）→ 不应展开
    setup_registry()
    _manual_ids["ids"] = ["weather"]
    tools, _ = await td.route_tools("hello there", "s1", user_embedding=[0.0, 1.0],
                                    mem_enabled=True, search_enabled=True, mcp_mode="auto")
    check("auto：钉选的外部工具不被强制展开（无语义命中时）", "get_weather" not in names(tools))

    # 2) manual + 钉选 weather → 应展开
    setup_registry()
    _manual_ids["ids"] = ["weather"]
    tools, _ = await td.route_tools("hello there", "s2", user_embedding=[0.0, 1.0],
                                    mem_enabled=True, search_enabled=True, mcp_mode="manual")
    check("manual：钉选的外部工具被展开", "get_weather" in names(tools))

    # 3) 并发刷新：auto 下 weather 语义命中（embedding 对齐 [1,0]），但在快照之后、遍历之前
    #    通过打桩 _external_keyword_match 清空 live registry，模拟并发刷新把注册表清掉。
    #    若 route_tools 读 live → 工具丢失；读快照 → 仍在。
    setup_registry()
    _manual_ids["ids"] = []
    _orig = td._external_keyword_match

    def _wipe_then_match(msg, ext=None, cats=None):
        for d in (td.CATEGORIES, td._external_categories, td.TOOL_SCHEMAS, td._category_embeddings):
            d.clear()
        return set()

    td._external_keyword_match = _wipe_then_match
    try:
        tools, _ = await td.route_tools("weather", "s3", user_embedding=[1.0, 0.0],
                                        mem_enabled=True, search_enabled=True, mcp_mode="auto")
    finally:
        td._external_keyword_match = _orig
    check("并发刷新清空 live registry 后，route_tools 仍从快照拿到工具", "get_weather" in names(tools))


asyncio.run(run())

if _failures:
    print(f"\n❌ {len(_failures)} 个用例失败：{_failures}")
    sys.exit(1)
print("\n✅ 全部用例通过")
