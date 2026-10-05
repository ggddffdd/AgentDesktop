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

# 备份原始内容，结束时确保还原。
# 用**字节**快照而不是文本：文本模式读写会把 CRLF 归一/再转换（本仓生产源码全 CRLF、
# tests 全 LF），一旦方向搞反就是「整份假 diff」（L215 同类坑）。
_BACKUP = {p: open(p, "rb").read() for p in (EXEC, GRAPH, BRIDGE)}


def _env(check):
    e = dict(os.environ)
    e["CX_CHECK"] = check
    return e


def _run(check=None):
    env = _env(check) if check else dict(os.environ)
    return subprocess.run([PY, TEST], cwd=ROOT, env=env,
                          capture_output=True, text=True)


FAILS = []
# 检查点计数（每个 _pg case + 末尾反向基线各 +1）—— 供统一输出契约报 PASS 总数。
# 不硬编码 case 数：以后增删 case 忘了改常量就报不出准数（这正是跨版本哑弹的成因之一）。
CHECKS = [0]


def _read_src(fp):
    """读被测源码：返回 (按 \\n 归一的文本, 该文件自身的行尾)。

    本仓生产源码是 CRLF、tests 是 LF，所以不能既要求「多行锚点用 \\n 写得出来」，
    又要求「写回后行尾不变」——必须显式记住行尾再还原，否则任选一边都会踩坑
    （锚点匹配不上，或把 CRLF 改成 LF 造出整份假 diff）。
    """
    b = open(fp, "rb").read()
    crlf = b.count(b"\r\n")
    nl = "\r\n" if crlf and crlf >= b.count(b"\n") - crlf else "\n"
    return b.decode("utf-8").replace("\r\n", "\n"), nl


def _write_src(fp, text, nl):
    open(fp, "wb").write(text.replace("\n", nl).encode("utf-8"))


def _pg(desc, check, filepath, old, new):
    CHECKS[0] += 1
    # 先确认基线（未变异）该判据是绿的
    base = _run(check)
    if base.returncode != 0:
        print("ERR 基线已红: %s [%s]" % (desc, check))
        FAILS.append(desc)
        return
    src, nl = _read_src(filepath)
    if old not in src:
        print("ERR 锚点未命中: %s" % desc)
        FAILS.append(desc)
        return
    _write_src(filepath, src.replace(old, new, 1), nl)
    mut = _run(check)
    if mut.returncode == 0:
        print("ERR 未变红: %s -> %s" % (desc, check))
        FAILS.append(desc)
    else:
        print("PG OK: %s -> %s 变红" % (desc, check))
    # 还原（同编码同字节，行尾不变）
    _write_src(filepath, src, nl)


