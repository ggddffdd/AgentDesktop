# -*- coding: utf-8 -*-
"""v4.221 P1#2：续跑 API 异常必须记失败并保留断点。

审查报告 P1#2 指出：主 Agent 续跑分支在 API 调用失败时只 `log.error` + `break`，
没有设置 `_note_exit("api_error")` / `_resumable_stop`，导致检查点被当作正常结束
删除、轨迹误判成功。

修复：在 `agent.py` 续跑 except 块补两行：
    self._note_exit("api_error")
    self._resumable_stop = True

判据：
  S1 静态：续跑 except 块精确包含这两行（删任一行即不匹配 → FAIL）。用相邻顺序
          正则，不被其它位置的 `_resumable_stop = True` 误导；
  B1 行为：_infer_outcome 在 api_error 标志下返回 "model_error"，证明「加这行」
          能把失败标志送进结局推断（从而保留断点而非误删）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import AgentWorker                 # noqa: E402

AGENT_PATH = os.path.join(ROOT, "agent.py")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def main():
    print("-- P1#2 续跑 API 异常记失败 + 保留断点 --")
    src = open(AGENT_PATH, encoding="utf-8-sig").read()

    # S1 续跑块精确含两行修复（删任一行正则即不匹配）
    m = re.search(
        r'log\.error\("Agent 续跑调用失败: %s", e\)\n'
        r'(\s*)self\.tool_log\.emit\([^\n]*\)\n'
        r'(\s*)self\._note_exit\("api_error"\)[^\n]*\n'
        r'(\s*)self\._resumable_stop = True', src)
    check("S1 续跑 except 块含 _note_exit('api_error') 与 _resumable_stop", bool(m))

    # B1 行为：api_error 标志被 _infer_outcome 消费为 model_error
    w = AgentWorker.__new__(AgentWorker)
    w._exit_flags = {"api_error": 1, "tool_calls": 3}
    try:
        out = w._infer_outcome(steps=5, max_steps=10, duration_s=1)
        check("B1 api_error 标志 → 结局 model_error", out == "model_error",
              "got=%r" % out)
    except Exception as e:
        check("B1 _infer_outcome 可调用", False, repr(e))

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
