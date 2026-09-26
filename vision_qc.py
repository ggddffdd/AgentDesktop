"""视觉质检（VLM QC）公共模块 —— 导演台与数字人分身共用。

抽出来独立成模块的原因：
  - 质检本质是「图片 + 问题 -> 结构化判定」，与谁调用无关；
  - 数字人面板不该为了复用一个函数就去依赖整个 video_pipeline（那会拉进 tools / matplotlib）；
  - 独立模块不依赖 PySide6 / tools，离线单测可以直接 import。

设计铁律：**质检不可用时一律放行**。VLM 是增强手段，绝不能因为没配 key、
接口抖动或超时就把整条生成流程卡死。

模型：DeepSeek 视觉模型（config 里 profile 名为 'DeepSeek 官方'，
model 为 deepseek-flash，走用户已付费订阅通道）。

v4.168.0（审查 #6）三处改造：
  ① **降采样**：质检前把图缩到长边 1280 —— 4K PNG 直传既慢又贵，
     而判「脸崩没崩、人物是不是同一个人」1280 足够。
  ② **正确 MIME**：此前无论原图是 PNG / WebP 一律标 `image/jpeg`，
     与真实字节不符（部分通道会因此解析出错）。现在按**实际编码格式**标，
     且编码格式由我们自己决定（有 alpha 存 PNG，否则存 JPEG）。
  ③ **可观测**：qc_pass / qc_fail / qc_skipped_no_key / qc_skipped_error
     全部计数，「质检调用失败就放行」这条策略本身合理，但必须看得见
     到底是偶尔失败，还是绝大多数根本没真正质检过。
"""
import base64
import io
import json
import os
import threading
import urllib.request

# --------------------------------------------------------------------------
# 质检结果状态（UI 据此显示「已质检 / 质检跳过」，绝不把未质检显示成通过）
# --------------------------------------------------------------------------
QC_PASS = "pass"                      # 真质检且通过
QC_FAIL = "fail"                      # 真质检且不通过
QC_SKIP_NO_KEY = "skipped_no_key"     # 没配视觉 key
QC_SKIP_ERROR = "skipped_error"       # 调用异常（网络/超时/接口报错）
QC_SKIP_NOFILE = "skipped_nofile"     # 图片文件不存在
QC_OFF = "off"                        # 用户自己关了质检开关

QC_LABELS = {
    QC_PASS: "✅ 已质检·通过",
    QC_FAIL: "⚠️ 已质检·未通过",
    QC_SKIP_NO_KEY: "⏭ 质检跳过（未配置视觉 key）",
    QC_SKIP_ERROR: "⏭ 质检跳过（调用失败）",
    QC_SKIP_NOFILE: "⏭ 质检跳过（无图片文件）",
    QC_OFF: "⏭ 质检未开启",
}

# 计数桶名与审查要求一致（日志/报告里可直接 grep）
QC_STAT_KEYS = ("qc_pass", "qc_fail", "qc_skipped_no_key",
                "qc_skipped_error", "qc_skipped_nofile")

_STATS = {k: 0 for k in QC_STAT_KEYS}
_STATS_LOCK = threading.Lock()

# 降采样参数：长边上限 + 非 alpha 图的 JPEG 质量
QC_MAX_SIDE = 1280
QC_JPEG_QUALITY = 88


def reset_stats():
    """清零统计（每次开工前调一次，让报告反映本轮真实情况）。"""
    with _STATS_LOCK:
        for k in QC_STAT_KEYS:
            _STATS[k] = 0


def stats():
    """返回统计快照（副本，调用方可随意改）。"""
    with _STATS_LOCK:
        return dict(_STATS)


