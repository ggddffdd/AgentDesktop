# -*- coding: utf-8 -*-
"""第 5/6 步增强 · 判据扰动（agnes_bridge / executors / canvas_graph）。

机制：对每个被测源文件做单点变异（覆盖写回）→ 用 CX_CHECK 只跑目标判据 →
断言该判据由绿变红（returncode!=0）→ 还原。末尾跑一次全量判据确认不改动时
全绿（反向基线）。

本脚本针对 test_canvas_agnes.py 的 B 行为判据做非空转验证：
  B1 inpaint_fn 注入后真被调用 + 编辑图落盘
  B2 gen_video 调用 video_fn + clip 登记 + 收到上游 image
  B3 promo_fx 调用 motion_fn + video 路径 = motion 返回
  B4 促销动效预览 GIF 已生成（画布级）
  B5 _upstream_asset_paths 递归收集上游
  B6 无帧退化 GIF 仍生成

命中点必须真实命中红名，否则视为扰动失效（ERR 报错）。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST = os.path.join("tests", "test_canvas_agnes.py")

EXEC = os.path.join(ROOT, "executors.py")
GRAPH = os.path.join(ROOT, "canvas_graph.py")
BRIDGE = os.path.join(ROOT, "agnes_bridge.py")

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
# （防进程被强杀后留下半截变异体，让后续判据「基线已红」）
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# 备份原始内容，结束时确保还原
_BACKUP = {EXEC: open(EXEC, encoding="utf-8").read(),
           GRAPH: open(GRAPH, encoding="utf-8").read(),
           BRIDGE: open(BRIDGE, encoding="utf-8").read()}


def _env(check):
    e = dict(os.environ)
    e["CX_CHECK"] = check
    return e


def _run(check=None):
    env = _env(check) if check else dict(os.environ)
    return subprocess.run([PY, TEST], cwd=ROOT, env=env,
                          capture_output=True, text=True)


FAILS = []


def _pg(desc, check, filepath, old, new):
    # 先确认基线（未变异）该判据是绿的
    base = _run(check)
    if base.returncode != 0:
        print("ERR 基线已红: %s [%s]" % (desc, check))
        FAILS.append(desc)
        return
    src = open(filepath).read()
    if old not in src:
        print("ERR 锚点未命中: %s" % desc)
        FAILS.append(desc)
        return
    open(filepath, "w").write(src.replace(old, new, 1))
    mut = _run(check)
    if mut.returncode == 0:
        print("ERR 未变红: %s -> %s" % (desc, check))
        FAILS.append(desc)
    else:
        print("PG OK: %s -> %s 变红" % (desc, check))
    # 还原
    open(filepath, "w").write(src)


def main():
    # PG1 use_real_executors 不把 inpaint_fn 透传给 apply_real_executors → B1 红
    _pg("use_real_executors 丢弃 inpaint_fn", "B1", GRAPH,
        "return apply_real_executors(self, asset_root, inpaint_fn, video_fn, motion_fn)",
        "return apply_real_executors(self, asset_root, None, video_fn, motion_fn)")

    # PG2 gen_video_executor 强制走 None 分支（即使注入 video_fn 也不调用）→ B2 红
    _pg("gen_video 强制 None 分支不调 video_fn", "B2", EXEC,
        "        if video_fn is None:",
        "        if True:  # 扰动：强制 None 分支")

    # PG3 promo_fx 调用 motion_fn 但丢弃其返回值 → B3b 路径不匹配 红
    _pg("promo_fx 丢弃 motion_fn 返回值", "B3", EXEC,
        "        res = motion_fn(img_paths, out_path, params)",
        "        motion_fn(img_paths, out_path, params); res = \"Z:/tampered.gif\"  # 扰动：绕过真实返回值")

    # PG4 make_promo_motion_preview 不真生成预览文件 → B4 红
    _pg("预览生成器返回不存在路径", "B4", BRIDGE,
        "    return pil_promo_motion(paths, out_gif, params)",
        "    return out_gif + \".missing\"  # 扰动：不真生成预览")

    # PG5 _upstream_asset_paths 不遍历边 → B5 红
    _pg("_upstream_asset_paths 不遍历边", "B5", EXEC,
        "    for e in getattr(graph, \"data_edges\", []) or []:",
        "    for e in []:  # 扰动：不遍历边")

    # PG6 pil_promo_motion 不落盘（退化帧也写不出）→ B6 红
    _pg("pil_promo_motion 不落盘", "B6", BRIDGE,
        "    frames[0].save(out_path, save_all=True, append_images=frames[1:],\n                   duration=int(1000 / max(1, fps)), loop=0)",
        "    pass  # 扰动：不落盘")

    # 反向基线：所有文件已还原，全量判据应全绿
    final = _run()
    if final.returncode != 0:
        print("ERR 反向基线非绿（可能未完全还原）")
        FAILS.append("反向基线")
    else:
        print("反向基线 ALL GREEN")


if __name__ == "__main__":
    try:
        main()
    finally:
        # 无论如何还原三份原始文件
        for fp, content in _BACKUP.items():
            open(fp, "w", encoding="utf-8").write(content)
    if FAILS:
        print("\n扰动失败项: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("\n全部扰动命中 + 反向基线绿")
    sys.exit(0)
