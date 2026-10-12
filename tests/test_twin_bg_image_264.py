# v4.263.0（P2）：数字人「自定义背景图」换背景回归。
#
# 根因/方案（来自对话）：
#   旧「关掉保持背景 → 模型凭空重诠释背景」不可控、易漂移（大哥实测「背景变一小会」）。
#   P2 改为：选一张背景图 → 先用 Agnes 图生图把本人融进背景合成首帧图，
#   再 keyframe 首尾双帧强锁这张合成图 → 换背景可控 + 全程不漂移。
#
# 本套件全程打桩：不联网、不跑真 ffmpeg、不写真实用户目录（有沙箱产品目录兜底）。
# 关键行为判据（防接线被改回去）：
#   · bg 模式必须调 tool_image_gen 且传 image=[本人参考图, 背景图]（图生图契约）；
#   · 合成成功 → 首帧改用合成图（背景被换且被锁）；
#   · 合成失败 → 回退原参考图（不崩溃、成片仍产出，绝不哑弹）；
#   · 指纹纳入背景图（换背景图必须重生成，不能被旧缓存复用）；
#   · 源码契约：_work 含 tool_image_gen 的 image= 调用、_twin_fingerprint 含 "bg": 字段。
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import digital_twin_panel as dt  # noqa: E402

_PASS = []
_FAIL = []


def check(name, cond, extra=""):
    if cond:
        _PASS.append(name)
        print(f"✅ {name} {extra}")
    else:
        _FAIL.append(name)
        print(f"❌ {name} {extra}")


_REAL_PRODUCTS = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "产物")


def _run(bg_image=None, img2img_fail=False, n=2, ref_mode=True, workers=1, **kw):
    """同步跑一次 TwinGenThread._work（全打桩，支持 bg_image）。

    返回 (done 载荷, logs, thread, app_dir, ff_log, img2img_log, review_bg_log)
    """
    app_dir = tempfile.mkdtemp(prefix="xc_twin_bg_sbx_")
    seg_texts = [f"P{k + 1}台词内容测试" for k in range(n)]
    segs = [(t, 5) for t in seg_texts]
    ff_log, img2img_log, review_bg_log = [], [], []
    logs, seen = [], {"done": None}

    def fake_gen(cfg, ad, prompt, sec, *a, **k):
        ff_log.append((k.get("first_frame"), k.get("last_frame")))
        p = os.path.join(ad, "products", f"seg_{len(ff_log)}.mp4")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"\x00" * 32)
        return (os.path.relpath(p, ad).replace("\\", "/"),
                "video", os.path.basename(p))

    def fake_img2img(cfg, ad, prompt, size=None, progress=None, image=None):
        img2img_log.append({"image": image, "size": size})
        if img2img_fail:
            return "ERR:图生图失败（队列繁忙）"
        p = os.path.join(ad, "products", "图片", "composite.png")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"\x00" * 32)
        return (os.path.relpath(p, ad).replace("\\", "/"), "image", "composite.png")

    def fake_concat(paths, out, ffmpeg=None, log=None):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"\x00" * 64)
        return True

    def fake_review(cfg, portrait, probe, log=None, keep_bg=True):
        review_bg_log.append(keep_bg)
        return (True, "ok")

    names = ("_ffmpeg", "fit_image_to_aspect", "extract_last_frame",
             "concat_videos", "burn_ai_mark", "extract_probe_frame")
    saved = {nm: getattr(dt, nm) for nm in names}
    saved["gen"] = dt.tools_mod.tool_video_gen
    saved["img"] = dt.tools_mod.tool_image_gen
    saved["products"] = getattr(dt.tools_mod, "PRODUCTS_DIR", None)
    saved["review"] = dt.vq.review_identity

    dt._ffmpeg = lambda: "ffmpeg"
    dt.fit_image_to_aspect = lambda *a, **k: False   # 简化：合成图直接用、不裁剪
    dt.extract_last_frame = lambda *a, **k: True
    dt.concat_videos = fake_concat
    dt.burn_ai_mark = lambda *a, **k: False
    dt.extract_probe_frame = lambda *a, **k: True
    dt.tools_mod.tool_video_gen = fake_gen
    dt.tools_mod.tool_image_gen = fake_img2img
    dt.tools_mod.PRODUCTS_DIR = "products"
    dt.vq.review_identity = fake_review

    # 背景图需是真实存在的文件（_work 里 os.path.isfile 校验）
    bg_arg = None
    if bg_image is not None:
        bg_arg = os.path.join(app_dir, "bg.png")
        with open(bg_arg, "wb") as f:
            f.write(b"\x00" * 32)

    th = None
    try:
        portrait = os.path.join(app_dir, "portrait.png")
        with open(portrait, "wb") as f:
            f.write(b"\x00" * 32)
        th = dt.TwinGenThread(
            {}, app_dir, portrait, segs, "场景", True, "768x1152",
            dual_frame=True, ai_mark=False, qc=kw.get("qc", True),
            max_gen_retry=0, gen_retry_delay=0.0, max_qc_retry=0,
            resume=False, job_dir=None, ref_mode=ref_mode, workers=workers,
            bg_image=bg_arg)
        th.log.connect(lambda m: logs.append(str(m)))
        th.done.connect(lambda r: seen.__setitem__("done", r))
        try:
            th._work()
        except Exception as _e:
            # 模拟 TwinGenThread.run() 的兜底：异常也走 done.emit（绝不静默崩）
            seen["done"] = f"异常：{_e}"
    finally:
        for nm in names:
            setattr(dt, nm, saved[nm])
        dt.tools_mod.tool_video_gen = saved["gen"]
        dt.tools_mod.tool_image_gen = saved["img"]
        if saved["products"] is None:
            try:
                del dt.tools_mod.PRODUCTS_DIR
            except AttributeError:
                pass
        else:
            dt.tools_mod.PRODUCTS_DIR = saved["products"]
        dt.vq.review_identity = saved["review"]
    return seen["done"], logs, th, app_dir, ff_log, img2img_log, review_bg_log