def stats_line():
    """一行可读统计，供日志/报告收尾用。"""
    s = stats()
    total_real = s["qc_pass"] + s["qc_fail"]
    skipped = s["qc_skipped_no_key"] + s["qc_skipped_error"] + s["qc_skipped_nofile"]
    return (f"质检统计：实际质检 {total_real} 次"
            f"（通过 {s['qc_pass']} / 未通过 {s['qc_fail']}）"
            f"；跳过 {skipped} 次"
            f"（无key {s['qc_skipped_no_key']} / 异常 {s['qc_skipped_error']}"
            f" / 无文件 {s['qc_skipped_nofile']}）")


def _bump(key):
    try:
        with _STATS_LOCK:
            _STATS[key] = _STATS.get(key, 0) + 1
    except Exception:
        pass


def status_label(status):
    return QC_LABELS.get(status or "", "⏭ 质检未知状态")


def is_real_qc(status):
    """是否真的质检过（区别于「跳过放行」）。"""
    return status in (QC_PASS, QC_FAIL)


# --------------------------------------------------------------------------
# 图片编码：降采样 + 真实 MIME
# --------------------------------------------------------------------------
def encode_image_for_qc(path, max_side=QC_MAX_SIDE):
    """把本地图片压成「体积小、MIME 正确」的 data URI 片段。

    返回 (b64, mime, meta)：
        b64  —— base64 文本（不含 data: 前缀）
        mime —— 与**实际字节**一致的 MIME（image/jpeg 或 image/png）
        meta —— {"src_bytes","out_bytes","src_size","out_size","format"}

    降级：Pillow 不可用或解码失败时，退回原文件字节 + 按扩展名猜 MIME
    （宁可传一张大图，也不能因为压缩失败就让质检整条挂掉）。
    """
    src_bytes = os.path.getsize(path)
    try:
        from PIL import Image
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            long_side = max(w, h)
            if long_side > max_side:
                scale = float(max_side) / float(long_side)
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                               Image.LANCZOS)
            has_alpha = im.mode in ("RGBA", "LA", "PA") or (
                im.mode == "P" and "transparency" in (im.info or {}))
            buf = io.BytesIO()
            if has_alpha:
                im.save(buf, format="PNG", optimize=True)
                mime, out_fmt = "image/png", "PNG"
            else:
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.save(buf, format="JPEG", quality=QC_JPEG_QUALITY, optimize=True)
                mime, out_fmt = "image/jpeg", "JPEG"
            raw = buf.getvalue()
            b64 = base64.b64encode(raw).decode("ascii")
            return b64, mime, {
                "src_bytes": src_bytes, "out_bytes": len(raw),
                "src_size": f"{w}x{h}", "out_size": f"{im.size[0]}x{im.size[1]}",
                "src_format": fmt or "?", "format": out_fmt,
                "downscaled": long_side > max_side,
            }
    except Exception:
        with open(path, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(path)[1].lower()
        mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                ".bmp": "image/bmp"}.get(ext, "image/jpeg")
        return (base64.b64encode(raw).decode("ascii"), mime,
                {"src_bytes": src_bytes, "out_bytes": len(raw),
                 "format": "raw", "downscaled": False})


def _mime_of(path):
    """仅取 MIME（不编码），供测试/调用方单独使用。"""
    try:
        return encode_image_for_qc(path)[1]
    except Exception:
        return "image/jpeg"


# 关键帧质检（剧情/分镜用）：看画面本身是否符合分镜描述、有无畸变
REVIEW_QUESTION_KEYFRAME = """You are a strict QC inspector for AI-generated video keyframes.
Compare the image against the required shot description above and check for:
1) Character consistency (face, hair, clothing match the locked character design)
2) Setting correctness (the environment matches the required scene)
3) Anatomical defects (extra/missing limbs, extra fingers, distorted hands,
   warped faces, melting or duplicated body parts)
4) Any obvious visual corruption, blur, or artifacts

Answer in EXACTLY this format, no other text:
VERDICT: PASS
ISSUES: <one short sentence, or "none">

Replace PASS with FAIL if you find ANY of the problems above.
"""