def main():
    # PG1 use_real_executors 不把 inpaint_fn 透传给 apply_real_executors → B1 红
    _pg("use_real_executors 丢弃 inpaint_fn", "B1", GRAPH,
        "return apply_real_executors(self, asset_root, inpaint_fn, video_fn, motion_fn, text2img_fn)",
        "return apply_real_executors(self, asset_root, None, video_fn, motion_fn, text2img_fn)")

    # PG2 gen_video_executor 强制走 None 分支（即使注入 video_fn 也不调用）→ B2 红
    _pg("gen_video 强制 None 分支不调 video_fn", "B2", EXEC,
        "        if video_fn is None:",
        "        if True:  # 扰动：强制 None 分支")

    # PG3 promo_fx 调用 motion_fn 但丢弃其返回值 → B3b 路径不匹配 红
    _pg("promo_fx 丢弃 motion_fn 返回值", "B3", EXEC,
        "        res = motion_fn(img_paths, out_path, params)",
        "        motion_fn(img_paths, out_path, params); res = \"Z:/tampered.gif\"  # 扰动：绕过真实返回值")

    # PG4 make_promo_motion_preview 不真生成预览文件 → B4 红
    # （2026-10-04：锚点随「回填 stats」改动而更新 —— 收集与生成之间多了 info 中转）
    _pg("预览生成器返回不存在路径", "B4", BRIDGE,
        '    return pil_promo_motion(info["paths"], out_gif, params)',
        '    return out_gif + ".missing"  # 扰动：不真生成预览')

    # PG5 _upstream_asset_paths 不遍历边 → B5 红
    _pg("_upstream_asset_paths 不遍历边", "B5", EXEC,
        "    for e in getattr(graph, \"data_edges\", []) or []:",
        "    for e in []:  # 扰动：不遍历边")

    # PG6 pil_promo_motion 不落盘（退化帧也写不出）→ B6 红
    _pg("pil_promo_motion 不落盘", "B6", BRIDGE,
        "    frames[0].save(out_path, save_all=True, append_images=frames[1:],\n                   duration=int(1000 / max(1, fps)), loop=0)",
        "    pass  # 扰动：不落盘")

    # PG7 把明文 key 硬编码回 agnes_bridge → A8 红（2026-10-03 泄露事故的防回潮守卫）
    _pg("明文 key 回潮（重新硬编码）", "A8", BRIDGE,
        '_DEFAULT_BASE = "https://api.agnes-ai.cn/v1"',
        '_DEFAULT_KEY = "sk-fake0123456789abcdefghijklmnopqrstuv"  # 扰动：明文回潮\n'
        '_DEFAULT_BASE = "https://api.agnes-ai.cn/v1"')

    # PG8 _default_cred 忽略 env（优先级写反）→ B7c 红
    # 注：必须打成**行为**变异（B 组真跑解析），打在注释/字符串上的变异只会造成假绿。
    _pg("凭据解析忽略 env（优先级写反）", "B7", BRIDGE,
        '    key = (os.environ.get("AGNES_API_KEY") or "").strip()',
        '    key = ""  # 扰动：忽略 env')

    # ------------------------------------------------------------------
    # PC1~PC3（2026-10-04）：促销动效帧的**消费侧四态过滤**非空转验证。
    # 背景：旧实现只看 `kind == "image"` + `os.path.exists`，占位物被收进帧列表后
    # 在 PIL 里静默丢掉（界面上一排 completed，出来却是纯色渐变且无任何提示）。
    # ------------------------------------------------------------------
    # PC1 collect_promo_frames 不看四态（退回「有路径就当帧」）→ B8/B9 红
    _pg("帧收集不过四态（占位物也当帧）", "B8", BRIDGE,
        "            validity, _why = cg.assess_asset(a, asset_root)\n"
        "            if validity == cg.ASSET_REAL:\n"
        "                paths.append(a.path)\n"
        "            else:\n"
        "                skipped[validity] = skipped.get(validity, 0) + 1\n"
        '                detail.append({"node": nid, "port": p, "why": validity})',
        "            paths.append(a.path)  # 扰动：不过四态，一律当帧")

    # PC2 promo_fx_executor 消费侧不做四态判定 → B10 红（占位上游也当帧）
    _pg("promo 执行器不做消费侧四态过滤", "B10", EXEC,
        '            ref = f.get("ref")\n'
        "            if ref is not None:\n"
        "                validity, _why = assess_asset(ref, asset_root)\n"
        "                if validity != ASSET_REAL:\n"
        "                    skipped[validity] = skipped.get(validity, 0) + 1\n"
        "                    continue",
        "            pass  # 扰动：消费侧不做四态过滤（占位上游也当帧）")

    # PC3 递归上游不反查 ref → B10 红。
    # 这条专治本轮踩到的坑：promo 的 image 帧**全走递归上游通道**（promo←vid←img），
    # 若递归项拿不到 ref，PC2 那段过滤就是死代码 —— 加了防护却毫无作用，
    # 而且**看着像做过了**。必须有一条变异钉住它。
    _pg("递归上游不反查 ref（过滤变死代码）", "B10", EXEC,
        "            if isinstance(a, AssetRef) and a.path == path:\n"
        "                return a",
        "            pass  # 扰动：不反查（递归上游永远拿不到 ref）")

    # 反向基线：所有文件已还原，全量判据应全绿
    CHECKS[0] += 1
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
        # 无论如何还原三份原始文件（字节级，行尾/编码都不受影响）
        for fp, blob in _BACKUP.items():
            open(fp, "wb").write(blob)
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d" % (CHECKS[0] - len(FAILS), len(FAILS)))
    if FAILS:
        print("\n扰动失败项: %s" % ", ".join(FAILS))
        sys.exit(1)
    print("\n全部扰动命中 + 反向基线绿")
    sys.exit(0)
