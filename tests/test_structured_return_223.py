# -*- coding: utf-8 -*-
"""结构化返回根治（审查报告 P2，v4.223）验收判据。

v4.218 报告点名根因：`tool_contract._infer_ok` 靠中文前缀猜成败
（"已终止"=成功、"失败"=失败）——工具改一句文案就能翻转结论，且模型可以靠
"话术"伪装成功。v4.220 建了 ToolResult 框架，但仍留着这条 string-match 兜底。

本轮根治：
  SR1 登记了显式结局契约的工具，成败由**真实信号**判定，不看文案
      （write_file：文件是否真的落地且非空）。改文案翻不动结论。
  SR2 无契约的老工具仍需 _infer_ok 兜底，但结论被显式标注
      verified=False + error_code="INFERRED"，不再冒充已验证。
  SR3 ToolResult 补全机器可读字段 error_code / retryable / verified。
  SR4 fail()/ok_result() 两个工厂正确填充新字段。
  SR5 原生 ToolResult 的 ok 是权威值，from_legacy 不得改写。
  SR6 exec_tool 端到端：write_file 真落地→ok=True；删掉文件后同样调用→ok=False。

扰动脚本删掉契约判定 / 摘掉 INFERRED 标注后，对应判据必须翻红（哑弹 0）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tool_contract as tc  # noqa: E402
import tools  # noqa: E402
from permissions import Decision  # noqa: E402

_ALLOW = Decision(allowed=True, needs_user=False, reason="test", rule="test")

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


def test_contract_overrides_wording():
    print("== SR1 契约优先：文案翻不动结论 ==")
    ws = getattr(tools, "WORKSPACE_DIR", os.getcwd())
    probe = "_sr223_probe.txt"
    ap = os.path.join(ws, probe)
    try:
        with open(ap, "w", encoding="utf-8") as f:
            f.write("hello")
        r_fail_words = tc.ToolResult.from_legacy(
            ("失败：磁盘已满，未写入任何内容", [], None),
            name="write_file", args={"path": probe})
        check("SR1 文件真落地：即使文案说失败，ok 仍为 True",
              r_fail_words.ok is True and r_fail_words.verified is True,
              "ok=%s verified=%s" % (r_fail_words.ok, r_fail_words.verified))
    finally:
        try:
            os.remove(ap)
        except OSError:
            pass
    r_ok_words = tc.ToolResult.from_legacy(
        ("已写入：成功（共 5 字符）", [], None),
        name="write_file", args={"path": probe})
    check("SR1 文件未落地：即使文案说成功，ok 仍为 False",
          r_ok_words.ok is False and r_ok_words.verified is True,
          "ok=%s verified=%s" % (r_ok_words.ok, r_ok_words.verified))


def test_legacy_marked_unverified():
    print("== SR2 无契约兜底必须显式标注「猜的」 ==")
    r = tc.ToolResult.from_legacy(("随便一句不带成败词的话", [], None))
    check("SR2 走 _infer_ok 兜底时 verified=False",
          r.verified is False, "verified=%s" % r.verified)
    check("SR2 走 _infer_ok 兜底时 error_code=INFERRED",
          r.error_code == "INFERRED", "error_code=%s" % r.error_code)
    r2 = tc.ToolResult.from_legacy(("失败：xxx", [], None))
    check("SR2 兜底判定失败时 error_code=UNVERIFIED_OUTCOME",
          r2.error_code == "UNVERIFIED_OUTCOME", "error_code=%s" % r2.error_code)


def test_new_fields():
    print("== SR3 ToolResult 机器可读字段补全 ==")
    r = tc.ToolResult(ok=True, msg="m")
    check("SR3 error_code 字段存在且默认 None",
          hasattr(r, "error_code") and r.error_code is None, "got=%s" % r.error_code)
    check("SR3 retryable 字段存在且默认 False",
          hasattr(r, "retryable") and r.retryable is False, "got=%s" % r.retryable)
    check("SR3 verified 字段存在且默认 False",
          hasattr(r, "verified") and r.verified is False, "got=%s" % r.verified)


def test_factories():
    print("== SR4 工厂方法填充新字段 ==")
    f = tc.ToolResult.fail("炸了")
    check("SR4 fail() 填 error_code 且 verified=True",
          f.ok is False and f.error_code == "TOOL_FAILED" and f.verified is True,
          "code=%s verified=%s" % (f.error_code, f.verified))
    f2 = tc.ToolResult.fail("炸了", error_code="NET_DOWN", retryable=True)
    check("SR4 fail() 可指定 error_code/retryable",
          f2.error_code == "NET_DOWN" and f2.retryable is True,
          "code=%s retryable=%s" % (f2.error_code, f2.retryable))
    o = tc.ToolResult.ok_result("成了")
    check("SR4 ok_result() verified=True", o.ok is True and o.verified is True,
          "verified=%s" % o.verified)


def test_native_toolresult_authoritative():
    print("== SR5 原生 ToolResult 的 ok 是权威值 ==")
    native = tc.ToolResult(ok=False, msg="已终止 3 个进程", verified=True,
                           error_code="PARTIAL")
    out = tc.ToolResult.from_legacy(native, name="process_kill", args={})
    check("SR5 原生 ok 不被文案/契约改写",
          out is native and out.ok is False and out.error_code == "PARTIAL",
          "ok=%s code=%s" % (out.ok, out.error_code))


def test_exec_tool_end_to_end():
    print("== SR6 exec_tool 端到端（真实写入信号）==")
    ws = getattr(tools, "WORKSPACE_DIR", os.getcwd())
    probe = "_sr223_e2e.txt"
    ap = os.path.join(ws, probe)
    try:
        r = tools.exec_tool(None, ws, "write_file",
                            {"path": probe, "content": "v4.223"},
                            perm_ctx=_ALLOW)
        check("SR6 真写入 → exec_tool 返回 ok=True",
              isinstance(r, tc.ToolResult) and r.ok is True,
              "ok=%s msg=%s" % (getattr(r, "ok", "?"), getattr(r, "msg", "?")))
        check("SR6 真写入 → verified=True（结论经过验证）",
              getattr(r, "verified", None) is True, "verified=%s" % getattr(r, "verified", "?"))
    finally:
        try:
            os.remove(ap)
        except OSError:
            pass
    # 制造真实写入失败：目标路径指向一个已存在的**目录**（open(dir,'w') 必失败）。
    # 注意：不能「先写成功再删文件后重放」——重放会把文件重新写出来，契约会如实判 True。
    _dir = os.path.join(ws, "_sr223_dir_probe")
    try:
        os.makedirs(_dir, exist_ok=True)
        r2 = tools.exec_tool(None, ws, "write_file",
                             {"path": "_sr223_dir_probe", "content": "v4.223"},
                             perm_ctx=_ALLOW)
        check("SR6 写入未落地 → ok=False",
              isinstance(r2, tc.ToolResult) and r2.ok is False,
              "ok=%s msg=%s" % (getattr(r2, "ok", "?"), getattr(r2, "msg", "?")))
        r3 = tc.ToolResult.from_legacy(
            ("已写入：成功（谎报）", [], None),
            name="write_file", args={"path": "_sr223_dir_probe"})
        check("SR6 未落地时即便谎报成功文案，ok 仍为 False",
              r3.ok is False, "ok=%s" % r3.ok)
    finally:
        try:
            os.rmdir(_dir)
        except OSError:
            pass


def main():
    test_contract_overrides_wording()
    test_legacy_marked_unverified()
    test_new_fields()
    test_factories()
    test_native_toolresult_authoritative()
    test_exec_tool_end_to_end()
    print("\nSTRUCTURED_RETURN_223 PASS=%d FAIL=%d" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