# 数字人质检：身份一致性优先（脸崩了整条就是废片）
REVIEW_QUESTION_IDENTITY = """You are a strict QC inspector for AI-generated digital human videos.
Picture 1 is the ORIGINAL reference photo of the real person.
Picture 2 is a frame from the generated video.

Check ONLY these, in order of importance:
1) IDENTITY: Is the person in Picture 2 clearly the SAME individual as Picture 1?
   Compare face shape, facial features, skin tone, hairstyle, apparent age and gender.
   Minor lighting/angle differences are acceptable; a DIFFERENT PERSON is not.
2) FACE INTEGRITY: Any warped, melted, blurred, duplicated or distorted face?
3) ANATOMY: Extra/missing fingers, extra arms, distorted hands, broken limbs?
4) BACKGROUND: If a background lock was requested, is the background still
   the same place as the reference photo (not a newly invented room)?

Answer in EXACTLY this format, no other text:
VERDICT: PASS
ISSUES: <one short sentence, or "none">

Replace PASS with FAIL if the identity changed, OR if the face/anatomy is broken.
"""


def vision_profile(cfg):
    """从 config 取 DeepSeek 视觉模型凭据，返回 (base, key, model)。

    profile 名必须是 'DeepSeek 官方'（已在用户机器上核实存在且 key 已配）。
    名字写错会导致质检**静默失效**——看起来在跑实则没生效，这种错最隐蔽。
    """
    prof = ((cfg or {}).get("model_profiles") or {}).get("DeepSeek 官方") or {}
    base = (prof.get("base_url") or "https://api.deepseek.com").rstrip("/")
    key = prof.get("api_key") or ""
    model = prof.get("model") or "deepseek-flash"
    return base, key, model


def review_images(cfg, image_paths, question, max_tokens=400, log=None, meta=None):
    """把「1~N 张图 + 问题」发给视觉模型，返回回答文本；不可用/失败返回空串。

    image_paths: 本地图片路径列表（按顺序作为 Picture 1..N）。
    返回 '' 表示质检不可用，调用方应放行。

    v4.168.0：发送前统一降采样 + 真实 MIME；每次调用都计入统计桶。
    meta（可选 dict）：调用方传入后会被填入本次质检的元信息
    （status / mime / sizes / skipped 原因），供 UI 显示「已质检 / 质检跳过」。
    """
    def _fill(status, **kw):
        if meta is not None:
            meta.update({"status": status, "label": status_label(status)})
            meta.update(kw)

    base, key, model = vision_profile(cfg)
    if not key:
        _bump("qc_skipped_no_key")
        _fill(QC_SKIP_NO_KEY, reason="未配置 DeepSeek 视觉 key")
        if log:
            log("  ⚠️ 未配置 DeepSeek 视觉 key，质检跳过（放行）")
        return ""
    paths = [p for p in (image_paths or []) if p and os.path.isfile(p)]
    if not paths:
        _bump("qc_skipped_nofile")
        _fill(QC_SKIP_NOFILE, reason="图片文件不存在")
        return ""
    try:
        content = []
        metas = []
        for p in paths:
            b64, mime, m = encode_image_for_qc(p)
            metas.append(m)
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})
        content.append({"type": "text", "text": question})
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/chat/completions", data=payload, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8", "ignore"))
        text = (((resp.get("choices") or [{}])[0]
                 .get("message", {}).get("content", "")) or "").strip()
        if meta is not None:
            meta.update({
                "mime": metas[0].get("format", "") if metas else "",
                "images": metas,
                "sent_bytes": sum(x.get("out_bytes", 0) for x in metas),
                "src_bytes": sum(x.get("src_bytes", 0) for x in metas),
                "downscaled": any(x.get("downscaled") for x in metas),
            })
            # 真正通过/不通过由 parse_verdict 决定，这里先落到 meta，稍后覆盖
        return text
    except Exception as e:
        _bump("qc_skipped_error")
        _fill(QC_SKIP_ERROR, reason=str(e)[:200])
        if log:
            log(f"  ⚠️ VLM 质检调用失败（放行）：{e}")
        return ""


