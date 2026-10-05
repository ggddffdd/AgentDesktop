# -*- coding: utf-8 -*-
"""画布 gen_image 真文生图（v4.211.11）判据扰动 —— tests/test_canvas_text2img.py 的 8 处防线逐个拆。

守的是：Agnes 纯文生图注入链（UI → worker → use_real_executors → executor）
+ 兜底/诚实失败语义 + 上游 prompt 数据边解析 + payload 防退化成改图。

口径与 _perturb_canvas_async_run.py 一致（严两处）：
  ① 必须目标判据本身翻红（rc!=0 一律算命中的旧口径会掩盖哑弹）；
  ② 子进程 timeout=120 —— 变异导致挂起记失败，不拖死整轮回归。

变异跨 4 文件（canvas_panel / executors / agnes_bridge / canvas_graph），
还原由护栏按字节快照写回，收尾逐字节比对。判据全程 mock，零网络。

独立运行：python _perturb_canvas_text2img.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
TEST = os.path.join(ROOT, "tests", "test_canvas_text2img.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST_TIMEOUT = 120

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

SNAP = _guard.arm()

CP = os.path.join(ROOT, "canvas_panel.py")
EX = os.path.join(ROOT, "executors.py")
AB = os.path.join(ROOT, "agnes_bridge.py")
CGF = os.path.join(ROOT, "canvas_graph.py")

# (name, 必须全红的判据前缀列表, 被测文件, old 锚点, new 替换)
MUTATIONS = [
    # ---- A 注入生效 ----
    # PT1：执行器分支整体关死 → fn 永不被调、产物退回 PIL → A1/A3 红。
    ("PT1_branch_off", ["A1", "A3"], EX,
     "        if text2img_fn is not None and prompt:",
     "        if False:  # 扰动：真生图分支关死"),
    # PT5：上游 prompt 解析删掉 → 示例图形态（prompt 在 src）断流 → A5 红。
    ("PT5_no_upstream", ["A5"], EX,
     "        if graph is not None:\n"
     "            for e in graph.data_edges:\n"
     "                if e.to_node == node.id and e.to_port == \"prompt\":\n"
     "                    up = graph.nodes.get(e.from_node)\n"
     "                    if up is not None:\n"
     "                        p = ((up.config or {}).get(\"prompt\") or \"\").strip()\n"
     "                        if p:\n"
     "                            return p\n"
     "        return \"\"",
     "        return \"\"  # 扰动：上游 prompt 解析被删"),
    # ---- B 兜底语义 ----
    # PT6：兜底改抛错 → 未注入场景直接 failed 而非出占位图 → B1 红。
    ("PT6_no_fallback", ["B1"], EX,
     "        else:\n"
     "            # 未注入（无 key / 无 prompt）→ 本地 PIL 渐变占位（离线兜底）。\n"
     "            _make_source_image(node, src_path)",
     "        else:\n"
     "            raise RuntimeError(\"扰动：兜底被删\")"),
    # ---- C/D 源码契约 ----
    # PT2：payload 塞回 image 键 → 文生图退化成图生图改图 → D2 红。
    ("PT2_payload_image", ["D2"], AB,
     "            \"extra_body\": {\"response_format\": \"url\"},",
     "            \"extra_body\": {\"image\": [\"data:x\"], \"response_format\": \"url\"},"),
    # PT3：apply_real_executors 不透传 → 注入在装配层断链 → E1 红。
    ("PT3_apply_drop", ["E1"], EX,
     "        node.executor = build_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn, text2img_fn)",
     "        node.executor = build_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn)"),
    # ---- E UI 全链路 ----
    # PT4：worker.run 装配时不带 text2img_fn → 注入在 worker 层断链 → E1 红。
    ("PT4_worker_drop", ["E1"], CP,
     "            self.graph.use_real_executors(\n"
     "                self.asset_root, inpaint_fn=self.inpaint_fn,\n"
     "                video_fn=self.video_fn, text2img_fn=self.text2img_fn)",
     "            self.graph.use_real_executors(\n"
     "                self.asset_root, inpaint_fn=self.inpaint_fn,\n"
     "                video_fn=self.video_fn)"),
    # PT7：_run_graph 不再 import/注入 text2img → UI 层断链 → E1 红。
    ("PT7_ui_drop", ["E1"], CP,
     "            from agnes_bridge import (get_agnes_inpaint_fn, get_agnes_text2img_fn,\n"
     "                                      get_agnes_video_fn)",
     "            from agnes_bridge import get_agnes_inpaint_fn, get_agnes_video_fn"),
    # PT8：示例图 src 默认 prompt 删掉 → demo 图无 prompt → E1/E2 红
    #（E1：fn 不被调；E2：产物退回 480×270 占位）。
    ("PT8_demo_no_prompt", ["E1", "E2"], CGF,
     "                     outputs={\"prompt\": Port(\"prompt\", \"prompt\")},\n"
     "                     config={\"prompt\": \"示例：雪山下的松树林，清晨薄雾，写实摄影风格\"})",
     "                     outputs={\"prompt\": Port(\"prompt\", \"prompt\")})"),
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
    base_ok = (rc == 0) and ("PASS=24 FAIL=0" in out)
    print("反向基线（无变异，全跑）: %s" % ("OK 24/0" if base_ok else "FAIL"))
    if not base_ok:
        print(out[-3000:])
    ok_all = ok_all and base_ok

    results = []
    touched = [CP, EX, AB, CGF]
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

            print("  [%s] %-20s 目标 %s %s%s"
                  % ("OK " if hit else "BAD", name, "/".join(targets),
                     "✓" if hit else "✗", ("  — " + why) if why else ""))
            results.append((name, hit))
            ok_all = ok_all and hit

            with open(path, "rb") as f:
                if f.read() != SNAP[os.path.abspath(path)]:
                    print("  [BAD] %s 还原后字节不一致！" % name)
                    ok_all = False
    finally:
        for p in touched:
            restore(p)

    for p in touched:
        with open(p, "rb") as f:
            if f.read() != SNAP[os.path.abspath(p)]:
                print("  [BAD] 收尾还原失败: %s" % p)
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
