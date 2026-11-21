# -*- coding: utf-8 -*-
"""v4.321.0 判据：run_python 结局判定基于结构性真实信号（根治 ok=False 误报）。

背景（2026-11-21 大哥自检 EV#2446:L2）：
  - run_python 执行成功、stdout 完整输出，但日志记录 ok=False + UNVERIFIED_OUTCOME。
  - 根因：run_python 未登记 outcome 契约 → ToolResult.from_legacy 走 _infer_ok
    兜底 → 末尾 `return "失败" not in s and "错误" not in s` 全局子串判定。
    脚本正文里本就常含「失败/错误」（排查报告、ERROR 日志），被误判调用失败。

修法（结构性信号，零文案匹配）：
  - tool_run_python 加 with_ok=False：成功/失败各 return 点统一带 ok 布尔，
    ok = (是否走失败分支 + 子进程 returncode==0)。默认参数保证既有 2-tuple 调用零影响。
  - _h_run_python 传 with_ok=True，直接构造 ToolResult(ok=..., verified=True)，
    走 from_legacy 分支①（本身就是 ToolResult → 直接采用），根本不进 _infer_ok。

判据两层：
  A 层 —— 行为：正文含失败字样仍 ok=True；异常/空代码/退出码非 0 ok=False；
           默认 2-tuple 兼容；handler 端到端返回 ToolResult 且 verified=True。
  B 层 —— 源码契约（语义锚定，防注释/示例假绿 + 扰动必红）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


TOOLS_SRC = read_src(os.path.join(ROOT, "tools.py"))
TC_SRC = read_src(os.path.join(ROOT, "tool_contract.py"))

try:
    import tools as _tools
    from tool_contract import ToolResult
    _CFG = {"python_exe": sys.executable}
except Exception as e:
    _tools = None
    ToolResult = None
    print("import ERR", e)

# ---------------- A 层：行为 ----------------
print("-- A 层：结构性成功信号 --")
def _call_with_ok(code):
    """防御式取 (msg, dels, ok)；扰动把 with_ok 改坏导致解包失败时，
    返回 (None, None, None) 让判据记 FAIL，而不是抛 traceback 掩盖真实失败。"""
    try:
        ret = _tools.tool_run_python(".", code, cfg=_CFG, with_ok=True)
        if isinstance(ret, tuple) and len(ret) == 3:
            return ret
    except Exception:
        pass
    return (None, None, None)


if _tools is not None:
    # A1 正常完成、正文含失败/错误字样 -> ok=True（本次修复核心）
    r, d, ok = _call_with_ok('print("本次调用失败：日志里有 ERROR 和错误字样，脚本正常结束")')
    check("A1 正文含失败/ERROR 仍判 ok=True", ok is True, "ok=%r" % ok)

    # A2 脚本 raise -> ok=False（子进程退出码非 0）
    _, _, ok2 = _call_with_ok("raise ValueError('boom')")
    check("A2 脚本异常退出码非0 判 ok=False", ok2 is False, "ok=%r" % ok2)

    # A3 空代码 -> ok=False（入口失败分支）
    _, _, ok3 = _call_with_ok("")
    check("A3 空代码判 ok=False", ok3 is False, "ok=%r" % ok3)

    # A4 默认 2-tuple 兼容（既有测试/调用方零影响）
    ret = _tools.tool_run_python(".", "print(1+1)", cfg=_CFG)
    check("A4 默认返回仍是 2-tuple", isinstance(ret, tuple) and len(ret) == 2,
          "len=%s" % (len(ret) if isinstance(ret, tuple) else type(ret)))

    # A5 handler 端到端：直接返回 ToolResult，verified=True，无 UNVERIFIED 戳
    try:
        tr = _tools._h_run_python(_CFG, ".", {"code": 'print("调用失败 ERROR 错误")'})
    except Exception:
        tr = None
    check("A5 handler 返回 ToolResult", isinstance(tr, ToolResult),
          "type=%s" % type(tr).__name__)
    check("A6 handler ok=True 且 verified=True（不进 _infer_ok）",
          getattr(tr, "ok", None) is True and getattr(tr, "verified", None) is True
          and getattr(tr, "error_code", None) is None,
          "ok=%r verified=%r ecode=%r" % (getattr(tr, "ok", None),
                                          getattr(tr, "verified", None),
                                          getattr(tr, "error_code", None)))

    # A7 handler 异常 -> ok=False + RUN_FAILED
    try:
        tr2 = _tools._h_run_python(_CFG, ".", {"code": "raise RuntimeError('x')"})
    except Exception:
        tr2 = None
    check("A7 handler 异常 ok=False + RUN_FAILED",
          getattr(tr2, "ok", None) is False and getattr(tr2, "error_code", None) == "RUN_FAILED",
          "ok=%r ecode=%r" % (getattr(tr2, "ok", None), getattr(tr2, "error_code", None)))
else:
    check("A 层可运行", False, "tools import 失败")

# ---------------- B 层：源码契约（语义锚定） ----------------
print("-- B 层：源码语义锚定（防假绿/扰动必红）--")

# B1 函数签名带 with_ok=False（行首锚定）
check("B1 tool_run_python 带 with_ok 参数",
      re.search(r'def tool_run_python\([^)]*with_ok\s*=\s*False', TOOLS_SRC, re.S) is not None)

# B2 内部 _ret 打包函数按 ok 决定 tuple 长度（三元结构，非文案）
check("B2 _ret 按 with_ok 打包 ok 布尔",
      re.search(r"return \(msg, dels, ok\) if with_ok else \(msg, dels\)", TOOLS_SRC) is not None)

# B3 成功信号用子进程退出码（结构信号），不是中文子串
m = re.search(r"def tool_run_python\b(?:(?!\ndef )[\s\S])*", TOOLS_SRC)
body = m.group(0) if m else ""
check("B3 成功信号锚定 returncode（结构，非文案）",
      m is not None and "returncode" in body and "_ok = (_rc == 0)" in body,
      "func_found=%s" % bool(m))
check("B4 函数体无中文文案子串判定（未复活反模式）",
      '"失败" not in' not in body and '"错误" not in' not in body
      and "startswith(_FAILURE" not in body)

# B5 handler 用 with_ok=True 且显式构造 ToolResult（语义锚定）
hm = re.search(r"def _h_run_python\b(?:(?!\ndef )[\s\S])*", TOOLS_SRC)
hbody = hm.group(0) if hm else ""
check("B5 handler 传 with_ok=True",
      hm is not None and "with_ok=True" in hbody, "func_found=%s" % bool(hm))
check("B6 handler 显式构造 ToolResult(ok=...)",
      re.search(r"return ToolResult\(\s*ok\s*=\s*bool\(_ok\)", hbody) is not None)
check("B7 handler verified=True（非推断）", "verified=True" in hbody)

# 改判据不删判据：_infer_ok 全局兜底必须保留（82 未登记工具依赖）
check("B8 _infer_ok 全局兜底仍在（未动全局）",
      'return "失败" not in s and "错误" not in s' in TC_SRC)

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
