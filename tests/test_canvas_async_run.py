# -*- coding: utf-8 -*-
"""画布「运行」后台线程化（v4.211.10）—— 判据套件。

背景（2026-10-05 用户实弹反馈"点运行程序就卡死"）：
  v4.211.8 修好「运行假成功」后按钮真的开始跑图，暴露结构性问题——
  `_run_graph` 在 GUI 线程**同步**跑整图：gen_video 连 Agnes 生视频
  （提交 120s + 轮询 60×8s + 下载 300s，典型 1~5 分钟），事件循环被占住
  → Windows 判「未响应」。修法：整图执行搬进 QThread 后台线程
  （`_GraphRunWorker`），UI 经信号回 GUI 线程；协作式取消（CancellationToken，
  TaskGraph 原生支持）+ 运行期防编辑守卫（run_active，防数据竞争）。

五组判据（独立运行：QT_QPA_PLATFORM=offscreen python tests/test_canvas_async_run.py）：

  A 后台跑图 —— 非阻塞/事件循环活着/按钮态/端到端落盘/恢复
  B 协作式停止 —— 令牌取消/下游不冒充完成/按钮恢复
  C 失败吸收 —— 执行器异常 → 节点级 failed（不炸线程）
  D 运行期防编辑 —— 建删节点/删边入口全被拒（数据竞争防线）
  E 源码契约 —— QThread 子类/finished 收口/退出保护/守卫在执行层

⚠️ 只在 %TEMP% 临时目录写产物；会把 cwd 切到临时目录（worker 按 cwd 落盘），跑完恢复。
"""

import ast
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  — " + str(extra)) if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print("\n[!] 未捕获异常：%s: %s" % (t.__name__, v))
    print("PASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL) + 1))
    sys.exit(1)


sys.excepthook = _hook

CP_PATH = os.environ.get("CP_PATH") or os.path.join(ROOT, "canvas_panel.py")
CP_SRC = open(CP_PATH, encoding="utf-8").read()


def _run_part(fn):
    print()
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        check("%s 组抛异常" % fn.__name__.strip("_").upper(), False, repr(e))


def _wait_worker(panel, timeout_ms=8000):
    """等 worker 跑完；带兜底定时器（正常 ≤3s；变异致挂死时按超时退出，判据自然红）。

    兜底 8s 的原因：一次变异（如删 worker.start）影响**所有组**的运行
    （A/B/C/D 都走 _run_graph），若兜底过长，四组各挂一次就超时被记
    "挂起"而非"判据翻红"—— 扰动口径里挂起不算命中。
    """
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    panel._run_worker.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


# ---------------------------------------------------------------- A 后台跑图
def _a():
    print("A 组 · 后台线程跑图（不卡界面）")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841

    import agnes_bridge
    import canvas_panel as cp

    slow_done = []

    def slow_video_fn(node, asset_root, src_path, prompt):
        time.sleep(1.5)   # 模拟 Agnes 联网生视频
        slow_done.append(node.id)
        out = os.path.join(asset_root, "video", "%s_clip.mp4" % node.id)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"fake_mp4_probe")
        return out

    agnes_bridge.get_agnes_video_fn = lambda: slow_video_fn
    agnes_bridge.get_agnes_inpaint_fn = lambda: None

    work = tempfile.mkdtemp(prefix="canvas_async_a_")
    orig = os.getcwd()
    try:
        os.chdir(work)
        g = cp.build_demo()
        panel = cp.CanvasPanel(g)

        check("A0 点运行前 run_active=False", panel.scene.run_active is False)
        t0 = time.time()
        panel._run_graph()
        check("A1 ★ 点「运行」立即返回（<0.5s，非阻塞）", time.time() - t0 < 0.5,
              "%.2fs" % (time.time() - t0))
        check("A2 运行中 btn_run 禁用且文本=运行中…",
              (not panel.btn_run.isEnabled()) and panel.btn_run.text() == "运行中…",
              panel.btn_run.text())
        check("A3 运行中 btn_stop 可用", panel.btn_stop.isEnabled())
        check("A4 运行中 run_active=True", panel.scene.run_active is True)

        # 运行期间事件循环必须活着（QTimer 能触发 = Windows 不会判「未响应」）
        tick = []
        QTimer.singleShot(400, lambda: tick.append(1))
        # 运行中重复点运行：必须被拒（不二次起线程）
        panel._run_graph()
        texts_mid = [panel.detail.item(i).text() for i in range(panel.detail.count())]
        check("A5 ★ 运行中重复点运行被拒（提示已有一次）",
              any("已有一次运行" in t for t in texts_mid), str(texts_mid[-2:]))

        _wait_worker(panel)
        check("A6 ★ 运行期间事件循环活着（QTimer 触发过）", len(tick) > 0)
        check("A7 ★ 慢节点真执行了（vid 走了 slow fn）", "vid" in slow_done)
        files = []
        for dirpath, _dirs, fns in os.walk(os.path.join(work, "canvas_runtime")):
            files.extend(fns)
        check("A8 ★ 磁盘真落盘 img_source.png",
              any("img_source.png" in x for x in files), str(files))
        check("A9 ★ 磁盘真落盘 vid_clip.mp4（慢节点产物）",
              any("vid_clip.mp4" in x for x in files))
        texts = [panel.detail.item(i).text() for i in range(panel.detail.count())]
        check("A10 详情含「运行完成」", any("运行完成" in t for t in texts))
        check("A11 跑完按钮恢复（run 可用/文本=运行/stop 禁用）",
              panel.btn_run.isEnabled() and panel.btn_run.text() == "运行"
              and not panel.btn_stop.isEnabled())
        check("A12 跑完 run_active=False", panel.scene.run_active is False)
        check("A13 跑完 worker/token 引用已清",
              panel._run_worker is None and panel._run_token is None)
    finally:
        os.chdir(orig)


