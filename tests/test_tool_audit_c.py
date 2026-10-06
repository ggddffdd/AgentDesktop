# -*- coding: utf-8 -*-
"""C 批（任务板 #3 / 盘点 G1）判据：主对话链**决策审计落盘**。

盘点缺口（G1）：`legion_permissions.py` 早就做到了「每笔非只读决策落账本」
（锁 + 参数摘要 + 脱敏预览），对应宪法第二章第 ③ 条「每笔自动放行留审计痕
（可抽查）」。而主对话链（`agent.py` + `permissions.decide()`）当时只在
`route_log.jsonl` 里留了一条 `event=tool`，它有三个够不着的地方：

  ① 只有 1 个调用点（agent.py 串行路径）—— 并发批决策、run_workflow 子代理
     校验、以及日后新增的调用点**都不留痕**；
  ② 只有 12 位**不可逆**哈希，回答不了「确认的那笔到底杀了哪个进程」；
  ③ 它是路由诊断日志（20MB 滚成 `.1`），证据会被静默滚掉。

本套件把「挂在决策入口 + 带可读脱敏摘要 + 带授权来源」这三件事变成
「改坏了必然翻红」，八组：

  A 组 每笔非只读决策 = 账本 +1 行；18 条闸门分支逐条覆盖，且记录与返回值**逐字段一致**
  B 组 只读不记的口径边界（含「被拒的只读也不记」）
  C 组 记录字段完备（含 args_digest 长度 / 决策三态 / 时间格式）
  D 组 脱敏：敏感键抹除、长值截断、畸形 / 不可序列化入参不抛
  E 组 零副作用契约：默认不落盘（判据套件不许污染真实账本）、写盘失败不改决策
  F 组 接线判据：ui.py（唯一真实构造点）必须显式传 audit_dir —— AST 判定，不靠字符匹配
  G 组 两账本同源：军团与主对话共用同一把锁 / 摘要 / 脱敏 / 字段名，origin 可区分
  H 组 源码契约：审计**必须**在 `decide` 函数体之外（AST 抽取契约不许被破坏）

扰动用环境变量指向变异副本：
  TOOL_AUDIT_PATH / PERM_PATH / LEGION_PERM_PATH / UI_PATH

**零副作用保证**：本套件绝不往真实账本写一个字节（E 组专门守这条），
也绝不执行任何工具 —— 只调 `decide()`（纯内存决策）。

用法：python tests/test_tool_audit_c.py
"""
import ast
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


def _load_override(env_key, modname):
    """按环境变量把某模块换成变异副本（扰动用）；未设则什么都不做。

    顺序有讲究：tool_audit 必须先加载 —— permissions 会 `import tool_audit`，
    若此时 sys.modules 里已经是变异版，permissions 拿到的就是变异版。
    """
    p = os.environ.get(env_key)
    if not p:
        return
    spec = importlib.util.spec_from_file_location(modname, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)


_load_override("TOOL_AUDIT_PATH", "tool_audit")
_load_override("PERM_PATH", "permissions")
_load_override("LEGION_PERM_PATH", "legion_permissions")

import tool_audit                                        # noqa: E402
import permissions                                       # noqa: E402
from permissions import PermissionEngine                 # noqa: E402
import legion_permissions as lp                          # noqa: E402

PERM_PATH = os.environ.get("PERM_PATH") or os.path.join(ROOT, "permissions.py")
TOOL_AUDIT_PATH = os.environ.get("TOOL_AUDIT_PATH") or os.path.join(ROOT, "tool_audit.py")
UI_PATH = os.environ.get("UI_PATH") or os.path.join(ROOT, "ui.py")

PERM_SRC = open(PERM_PATH, encoding="utf-8-sig").read()
TA_SRC = open(TOOL_AUDIT_PATH, encoding="utf-8-sig").read()

LEDGER = tool_audit.MAIN_AUDIT_NAME                 # "tool_audit.jsonl"
DECISIONS = ("allow", "confirm", "deny")
BY_VALUES = ("engine_auto", "pending_user", "engine_deny")
CORE_FIELDS = ("ts", "time", "origin", "tool", "args_digest", "args_preview",
               "decision", "rule", "reason", "by")


def _mk(**kw):
    """造一个引擎。audit_dir 缺省为 ""（不落盘）—— 与生产默认值一致。"""
    return PermissionEngine(mode=kw.pop("mode", "interactive"), **kw)


