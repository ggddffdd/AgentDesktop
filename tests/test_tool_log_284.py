# -*- coding: utf-8 -*-
"""v4.284.0 P1b 判据：工具日志补齐参数/成败（judge-first）。

彻查根因（2026-10-16，agent_log.db 9852 条实证）
------------------------------------------------
工具日志只有一行「执行工具: X」——**不记参数、不记结果、不记得败**。
后果：「模型传错参数」「同一调用是否重复执行」永远无法定论
（app_kill 22:16/22:24 连发 2 条即因此查不出是真调了两次还是日志重复；
日志点在 handler 执行前，2 条 = 真调了 2 次，但没有证据支撑这个结论）。

修复（tools.py exec_tool 两个执行分支）
----------------------------------------
  1. 入口行补 args 摘要：`执行工具: X | args={...}`（先 `_trunc_args` 截断，
     再过 `sanitize_for_model` 脱敏——sk-/Bearer 类凭据不得进日志）；
  2. 出口行补成败 + 结果摘要：`工具完成: X | ok=True/False | 单行摘要`
     （`_log_brief` 换行折空格 + 截断，日志表按行读）。
registry 分支与 MCP 分支同口径；拒绝/异常分支的既有 ERROR 日志不动。

本套件行为级：monkeypatch `tools.get_logger` 捕获落库调用，注册 read 级
探针工具真走 `exec_tool`（权限闸门对 READ 放行，不发网络、不碰磁盘）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


class _Cap(object):
    """假结构化日志器：捕获 info/warning/error 三元组。"""

    def __init__(self):
        self.rows = []

    def info(self, msg, module=None, extra=None, session_id=None, exc_info=None):
        self.rows.append(("INFO", str(msg), extra or {}))

    def warning(self, msg, *a, **k):
        self.rows.append(("WARN", str(msg), {}))

    def error(self, msg, *a, **k):
        self.rows.append(("ERR", str(msg), {}))


def main():
    import tools

    cap = _Cap()
    _orig_logger = tools.get_logger
    tools.get_logger = lambda: cap
    try:
        # ===== A. registry 分支：入口 args + 出口 ok =====
        @tools.register_tool("_log_probe_284", risk="read")
        def _probe(cfg, app_dir, args, progress=None):
            return ("探针结果第一行\n第二行\n第三行", [], None)

        tr = tools.exec_tool(None, ".", "_log_probe_284",
                             {"query": "昆明天气", "api_key": "sk-abcdef123456"})
        infos = [r for r in cap.rows if r[0] == "INFO"]
        entry = [r for r in infos if r[1].startswith("执行工具: _log_probe_284")]
        done = [r for r in infos if r[1].startswith("工具完成: _log_probe_284")]

        check("A1 入口行存在且带 args", len(entry) == 1 and "args=" in entry[0][1],
              "rows=%s" % [r[1][:60] for r in infos])
        check("A2 入口 args 含真实参数值", "昆明天气" in (entry[0][1] if entry else ""))
        check("A3 入口 args 已脱敏（sk- 凭据不进日志）",
              entry and "sk-abcdef123456" not in entry[0][1]
              and "***" in entry[0][1],
              "entry=%s" % (entry[0][1] if entry else None))
        check("A4 extra_data 带 args 字段（可按工具/参数检索）",
              entry and "args" in entry[0][2] and entry[0][2].get("tool") == "_log_probe_284")
        check("A5 出口行存在且带 ok 标记", len(done) == 1 and "ok=" in done[0][1],
              "rows=%s" % [r[1][:60] for r in infos])
        check("A6 出口 ok 与 ToolResult 一致",
              done and ("ok=%s" % bool(tr.ok)) in done[0][1],
              "tr.ok=%s line=%s" % (tr.ok, done[0][1] if done else None))
        check("A7 出口结果是单行（换行已折空格）",
              done and "\n" not in done[0][1] and "第一行" in done[0][1]
              and "第二行" in done[0][1])
        check("A8 出口 extra_data 带 ok 布尔（可按成败筛选）",
              done and isinstance(done[0][2].get("ok"), bool))
        check("A9 探针工具真的执行了（不是空转）", bool(tr.msg))

        # ===== B. 长结果/长参数截断（不撑爆日志表）=====
        cap.rows = []

        @tools.register_tool("_log_probe_long_284", risk="read")
        def _probe_long(cfg, app_dir, args, progress=None):
            return ("x" * 5000, [], None)

        tools.exec_tool(None, ".", "_log_probe_long_284", {"blob": "y" * 5000})
        infos2 = [r for r in cap.rows if r[0] == "INFO"]
        done2 = [r for r in infos2 if r[1].startswith("工具完成")]
        check("B1 入口 args 截断（extra_data 字段有界）",
              all(len(str(r[2].get("args") or "")) <= 320 for r in infos2),
              "lens=%s" % [len(str(r[2].get("args") or "")) for r in infos2])
        check("B2 出口结果摘要截断到 120 内（第二个 | 之后的部分）",
              done2 and all(len(r[1].split("| ", 2)[-1]) <= 121 for r in done2),
              "lens=%s" % [len(r[1].split("| ", 2)[-1]) for r in done2])

        # ===== C. 参数非 dict（None）不炸 =====
        cap.rows = []
        try:
            tools.exec_tool(None, ".", "_log_probe_284", None)
            check("C1 args=None 不炸且有入口行",
                  any(r[1].startswith("执行工具: _log_probe_284") for r in cap.rows))
        except Exception as e:
            check("C1 args=None 不炸且有入口行", False, str(e))

        # ===== D. 结构护栏：两分支同口径 + 脱敏在入口 =====
        src = open(os.path.join(ROOT, "tools.py"), encoding="utf-8").read()
        check("D1 _log_brief 助手已定义", "def _log_brief(text, limit=120):" in src)
        check("D2 入口 args 行出现两次（registry + MCP 两分支）",
              src.count('f"执行工具: {name} | args={_args_log}"') == 2,
              "count=%d" % src.count('f"执行工具: {name} | args={_args_log}"'))
        check("D3 出口完成行出现两次（两分支同口径）",
              src.count('f"工具完成: {name} | ok={bool(_tr.ok)} | {_log_brief(_tr.msg)}"') == 2,
              "count=%d" % src.count(
                  'f"工具完成: {name} | ok={bool(_tr.ok)} | {_log_brief(_tr.msg)}"'))
        check("D4 入口 args 过 sanitize_for_model 脱敏",
              "_args_log = sanitize_for_model(_log_brief(_trunc_args(args, 300)))" in src)
        check("D5 旧「只记工具名」的裸行已清零",
              'f"执行工具: {name}", module="tools", extra={"tool": name}' not in src)
    finally:
        tools.get_logger = _orig_logger

    print("TOOL_LOG PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