# ---------------------------------------------------------------- B 协作式停止
def _b():
    print("B 组 · 协作式停止（令牌取消）")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841

    import agnes_bridge
    import canvas_panel as cp

    def slow_no_out(node, asset_root, src_path, prompt):
        time.sleep(2.5)   # 慢且无产出（模拟视频生成中喊停）
        return None

    agnes_bridge.get_agnes_video_fn = lambda: slow_no_out

    work = tempfile.mkdtemp(prefix="canvas_async_b_")
    orig = os.getcwd()
    try:
        os.chdir(work)
        g = cp.build_demo()
        panel = cp.CanvasPanel(g)
        panel._run_graph()
        check("B0 运行已启动", panel._run_worker is not None
              and panel._run_worker.isRunning())

        token_state = []
        QTimer.singleShot(900, lambda: (
            panel._stop_run(),
            token_state.append(bool(panel._run_token
                                    and panel._run_token.is_cancelled))))
        _wait_worker(panel)

        check("B1 ★ 点停止后令牌确已取消", any(token_state), str(token_state))
        texts = [panel.detail.item(i).text() for i in range(panel.detail.count())]
        check("B2 详情有停止回执（已停止/停止请求）",
              any(("已停止" in t) or ("停止请求" in t) for t in texts))
        # 取消语义：vid 之后的节点绝不能冒充 completed
        after = [g.nodes[n].status for n in ("twin", "promo", "final")]
        check("B3 ★ 下游节点未冒充完成", all(s != "completed" for s in after), str(after))
        check("B4 停止后按钮全恢复",
              panel.btn_run.isEnabled() and not panel.btn_stop.isEnabled())
    finally:
        os.chdir(orig)


