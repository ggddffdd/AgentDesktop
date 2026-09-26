# -*- coding: utf-8 -*-
"""AgentNode 模型调用失败必须显式失败（军团模块审查 #2）

背景
----
`agent_node.AgentNode.run()` 里模型调用异常原先只 `break` 就完事 ——
函数随后照常返回，`output` 是空串，`TaskGraph` 见「正常返回」就把节点标
`completed`。于是「成员崩了」被记成「成员交了空稿」：任务板、PM、最终报告
都看不见，还会白烧后续波的 token。

（工具执行异常那条线 v4.140 已改为 re-raise，本条是同级缺口。）

本套件用**真实 AgentNode 对象 + 假模型客户端**验证：
  ① 模型调用异常 → 抛 AgentExecutionError（带 stage / turn / 根因）；
  ② 跑满轮次且无正文 → 兜底文案 + state 里打 incomplete 标记；
  ③ 有正文时不受影响（不误标）。
无 Qt、无网络。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_node as an  # noqa: E402

_n_pass = 0
_n_fail = 0


def check(name, ok, detail=""):
    global _n_pass, _n_fail
    if ok:
        _n_pass += 1
        print(f"  [PASS] {name}")
    else:
        _n_fail += 1
        line = f"  [FAIL] {name}"
        if detail:
            line += f"  —— {detail}"
        print(line)


class _MWRaise:
    """模型客户端：每次调用都抛异常。"""

    cfg = {}

    def _agent_call(self, messages, tools, model_override=None, thinking=False):
        raise RuntimeError("HTTP 500 模拟模型故障")


class _MWToolOnly:
    """模型客户端：永远只回工具调用、不给正文（模拟跑满轮次无产出）。"""

    cfg = {}

    def _agent_call(self, messages, tools, model_override=None, thinking=False):
        return {"content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "not_allowed_tool", "arguments": "{}"},
        }]}


class _MWText:
    """模型客户端：直接给出正文。"""

    cfg = {}

    def _agent_call(self, messages, tools, model_override=None, thinking=False):
        return {"content": "这是正文产出", "tool_calls": []}


def main():
    # ---- ① 模型调用异常必须抛出 ----
    node = an.AgentNode("研究员", "你是研究员", tools=set(),
                        mw=_MWRaise(), max_turns=2)
    err = None
    try:
        node.run({"messages": [], "context": ""})
        check("模型调用异常必须抛出（不能正常 return）", False,
              "run() 正常返回了 —— 会被 TaskGraph 误标 completed")
    except an.AgentExecutionError as e:
        err = e
        check("模型调用异常抛出 AgentExecutionError", True)
    except Exception as e:  # 其它类型也算错（要求是专用异常）
        check("模型调用异常抛出 AgentExecutionError", False,
              f"实际抛出 {type(e).__name__}")

    if err is not None:
        check("异常带 stage=model_call", getattr(err, "stage", "") == "model_call",
              str(getattr(err, "stage", "")))
        check("异常带 turn=1", getattr(err, "turn", None) == 1,
              str(getattr(err, "turn", None)))
        check("异常保留根因", isinstance(getattr(err, "root_error", None), RuntimeError))
        check("异常消息含角色名", "研究员" in str(err), str(err)[:60])
        check("异常是 RuntimeError 子类（TaskGraph 的 except Exception 能兜住）",
              isinstance(err, RuntimeError))

    # ---- ② 跑满轮次且无正文 → incomplete ----
    node2 = an.AgentNode("写手", "你是写手", tools={"not_allowed_tool"},
                         mw=_MWToolOnly(), max_turns=2)
    st2 = node2.run({"messages": [], "context": ""})
    check("跑满轮次无正文 → 打 incomplete 标记",
          st2.get("写手_incomplete") is True, repr(st2.get("写手_incomplete")))
    out2 = (st2.get("写手_output") or "").strip()
    check("无正文时给出可读兜底文案（不是空串）", bool(out2), repr(out2))
    check("兜底文案说明是「未产出正文」", "未产出" in out2, out2[:60])

    # ---- ③ 有正文时不受影响 ----
    node3 = an.AgentNode("写手", "你是写手", tools=set(),
                         mw=_MWText(), max_turns=2)
    st3 = node3.run({"messages": [], "context": ""})
    check("有正文时不打 incomplete", "写手_incomplete" not in st3,
          repr(st3.get("写手_incomplete")))
    check("正文原样落盘", st3.get("写手_output") == "这是正文产出",
          repr(st3.get("写手_output")))

    print(f"\nPASS={_n_pass}  FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
