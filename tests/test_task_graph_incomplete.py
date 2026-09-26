# -*- coding: utf-8 -*-
"""「跑满轮次无正文」不能冒充成功 —— incomplete 终态贯通（审查 #2 残余 / v4.168.0）。

背景
----
v4.166.0 已经修掉「模型调用异常被 break 吞掉 → 空稿 completed」这条线。
但同级的另一个缺口还在：

    AgentNode 跑满 max_turns，每一轮都只有工具调用、没有正文
      → for-else 兜底，把一句可读说明当 output 正常返回
      → TaskGraph 见「executor 正常 return」→ 标 completed
      → 任务板/PM/结项报告全都以为"成员交了东西"

本套件钉住修好后的链路：
  ① TaskGraph 认识 incomplete 终态（既不冒充 completed，也不冤枉成 failed）
  ② incomplete 是**终态**：不会让整张图死锁等它
  ③ 保留键不污染下游 state（下游拿不到框架内部标记，但拿得到 incomplete 事实）
  ④ legion_worker 把 incomplete 变成任务板状态 + 结项报告补录

纯标准库 + AST，无 Qt。
"""
import ast
import os
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT))

import task_graph as TG   # noqa: E402

SRC = (ROOT / "legion_worker.py").read_text(encoding="utf-8-sig")
NODE = (ROOT / "agent_node.py").read_text(encoding="utf-8-sig")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def part_a():
    print("=== A) 终态词表 ===")
    check("导出 INCOMPLETE_FLAG 保留键", isinstance(TG.INCOMPLETE_FLAG, str)
          and TG.INCOMPLETE_FLAG)
    check("保留键不出现在常规字段名里", not TG.INCOMPLETE_FLAG.startswith("_output"))
    check("Task 状态注释含 incomplete", "incomplete" in (TG.Task.__doc__ or "")
          or "incomplete" in TG.__doc__)


def part_b():
    print("\n=== B) 执行器自报无产出 → 标 incomplete ===")
    tg = TG.TaskGraph()
    tg.create("m0", "成员0", lambda s: {**s, "m0_output": "（跑满 6 轮未产出正文）",
                                        TG.INCOMPLETE_FLAG: True})
    st = tg.run({})
    task = tg._tasks["m0"]
    check("节点状态 = incomplete", task.status == "incomplete", task.status)
    check("不是 completed", task.status != "completed")
    check("不是 failed", task.status != "failed")
    check("result 里带 incomplete 事实", task.result.get("incomplete") is True,
          str(task.result))
    check("state 里也带 incomplete 事实", st["m0"].get("incomplete") is True,
          str(st.get("m0")))
    check("保留键没被塞进 state",
          TG.INCOMPLETE_FLAG not in st["m0"], str(st.get("m0")))

    print("\n-- B2 正常产出仍标 completed（不能一刀切）--")
    tg2 = TG.TaskGraph()
    tg2.create("m1", "成员1", lambda s: {**s, "m1_output": "正文在这儿"})
    tg2.run({})
    check("正常节点 = completed", tg2._tasks["m1"].status == "completed")
    check("正常节点不带 incomplete", "incomplete" not in (tg2._tasks["m1"].result or {}))

    print("\n-- B3 异常仍标 failed（三条线互不串）--")
    def _boom(s):
        raise RuntimeError("模型挂了")

    tg3 = TG.TaskGraph()
    tg3.create("m2", "成员2", _boom)
    tg3.run({})
    check("异常节点 = failed", tg3._tasks["m2"].status == "failed", tg3._tasks["m2"].status)


