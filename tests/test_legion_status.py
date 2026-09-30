# -*- coding: utf-8 -*-
"""军团内部可观测（v4.183.0）回归测试

覆盖：
- 审计采集纯函数：_read_jsonl 坏行容错 / _fmt 格式化 / collect_audit 合并+过滤+排序
- LegionStatusStrip：波次 / 成员运行态计数 / 闸门待批↔已批 / reset
- LegionAuditFeed：构建不崩 + actor 切换不崩
- 接线守卫：worker.board_update 信号在 _board() 后真实发射（仿 test_task_wiring）

跑法：python tests/test_legion_status.py
退出码 0=全过，1=有失败。
"""
import sys
import os
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import legion_status_widget as m
from legion_status_widget import (
    _read_jsonl, _fmt, collect_audit, LegionStatusStrip, LegionAuditFeed,
)

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


# ---- 纯函数 ----

def test_read_jsonl():
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                      encoding="utf-8") as f:
        f.write(json.dumps({"a": 1}) + "\n")
        f.write("这不是合法json\n")          # 坏行
        f.write(json.dumps({"b": 2}) + "\n")
        path = f.name
    rows = _read_jsonl(path)
    check("read_jsonl 跳过坏行", len(rows) == 2)
    check("read_jsonl 内容正确", rows[0].get("a") == 1 and rows[1].get("b") == 2)
    check("read_jsonl 缺文件返回空", _read_jsonl("__no_such__.jsonl") == [])
    os.unlink(path)


def test_fmt():
    msg = {"event": "user_message", "time": "10:00:00", "wave": 2,
           "text": "把范围收窄到3C"}
    check("fmt 用户指令", "💬 指令" in _fmt(msg) and "波2" in _fmt(msg))
    allow = {"time": "10:01:00", "decision": "allow", "by": "user", "wave": 2,
             "role": "研究员"}
    check("fmt 授权批准", "✅ 批准" in _fmt(allow) and "user" in _fmt(allow))
    deny = {"time": "10:02:00", "decision": "deny", "by": "engine", "wave": 3,
            "role": "写文件"}
    check("fmt 授权拒绝", "⛔ 拒绝" in _fmt(deny) and "engine" in _fmt(deny))
    tool = {"_src": "tool", "time": "10:03:00", "decision": "allow",
            "by": "engine", "wave": 1, "tool": "web_search"}
    check("fmt 工具放行", "工具 web_search" in _fmt(tool))


def test_collect_audit():
    auth = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                       encoding="utf-8")
    tool = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                       encoding="utf-8")
    # auth 账本：用户指令 + 授权（user）+ 工具审计（engine）
    auth.write(json.dumps({"time": "10:00", "event": "user_message",
                           "project_id": "P1", "wave": 1, "by": "user",
                           "text": "hi"}) + "\n")
    auth.write(json.dumps({"time": "10:01", "decision": "allow",
                           "project_id": "P1", "wave": 1, "by": "user",
                           "role": "R"}) + "\n")
    tool.write(json.dumps({"time": "10:02", "decision": "deny",
                           "project_id": "P1", "wave": 1, "by": "engine",
                           "tool": "write_file"}) + "\n")
    auth.close()
    tool.close()
    try:
        rows = collect_audit(auth.name, tool.name)
        check("collect 合并两账本(3条)", len(rows) == 3)
        check("collect 按时间排序", [r.get("time") for r in rows]
              == ["10:00", "10:01", "10:02"])
        # 项目过滤
        rows_p2 = collect_audit(auth.name, tool.name, project_id="P2")
        check("collect 项目过滤", rows_p2 == [])
        # actor 过滤
        rows_user = collect_audit(auth.name, tool.name, actor="user")
        check("collect actor=user(2条)", len(rows_user) == 2
              and all(r.get("by") == "user" for r in rows_user))
        rows_engine = collect_audit(auth.name, tool.name, actor="engine")
        check("collect actor=engine(1条)", len(rows_engine) == 1
              and rows_engine[0].get("by") == "engine")
    finally:
        os.unlink(auth.name)
        os.unlink(tool.name)


# ---- Qt 控件 ----

def test_status_strip():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    s = LegionStatusStrip()
    check("strip 初始波次 —/—", "—/—" in s._lbl_wave.text())
    s.set_total_waves(3)
    s.on_board("pid", "w1_m1", "研究员", 1, "running")
    s.on_board("pid", "w1_m2", "工程师", 1, "done")
    s.on_board("pid", "w1_m3", "设计师", 1, "error")
    check("strip 波次 1/3", s._lbl_wave.text() == "波 1/3")
    check("strip 运行中计数=1", "运行中1" in s._lbl_members.text())
    check("strip 完成计数=1", "完成1" in s._lbl_members.text())
    check("strip 异常计数=1", "异常1" in s._lbl_members.text())
    check("strip 闸门默认 —", "闸门 —" in s._lbl_gate.text())
    # 闸门待批
    s.on_board("pid", "gate_w1", "验收", 1, "running")
    check("strip 闸门 待批", "闸门 待批" in s._lbl_gate.text())
    # 闸门已批（含色变由 guard 间接覆盖：文本即可）
    s.on_board("pid", "gate_w1", "验收", 1, "done")
    check("strip 闸门 已批", "闸门 已批" in s._lbl_gate.text())
    # 多波：当前波取最大成员波
    s.on_board("pid", "w2_m1", "研究员", 2, "running")
    check("strip 当前波=2", s._lbl_wave.text() == "波 2/3")
    # reset
    s.reset()
    check("strip reset 波 —/—", "—/—" in s._lbl_wave.text())
    check("strip reset 计数清零", "运行中0 完成0 异常0"
          in s._lbl_members.text())
    s.deleteLater()


def test_audit_feed_build():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = LegionAuditFeed()
    # actor 切换不应崩
    w._on_actor("user")
    w._on_actor("全部")
    check("audit_feed 构建+actor切换不崩", True)
    w.deleteLater()


# ---- 接线守卫 ----

def test_worker_board_signal():
    """worker.board_update 在 _board() 后真实发射（关键回归：漏接=状态条不跳）。"""
    from legion_worker import LegionWorker
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = LegionWorker(None, {})
    w.pid = "pidX"
    w.project = {"name": "测试军团"}
    captured = []
    w.board_update.connect(lambda a, b, c, d, e: captured.append((a, b, c, d, e)))
    # 触发 _board（内部会再广播 board_update）
    w._board("w1_m1", role="研究员", wave=1, status="running")
    check("board_update 发射1次", len(captured) == 1)
    check("board_update 参数正确",
          captured[0] == ("pidX", "w1_m1", "研究员", "1", "running"))
    # pid=None 时不崩
    w2 = LegionWorker(None, {})
    w2.pid = None
    try:
        w2._board("x", status="done")
        check("_board pid=None 不崩", True)
    except Exception:
        check("_board pid=None 不崩", False)
    w.deleteLater()


def main():
    test_read_jsonl()
    test_fmt()
    test_collect_audit()
    test_status_strip()
    test_audit_feed_build()
    test_worker_board_signal()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