# ---------------------------------------------------------------- C 失败吸收
def _c():
    print("C 组 · 执行器异常 → 节点级 failed（不炸线程）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841

    import agnes_bridge
    import canvas_panel as cp

    def boom(node, asset_root, src_path, prompt):
        raise RuntimeError("判据探针: 生视频炸了")

    agnes_bridge.get_agnes_video_fn = lambda: boom

    work = tempfile.mkdtemp(prefix="canvas_async_c_")
    orig = os.getcwd()
    try:
        os.chdir(work)
        g = cp.build_demo()
        panel = cp.CanvasPanel(g)
        panel._run_graph()
        _wait_worker(panel)

        check("C1 ★ 执行器异常被吸收成节点级 failed（线程未崩）",
              g.nodes["vid"].status == "failed", g.nodes["vid"].status)
        check("C2 worker 正常收尾（引用已清）", panel._run_worker is None)
        check("C3 失败后按钮恢复", panel.btn_run.isEnabled())
        check("C4 上游 img 不受牵连（completed）",
              g.nodes["img"].status == "completed", g.nodes["img"].status)
    finally:
        os.chdir(orig)


# ---------------------------------------------------------------- D 运行期防编辑
def _d():
    print("D 组 · 运行期防编辑（数据竞争防线）")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841

    import agnes_bridge
    import canvas_panel as cp

    agnes_bridge.get_agnes_video_fn = lambda: (
        lambda node, asset_root, src_path, prompt: time.sleep(1.2) or None)

    work = tempfile.mkdtemp(prefix="canvas_async_d_")
    orig = os.getcwd()
    try:
        os.chdir(work)
        g = cp.build_demo()
        panel = cp.CanvasPanel(g)
        panel._run_graph()

        n0, de0 = len(g.nodes), len(g.data_edges)
        # ⚠️ D 组每条判据**操作前重取基线**：变异拆掉守卫后，前一条操作的副作用
        # 会污染共享基线（删1个+建1个=数量不变 → 假绿；D4 还会 KeyError 崩）。
        # 右键删节点（执行层入口）
        panel.scene._apply_menu_choice("del", "img", None)
        check("D1 ★ 运行中右键删节点被拒", len(g.nodes) == n0)
        # 右键建节点（执行层入口）。⚠️ 必须传合法 pos：传 None 的话守卫在=提前return 绿、
        # 守卫拆了=pos.x() 抛异常 → 判据以「D 组抛异常」方式红，[FAIL] D2 永不出现（L280）。
        from PySide6.QtCore import QPointF
        nb = len(g.nodes)
        panel.scene._apply_menu_choice("add", "gen_image", QPointF(0, 0))
        check("D2 ★ 运行中右键建节点被拒", len(g.nodes) == nb)
        # 删选中边按钮：先真选中一条**数据边**（否则循环空转 = 空断据，
        # 守卫拆了也绿；且顺序边删了动 order_edges，只断言 data_edges 会漏）。
        de, oe = len(g.data_edges), len(g.order_edges)
        edges = [it for it in panel.scene.items()
                 if isinstance(it, cp.CanvasEdgeItem) and it.spec.kind == "data"]
        if edges:
            edges[0].setSelected(True)
        panel._delete_selected()
        check("D3 ★ 运行中「删除选中连线」被拒",
              len(g.data_edges) == de and len(g.order_edges) == oe,
              "data %d->%d order %d->%d（选中数据边 %d 条）"
              % (de, len(g.data_edges), oe, len(g.order_edges), len(edges)))

        _wait_worker(panel)
        # 跑完后编辑能力恢复：验证「建节点」（不用删 img —— 变异路径下 img 可能已被删）
        na = len(g.nodes)
        panel.scene._apply_menu_choice("add", "gen_image", QPointF(0, 0))
        check("D4 ★ 跑完后建节点恢复可用", len(g.nodes) == na + 1,
              "%d -> %d" % (na, len(g.nodes)))
    finally:
        os.chdir(orig)


# ---------------------------------------------------------------- E 源码契约
def _e():
    print("E 组 · 源码契约（AST 限定函数体，见 L270）")
    tree = ast.parse(CP_SRC)

    # E1：_GraphRunWorker 必须是 QThread 子类（后台线程是结构性修复）
    cls = next((c for c in ast.walk(tree)
                if isinstance(c, ast.ClassDef) and c.name == "_GraphRunWorker"), None)
    bases = [getattr(b, "id", getattr(b, "attr", "")) for b in cls.bases] if cls else []
    check("E1 ★ _GraphRunWorker 继承 QThread", "QThread" in bases, str(bases))

    # E2：worker.run 内 reset_for_rerun 早于 run（防「忘了调」——重跑语义迁进线程）
    ok, detail = False, "未找到 worker.run"
    if cls is not None:
        run_fn = next((m for m in cls.body
                       if isinstance(m, ast.FunctionDef) and m.name == "run"), None)
        if run_fn is not None:
            rl, dl = [], []
            for n in ast.walk(run_fn):
                if isinstance(n, ast.Call):
                    nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                    if nm == "reset_for_rerun":
                        rl.append(n.lineno)
                    elif nm == "run":
                        dl.append(n.lineno)
            ok = bool(rl) and bool(dl) and min(rl) < min(dl)
            detail = "reset@%s run@%s" % (sorted(rl), sorted(dl))
    check("E2 ★ worker.run 内 reset_for_rerun 早于 run", ok, detail)

    # E3：_run_graph 走 worker（防退回 GUI 线程同步跑）
    seg = CP_SRC.split("def _run_graph")[1].split("def _on_run_succeeded")[0]
    check("E3 ★ _run_graph 构造并 start _GraphRunWorker",
          "_GraphRunWorker(" in seg and "worker.start()" in seg)

    # E4：_stop_run 真调 cancel（协作式取消，非静默）
    seg_stop = CP_SRC.split("def _stop_run")[1].split("def _shutdown_run")[0]
    check("E4 ★ _stop_run 调 token.cancel", ".cancel(" in seg_stop)

    # E5：退出保护（aboutToQuit → cancel + wait，防 QThread 带线程销毁）
    check("E5 退出保护（aboutToQuit 连 _shutdown_run + wait）",
          "aboutToQuit" in CP_SRC and "def _shutdown_run" in CP_SRC
          and "w.wait(" in CP_SRC.split("def _shutdown_run")[1])

    # E6：防编辑守卫在执行层（_apply_menu_choice），不在菜单层
    seg_menu = CP_SRC.split("def _apply_menu_choice")[1].split("def keyPressEvent")[0]
    check("E6 ★ _apply_menu_choice 内有 run_active 守卫", "run_active" in seg_menu)

    # E7：keyPressEvent 的 Delete 分支有守卫
    seg_key = CP_SRC.split("def keyPressEvent")[1].split("def ", 1)[0] \
        if "def keyPressEvent" in CP_SRC else ""
    seg_key = CP_SRC.split("def keyPressEvent")[1][:900]
    check("E7 keyPressEvent Delete 分支有 run_active 守卫",
          "run_active" in seg_key.split("super().keyPressEvent")[0])


if __name__ == "__main__":
    for _part in (_a, _b, _c, _d, _e):
        _run_part(_part)
    print("\nPASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
    if FAIL:
        print("FAIL 项:", " | ".join(FAIL[:6]))
        sys.exit(1)
    print("ALL GREEN")
