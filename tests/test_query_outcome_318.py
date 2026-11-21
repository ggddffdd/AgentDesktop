# -*- coding: utf-8 -*-
"""v4.318.0 判据：纯查询工具登记 outcome 契约（根治「完整返回却被判失败」）。

背景（2026-11-21 大哥自检 EV#2347）：
  - sys_info / log_query 明明把完整文本返回（4886 / 1352 字符），却被打
    ok=False + error_code=UNVERIFIED_OUTCOME + 「本次调用失败」戳。
  - 根因：这两个工具没有登记 outcome 契约 → ToolResult.from_legacy 走
    _infer_ok 兜底 → 末尾 `return "失败" not in s and "错误" not in s`
    全局子串判定。查询正文里本来就常含这两字（sys_info 能力清单里有
    「部分工具失败时重试」、log_query 返回的日志正文里有 ERROR/失败），
    于是「查到了」被误判成「调用失败」，给排查带偏。

修法（小范围）：
  - 给 sys_info / log_query 登记 outcome 契约，基于**结构性真实信号**：
    exec_tool 里 handler 抛异常会走 _reject（ToolResult.fail），根本不进
    from_legacy 的契约判定；能带 3-tuple 走到这里的查询必然是 handler
    正常返回，msg 非空字符串即「查询结果已产出」（"（无匹配日志）"这类
    空结果也是合法成功）。契约绝不对 msg 内容做中文子串匹配。
  - **不动 _infer_ok 全局兜底**（源码注释明确警告：82 个未登记工具依赖它，
    翻白名单会大范围回归）。

判据两层：
  A 层 —— 行为：resolve_ok / from_legacy 端到端；含对照组证明全局未动。
  B 层 —— 源码契约（语义锚定，防注释假绿）。
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


TC_SRC = read_src(os.path.join(ROOT, "tool_contract.py"))

try:
    import tool_contract as tc
except Exception as e:
    tc = None
    print("import tool_contract ERR", e)

# 查询正文样本：刻意含「失败」「错误」两字 —— 这正是误判触发条件
SYSINFO_WITH_FAIL = "工具数: 84\n注意: 部分工具失败时会自动重试\n运行正常"
LOGQUERY_WITH_ERR = "[14:43:01] [ERROR] 连接失败\n[14:43:02] [INFO] ok\n共 1352 字符"

print("=== A 层：行为 ===")

if tc is not None:
    # ---------- resolve_ok 契约表态 ----------
    check("A1  sys_info 含失败字样 → 契约表态 True",
          tc.resolve_ok("sys_info", SYSINFO_WITH_FAIL, {}) is True,
          "got=%r" % (tc.resolve_ok("sys_info", SYSINFO_WITH_FAIL, {})
                      if hasattr(tc, "resolve_ok") else "NO resolve_ok"))
    check("A2  log_query 含错误字样 → 契约表态 True",
          tc.resolve_ok("log_query", LOGQUERY_WITH_ERR, {}) is True)
    check("A3  空 msg → 契约不表态(None)，交兜底",
          tc.resolve_ok("sys_info", "", {}) is None)
    check("A4  非字符串 msg → 契约不表态(None)",
          tc.resolve_ok("sys_info", None, {}) is None)

    # ---------- from_legacy 端到端 ----------
    r1 = tc.ToolResult.from_legacy((SYSINFO_WITH_FAIL, [], None),
                                   name="sys_info", args={})
    check("A5  sys_info 完整返回 → ok=True + verified + 无失败戳",
          r1.ok is True and r1.verified is True
          and r1.error_code != "UNVERIFIED_OUTCOME",
          "ok=%r verified=%r ecode=%r" % (r1.ok, r1.verified, r1.error_code))
    r2 = tc.ToolResult.from_legacy((LOGQUERY_WITH_ERR, [], None),
                                   name="log_query", args={})
    check("A6  log_query 完整返回 → ok=True + verified + 无失败戳",
          r2.ok is True and r2.verified is True
          and r2.error_code != "UNVERIFIED_OUTCOME",
          "ok=%r verified=%r ecode=%r" % (r2.ok, r2.verified, r2.error_code))

    # ---------- 对照组：全局兜底未动 ----------
    r3 = tc.ToolResult.from_legacy(("工具数: 84\n部分工具失败时重试", [], None),
                                   name="process_list", args={})
    check("A7  未登记工具仍走兜底 → 含失败字样判 False（全局未动）",
          r3.ok is False and r3.error_code == "UNVERIFIED_OUTCOME",
          "ok=%r ecode=%r" % (r3.ok, r3.error_code))
    r4 = tc.ToolResult.from_legacy(("（无匹配日志）", [], None),
                                   name="not_registered_tool", args={})
    check("A8  未登记工具无失败字样 → INFERRED（全局未动）",
          r4.error_code == "INFERRED", "ecode=%r" % r4.error_code)

    # ---------- 空结果也是合法成功 ----------
    r5 = tc.ToolResult.from_legacy(("（无匹配日志）", [], None),
                                   name="log_query", args={})
    check("A9  log_query 空结果 → 仍判成功（合法空查询）",
          r5.ok is True and r5.verified is True,
          "ok=%r verified=%r" % (r5.ok, r5.verified))

    # ---------- 原 write_file 契约不受影响 ----------
    check("A10 write_file 原契约仍在（不误伤）",
          tc.resolve_ok("write_file", "已写入", {"path": "__nonexistent_318__.txt"}) is False)
else:
    for n in ["A1 sys_info 契约", "A2 log_query 契约", "A3 空msg", "A4 非str",
              "A5 sys_info端到端", "A6 log_query端到端", "A7 对照组失败",
              "A8 对照组INFERRED", "A9 空结果", "A10 write_file不误伤"]:
        check(n, False, "tool_contract 未导入")

print("=== B 层：源码契约（语义锚定）===")

# 登记必须是真实调用（行首锚定，不能是 docstring / 注释里的示例文字）
check("B1  register_outcome(\"sys_info\" 真实调用",
      re.search(r'^\s*register_outcome\(\s*["\']sys_info["\']', TC_SRC, re.M) is not None)
check("B2  register_outcome(\"log_query\" 真实调用",
      re.search(r'^\s*register_outcome\(\s*["\']log_query["\']', TC_SRC, re.M) is not None)

# 契约函数体不得对中文文案做子串匹配（否则等于把 _infer_ok 反模式换个地方复活）
m = re.search(r"def _outcome_readonly_query\b(?:(?!\ndef )[\s\S])*", TC_SRC)
body = m.group(0) if m else ""
check("B3  查询契约基于结构信号，无中文子串匹配",
      m is not None
      and '"失败" not in' not in body
      and '"错误" not in' not in body
      and "startswith(_FAILURE" not in body,
      "func_found=%s" % bool(m))
check("B4  契约以非空调 msg 为真实信号（isinstance + strip）",
      "isinstance(msg, str)" in body and "strip()" in body)

# 改判据不删判据：全局兜底那行必须保留（82 工具依赖，翻白名单会大范围回归）
check("B5  _infer_ok 全局兜底仍在（未动全局）",
      'return "失败" not in s and "错误" not in s' in TC_SRC)

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
