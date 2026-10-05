# -*- coding: utf-8 -*-
"""v4.213.0 判据：自动化执行任务**先执行后标记**（外部审核 P1 修复）。

背景：旧顺序是 tick 里先 `mark_fired`（once 类型同时置 disabled）再
`_fire_automation_run`，而 _fire 内部异常全吞、空消息静默 return ——
任务被记成「跑过」但实际没执行，**一次性任务就此静默丢失**。

守的东西（每条都能被 `_perturb_automation_fire.py` 单独翻红）：

  A1  执行分支里，mark_fired 必须包在 `if self._fire_automation_run(t):`
      的成功守卫内 —— fire 失败不得标记；
  A2  执行分支里不存在守卫之外的裸 mark_fired（旧顺序回归即红）；
  C   空消息（任务本身坏了）要标记 fired 防每秒无限重试，且该守卫
      出现在 fire 之前；
  B   `self._busy` 跳过保留（回归守卫）；
  D1  _fire_automation_run 成功路径 return True、异常路径 return False；
  D2  异常路径回滚刚追加的会话消息（messages.remove + save），
      防重试时同一指令重复堆积；
  E   行为判据（纯函数，不碰 Qt）：automation.mark_fired 对 once
      置 disabled + last_run 回写；is_due 在 last_run>0 后不再到期
      —— 这两条合起来 = 「失败不标记 → 下一 tick 自动重试」的机制基础。

扰动用环境变量 UI_PATH 指向变异副本（不动真源码）。
用法：python tests/test_automation_fire_order.py
"""
import ast
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print(f"  [PASS] {name}")
        _p += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        _f += 1


def load_ui_ast():
    """读 ui.py（或 UI_PATH 指向的变异副本）→ (source, tree)。读不了直接致命退出。"""
    path = os.environ.get("UI_PATH") or os.path.join(ROOT, "ui.py")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            src = f.read()
        tree = ast.parse(src)
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] ui.py 可解析  {type(e).__name__}: {e}")
        sys.exit(1)
    return src, tree


def find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def is_mark_fired_call(node):
    """automation.mark_fired(...) 调用。"""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mark_fired"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "automation")


def is_fire_call(node):
    """self._fire_automation_run(...) 调用。"""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_fire_automation_run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self")


def calls_in(stmts):
    """语句列表里（含嵌套）的所有 Call 节点，按源码序。"""
    out = []
    for st in stmts:
        for node in ast.walk(st):
            if isinstance(node, ast.Call):
                out.append(node)
    return sorted(out, key=lambda n: (n.lineno, n.col_offset))


def find_remind_if(tick_fn):
    """tick 函数里的 `if action == automation.ACT_REMIND:` 节点；orelse = 执行分支。"""
    for node in ast.walk(tick_fn):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                and t.left.id == "action" and len(t.ops) == 1
                and isinstance(t.ops[0], ast.Eq) and len(t.comparators) == 1):
            c = t.comparators[0]
            if (isinstance(c, ast.Attribute) and c.attr == "ACT_REMIND"
                    and isinstance(c.value, ast.Name) and c.value.id == "automation"):
                return node
    return None


def msg_get_chain(node):
    """t.get("message") 形态（含 `(t.get("message") or "").strip()` 外壳）。"""
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "message"):
            return True
    return False