def _read(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))     # 坏行会在这里炸出来（也是判据）
    return out


def _text(path):
    """账本原文（不存在就返回空串，**不许因此崩溃**）。

    教训：本套件第一版在 C/D 组直接 `open(p)`，于是当审计被整体拆掉（账本压根
    不存在）时套件抛 FileNotFoundError 崩在 C 组 —— 后面 G/H 组根本没跑，
    扰动脚本看到的「红项」就少了一大半。判据**崩溃**与判据**转红**是两回事：
    崩掉的套件会把真正的红项藏起来。故所有读账本的地方一律走这里。
    """
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _first(path):
    """第一条记录；没有就返回 {}（同样不许崩）。"""
    rows = _read(path)
    return rows[0] if rows else {}


def _nth(path, i):
    rows = _read(path)
    return rows[i] if len(rows) > i else {}


def _count(path):
    return sum(1 for ln in open(path, encoding="utf-8") if ln.strip()) \
        if os.path.exists(path) else 0


def _tmpdir():
    return tempfile.mkdtemp(prefix="c_audit_")


def _sig(d):
    """Decision 的判据面快照（用于「审计有没有改变决策」的逐字段比对）。"""
    return (d.allowed, d.needs_user, d.rule, d.reason)


# ===========================================================================
# A 组：每笔非只读决策 = 账本 +1 行（覆盖全部闸门分支）
# ===========================================================================
# (标题, 引擎 kwargs, 构造后动作, tool, args, intent, task_risk,
#  期望 rule, 期望 decision, 期望 by)
_CASES = [
    ("交互·EXEC→tier:manual（run_command）", {}, None, "run_command",
     {"command": "echo hi"}, True, None, "tier:manual", "confirm", "pending_user"),
    ("交互·EXEC→tier:manual（window_focus，换工具名同口径）", {}, None,
     "window_focus", {"title": "T"}, True, None, "tier:manual", "confirm", "pending_user"),
    ("交互·记录在案的自动放行（process_kill→always_confirm）", {}, None,
     "process_kill", {"name": "a.exe"}, True, None, "always_confirm", "confirm", "pending_user"),
    ("交互·WRITE_LOCAL/semi→tier:semi（image_gen）", {}, None, "image_gen",
     {"prompt": "x"}, True, None, "tier:semi", "allow", "engine_auto"),
    ("交互·同一档位换工具名（clipboard_write）", {}, None, "clipboard_write",
     {"text": "x"}, True, None, "tier:semi", "allow", "engine_auto"),
    ("discuss 模式全拒", {"mode": "discuss"}, None, "run_command", {"command": "x"},
     True, None, "mode:discuss", "deny", "engine_deny"),
    ("plan 模式只读之外的拒", {"mode": "plan"}, None, "run_command", {"command": "x"},
     True, None, "mode:plan", "deny", "engine_deny"),
    ("外发白名单围栏", {}, None, "send_email", {"to": "a@b.c"},
     True, None, "external_block", "deny", "engine_deny"),
    ("路径作用域围栏", {"scope_paths": ["C:/Users/xyb/Documents"]}, None,
     "write_file", {"path": "C:/Windows/System32/x.dll", "content": "x"},
     True, None, "scope", "deny", "engine_deny"),
    ("硬确认档", {}, None, "create_skill", {"name": "s"},
     True, None, "always_confirm", "confirm", "pending_user"),
    ("参数级高危", {}, None, "run_command", {"command": "rm -rf build"},
     True, None, "high_risk_exec", "confirm", "pending_user"),
    ("任务级风险闸", {}, None, "write_file", {"path": "a.txt", "content": "x"},
     True, "critical", "task_risk_critical", "confirm", "pending_user"),
    ("来源闸（本轮无执行意图）", {}, None, "image_gen", {"prompt": "x"},
     False, None, "implicit_intent", "confirm", "pending_user"),
    ("会话全信任（process_kill 仍硬确认，不可绕过）", {}, "trust_all", "process_kill", {"name": "a.exe"},
     True, None, "always_confirm", "confirm", "pending_user"),
    ("会话信任（绑参数，process_kill 仍硬确认）", {}, ("trust", "process_kill", {"name": "a.exe"}),
     "process_kill", {"name": "a.exe"}, True, None, "always_confirm", "confirm", "pending_user"),
    ("配置免确认白名单", {"auto_allow": {"mouse_click"}}, None,
     "mouse_click", {"x": 1, "y": 2}, True, None, "auto_allow", "allow", "engine_auto"),
    ("auto 模式·执行仍要确认", {"mode": "auto"}, None, "run_command", {"command": "echo"},
     True, None, "auto:gate", "confirm", "pending_user"),
    ("auto 模式·写入直行", {"mode": "auto"}, None, "image_gen", {"prompt": "x"},
     True, None, "auto", "allow", "engine_auto"),
    ("外发白名单 + auto", {"mode": "auto", "external_allow": {"send_email"}}, None,
     "send_email", {"to": "a@b.c"}, True, None, "external_allow", "allow", "engine_auto"),
]


