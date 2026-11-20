# -*- coding: utf-8 -*-
"""v4.309 执行结算修复判据的真实源码扰动验证（改坏必红）。

每组扰动改一处真实源码 → 对应 want 判据必须翻红；改不红（假绿）即 FAIL。
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _perturb_guard

SUITE = os.path.join(ROOT, "tests", "test_execution_settle_309.py")
CR = "\r\n"
LF = "\n"

# (相对路径, 标签, 原行列表, 替换文本, want判据, 换行)
CASES = [
    # P1-1: 串行拒绝分支去掉结构化失败 → B1 红
    ("agent.py", "拒绝不构造失败结果",
     ['return _wf_early(self, "denied", f"（子代理工作流未执行：{dec.reason}）")'],
     'return "（子代理工作流未执行：%s）" % dec.reason  # 扰动：不标失败', "B8", CR),
    # P1-1: 签名登记判据退回裸 ok → B7 红
    ("agent_result_mixin.py", "签名登记绕过判据",
     ["if _es309.should_register_sig("],
     "if True:  # 扰动：绕过签名判据", "B7", LF),
    # P1-3: 续跑复位 helper 停用 → B12 红
    ("agent_exec_mixin.py", "续跑不复位 force",
     ["    if _es309.continuation_should_reset_force(True):"],
     "    if False:  # 扰动：不复位 force", "B12", CR),
    # P2-3: 结局 success 闸门停用 → B14 红
    ("agent.py", "success 不查账本",
     ["        if not _infer_success_ok(self, _tool_calls, _dlv, _unmet if _dlv else None):  # v4.309 P2-3"],
     "        if True:  # 扰动：success 不查账本", "B14", CR),
    # P2-3: 超时恢复硬编码 180 → B15 红
    ("agent.py", "超时恢复硬编码",
     ["        if _duration_over_budget(self, duration_s):  # v4.309 P2-3：配置预算非硬编码"],
     "        if duration_s and duration_s > 180:  # 扰动：硬编码", "B15", CR),
    # P2-4: 停止时又发 finished → B18 红
    ("agent_exec_mixin.py", "停止时不闭合卡片",
     ["                _final_ok = (_okc and not self._stop_requested)"],
     "                _final_ok = _okc  # 扰动：停止也记成功卡片", "B18", CR),
    # P2-5: 取消传播关掉 → B16 红
    ("task_graph.py", "取消不传播下游",
     ["                has_failed = any(t.status in (\"failed\", \"cancelled\")"],
     "                has_failed = any(t.status == \"failed\"  # 扰动：不含取消", "B16", LF),
    # P3-2: is_ready 缺失依赖放行 → B17 红
    ("task_graph.py", "缺失依赖放行",
     ["        if not _es309.is_ready_with_missing_dep(self.blocked_by, task_map):"],
     "        if False:  # 扰动：不校验缺失依赖", "B17", LF),
    # v4.310.0（第三方复核 P2-1/P2-2/P3-1/P3-2）
    # P2-1: 未知工作流类型恢复 return "" → B20 红
    ("agent.py", "未知类型不标失败",
     ['            return _wf_early(self, "unexecuted", f"（未知工作流类型 {workflow_type}，未执行）")'],
     '            return ""  # 扰动：未知类型不标失败', "B20", CR),
    # P2-2: 续跑不补收尾闸门 → B21 红
    ("agent.py", "续跑不补闸门",
     ['                        if self._tstate_resume_finish(rstep, self._max_resume_steps, _tracer, agent_loop): continue'],
     '                        pass  # 扰动：续跑不补闸门', "B21", CR),
    # P3-1: 并发异常恢复 _tr=None → B22 红
    ("agent_exec_mixin.py", "并发异常不标失败",
     ['                    _tr = _unexecuted_result(str(e), "crash")'],
     '                    _tr = None  # 扰动：并发异常不标失败', "B22", CR),
    # P3-2: 异常兜底不补发 finished → B23 红
    ("agent_exec_mixin.py", "异常不补finished",
     ['            self._close_open_tool_cards(',
      '                "（系统提示：该工具调用执行异常，未取得结果。）")'],
     '            pass  # 扰动：不补发finished', "B23", CR),
    # v4.311.0（第三方复验）：补发用假名 → B23b 红
    ("agent_exec_mixin.py", "补发用假名",
     ['                self._emit_tool_finished(_nm, result_str, _idx, 0, False)'],
     '                self._emit_tool_finished("（工具异常）", result_str, _idx, 0, False)  # 扰动：假名', "B23b", CR),
    # v4.311.0：续跑收尾闸门传模块形参 → B24 红
    ("agent_task_mixin.py", "续跑闸门传模块",
     ['            if self._tstate_nudge_now(self._tstate, step, max_steps):'],
     '            if self._tstate_nudge_now(task_state, step, max_steps):  # 扰动：传模块', "B24", CR),
]


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1500:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old_lines, new, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        old = nl.join(old_lines)
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s" % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, "r", encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
