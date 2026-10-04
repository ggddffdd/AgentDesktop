# -*- coding: utf-8 -*-
"""画布「运行」重跑语义 + 自动重绘 + 局部编辑参数键 —— 判据扰动。

守的是 2026-10-04 定位并修掉的三处硬伤对应的判据
（`tests/test_canvas_rerun_refresh.py` 的 A/B/C/D/E 五组）：

  1. 「运行」按钮静默假成功 —— 重跑前没把图重置回 pending；
  2. 画布不自动重绘 —— 数据层变了画面不动（含删除顺序边这条完全无效的支路）；
  3. 局部编辑「锐化」参数框不生效 —— 对话框写 factor，引擎读 amount。

做法：原地变异被测源码（先经 `_perturb_guard.arm()` 快照，跑完按字节还原），
对每个变异断言**目标判据本身**从 OK 变成 FAIL。

⚠️ 与既有的 `_perturb_canvas_panel.py` 有一处刻意的差别：那里的
`target_failed()` 把「rc != 0」一律视为扰动有效 —— 于是一个变异只要弄红
**任意一条**判据（哪怕不是目标那条）也会被判成"命中"。这会让「目标判据其实
是哑弹」被掩盖。这里收紧为：必须 `[FAIL] <目标>` 真的出现在输出里；
只有整套崩到连统计行（`PASS=`）都没有时（导入/语法错），才退化为 rc != 0。

独立运行：python _perturb_canvas_rerun_refresh.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
TEST = os.path.join(ROOT, "tests", "test_canvas_rerun_refresh.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
# 单次判据约 3 秒；给足余量，同时保证"变异导致挂起"能在可接受时间内暴露。
TEST_TIMEOUT = 120

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

SNAP = _guard.arm()

TG = os.path.join(ROOT, "task_graph.py")
CG = os.path.join(ROOT, "canvas_graph.py")
CP = os.path.join(ROOT, "canvas_panel.py")

# (name, 必须全红的判据列表, 被测文件, old 锚点, new 替换)
#
# 每个 `new` 都带 `# 扰动：…` 标记（护栏约定）；替换后仍是合法 Python
# （行为类变异要能被真跑起来，否则测不出"行为红"）。
MUTATIONS = [
    # ---- 缺陷①：重跑语义（三处独立防线，逐个拆）----
    # PR1：画布「运行」不再重置 → 真执行器一个不被调用 → 磁盘 0 文件。
    #      同时该行从 `_run_graph` 消失，AST 契约 E1 也红（"防忘了调"那层）。
    ("PR1_run_no_reset", ["E1", "B1"], CP,
     "        self.graph.reset_for_rerun()\n        try:",
     "        try:  # 扰动：不重置，run() 一个节点都不执行\n"),
    # PR2：底层 TaskGraph 不归 pending → 节点状态看着是 pending，任务仍是终态，
    #      run() 照样空转。**只在 CanvasGraph 上重置是不够的** —— 这正是
    #      "多道防线要一起拆"的那类：只改画布层，底层仍会挡住重跑。
    ("PR2_no_tg_reset", ["A7"], CG,
     "        self._tg.reset()\n",
     "        pass  # 扰动：不重置底层任务状态\n"),
    # PR3：run() 入口自己把任务重置回 pending（= 「不 reset 也照样重跑」）。
    #      A8 是**对照实验**（不 reset 必须 0 文件）：把这条前提拆掉，对照组
    #      就会落盘 → A8 红。它证明 A8 不是"永远为真"的空断言。
    #
    #      ⚠️ 这版是第二稿。第一稿写的是「is_ready 不再要求 pending」
    #      （`if self.status != "pending"` → `if False:`），实测**直接死循环**：
    #      completed 的任务重新满足 is_ready，`run()` 的 while True 每轮都能
    #      捞到"就绪"任务 → 反复重执行 → 子进程永不返回（表现为整轮回归被
    #      超时强杀、且护栏来不及还原，留下 `if False:  # 扰动` 残留）。
    #      教训：**变异必须落在判据能观测的位置，而不是让被测代码失去终止性** ——
    #      后者测出来的是"挂起"，不是"判据变红"，还会污染下一轮。
    ("PR3_run_self_reset", ["A8"], TG,
     "        state = dict(state)\n"
     "        # 无任务的空图直接返回\n"
     "        if not self._tasks:\n"
     "            return state\n",
     "        state = dict(state)\n"
     "        # 无任务的空图直接返回\n"
     "        if not self._tasks:\n"
     "            return state\n"
     "        for _t in self._tasks.values():\n"
     "            _t.status = \"pending\"  # 扰动：入口自动重置\n"),
    # PR4：重跑不清上一轮 out_assets → 旧产物引用被当成本轮结果（stale 语义被污染）。
    ("PR4_no_clear_assets", ["A5"], CG,
     "            node.out_assets = {p: None for p in node.outputs}\n",
     "            pass  # 扰动：不清 out_assets\n"),

    # ---- 缺陷②：自动重绘（含"删除顺序边"这条完全无效的支路）----
    # PR5：断「indexChanged → _refresh_view」信号通道 → push 删边后画面不再更新。
    #      targets 只报 C1：断信号后 C2 的画面断言会「巧合绿」（画面从没变过 =
    #      undo 后的"恢复"假象），C2 的数据断言由 PR7 守。
    ("PR5_delete_no_refresh", ["C1"], CP,
     "        self.undo_stack.indexChanged.connect(self._refresh_view)",
     "        pass  # 扰动：断信号通道"),
    # PR6：RemoveEdgeCommand 退回"只处理数据边"（顺序边点了没反应的原始 bug）。
    #      静态契约 E3（remove_order_edge 字样消失）与行为 C3 一起红。
    ("PR6_order_edge_revert", ["C3"], CP,
     "        if self.kind == \"order\":\n"
     "            self.graph.remove_order_edge(self.f[0], self.f[2], self.label)\n"
     "        else:\n"
     "            self.graph.remove_data_edge(*self.f)\n",
     "        self.graph.remove_data_edge(*self.f)  # 扰动：顺序边不管\n"),
    # PR7：撤销根本不执行 → 数据不回来、画面也不恢复。
    #      （v4.211.9 起 _undo 只调 undo()，重绘由 indexChanged 驱动——
    #       变异点从"删手动刷新"改为"撤销不执行"，守的是同一语义。）
    ("PR7_undo_no_refresh", ["C2"], CP,
     "            self.undo_stack.undo()",
     "            pass  # 扰动：撤销不执行"),
    # PR8：重绘时顺带刷新详情区 → 把"上一次操作的输出"（运行结果）冲掉。
    ("PR8_refresh_kills_detail", ["C5"], CP,
     "        self.render_graph(self.graph, fit=False, refresh_detail=False)",
     "        self.render_graph(self.graph, fit=False, refresh_detail=True)  # 扰动：冲掉详情区"),
    # PR9：连线后不发信号 → 面板收不到"该重画了"的通知。
    #      锚点锁 _finish_link（v4.211.9 起 _apply_menu_choice 里也有
    #       cmd.redo()+emit 同构片段，裸锚点不唯一，须带 ConnectCommand 前缀）。
    ("PR9_no_graphchanged", ["C6"], CP,
     "        cmd = ConnectCommand(self.graph, self.undo_stack, *f)\n"
     "        if self.undo_stack is not None:\n"
     "            self.undo_stack.push(cmd)\n"
     "        else:\n"
     "            cmd.redo()\n"
     "        self.graphChanged.emit()",
     "        cmd = ConnectCommand(self.graph, self.undo_stack, *f)\n"
     "        if self.undo_stack is not None:\n"
     "            self.undo_stack.push(cmd)\n"
     "        else:\n"
     "            cmd.redo()\n"
     "        pass  # 扰动：不发信号"),

    # ---- 缺陷③：局部编辑参数键 ----
    # PR10：sharpen 写回 factor（原始 bug）→ 引擎读 amount 读不到，静默回落 1.5。
    ("PR10_sharpen_key", ["D sharpen"], CP,
     '                    if op == "blur":\n'
     '                        key = "sigma"\n'
     '                    elif op == "sharpen":\n'
     '                        key = "amount"\n'
     "                    else:\n"
     '                        key = "factor"\n',
     '                    key = "sigma" if op == "blur" else "factor"  # 扰动：sharpen 写回 factor\n'),
]


def run_test():
    env = dict(os.environ)
    # 判据源码切片与行为 import 都指向**被变异的真文件**
    env["TG_PATH"] = TG
    env["CG_PATH"] = CG
    env["CP_PATH"] = CP
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        p = subprocess.run([PY, TEST], capture_output=True, text=True, env=env,
                           cwd=ROOT, timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        # 变异让被测代码**失去终止性**（死循环）→ 不是"判据变红"，是挂起。
        # 必须当失败处理：否则整轮回归被拖死，且护栏来不及还原就留下残留
        # （2026-10-04 实测踩到一次，见 PR3 的说明）。
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        return 124, out + "\n[扰动] 子进程超时 %ds —— 变异导致挂起而非判据翻红" % TEST_TIMEOUT
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def target_failed(targets, rc, out):
    """目标判据**每一条**都得真翻红。收紧口径见模块 docstring。"""
    if rc == 124:
        return False, "子进程超时（挂起，不算翻红）"
    missing = [t for t in targets if ("[FAIL] %s" % t) not in out]
    if not missing:
        return True, ""
    if rc != 0 and "PASS=" not in out:
        return True, "整套崩（无统计行）"
    return False, "未红的是: %s" % ", ".join(missing)


def restore(path):
    blob = SNAP.get(os.path.abspath(path))
    if blob is None:
        return
    with open(path, "wb") as f:
        f.write(blob)


def main():
    ok_all = True

    # 反向基线：不变异必须 ALL GREEN —— 否则判据本身就是假红，扰动结论无意义
    rc, out = run_test()
    base_ok = (rc == 0) and ("ALL GREEN" in out)
    print("反向基线（无变异，全跑）: %s" % ("OK ALL GREEN" if base_ok else "FAIL"))
    if not base_ok:
        print(out[-3000:])
    ok_all = ok_all and base_ok

    results = []
    try:
        for name, targets, path, old, new in MUTATIONS:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if old not in content:
                print("  [SKIP] %s — 锚点缺失: %r" % (name, old[:48]))
                results.append((name, False))
                ok_all = False
                continue
            if content.count(old) != 1:
                print("  [SKIP] %s — 锚点不唯一（命中 %d 次）: %r"
                      % (name, content.count(old), old[:48]))
                results.append((name, False))
                ok_all = False
                continue

            with open(path, "w", encoding="utf-8") as f:
                f.write(content.replace(old, new, 1))
            try:
                rc, out = run_test()
                hit, why = target_failed(targets, rc, out)
            finally:
                restore(path)

            print("  [%s] %-24s 目标 %s %s%s"
                  % ("OK " if hit else "BAD", name, "/".join(targets),
                     "✓" if hit else "✗", ("  — " + why) if why else ""))
            results.append((name, hit))
            ok_all = ok_all and hit

            # 还原后回读校验：还原不干净会让后续变异连环假红
            with open(path, "rb") as f:
                if f.read() != SNAP[os.path.abspath(path)]:
                    print("  [BAD] %s 还原后字节不一致！" % name)
                    ok_all = False
    finally:
        for _p in (TG, CG, CP):
            restore(_p)

    # 收尾：三个被测文件必须与快照逐字节一致
    for _p in (TG, CG, CP):
        with open(_p, "rb") as f:
            same = f.read() == SNAP[os.path.abspath(_p)]
        if not same:
            print("  [BAD] 收尾还原失败: %s" % os.path.basename(_p))
            ok_all = False

    total = len(MUTATIONS)
    ok_n = sum(1 for _, h in results if h)
    print("\n扰动总数: %d  有效: %d  基线: %s" %
          (total, ok_n, "OK" if base_ok else "FAIL"))
    print("PERTURB PASS=%d FAIL=%d"
          % (ok_n, total - ok_n + (0 if base_ok else 1)))
    if ok_all:
        print("ALL PERTURB OK: 判据非空转")
        sys.exit(0)
    print("PERTURB FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