def part_c():
    print("\n=== C) incomplete 是终态：不死锁、不拖住后续波 ===")
    tg = TG.TaskGraph()
    tg.create("a", "A", lambda s: {**s, "a_output": "（无正文）",
                                   TG.INCOMPLETE_FLAG: True})
    tg.create("b", "B", lambda s: {**s, "b_output": "ok"})
    tg.depend("b", "a")
    done = threading.Event()

    def _runner():
        tg.run({})
        done.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=10)
    check("任务图在 10 秒内跑完（没有死锁等待 incomplete 依赖）", done.is_set())
    check("上游 = incomplete", tg._tasks["a"].status == "incomplete")
    # b 依赖 a，a 未 completed → b 不会被派发，但图必须能正常收尾
    check("下游保持 pending（不假装成功，也不空跑）",
          tg._tasks["b"].status == "pending", tg._tasks["b"].status)

    print("\n-- C2 波内并行：一个 incomplete 不拖累其他人 --")
    tg2 = TG.TaskGraph()
    tg2.create("m0", "M0", lambda s: {**s, "m0_output": "（无正文）",
                                      TG.INCOMPLETE_FLAG: True})
    tg2.create("m1", "M1", lambda s: {**s, "m1_output": "正常产出"})
    tg2.create("m2", "M2", lambda s: {**s, "m2_output": "正常产出2"})
    tg2.run({})
    sts = {k: v.status for k, v in tg2._tasks.items()}
    check("一个 incomplete 两个 completed", sts == {"m0": "incomplete",
                                                  "m1": "completed",
                                                  "m2": "completed"}, str(sts))

    print("\n-- C3 与取消态共存不互相覆盖 --")
    import cancel_token as ct
    tok = ct.CancellationToken(name="t")
    tg3 = TG.TaskGraph()
    tk = tok.child(name="m0")
    tk.cancel("用户点了停止", stage="tool_call")

    def _late(s):
        tk.raise_if_cancelled("before_start")
        return {**s, TG.INCOMPLETE_FLAG: True}

    tg3.create("m0", "M0", _late)
    tg3.run({}, token=tok)
    check("已取消优先于 incomplete", tg3._tasks["m0"].status == "cancelled",
          tg3._tasks["m0"].status)


def part_d():
    print("\n=== D) 军团侧接线：任务板 / 日志 / 报告 ===")
    check("legion_worker 导入 INCOMPLETE_FLAG",
          "INCOMPLETE_FLAG" in SRC.split("def _wrap", 1)[0])
    seg = SRC.split("def _wrap", 1)[1][:2600]
    check("_wrap 检查成员 incomplete 标记",
          "_incomplete" in seg and "INCOMPLETE_FLAG" in seg)
    check("_wrap 任务板写 incomplete 状态", 'status="incomplete"' in seg)
    check("_wrap 打日志提示不计为完成", "不计为完成" in seg)

    check("_run_wave 扫描 incomplete 节点", "incomplete" in SRC
          and "_wave_incomplete_members" in SRC)
    check("incomplete 与 failed 分开登记",
          "_wave_incomplete_members.setdefault" in SRC
          and "_wave_failed_members.setdefault" in SRC)

    print("\n-- D2 结项报告补录 --")
    i = SRC.find("_make_final_report")
    seg2 = SRC[i:i + 8000]
    check("报告读 _wave_incomplete_members", "_wave_incomplete_members" in seg2)
    check("报告节标题明确", "成员无产出" in seg2 and "incomplete" in seg2)
    check("报告节说明无有效交付物", "无有效交付物" in seg2)
    check("补录块在 return 之前",
          seg2.find("_wave_incomplete_members") < seg2.find("return _sum"))

    print("\n-- D3 agent_node 侧仍保留显式标记（矩阵不丢）--")
    check("agent_node 写 <name>_incomplete", "_incomplete\" ] = True" in NODE
          or '_incomplete"] = True' in NODE)
    check("agent_node 日志写明 incomplete", "标记 incomplete" in NODE)
    check("agent_node 跑满轮次不静默", "跑满" in NODE)


def part_e():
    print("\n=== E) 源码契约：四态齐备、互不吞并 ===")
    src = (ROOT / "task_graph.py").read_text(encoding="utf-8-sig")
    for st in ("completed", "failed", "cancelled", "incomplete"):
        check(f"状态机含 {st}", f'"{st}"' in src)
    check("all_done 把 incomplete 当终态",
          '"completed", "cancelled", "incomplete"' in src)
    check("any_failed 把 incomplete 当阻断信号",
          'in ("failed", "incomplete")' in src)
    ast.parse(src)


def main():
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