def part_a_one_line_per_decision():
    print("\n-- A) 每笔非只读决策 = 账本 +1 行 --")
    for (title, kw, act, tool, args, intent, tr, exp_rule, exp_dec, exp_by) in _CASES:
        tmp = _tmpdir()
        try:
            p = os.path.join(tmp, LEDGER)
            e = _mk(audit_dir=tmp, **kw)
            if act == "trust_all":
                e.set_session_trusted()
            elif isinstance(act, tuple) and act[0] == "trust":
                e.trust_tool(act[1], act[2])
            d = e.decide(tool, args, explicit_intent=intent, task_risk=tr)
            rows = _read(p)
            ok = len(rows) == 1
            check(f"A {title}：+1 行", ok, f"rows={len(rows)}")
            if not ok:
                continue
            r = rows[0]
            check(f"A {title}：账本记的 rule 就是决策的 rule",
                  r["rule"] == d.rule == exp_rule,
                  f"账本={r['rule']!r} 返回={d.rule!r} 期望={exp_rule!r}")
            check(f"A {title}：三态映射正确（{exp_dec}）",
                  r["decision"] == exp_dec and r["by"] == exp_by,
                  f"decision={r['decision']!r} by={r['by']!r}")
            check(f"A {title}：allowed/need_confirm 与返回一致",
                  r["allowed"] == d.allowed and r["need_confirm"] == d.needs_user,
                  f"{r.get('allowed')}/{r.get('need_confirm')} vs {d.allowed}/{d.needs_user}")
            check(f"A {title}：source 如实标注",
                  r["source"] == ("explicit" if intent else "implicit"), r.get("source"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # 累计口径：连续 5 笔 → 恰好 5 行
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(audit_dir=tmp)
        for i in range(5):
            e.decide("run_command", {"command": f"echo {i}"})
        check("★ 连续 5 笔非只读决策 → 恰好 5 行", _count(p) == 5, f"实际 {_count(p)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# B 组：只读不记（口径边界）
# ===========================================================================
_READS = ("read_file", "web_search", "screenshot", "window_list", "process_list",
          "clipboard_read", "window_get_info", "browser_read")


def part_b_read_only_not_recorded():
    print("\n-- B) 只读不记（与军团同一口径）--")
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(audit_dir=tmp)
        for t in _READS:
            e.decide(t, {"path": "a"} if t in ("read_file",) else {})
        check(f"★ {len(_READS)} 个只读工具全部不记（账本 0 行）",
              _count(p) == 0, f"实际 {_count(p)} 行")
        # 非只读对照组：证明"不记"是只读特有，而不是整个套件没落盘
        e.decide("process_kill", {"name": "a.exe"})
        check("对照：同引擎下一笔非只读立刻落盘（证明确实接上了）",
              _count(p) == 1, f"实际 {_count(p)} 行")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 边界：discuss 把只读也拒了 —— 口径按**风险类别**，不按结果
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(mode="discuss", audit_dir=tmp)
        d = e.decide("read_file", {"path": "a"})
        check("被拒的只读仍不记（口径按风险类别，不按决策结果）",
              (not d.allowed) and _count(p) == 0, f"allowed={d.allowed} 行数={_count(p)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # fail-closed：未登记工具（classify 落 EXTERNAL）必须记 —— 宁可多记不可漏记
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(audit_dir=tmp)
        e.decide("__no_such_tool_xyz__", {})
        check("★ 未登记工具（fail-closed 落 EXTERNAL）也记账 —— 可多记不可漏记",
              _count(p) == 1, f"实际 {_count(p)} 行")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# C 组：记录字段完备
# ===========================================================================
def part_c_record_shape():
    print("\n-- C) 记录字段完备 --")
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(audit_dir=tmp)
        e.decide("process_kill", {"name": "notepad.exe"}, explicit_intent=True)
        e.decide("send_email", {"to": "a@b.c"})
        rows = _read(p)
        check("有记录可查", len(rows) >= 2, f"{len(rows)}")
        if not rows:
            return
        r = rows[0]
        missing = [k for k in CORE_FIELDS if k not in r]
        check(f"★ 核心字段齐全（缺一个都无法对账）：{missing or '无'}",
              not missing, f"缺 {missing}")
        check("★ args_digest 为 12 位十六进制（与军团同长）",
              isinstance(r.get("args_digest"), str) and len(r["args_digest"]) == 12,
              repr(r.get("args_digest")))
        check("★ args_preview 非空（光有哈希等于查不出「干了啥」）",
              bool(r.get("args_preview")), repr(r.get("args_preview")))
        check('origin == "main"（两账本可区分）', r.get("origin") == "main", r.get("origin"))
        check("tool 名如实记录（写成空串就等于不知道干了什么）",
              r.get("tool") == "process_kill", repr(r.get("tool")))
        check("decision ∈ {allow,confirm,deny}", r.get("decision") in DECISIONS,
              repr(r.get("decision")))
        check("by ∈ {engine_auto,pending_user,engine_deny}", r.get("by") in BY_VALUES,
              repr(r.get("by")))
        check("rule 非空（能看出命中哪道闸）", bool(r.get("rule")), repr(r.get("rule")))
        try:
            time.strptime(r.get("time", ""), "%Y-%m-%d %H:%M:%S")
            ok_time = True
        except Exception:
            ok_time = False
        check("time 可解析（%Y-%m-%d %H:%M:%S）", ok_time, repr(r.get("time")))
        check("ts 是数字（可与 route_log 对时间轴）",
              isinstance(r.get("ts"), (int, float)), repr(r.get("ts")))
        check("账本文件名就是 tool_audit.jsonl（改名会让抽查文档失效）",
              LEDGER == "tool_audit.jsonl", LEDGER)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# D 组：脱敏
# ===========================================================================
def part_d_redact():
    print("\n-- D) 脱敏：能看出「干了啥」，但不留凭据 --")
    tmp = _tmpdir()
    try:
        p = os.path.join(tmp, LEDGER)
        e = _mk(audit_dir=tmp)
        e.decide("run_command", {"command": "curl -H 'Authorization: Bearer X'",
                                 "api_key": "sk-SECRETVALUE123456",
                                 "token": "tk-SECRET", "password": "pw-SECRET",
                                 "secret": "sc-SECRET", "cookie": "ck-SECRET"})
        blob = _text(p)
        # 前置：账本没写下来的话，下面「不泄漏」是空判（谓词变空 = 假绿）
        check("D 前置：账本真的写下来了（否则泄漏检查是空判）",
              bool(blob.strip()), "账本为空")
        leaked = [s for s in ("sk-SECRETVALUE123456", "tk-SECRET", "pw-SECRET",
                              "sc-SECRET", "ck-SECRET") if s in blob]
        check(f"★ 敏感键的值一律不落盘（泄漏：{leaked or '无'}）", not leaked, str(leaked))
        r = _first(p)
        check("  但键名还在（能看出传了哪些参数）",
              "api_key" in r.get("args_preview", "") and "***" in r.get("args_preview", ""),
              r.get("args_preview"))

        # 长字符串截断
        long = "A" * 400
        e.decide("keyboard_type", {"text": long})
        r2 = _nth(p, 1)
        check("★ 长字符串被截断（账本不被单笔超长参数撑爆）",
              "…" in r2.get("args_preview", "")
              and len(r2.get("args_preview", "")) <= 250,
              f"len={len(r2.get('args_preview', ''))}")

        # 畸形 / 不可序列化入参：不抛、不中断落盘
        before = _count(p)
        try:
            e.decide("run_command", ["a", "b"])           # 非 dict
            e.decide("run_command", "raw-string")          # 非 dict
            e.decide("run_command", {"o": object()})       # 不可 JSON 序列化
            raised = ""
        except Exception as ex:                            # noqa: BLE001
            raised = repr(ex)
        check("★ 畸形 / 不可序列化入参不抛异常（审计不许反过来打断决策）",
              not raised, raised)
        check("  且这几笔照常落账（没被参数问题吞掉）",
              _count(p) == before + 3, f"{before}→{_count(p)}")

        check("redact(object()) 返回 str 而不是炸掉",
              isinstance(tool_audit.redact(object()), str))
        check("digest(不可序列化) 返回 12 位",
              len(tool_audit.digest({"o": object()})) == 12)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# E 组：零副作用契约
# ===========================================================================
def part_e_no_side_effects():
    print("\n-- E) 零副作用契约 --")
    # E1 默认（不接线）绝不碰真实账本 —— 这条保证授权层的 7~9 个套件不被污染
    real = os.path.join(tool_audit.default_dir(), LEDGER)
    n0 = _count(real)
    e = _mk(mode="auto")            # 默认 audit_dir="" —— 与生产默认一致
    for _ in range(6):
        e.decide("process_kill", {"name": "x.exe"})
        e.decide("send_email", {"to": "a@b.c"})
    n1 = _count(real)
    check("★ 未接线时**一个字节都不写真实账本**（判据套件零副作用）",
          n1 == n0, f"{real} 行数 {n0}→{n1}")
    check('显式 audit_dir="" 等价于关闭', _mk(audit_dir="").audit_dir == "")

    # E2 tool_audit.write 的失败面：一律返回 False、一律不抛
    try:
        ok_empty = tool_audit.write("", LEDGER, {"a": 1})
        raised = ""
    except Exception as ex:                              # noqa: BLE001
        ok_empty, raised = None, repr(ex)
    check("write('') → False 且不抛", ok_empty is False and not raised, f"{ok_empty} {raised}")

    tmp = _tmpdir()
    try:
        blocker = os.path.join(tmp, "blocker.txt")
        open(blocker, "w", encoding="utf-8").write("x")   # 拿一个**文件**当父目录
        bad = os.path.join(blocker, "sub")
        try:
            ok_bad = tool_audit.write(bad, LEDGER, {"a": 1})
            raised = ""
        except Exception as ex:                          # noqa: BLE001
            ok_bad, raised = None, repr(ex)
        check("★ 目录不可建（父路径是文件）→ False 且不抛",
              ok_bad is False and not raised, f"{ok_bad} {raised}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # E3 写盘失败**不改变决策**：与「接线」和「不接线」两个引擎逐字段比对
    tmp = _tmpdir()
    try:
        blocker = os.path.join(tmp, "blocker.txt")
        open(blocker, "w", encoding="utf-8").write("x")
        bad_dir = os.path.join(blocker, "sub")
        probes = [({}, "run_command", {"command": "echo hi"}, True, None),
                  ({}, "image_gen", {"prompt": "x"}, False, None),
                  ({"mode": "auto"}, "skill_install", {"name": "s"}, True, None),
                  ({}, "write_file", {"path": "C:/Windows/x", "content": "y"},
                   True, "critical")]
        bad_sigs, clean_sigs = [], []
        try:
            for kw, name, args, intent, tr in probes:
                bad_sigs.append(_sig(_mk(audit_dir=bad_dir, **kw).decide(
                    name, args, explicit_intent=intent, task_risk=tr)))
                clean_sigs.append(_sig(_mk(**kw).decide(
                    name, args, explicit_intent=intent, task_risk=tr)))
            raised = ""
        except Exception as ex:                          # noqa: BLE001
            raised = repr(ex)
        check("★ 审计写盘失败时 decide() 不抛异常", not raised, raised)
        check("★★ 审计写盘失败时决策逐字段不变（审计是证据不是闸门）",
              bad_sigs == clean_sigs, f"{bad_sigs} vs {clean_sigs}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# F 组：接线判据（AST —— 唯一真实构造点必须传 audit_dir）
# ===========================================================================
def part_f_wiring():
    print("\n-- F) 接线判据：ui.py 的引擎构造 --")
    ui_src = open(UI_PATH, encoding="utf-8-sig").read()
    tree = ast.parse(ui_src)

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "PermissionEngine"]
    check("ui.py 里能找到 PermissionEngine 构造点", bool(calls), f"{len(calls)} 处")
    if calls:
        missing = [c.lineno for c in calls
                   if "audit_dir" not in {k.arg for k in c.keywords}]
        check("★ 每一处构造都显式接线 audit_dir（漏接线 = 主对话决策无痕）",
              not missing, f"未接线行号 {missing}")

    # 接线用的目录来源必须是 permissions 里的那个函数（不是手写路径）
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "permissions":
            imported |= {a.name for a in n.names}
    check("ui.py 从 permissions 取 default_audit_dir（单一事实源，不手拼路径）",
          "default_audit_dir" in imported, sorted(imported))

    # 引擎默认值必须是"关闭"：否则上面那条接线就有"没接也照样写"的退路，
    # 而代价是所有判据套件都会往真实账本写垃圾。
    sig_default = PermissionEngine.__init__.__defaults__
    check('★ PermissionEngine 的 audit_dir 默认值必须是 ""（关闭）',
          sig_default and sig_default[-1] == "", repr(sig_default))


# ===========================================================================
# G 组：两账本同源
# ===========================================================================
def part_g_same_source():
    print("\n-- G) 军团与主对话共用同一份范式 --")
    check("★ legion._digest 就是 tool_audit.digest（同一函数对象，不是复制品）",
          lp._digest is tool_audit.digest)
    check("★ legion._redact 就是 tool_audit.redact", lp._redact is tool_audit.redact)
    check("★ legion._AUDIT_LOCK 就是 tool_audit.AUDIT_LOCK（共用一把锁）",
          lp._AUDIT_LOCK is tool_audit.AUDIT_LOCK)
    check("军团账本文件名未变（UI 面板 legion_status_widget 在读它）",
          lp._AUDIT_NAME == tool_audit.LEGION_AUDIT_NAME == "legion_tool_audit.jsonl",
          lp._AUDIT_NAME)
    check("两账本文件名不同（可分开抽查，也可按 origin 合并）",
          tool_audit.MAIN_AUDIT_NAME != tool_audit.LEGION_AUDIT_NAME)

    tmp = _tmpdir()
    try:
        a = lp.LegionPermissionAdapter(run_id="r1", scope_paths=[tmp], audit_dir=tmp)
        a.check("run_command", {"command": "echo hi"}, role="研究员", wave=1)
        rows = _read(os.path.join(tmp, "legion_tool_audit.jsonl"))
        check("军团仍照常留痕", len(rows) == 1, f"{len(rows)}")
        if rows:
            r = rows[0]
            missing = [k for k in CORE_FIELDS if k not in r]
            check(f"★ 军团记录 ⊇ 主对话核心字段（同一 build_record 产出）：{missing or '无'}",
                  not missing, f"缺 {missing}")
            check('军团 origin == "legion"', r.get("origin") == "legion", r.get("origin"))
            check("军团独有字段仍在（run_id/role/wave 可定位到人）",
                  r.get("run_id") == "r1" and r.get("role") == "研究员" and r.get("wave") == 1,
                  f"{r.get('run_id')}/{r.get('role')}/{r.get('wave')}")
            check("军团的 args_digest 长度与主对话一致（12）",
                  len(r.get("args_digest", "")) == 12, repr(r.get("args_digest")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===========================================================================
# H 组：源码契约（审计必须在 decide 函数体之外）
# ===========================================================================
def part_h_source_contract():
    print("\n-- H) 源码契约：不许破坏 AST 抽取 / 不许改签名 --")
    tree = ast.parse(PERM_SRC)
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "PermissionEngine"), None)
    check("PermissionEngine 仍在 permissions.py", cls is not None)
    if cls is None:
        return
    fn = next((n for n in cls.body
               if isinstance(n, ast.FunctionDef) and n.name == "decide"), None)
    check("★ decide 仍是**名为 decide 的函数**（tests/test_permission_gates.py 的 G 组"
          "按此名 AST 抽取，改名会连带搞坏那套判据）", fn is not None)
    if fn is None:
        return
    decs = [getattr(d, "id", "") or getattr(d, "attr", "")
            for d in fn.decorator_list]
    check("★ decide 由 @_audited 装饰（这就是「每条 return 路径都落一笔」的实现方式）",
          "_audited" in decs, decs)
    seg = ast.get_source_segment(PERM_SRC, fn) or ""
    check("get_source_segment 确实拿到了函数体（非空）", bool(seg.strip()))
    check("装饰器行**不在**抽取段里（否则受限命名空间 exec 会 NameError）",
          not seg.lstrip().startswith("@"), seg[:40])
    for bad in ("tool_audit", "open(", "makedirs", "json.dumps"):
        check(f"★ decide 函数体不得出现 `{bad}`（审计留在函数体之外）",
              bad not in seg, f"出现在函数体里")

    # 签名不许被包装器改掉
    import inspect
    params = list(inspect.signature(PermissionEngine.decide).parameters)
    check("★ 对外签名仍是 (self, name, args, explicit_intent, task_risk)",
          params == ["self", "name", "args", "explicit_intent", "task_risk"], params)
    inner = getattr(PermissionEngine.decide, "__wrapped__", None)
    check("包装器保留了 __wrapped__（内省/调试能看到原始函数）", callable(inner))
    if callable(inner):
        iparams = list(inspect.signature(inner).parameters)
        check("包装器与原始函数参数表一致（不重抄签名 → 不存在漂移）",
              iparams == params, f"{iparams} vs {params}")

    # _audited 必须真的调用落盘（把审计实现钉在装饰器上，而不是某个调用点）
    helper = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_audited"), None)
    check("permissions.py 里有 _audited 定义", helper is not None)
    hnames = set()
    for n in ast.walk(helper) if helper else []:
        if isinstance(n, ast.Attribute):
            hnames.add(n.attr)
        elif isinstance(n, ast.Name):
            hnames.add(n.id)
    check("★ _audited 内部真的调用了 _audit_decision（不是空壳）",
          "_audit_decision" in hnames, sorted(hnames))
    ad = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_audit_decision"), None)
    check("_audit_decision 定义存在", ad is not None)
    if ad:
        asrc = ast.get_source_segment(PERM_SRC, ad) or ""
        check("_audit_decision 真的调了 tool_audit.write（不是只组记录不落盘）",
              "tool_audit.write" in asrc, asrc[:120])

    # tool_audit 侧：只读不记 / 锁 / 默认目录
    ta_tree = ast.parse(TA_SRC)
    tnames = {n.name for n in ast.walk(ta_tree) if isinstance(n, ast.FunctionDef)}
    check("tool_audit 提供 digest / redact / build_record / write / default_dir",
          {"digest", "redact", "build_record", "write", "default_dir"} <= tnames,
          sorted(tnames))
    wsrc = next((ast.get_source_segment(TA_SRC, n) for n in ast.walk(ta_tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "write"), "")
    check("★ write() 里有 try/except 兜底（审计失败绝不外泄异常）",
          "except" in wsrc and "return False" in wsrc)
    check("★ write() 用 AUDIT_LOCK 加锁（多线程追加不能靠「通常不碰撞」）",
          "AUDIT_LOCK" in wsrc, wsrc[-200:])
    bsrc = next((ast.get_source_segment(TA_SRC, n) for n in ast.walk(ta_tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "build_record"), "")
    check("★ build_record 必须同时写 args_digest 与 args_preview（缺一即无法抽查）",
          "args_digest" in bsrc and "args_preview" in bsrc)


def _run_part(fn):
    """跑一组判据，把**异常本身**也变成一条 FAIL。

    为什么不直接调：被测对象被整体拆掉时（例如「审计整个没了」），某一组很可能
    在读到空账本时抛异常。若让异常逃出去，`main()` 会中断，**后面几组根本不跑** ——
    扰动脚本看到的红项就少一大半，表现为「期望转红但没红」的假象，让人误以为
    是期望串写错了。判据崩溃与判据转红必须区分开，且都不能吞掉后续组的结论。
    """
    try:
        fn()
    except Exception as ex:                              # noqa: BLE001
        global _f
        _f += 1
        print(f"  [FAIL] 组 {fn.__name__} 抛异常未跑完：{type(ex).__name__}: {ex}")
        import traceback
        traceback.print_exc()


def main():
    for part in (part_a_one_line_per_decision, part_b_read_only_not_recorded,
                 part_c_record_shape, part_d_redact, part_e_no_side_effects,
                 part_f_wiring, part_g_same_source, part_h_source_contract):
        _run_part(part)
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