def _fingerprint_bg_variant(bg_path):
    return dt._twin_fingerprint("稿", os.path.join(tempfile.mkdtemp(), "p.jpg"),
                                 "场景", "768x1152", 8, True, True, True,
                                 bg_image=bg_path)


print("=" * 60)
print("U) 数字人自定义背景图（P2 · 图生图换背景）")
print("=" * 60)

_real_before = sorted(os.listdir(_REAL_PRODUCTS)) if os.path.isdir(_REAL_PRODUCTS) else []

# U1 指纹纳入背景图：换背景图 → 指纹变；无背景图 → 与原指纹不同（g 已抬到 3）
_b1 = _fingerprint_bg_variant(None)
_bp = os.path.join(tempfile.mkdtemp(), "bg1.png")
with open(_bp, "wb") as f:
    f.write(b"x" * 16)
_b2 = _fingerprint_bg_variant(_bp)
check("U1a 有/无背景图 → 指纹不同（指纹纳入 bg）", _b1 != _b2)
_bp2 = os.path.join(tempfile.mkdtemp(), "bg2.png")
with open(_bp2, "wb") as f:
    f.write(b"y" * 32)   # 内容与 _bp 不同（大小也不同）→ 指纹必变
_b3 = _fingerprint_bg_variant(_bp2)
check("U1b 换背景图（内容不同）→ 指纹变", _b2 != _b3)

# U2 + U3：bg 模式合成成功 → 调 tool_image_gen 且 image=[本人, 背景]；首帧用合成图
_d, _l, _th, _ad, _ff, _img, _rbg = _run(bg_image=True, n=2, ref_mode=True, workers=1)
check("U2 选了背景图 → 调了 tool_image_gen（图生图）", len(_img) == 1, f"calls={len(_img)}")
_img_arg = (_img[0]["image"] if _img else None)
check("U3 image 含两张参考图（本人 + 背景）",
      isinstance(_img_arg, (list, tuple)) and len(_img_arg) == 2,
      str(_img_arg))
# 合成图落盘到 products/图片/composite.png，fit 桩=False → portrait_uri=该路径
_comp_path = os.path.join(_ad, "products", "图片", "composite.png")
check("U4 合成成功 → 首帧改用合成图（背景被换且被锁）",
      _ff and os.path.normpath(_ff[0][0]) == os.path.normpath(_comp_path),
      str(_ff[:1]))
check("U5 成片仍产出（合成成功路径不哑弹）",
      isinstance(_d, str) and os.path.isfile(_d), str(_d))
check("U6 bg 模式质检 keep_bg 强制 True（锁合成图背景）",
      all(_rbg), str(_rbg))

# U7 + U8：合成失败 → 回退原参考图（首帧==原 portrait），成片仍产出
_d2, _l2, _th2, _ad2, _ff2, _img2, _rbg2 = _run(bg_image=True, img2img_fail=True,
                                                n=2, ref_mode=True, workers=1)
_portrait = os.path.join(_ad2, "portrait.png")
check("U7 合成失败 → 回退用原参考图作首帧（不崩溃）",
      _ff2 and _ff2[0][0] == _portrait, str(_ff2[:1]))
check("U8 合成失败 → 成片仍产出（绝不留哑弹）",
      isinstance(_d2, str) and os.path.isfile(_d2), str(_d2))
check("U9 合成失败 → 日志含回退告警",
      any("回退" in m or "合成失败" in m for m in _l2), str(_l2[:3]))

# U10 无背景图 → 不调 tool_image_gen（走原 keyframe 强锁，P1 行为不变）
_d3, _l3, _th3, _ad3, _ff3, _img3, _rbg3 = _run(bg_image=None, n=2, ref_mode=True,
                                                workers=1)
check("U10 无背景图 → 不调 tool_image_gen（沿用 P1 强锁）",
      len(_img3) == 0, f"calls={len(_img3)}")
check("U11 无背景图 → 首帧即原参考图（P1 行为一致）",
      _ff3 and os.path.normpath(_ff3[0][0]) == os.path.normpath(os.path.join(_ad3, "portrait.png")),
      str(_ff3[:1]))

# U12 + U13 源码契约：接线不能被改回去
import inspect as _ins
_src = _ins.getsource(dt.TwinGenThread._work)
# 检查 _work 内存在对 tool_image_gen 的 image= 调用（图生图接线）
_uses_img2img = ("tool_image_gen" in _src) and ("image=" in _src)
check("U12 _work 含 tool_image_gen 的 image= 调用（图生图接线存在）", _uses_img2img)
_fpsrc = _ins.getsource(dt._twin_fingerprint).replace(" ", "")
check("U13 _twin_fingerprint 含背景图字段 \"bg\":", '"bg":' in _fpsrc)

# U14 真实产品目录未被测试触碰（前后快照一致）
_real_after = sorted(os.listdir(_REAL_PRODUCTS)) if os.path.isdir(_REAL_PRODUCTS) else []
check("U14 真实产物目录未被测试写入（前后一致）",
      _real_after == _real_before, str(_real_after[:3]))


print()
print(f"PASS={len(_PASS)} FAIL={len(_FAIL)}")
if _FAIL:
    print("失败项：", _FAIL)
    sys.exit(1)
sys.exit(0)
