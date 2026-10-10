# -*- coding: utf-8 -*-
"""军团「UI 精简 + 管理命令对话化」回归判据（v4.247.0）

覆盖：
- legion.parse_manage_intent：管理命令识别（纯函数，精确匹配零误判）
- LegionChatPanel._render_card：纯文本渲染（角色前缀、无 HTML、系统无前缀）
- LegionChatPanel：管理命令 → 回调

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_chat_ui.py
退出码 0=全过，1=有失败。
"""
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legion
from legion import parse_manage_intent

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


def test_parse_manage_intent():
    check("管理·项目信息", parse_manage_intent("项目信息") == "info")
    check("管理·复制项目", parse_manage_intent("复制项目") == "dup")
    check("管理·删除项目", parse_manage_intent("删除项目") == "delete")
    check("管理·补录数据", parse_manage_intent("补录数据") == "manual")
    check("管理·清记忆", parse_manage_intent("清记忆") == "wipe")
    check("管理·上次报告", parse_manage_intent("上次报告") == "report")
    # 反向：精确匹配，不误判
    check("反向·带空格不误判", parse_manage_intent("帮我 删掉 项目") is None)
    check("反向·普通留言", parse_manage_intent("把选品范围收窄到3C") is None)
    check("反向·超长不误判", parse_manage_intent("项目信息" * 10) is None)
    check("反向·空文本", parse_manage_intent("") is None)


def test_render_plaintext():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_chat import LegionChatPanel
    p = LegionChatPanel()
    p._render_card("你", "组个团队")
    p._render_card("项目经理", "方案来了")
    p._render_card("系统", "等你审批")
    txt = p.log.toPlainText()
    check("纯文本·你前缀", "你：组个团队" in txt)
    check("纯文本·PM前缀", "项目经理：方案来了" in txt)
    check("纯文本·系统无前缀", "等你审批" in txt)
    check("纯文本·无HTML标签", "<div" not in txt and "border-left" not in txt)


def test_manage_callback():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_chat import LegionChatPanel
    p = LegionChatPanel()
    got = {}
    p.set_manager(lambda kind: got.update(kind=kind))
    p.input.setPlainText("项目信息")
    p.send()
    check("管理命令→回调", got.get("kind") == "info")


if __name__ == "__main__":
    test_parse_manage_intent()
    test_render_plaintext()
    test_manage_callback()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
