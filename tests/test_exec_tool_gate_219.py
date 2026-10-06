# -*- coding: utf-8 -*-
"""exec_tool 最终权限闸门（审查报告 P1 #4，v4.219）验收判据。

验证点：
  G1 无权限上下文时，EXEC 类工具（process_kill）被闸门拒绝（fail-closed）。
  G2 无权限上下文时，未登记工具（send_email → classify 兜底 EXTERNAL）被拒绝。
  G3 无权限上下文时，READ 类工具（web_search）放行（保向后兼容，只读不拦）。
  G4 携带 Decision(allowed=True) 时信任放行（不重复弹窗）。
  G5 携带 Decision(allowed=False) 时信任拒绝。
  G6 exec_tool 集成：无 ctx 调 EXEC 工具应被闸门拦截、未真正执行（deliverables 空 +
      返回文案含「exec_tool 需要权限上下文」）。

G1/G2/G6 是「改坏必红」核心：扰动脚本把闸门的 fail-closed 分支改成恒放行后，
这几条必须翻红（证明闸门是真实防线，不是摆设）。

无头可跑：不依赖 GUI / 真实进程。G6 即便闸门被绕过，process_kill 拿到假名也只
触发 `taskkill /IM <假名>` 返回「未找到进程」，无真实杀伤。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from risk import RiskClass, classify  # noqa: E402
from permissions import Decision  # noqa: E402

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


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- G1/G2/G3：无 ctx 时按风险分类 fail-closed ----------
def test_fail_closed_no_ctx():
    print("== G1/G2/G3 无 ctx 按风险分类 fail-closed ==")
    ok_ex, msg_ex = tools._permission_gate("process_kill", {"name": "x"}, None)
    check("无ctx时EXEC工具(process_kill)被最终闸门拒绝",
          (not ok_ex) and ("exec_tool 需要权限上下文" in msg_ex),
          "ok_ex=%s msg=%r" % (ok_ex, msg_ex))

    ok_ext, msg_ext = tools._permission_gate("send_email", {}, None)
    check("无ctx时未登记工具(send_email)被最终闸门拒绝",
          (not ok_ext) and ("exec_tool 需要权限上下文" in msg_ext),
          "ok_ext=%s msg=%r" % (ok_ext, msg_ext))

    # classify 兜底校验：send_email 确实落到 EXTERNAL
    check("send_email 风险类=EXTERNAL(兜底)", classify("send_email") == RiskClass.EXTERNAL,
          "risk=%s" % classify("send_email"))

    ok_read, _ = tools._permission_gate("web_search", {}, None)
    check("无ctx时READ工具(web_search)放行(向后兼容)", ok_read is True,
          "ok_read=%s" % ok_read)


# ---------- G4/G5：携带 Decision 信任其结论 ----------
def test_decision_trusted():
    print("== G4/G5 携带 Decision 信任其 allowed ==")
    allow = Decision(allowed=True, needs_user=False, reason="test-allow", rule="test")
    deny = Decision(allowed=False, needs_user=False, reason="test-deny", rule="test")

    ok_a, _ = tools._permission_gate("process_kill", {"name": "x"}, allow)
    check("携带Decision(allowed=True)信任放行", ok_a is True, "ok_a=%s" % ok_a)

    ok_d, msg_d = tools._permission_gate("process_kill", {"name": "x"}, deny)
    check("携带Decision(allowed=False)信任拒绝",
          (not ok_d) and ("权限决策拒绝执行" in msg_d),
          "ok_d=%s msg=%r" % (ok_d, msg_d))


# ---------- G6：exec_tool 集成拦截（不真正执行） ----------
def test_exec_tool_integration():
    print("== G6 exec_tool 集成：无 ctx 拒绝 EXEC 且未真正执行 ==")
    # 闸门生效（无 perm_ctx）：应直接返回拒绝文案，deliverables 为空，不触达 process_kill 执行。
    msg, deliverables, _ = tools.exec_tool(
        None, APP_DIR, "process_kill", {"name": "nonexistent_xyz_12345"})
    check("exec_tool集成:无ctx拒绝EXEC且未真正执行",
          ("exec_tool 需要权限上下文" in msg) and (deliverables == []),
          "msg=%r deliverables=%r" % (msg, deliverables))

    # 携带拒绝决策：同样应在闸门处拦截，不触达执行。
    deny = Decision(allowed=False, needs_user=False, reason="test-deny", rule="test")
    msg2, deliv2, _ = tools.exec_tool(
        None, APP_DIR, "process_kill", {"name": "nonexistent_xyz_12345"},
        perm_ctx=deny)
    check("exec_tool集成:携带拒绝Decision在闸门拦截",
          ("权限决策拒绝执行" in msg2) and (deliv2 == []),
          "msg2=%r deliv2=%r" % (msg2, deliv2))


def main():
    test_fail_closed_no_ctx()
    test_decision_trusted()
    test_exec_tool_integration()
    print("\n==== exec_tool 最终闸门结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
