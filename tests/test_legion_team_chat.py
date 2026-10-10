# -*- coding: utf-8 -*-
"""军团「组队对话化」回归判据（v4.246.0）

覆盖：
- legion.parse_team_build_intent：组队命令识别（纯函数，正向 + 反向不误判）
- legion.format_team_plan：方案排版（理解置顶 / 阵容 / 理由 / 空理解提示打回）
- LegionChatPanel：组队状态机 + 审批回调（Qt offscreen）

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_team_chat.py
退出码 0=全过，1=有失败。
"""
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legion
from legion import parse_team_build_intent, format_team_plan

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


# ---- 纯函数：组队命令识别 ----

def test_parse_team_build_intent():
    # 正向
    check("组队·组个团队", parse_team_build_intent("组个团队做小红书带货") == "做小红书带货")
    check("组队·帮我组建", parse_team_build_intent("帮我组建一个3人团队做抖音") == "一个3人团队做抖音")
    check("组队·拉个团队", parse_team_build_intent("拉个团队调研储能") == "调研储能")
    check("组队·组队短句", parse_team_build_intent("组队写公众号文章") == "写公众号文章")
    # 反向：不误判
    check("反向·启动军团", parse_team_build_intent("启动军团做X") is None)
    check("反向·组队的事", parse_team_build_intent("组队的事我再想想") is None)
    check("反向·空需求", parse_team_build_intent("组队") is None)
    check("反向·空文本", parse_team_build_intent("") is None)
    check("反向·普通留言", parse_team_build_intent("把选品范围收窄到3C类目") is None)


# ---- 纯函数：方案排版 ----

def _sample_plan():
    return {
        "understanding": "做小红书带货视频，先选品再写脚本最后把关",
        "name": "小红书带货小队",
        "emoji": "🛍️",
        "description": "一条龙带货内容",
        "waves": [
            {"members": [
                {"name": "选品官", "why": "定选品", "skills": ["kalodata"]},
                {"name": "写手", "why": "写脚本", "skills": []},
            ]},
            {"members": [{"name": "审校", "why": "把关质量", "skills": []}]},
        ],
        "reason": "先取证再创作最后把关",
        "missing_roles": [],
        "missing_skills": [],
    }


def test_format_team_plan():
    plan = _sample_plan()
    lib = [{"name": "选品官", "emoji": "🛒", "tools": ["web_search"]}]
    out = format_team_plan(plan, lib)
    check("排版·理解置顶", out.find("理解") < out.find("阵容"))
    check("排版·含阵容", "② 阵容编排" in out)
    check("排版·含成员", "选品官" in out and "写手" in out)
    check("排版·含理由", "组队理由" in out)
    # 空理解 → 提示打回
    plan2 = dict(_sample_plan(), understanding="")
    out2 = format_team_plan(plan2, [])
    check("排版·空理解提示打回", "打回" in out2)


# ---- Qt：组队状态机 + 审批回调 ----

def test_team_chat_state():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_chat import LegionChatPanel
    p = LegionChatPanel()
    got = {}
    p.set_team_approver(lambda intent, text: got.update(intent=intent, text=text))
    plan = _sample_plan()

    # 待审批态 + 批准 → pass
    p._pending_team_plan = {"plan": plan, "need": "做小红书"}
    p.input.setPlainText("批准")
    p.send()
    check("审批·批准→pass", got.get("intent") == "pass")

    # 待审批态 + 打回带意见 → reject + 原文
    got.clear()
    p._pending_team_plan = {"plan": plan, "need": "做小红书"}
    p.input.setPlainText("打回 把选品官换成竞品分析师")
    p.send()
    check("审批·打回→reject", got.get("intent") == "reject")
    check("审批·打回带意见", got.get("text") == "打回 把选品官换成竞品分析师")

    # 组队命令触发 builder 回调
    got.clear()
    p.set_team_builder(lambda need: got.update(built=need))
    p._pending_team_plan = None
    p.input.setPlainText("组个团队做小红书带货")
    p.send()
    check("组队命令→builder回调", got.get("built") == "做小红书带货")


if __name__ == "__main__":
    test_parse_team_build_intent()
    test_format_team_plan()
    test_team_chat_state()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
