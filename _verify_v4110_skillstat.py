# -*- coding: utf-8 -*-
"""v4.110 技能使用体检 —— 回归验证。

覆盖：
  A route_log.log_skill 语义（字段 / 旁路吞异常）
  B tools.py 自动埋点（成功 / 落空 / 空内容 三处 + 防御式 import）
  C ui.py  手动埋点（只在选中时记 + 防御式 import）
  D 真实调用 tool_use_skill（成功 / 落空 / 空名 三条路径真的落盘）
  E route_stats.py 技能榜（Top / 覆盖率 / 死重 / 落空榜 四块都在）
  F 打包配置（route_log 已进 hiddenimports）

防假通过的铁律（v4.109 踩过）：
  · 取方法源码必须断言非空再断言内容，空串会让 `x not in ""` 恒真。
  · 取不到的方法进 _MISSING，最后统一 FAIL。
"""

import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []
_MISSING = []


def chk(name, cond, extra=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, extra))


def _load(path):
    with io.open(path, encoding="utf-8-sig") as f:
        src = f.read()
    return ast.parse(src), src


def func_src(tree, src, name):
    """按函数名取源码。取不到记 _MISSING——调用方必须先断言非空。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    _MISSING.append(name)
    return ""


UI_PY = os.path.join(HERE, "ui.py")
TOOLS_PY = os.path.join(HERE, "tools.py")
STATS_PY = os.path.join(HERE, "route_stats.py")
SPEC = os.path.join(HERE, "小臭玩AI.spec")

ui_tree, ui_src = _load(UI_PY)
tools_tree, tools_src = _load(TOOLS_PY)

# ==================== A 段：route_log.log_skill ====================
print("== A 段 route_log.log_skill ==")
sys.path.insert(0, HERE)
import route_log

chk("A1 log_skill 存在", callable(getattr(route_log, "log_skill", None)))

_p = route_log._log_path()
_bak = None
_had = os.path.exists(_p)
if _had:
    with io.open(_p, encoding="utf-8", errors="replace") as _f:
        _bak = _f.read()

try:
    route_log.log_skill("测试技能甲", "manual")
    route_log.log_skill("测试技能乙", "auto", ok=False)
    _recs = route_log.read_recent(limit=50, event="skill")
    _sk = [r for r in _recs if r.get("name") == "测试技能甲"]
    chk("A2 写入 event=skill", bool(_sk))
    chk("A3 source 字段正确", _sk and _sk[-1].get("source") == "manual",
        str(_sk[-1] if _sk else None))
    _sk2 = [r for r in _recs if r.get("name") == "测试技能乙"]
    chk("A4 ok=False 落盘", _sk2 and _sk2[-1].get("ok") is False,
        str(_sk2[-1] if _sk2 else None))
    try:
        route_log.log_skill(object(), None, ok="not-a-bool")
        chk("A5 怪参数不抛异常（旁路）", True)
    except Exception as e:
        chk("A5 怪参数不抛异常（旁路）", False, repr(e))

    # ==================== B 段 tools.py 自动埋点 ====================
    print("== B 段 tools.py 自动埋点 ==")
    HS = func_src(tools_tree, tools_src, "_log_skill_hit")
    chk("B0 _log_skill_hit 源码非空", bool(HS))
    if HS:
        chk("B1 调 route_log.log_skill", "route_log.log_skill" in HS)
        chk("B2 source=auto", '"auto"' in HS)
        chk("B3 route_log 为 None 时跳过", "route_log is None" in HS)

    US = func_src(tools_tree, tools_src, "tool_use_skill")
    chk("B4 tool_use_skill 源码非空", bool(US))
    if US:
        chk("B5 成功路径埋点 ok=True", "_log_skill_hit(skill_name, ok=True)" in US)
        chk("B6 落空路径埋点 ok=False", "_log_skill_hit(skill_name, ok=False)" in US)
        chk("B7 落空埋点出现在未找到分支", US.find("_log_skill_hit(skill_name, ok=False)") < US.rfind("未找到技能"))

    chk("B8 route_log 防御式 import",
        "try:" in tools_src and "import route_log" in tools_src
        and "route_log = None" in tools_src)

    # ==================== C 段 ui.py 手动埋点 ====================
    print("== C 段 ui.py 手动埋点 ==")
    SP = func_src(ui_tree, ui_src, "_on_skill_pick")
    chk("C0 _on_skill_pick 源码非空", bool(SP))
    if SP:
        chk("C1 埋点存在", "route_log.log_skill" in SP)
        chk("C2 source=manual", '"manual"' in SP)
        chk("C3 只在选中时记（埋点在 if sk 之后）",
            SP.find("if sk:") >= 0 and SP.find("route_log.log_skill") > SP.find("if sk:"))
        chk("C4 埋点包了 try/except", SP.count("try:") >= 1)

    chk("C5 ui route_log 防御式 import",
        "try:" in ui_src and "import route_log" in ui_src
        and "route_log = None" in ui_src)

    # ==================== D 段 真实调用 ====================
    print("== D 段 真实调用 tool_use_skill ==")
    import tools

    _before = len(route_log.read_recent(limit=100000, event="skill"))
    _r1 = tools.tool_use_skill({}, HERE, "翻译")
    _r2 = tools.tool_use_skill({}, HERE, "绝对不存在的技能zzz")
    _r3 = tools.tool_use_skill({}, HERE, "   ")
    _after = route_log.read_recent(limit=100000, event="skill")

    chk("D1 真实技能加载成功", "已加载技能" in str(_r1), str(_r1)[:60])
    chk("D2 假技能返回未找到", "未找到技能" in str(_r2), str(_r2)[:60])
    chk("D3 空名不落日志", _r3 is not None and "未提供" in str(_r3), str(_r3)[:60])

    _new = _after[_before:] if len(_after) > _before else []
    chk("D4 真实调用落盘了日志", len(_new) >= 2, "新增 %d 条" % len(_new))
    _ok_hit = [r for r in _new if r.get("ok") and r.get("source") == "auto"]
    _bad_hit = [r for r in _new if r.get("ok") is False and r.get("source") == "auto"]
    chk("D5 成功记为 ok=True/auto", bool(_ok_hit), str(_new[:3]))
    chk("D6 落空记为 ok=False/auto", bool(_bad_hit), str(_new[:3]))
    chk("D7 空名未产生 skill 记录",
        not [r for r in _new if not (r.get("name") or "").strip()])

    # ==================== E 段 route_stats 技能榜 ====================
    print("== E 段 route_stats 技能榜 ==")
    import subprocess

    _env = dict(os.environ)
    _env["PYTHONIOENCODING"] = "utf-8"
    _pr = subprocess.run([sys.executable, STATS_PY], cwd=HERE, env=_env,
                         capture_output=True)
    _out = (_pr.stdout or b"").decode("utf-8", "replace")
    chk("E1 脚本退出码 0", _pr.returncode == 0, (_pr.stderr or b"").decode("utf-8", "replace")[-300:])
    chk("E2 输出技能使用榜章节", "技能使用榜" in _out)
    chk("E3 输出 Top 排行", "-- Top 排行 --" in _out)
    chk("E4 输出覆盖率", "覆盖率" in _out)
    chk("E5 输出死重候选", "从未使用（死重候选" in _out)
    chk("E6 输出技能判定", "-- 技能判定 --" in _out)

finally:
    # ==================== 还原日志 ====================
    try:
        if _bak is not None:
            with io.open(_p, "w", encoding="utf-8") as f:
                f.write(_bak)
        elif os.path.exists(_p):
            os.remove(_p)
    except Exception as e:
        print("  [警告] 日志还原失败：%s（%s）" % (e, _p))

# ==================== F 段 打包配置 ====================
print("== F 段 打包配置 ==")
_spec = ""
if os.path.exists(SPEC):
    with io.open(SPEC, encoding="utf-8-sig") as f:
        _spec = f.read()
chk("F1 spec 存在", bool(_spec))
chk("F2 hiddenimports 含 route_log", "route_log" in _spec)

# ==================== 汇总 ====================
if _MISSING:
    FAIL.append("取不到源码的方法：" + "、".join(_MISSING))
    print("  [警告] 以下方法未取到源码（类名/函数名写错会让断言恒真）：%s" % "、".join(_MISSING))

print("\n" + "=" * 56)
print("v4.110 技能体检回归：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAIL " + f)
    print("RESULT=FAIL")
else:
    print("RESULT=ALL_PASS")
