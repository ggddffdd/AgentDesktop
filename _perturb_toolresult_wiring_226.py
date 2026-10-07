# -*- coding: utf-8 -*-
"""扰动 v4.226 P1-2：ToolResult 是否真贯穿 Agent 状态判断。

背景（已复现，非理论）
----------------------
`tools.exec_tool()` 返回 `ToolResult`（带 ok / verified / error_code），
但 agent.py 两条执行路径曾当场解包成三元组 → 字段全丢 → 成败判断退化成
「看文案」。而执行后验证的失败标记 `[执行后验证未通过]` 追加在msg **尾部**，
`_TOOL_FAIL_MARKS` 只扫 `s[:400]` → 正文超400 字时连头都扫不到 →
**验证失败被记成成功**（UI 绿勾 + 账本 ok=True）。

本扰动按项目纪律，每条防线用两种手法各验一次：
  · 「整段删」——把整条判定拿掉
  · 「只删动作留条件」——留下 if/hasattr 外壳，只把动作退化成恒假/恒真
    （这是最阴的一种：源码里`if hasattr(...)` 还在，肉眼看不出问题）

判据：tests/test_toolresult_wiring_226.py必须翻对应红条。
验收标准 = **哑弹清零**（HIT == CASES 数）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_toolresult_wiring_226.py")

# 被扰动的文件：agent.py（执行路径）+ agent_result_mixin.py（判定真源）
FILES = ["agent.py", "agent_result_mixin.py", "agent_task_mixin.py"]

_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                           timeout=300, env=env, encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        return ["<TIMEOUT>"]
    out = (r.stdout or "") + (r.stderr or "")
    return re.findall(r"\[FAIL\] ([^\n]+)", out)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm()

# ------------------------------------------------------------
# 原串一律从真实源码抄（缩进差一级就 SKIP；SKIP 不能当"跑过了"）
# ------------------------------------------------------------
_JUDGE_OLD = '''        if tool_result is not None and hasattr(tool_result, "ok"):
            try:
                _ok = bool(tool_result.ok)
            except Exception:
                _ok = True
            if _ok:
                return (True, None)
            return (False, self._tr_err_head(tool_result))'''

_JUDGE_NEW_NONE = '''        if False:  # 扰动：整段删掉结构化判定
            pass'''

# 只删动作留条件：外壳 hasattr 还在，只把取 ok 换成恒真
_JUDGE_NEW_SHELL = '''        if tool_result is not None and hasattr(tool_result, "ok"):
            try:
                _ok = True
            except Exception:
                _ok = True
            if _ok:
                return (True, None)
            return (False, self._tr_err_head(tool_result))'''

_TSTATE_OLD = '''            if tool_result is not None and hasattr(tool_result, "ok"):
                _ok = bool(tool_result.ok)
            else:'''
_TSTATE_NEW = '''            if False:  # 扰动：账本不认ToolResult
                _ok = True
            else:'''

# agent.py：串行路径「exec_tool 返回值赋给_tr」
_AG_EXEC_OLD = "                        _tr = tools.exec_tool("
_AG_EXEC_NEW = "                        result_str, deliverables, schedule = tools.exec_tool("

# agent.py：UI 绿勾改回看文案
_AG_OKC_OLD = '''                "success": _okc,'''
_AG_OKC_NEW = '''                "success": not str(result_str)[:20].startswith(
                    ("失败", "错误", "工具执行异常", "未知工具")),'''

# agent_result_mixin.py：记账不再透传 tool_result
_RM_PASS_OLD = "                            self._tool_result_looks_failed, tool_result=tool_result)"
_RM_PASS_NEW = "                            self._tool_result_looks_failed)"

CASES = [
    # ---- 防线1：判定真源 ----
    ("判定真源整段删（ToolResult 完全不看）",
     "agent_result_mixin.py", _JUDGE_OLD, _JUDGE_NEW_NONE,
     ["A2 ToolResult.ok=False", "A3 同一场景", "A6 鸭子类型"]),

    ("判定真源只删动作留条件（有 hasattr 但恒判成功）",
     "agent_result_mixin.py", _JUDGE_OLD, _JUDGE_NEW_SHELL,
     ["A2 ToolResult.ok=False", "A3 同一场景", "A6 鸭子类型"]),

    # ---- 防线2：任务账本 ----
    ("任务账本不认ToolResult（退回字符串判据）",
     "agent_task_mixin.py", _TSTATE_OLD, _TSTATE_NEW,
     ["C9 账本行为级：ToolResult.ok=False → 账本记失败"]),

    # ---- 防线3：执行路径接线 ----
    ("串行路径当场解包 exec_tool（字段又丢了）",
     "agent.py", _AG_EXEC_OLD, _AG_EXEC_NEW,
     ["B3 串行 路径：exec_tool 返回值都赋给了 _tr"]),

    ("UI 绿勾改回看文案前缀",
     "agent.py", _AG_OKC_OLD, _AG_OKC_NEW,
     ["B4 两处 tool_finished 的 success 都取自 _okc"]),

    # ---- 防线4：记账透传 ----
    ("记账不再透传 tool_result",
     "agent_result_mixin.py", _RM_PASS_OLD, _RM_PASS_NEW,
     ["C8b _handle_tool_result 把 tool_result 透传给记账"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        mutated = src.replace(old, new, 1)
        write_raw(fp, (mutated.replace("\n", "\r\n") if _crlf[fp] else mutated)
                  .encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望 %s，实际 %s" % (expect, red[:4]))))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)