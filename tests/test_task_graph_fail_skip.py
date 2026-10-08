# -*- coding: utf-8 -*-
"""失败节点下游显式标 skipped（不再含糊停在 pending）—— 修复 B 点（v4.230.0）。

背景
----
原 run() 在「ready 为空且存在 failed」时直接 `break`，导致失败节点的下游永远停在
pending：既不像 cancelled 那样被显式终态化，也不像 failed 那样报错，调用方（主账本
task_state）拿到 pending 会误判「任务没做完」去错误 nudge，或静默收尾时漏报失败。
且原注释写「跳过它们继续」实为 break，注释与实现相反。

本套件钉住修复后的链路：
  ① 失败节点的下游（含多级传递闭包）被显式标 skipped
  ② 失败节点的 executor 不被调用（skipped 节点不进线程池）
  ③ 与失败节点无依赖关系的独立分支仍正常 completed
  ④ run() 不抛异常、正常返回（不死循环、不卡死）
  ⑤ skipped 节点在 to_dict / result 里可观测
  ⑥ incomplete 路径不受牵连（见 test_task_graph_incomplete）

纯标准库 + AST，无 Qt。
"""
import ast
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
sys.path.insert(0, str(ROOT))

import task_graph as TG  # noqa: E402

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
    print("=== A) 失败下游一级 → skipped，且 executor 不被调用 ===")
    calls = []

    def ok(name):
        def _f(s):
            calls.append(name)
            return {**s, f"{name}_out": 1}
        return _f

    def boom(s):
        raise RuntimeError("C 挂了")

    tg = TG.TaskGraph()
    tg.create("A", "A", ok("A"))
    tg.create("B", "B", ok("B"))
    tg.depend("B", "A")
    tg.create("C", "C", boom)
    tg.create("D", "D", ok("D"))
    tg.depend("D", "C")

    done = threading.Event()

    def _runner():
        tg.run({})
        done.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=10)
    check("run 在 10s 内正常返回（不死循环/不卡死）", done.is_set())
    check("A = completed", tg._tasks["A"].status == "completed", tg._tasks["A"].status)
    check("B = completed（依赖成功节点）", tg._tasks["B"].status == "completed", tg._tasks["B"].status)
    check("C = failed", tg._tasks["C"].status == "failed", tg._tasks["C"].status)
    check("D = skipped（失败下游显式终态）", tg._tasks["D"].status == "skipped", tg._tasks["D"].status)
    check("D 的 executor 从未被调用", "D" not in calls, str(calls))
    check("A/B 正常执行各一次", calls.count("A") == 1 and calls.count("B") == 1, str(calls))


def part_b():
    print("\n=== B) 失败下游多级传递闭包 → 全 skipped ===")

    def boom(s):
        raise RuntimeError("C 挂了")

    tg = TG.TaskGraph()
    tg.create("C", "C", boom)
    tg.create("D", "D", lambda s: {**s})
    tg.depend("D", "C")
    tg.create("E2", "E2", lambda s: {**s})
    tg.depend("E2", "D")
    tg.create("F", "F", lambda s: {**s})
    tg.depend("F", "E2")
    tg.run({})
    sts = {k: v.status for k, v in tg._tasks.items()}
    check("D/E2/F 全 skipped（多级传递）",
          sts == {"C": "failed", "D": "skipped", "E2": "skipped", "F": "skipped"}, str(sts))


def part_c():
    print("\n=== C) 独立分支不受失败牵连 ===")

    def boom(s):
        raise RuntimeError("C 挂了")

    tg = TG.TaskGraph()
    tg.create("C", "C", boom)
    tg.create("D", "D", lambda s: {**s})
    tg.depend("D", "C")
    tg.create("E", "E", lambda s: {**s, "E_out": 1})
    tg.create("G", "G", lambda s: {**s, "G_out": 1})
    tg.depend("G", "E")
    tg.run({})
    sts = {k: v.status for k, v in tg._tasks.items()}
    check("独立分支 E/G 正常 completed", sts.get("E") == "completed" and sts.get("G") == "completed", str(sts))
    check("D 仍 skipped", sts.get("D") == "skipped", str(sts))
    check("C failed", sts.get("C") == "failed", str(sts))


def part_d():
    print("\n=== D) skipped 可观测（to_dict / result）===")

    def boom(s):
        raise RuntimeError("C 挂了")

    tg = TG.TaskGraph()
    tg.create("C", "C", boom)
    tg.create("D", "D", lambda s: {**s})
    tg.depend("D", "C")
    tg.run({})
    d_dict = tg._tasks["D"].to_dict()
    check("D to_dict 暴露 skipped 状态", d_dict.get("status") == "skipped", str(d_dict))
    check("D result 标记 skipped 原因", (tg._tasks["D"].result or {}).get("skipped") is True, str(tg._tasks["D"].result))
    check("C result 是失败信息（非 skipped）", (tg._tasks["C"].result or {}).get("error") is not None, str(tg._tasks["C"].result))


def part_e():
    print("\n=== E) 源码契约：skipped 终态贯通、精确针对 failed ===")
    src = (ROOT / "task_graph.py").read_text(encoding="utf-8-sig")
    check("状态机新增 skipped 终态", '"skipped"' in src)
    check("all_done 把 skipped 当终态",
          '"completed", "cancelled", "incomplete", "skipped"' in src)
    check("失败分支用精确 failed 判定（不误伤 incomplete）",
          't.status == "failed"' in src)
    check("有新方法标记失败下游 skipped",
          "_mark_dependents_of_failed_skipped" in src)
    try:
        ast.parse(src)
        ast_ok = True
    except SyntaxError:
        ast_ok = False
    check("源码可 AST 解析", ast_ok)


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