def parse_verdict(text):
    """解析质检回答，返回 (passed: bool, note: str)。

    空回答（质检不可用）一律放行。只有明确出现 'VERDICT: FAIL' 才判不通过。
    """
    note = (text or "").strip()
    if not note:
        return True, ""
    passed = "VERDICT: FAIL" not in note.upper()
    return passed, note


def qc_call(cfg, image_paths, question, max_tokens=400, log=None):
    """一次质检的**完整结果**（v4.168.0）。

    返回 dict：
        status —— QC_PASS / QC_FAIL / QC_SKIP_* （见模块头）
        label  —— 给 UI 直接显示的中文标签
        passed —— 放行与否（跳过一律 True，铁律：质检不可用即放行）
        note   —— VLM 原文（跳过时为空）
        meta   —— 编码信息（体积、是否降采样、MIME）

    以前只有 (passed, note) 两元组，UI 无法区分「质检通过」与「压根没质检」——
    这两件事必须分得开，否则「未质检」会被当成「通过」糊过去。
    """
    meta = {}
    text = review_images(cfg, image_paths, question, max_tokens=max_tokens,
                         log=log, meta=meta)
    status = meta.get("status") or ""
    if not status:
        passed, note = parse_verdict(text)
        status = QC_PASS if passed else QC_FAIL
        _bump("qc_pass" if passed else "qc_fail")
    else:
        # review_images 已经计入跳过桶
        passed, note = True, ""
    if log and is_real_qc(status):
        meta_line = ""
        if meta.get("downscaled"):
            meta_line = (f"（已降采样 {meta.get('src_bytes', 0)//1024}KB→"
                         f"{meta.get('sent_bytes', 0)//1024}KB）")
        log(f"  🔎 质检 {status_label(status)}{meta_line}")
    meta["status"] = status
    return {
        "status": status,
        "label": status_label(status),
        "passed": passed,
        "note": note,
        "meta": meta,
    }


def review_keyframe(cfg, image_path, shot_desc, character_lock="", log=None,
                    with_meta=False):
    """导演台关键帧质检。默认返回 (passed, note)；with_meta=True 返回完整结果 dict。"""
    if not image_path or not os.path.isfile(image_path):
        _bump("qc_skipped_nofile")
        r = {"status": QC_SKIP_NOFILE, "label": status_label(QC_SKIP_NOFILE),
             "passed": True, "note": "", "meta": {}}
        return r if with_meta else (True, "")
    question = (f"Required shot description: {shot_desc}\n"
                f"{character_lock}\n\n{REVIEW_QUESTION_KEYFRAME}")
    r = qc_call(cfg, [image_path], question, log=log)
    return r if with_meta else (r["passed"], r["note"])


def review_identity(cfg, ref_path, frame_path, log=None, with_meta=False):
    """数字人质检：比对参考照与生成画面是否同一人。默认返回 (passed, note)。"""
    if not ref_path or not frame_path:
        _bump("qc_skipped_nofile")
        r = {"status": QC_SKIP_NOFILE, "label": status_label(QC_SKIP_NOFILE),
             "passed": True, "note": "", "meta": {}}
        return r if with_meta else (True, "")
    if not os.path.isfile(ref_path) or not os.path.isfile(frame_path):
        _bump("qc_skipped_nofile")
        r = {"status": QC_SKIP_NOFILE, "label": status_label(QC_SKIP_NOFILE),
             "passed": True, "note": "", "meta": {}}
        return r if with_meta else (True, "")
    r = qc_call(cfg, [ref_path, frame_path], REVIEW_QUESTION_IDENTITY, log=log)
    return r if with_meta else (r["passed"], r["note"])
