"""视觉质检（VLM QC）公共模块 —— 导演台与数字人分身共用。

抽出来独立成模块的原因：
  - 质检本质是「图片 + 问题 -> 结构化判定」，与谁调用无关；
  - 数字人面板不该为了复用一个函数就去依赖整个 video_pipeline（那会拉进 tools / matplotlib）；
  - 独立模块不依赖 PySide6 / tools，离线单测可以直接 import。

设计铁律：**质检不可用时一律放行**。VLM 是增强手段，绝不能因为没配 key、
接口抖动或超时就把整条生成流程卡死。

模型：DeepSeek 视觉模型（config 里 profile 名为 'DeepSeek 官方'，
model 为 deepseek-v4-flash-vision-exp，走用户已付费订阅通道）。
"""
import base64
import json
import os
import urllib.request


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
    model = prof.get("model") or "deepseek-v4-flash-vision-exp"
    return base, key, model


def review_images(cfg, image_paths, question, max_tokens=400, log=None):
    """把「1~N 张图 + 问题」发给视觉模型，返回回答文本；不可用/失败返回空串。

    image_paths: 本地图片路径列表（按顺序作为 Picture 1..N）。
    返回 '' 表示质检不可用，调用方应放行。
    """
    base, key, model = vision_profile(cfg)
    if not key:
        if log:
            log("  ⚠️ 未配置 DeepSeek 视觉 key，质检跳过（放行）")
        return ""
    paths = [p for p in (image_paths or []) if p and os.path.isfile(p)]
    if not paths:
        return ""
    try:
        content = []
        for p in paths:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
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
        return (((resp.get("choices") or [{}])[0]
                 .get("message", {}).get("content", "")) or "").strip()
    except Exception as e:
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


def review_keyframe(cfg, image_path, shot_desc, character_lock="", log=None):
    """导演台关键帧质检。返回 (passed, note)。"""
    if not image_path or not os.path.isfile(image_path):
        return True, ""
    question = (f"Required shot description: {shot_desc}\n"
                f"{character_lock}\n\n{REVIEW_QUESTION_KEYFRAME}")
    text = review_images(cfg, [image_path], question, log=log)
    return parse_verdict(text)


def review_identity(cfg, ref_path, frame_path, log=None):
    """数字人质检：比对参考照与生成画面是否同一人。返回 (passed, note)。"""
    if not ref_path or not frame_path:
        return True, ""
    if not os.path.isfile(ref_path) or not os.path.isfile(frame_path):
        return True, ""
    text = review_images(cfg, [ref_path, frame_path],
                         REVIEW_QUESTION_IDENTITY, log=log)
    return parse_verdict(text)