def group_a_order_and_guards(src, tree):
    print("== A/B/C 组：tick 执行分支的结构判据 ==")
    tick = find_func(tree, "_on_automation_tick")
    check("A0  _on_automation_tick 存在", tick is not None)
    if tick is None:
        return
    remind_if = find_remind_if(tick)
    check("A0  remind/执行 分支识别", remind_if is not None and bool(remind_if.orelse))
    if not (remind_if and remind_if.orelse):
        return
    run_branch = remind_if.orelse

    # A1：fire 成功守卫内的 mark_fired
    fire_guard = None
    for st in run_branch:
        if isinstance(st, ast.If) and is_fire_call(st.test):
            fire_guard = st
            break
    check("A1a 存在 `if self._fire_automation_run(t):` 成功守卫", fire_guard is not None)
    guard_marks = []
    if fire_guard:
        guard_marks = [c for c in calls_in(fire_guard.body) if is_mark_fired_call(c)]
    check("A1b 成功守卫体内有 mark_fired（fire 成功才标记）", bool(guard_marks))

    # A2：执行分支里不存在守卫之外的裸 mark_fired（旧顺序回归即红）
    stray = []
    for c in calls_in(run_branch):
        if not is_mark_fired_call(c):
            continue
        inside_guard = (fire_guard is not None
                        and c.lineno >= min(x.lineno for x in ast.walk(fire_guard)
                                            if hasattr(x, "lineno"))
                        and c.lineno <= max(getattr(x, "end_lineno", x.lineno)
                                            for x in ast.walk(fire_guard)
                                            if hasattr(x, "lineno")))
        if inside_guard:
            continue
        # 空消息守卫里的 mark_fired 是允许的（C 组），其它一律算裸奔
        allowed = False
        for st in run_branch:
            if (isinstance(st, ast.If) and isinstance(st.test, ast.UnaryOp)
                    and isinstance(st.test.op, ast.Not) and msg_get_chain(st.test)
                    and any(is_mark_fired_call(cc) for cc in calls_in(st.body))):
                lo = min(x.lineno for x in ast.walk(st) if hasattr(x, "lineno"))
                hi = max(getattr(x, "end_lineno", x.lineno)
                         for x in ast.walk(st) if hasattr(x, "lineno"))
                if lo <= c.lineno <= hi:
                    allowed = True
        if not allowed:
            stray.append(c.lineno)
    check("A2  执行分支无守卫外的裸 mark_fired", not stray, f"裸奔行号: {stray}")

    # C：空消息守卫（在 fire 之前，含 continue）
    empty_guard = None
    for st in run_branch:
        if (isinstance(st, ast.If) and isinstance(st.test, ast.UnaryOp)
                and isinstance(st.test.op, ast.Not) and msg_get_chain(st.test)):
            empty_guard = st
            break
    check("C1  空消息守卫存在（防止每秒无限重试）", empty_guard is not None)
    # C2/C3 无条件判（不能因 C1 没找到就静默跳过 —— AF2 扰动抓过这个哑弹）
    c2_ok = bool(empty_guard) and any(
        isinstance(st, ast.Continue) for st in empty_guard.body) and any(
        is_mark_fired_call(c) for c in calls_in(empty_guard.body))
    check("C2  空消息守卫内 mark_fired + continue", c2_ok)
    fire_line = (min(n.lineno for n in ast.walk(fire_guard)
                     if hasattr(n, "lineno"))
                 if fire_guard else 10 ** 9)
    c3_ok = bool(empty_guard) and empty_guard.lineno < fire_line
    check("C3  空消息守卫在 fire 之前", c3_ok,
          f"守卫@{getattr(empty_guard, 'lineno', None)} fire@{fire_line}")

    # B：busy 跳过保留（回归守卫）
    busy = any(isinstance(st, ast.If) and isinstance(st.test, ast.Attribute)
               and st.test.attr == "_busy"
               and any(isinstance(s, ast.Continue) for s in st.body)
               for st in run_branch)
    check("B   `if self._busy: continue` 跳过保留", busy)


def group_d_fire_contract(tree):
    print("== D 组：_fire_automation_run 的返回契约与回滚 ==")
    fn = find_func(tree, "_fire_automation_run")
    check("D0  _fire_automation_run 存在", fn is not None)
    if fn is None:
        return
    try_body = [st for st in fn.body if isinstance(st, ast.Try)]
    check("D1a 函数体以 try 组织", bool(try_body))
    if not try_body:
        return
    t = try_body[0]
    rets_true = any(isinstance(st, ast.Return)
                    and isinstance(st.value, ast.Constant) and st.value.value is True
                    for st in t.body)
    check("D1b 成功路径 return True", rets_true)
    rets_false = any(h.body and isinstance(h.body[-1], ast.Return)
                     and isinstance(h.body[-1].value, ast.Constant)
                     and h.body[-1].value.value is False
                     for h in t.handlers)
    check("D1c 异常路径 return False", rets_false)
    # D2：回滚 —— except 里有 messages.remove + store.save
    # 两条都无条件判：remove 没了 D2b 也必须红（AF4 扰动抓过「静默跳过」哑弹）
    rollback_handler = None
    for h in t.handlers:
        if any(isinstance(c.func, ast.Attribute) and c.func.attr == "remove"
               for c in calls_in(h.body)):
            rollback_handler = h
            break
    check("D2a 异常路径回滚 messages.remove", rollback_handler is not None,
          "except 里没有 remove 调用")
    d2b_ok = rollback_handler is not None and any(
        isinstance(c.func, ast.Attribute) and c.func.attr == "save"
        and isinstance(c.func.value, ast.Attribute)
        and c.func.value.attr == "store"
        for c in calls_in(rollback_handler.body))
    check("D2b 回滚后 store.save", d2b_ok)
    # D3：append 的消息先落到具名变量（回滚才有句柄）
    assigned_then_append = any(
        isinstance(st, ast.Assign) and len(st.targets) == 1
        and isinstance(st.targets[0], ast.Name) and st.targets[0].id == "appended"
        for st in t.body) and any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        and c.func.attr == "append"
        and c.args and isinstance(c.args[0], ast.Name)
        and c.args[0].id == "appended"
        for c in calls_in(t.body))
    check("D3  消息先赋值 appended 再 append（回滚句柄）", assigned_then_append)


def group_e_behavior():
    print("== E 组：automation 纯函数行为（重试机制基础） ==")
    import automation
    t_once = {"schedule_type": automation.SCHED_ONCE, "enabled": True,
              "at_date": "2026-01-01", "at_time": "09:00", "last_run": 0.0}
    check("E1  once mark_fired → disabled + last_run 回写",
          (automation.mark_fired(t_once) is None
           and not t_once["enabled"] and t_once["last_run"] > 0))
    check("E2  once 已触发后 is_due=False（失败不标记 → 自然重试的机制基础）",
          not automation.is_due(t_once, datetime.now()))


def main():
    print("==== 自动化先执行后标记：判据开始 ====")
    src, tree = load_ui_ast()
    group_a_order_and_guards(src, tree)
    group_d_fire_contract(tree)
    group_e_behavior()
    print(f"\n==== 自动化先执行后标记结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
