# -*- coding: utf-8 -*-
"""画布「运行后台线程化」判据扰动 —— tests/test_canvas_async_run.py 的 10 处防线逐个拆。

守的是 v4.211.10（用户实弹"点运行程序就卡死"的修复）：
  整图执行搬进 QThread（_GraphRunWorker）+ 协作式停止（CancellationToken）
  + 运行期防编辑守卫（run_active，防 GUI 线程与 worker 数据竞争）。

口径与 _perturb_canvas_ops.py 一致（严两处）：
  ① 必须目标判据本身翻红（rc!=0 一律算命中的旧口径会掩盖哑弹）；
  ② 子进程 timeout=120 —— 变异导致挂起记失败，不拖死整轮回归。

⚠️ 只变异 canvas_panel.py（LF 文件，text-mode 读写安全）；
   还原由护栏按字节快照写回，收尾逐字节比对。

独立运行：python _perturb_canvas_async_run.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
TEST = os.path.join(ROOT, "tests", "test_canvas_async_run.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST_TIMEOUT = 120

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

SNAP = _guard.arm()

CP = os.path.join(ROOT, "canvas_panel.py")

# (name, 必须全红的判据前缀列表, 被测文件, old 锚点, new 替换)
MUTATIONS = [
    # ---- A 后台跑图 ----
    # PA1：worker 不 start → 慢节点从未执行、磁盘零产物 → A7/A8 红。
    #      worker 未启动则 finished 不发 → 判据 _wait_worker 走 30s 兜底退出（不挂死）。
    ("PA1_no_start", ["A7", "A8"], CP,
     "        worker.start()",
     "        pass  # 扰动：不启动线程"),

    # PA2：_run_graph 不置 run_active=True → 守卫全失效 → A4/D1 红。
    ("PA2_no_flag_set", ["A4", "D1"], CP,
     "        self.scene.run_active = True",
     "        pass  # 扰动：不置运行标志"),

    # PA9：重复运行守卫拆掉 → 二次点运行再起一个 worker → A5 红。
    ("PA9_no_dup_guard", ["A5"], CP,
     "        if self._run_worker is not None and self._run_worker.isRunning():\n"
     "            self.detail.addItem(\"已有一次运行在进行中（可点「停止」）\")\n"
     "            return",
     "        if False:  # 扰动：允许并发跑\n"
     "            return"),

    # PA5：成功信号不发 → 详情永远没有「运行完成」→ A10 红。
    ("PA5_no_success_emit", ["A10"], CP,
     "            self.succeeded.emit(lines)",
     "            pass  # 扰动：成功不通知"),

    # PA6：收尾不复位 run_active → 下次编辑被永久拒绝 → A12 红。
    ("PA6_no_flag_reset", ["A12"], CP,
     "        self.scene.run_active = False",
     "        pass  # 扰动：不复位运行标志"),

    # ---- B 协作式停止 ----
    # PA4：_stop_run 不调 cancel → 令牌从未取消 → B1 红。
    ("PA4_no_cancel", ["B1"], CP,
     "        self._run_token.cancel(\"用户点了停止\", stage=\"user_stop\")",
     "        pass  # 扰动：不取消令牌"),

    # PA7：根本不建令牌 → 停止无从谈起 → B1/B2 红。
    ("PA7_no_token", ["B1", "B2"], CP,
     "        self._run_token = CancellationToken(name=\"canvas_run\")",
     "        self._run_token = None  # 扰动：不建令牌"),

    # ---- D 运行期防编辑 ----
    # PA3：_apply_menu_choice 守卫拆掉（执行层）→ 右键建删节点穿透 → D1/D2 红。
    ("PA3_menu_guard_off", ["D1", "D2"], CP,
     "        if self.run_active:\n"
     "            # 运行中拒绝建/删节点（数据竞争）。守卫放执行层而非菜单层：\n"
     "            # 任何调用路径（菜单/快捷键/判据直调）都过这里。\n"
     "            return\n"
     "        if kind == \"add\":",
     "        if False:  # 扰动：守卫失效\n"
     "            return\n"
     "        if kind == \"add\":"),

    # PA8：_delete_selected 行为层兜底拆掉 → 删边穿透 → D3 红。
    ("PA8_del_guard_off", ["D3"], CP,
     "        if self.scene.run_active:\n"
     "            # 运行中拒绝删边（按钮已禁用，这里是行为层兜底，防其他调用路径）\n"
     "            return",
     "        if False:  # 扰动：兜底失效\n"
     "            return"),

    # ---- E 源码契约 ----
    # PA10：keyPressEvent 的 Delete 分支守卫拆掉 → E7 红（契约：守卫必须在）。
    ("PA10_key_guard_off", ["E7"], CP,
     "            if self.run_active:\n"
     "                # 运行中拒绝删节点（数据竞争，同 contextMenuEvent 守卫）\n"
     "                ev.accept()\n"
     "                return",
     "            if False:  # 扰动：Delete 守卫失效\n"
     "                return"),
]


def run_test():
    env = dict(os.environ)
    env["CP_PATH"] = CP
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        p = subprocess.run([PY, TEST], capture_output=True, text=True, env=env,
                           cwd=ROOT, timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        return 124, out + "\n[扰动] 子进程超时 %ds —— 变异导致挂起而非判据翻红" % TEST_TIMEOUT
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def target_failed(targets, rc, out):
    """目标判据**每一条**都得真翻红（收紧口径见模块 docstring）。"""
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

    # 反向基线：不变异必须 ALL GREEN
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

            print("  [%s] %-22s 目标 %s %s%s"
                  % ("OK " if hit else "BAD", name, "/".join(targets),
                     "✓" if hit else "✗", ("  — " + why) if why else ""))
            results.append((name, hit))
            ok_all = ok_all and hit

            with open(path, "rb") as f:
                if f.read() != SNAP[os.path.abspath(path)]:
                    print("  [BAD] %s 还原后字节不一致！" % name)
                    ok_all = False
    finally:
        restore(CP)

    with open(CP, "rb") as f:
        if f.read() != SNAP[os.path.abspath(CP)]:
            print("  [BAD] 收尾还原失败: canvas_panel.py")
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
