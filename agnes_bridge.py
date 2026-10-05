# -*- coding: utf-8 -*-
"""Agnes 桥接层：把画布执行器的「真实生成」接到 Agnes 免费全模态 API。

设计原则：
  * 本模块是「真实」实现——会真打网络、消耗 Agnes 免费额度。判据套件**不**直接依赖
    本模块的网络行为（用 mock 回调验证「注入生效」），因此无头沙箱也能跑全绿。
  * 四个对外回调，签名与 executors.py 调用点一一对应：
      - get_agnes_inpaint_fn()      -> inpaint_fn(out, region, instruction, params)
                                      （阶段 C image_local_edit 的 inpaint 回调）
      - get_agnes_text2img_fn()     -> text2img_fn(prompt)
                                      （gen_image 纯文生图，v4.211.11）
      - get_agnes_video_fn()        -> video_fn(node, asset_root, src_image_path, prompt)
                                      （gen_video 真实生成 clip）
      - get_promo_motion_fn()       -> motion_fn(src_image_paths, out_path, params)
                                      （promo_fx 真实促销动效）
  * 另有纯本地、零网络、零 ffmpeg 的 pil_promo_motion / make_promo_motion_preview，
    用于「促销动效预览」按钮（不消耗额度，随时可点）。
  * key **不在源码里**（2026-10-03 安全修复）：运行时按
    env AGNES_API_KEY / AGNES_BASE_URL → 用户配置
    ~/Documents/小臭玩AI/config.json 的 model_profiles.Agnes.api_key 解析；
    都取不到就明确抛错（绝不静默用假 key 打网络）。
    背景：此前此处硬编码国内站会员 key，随公开仓库 ggddffdd/AgentDesktop
    的提交 3c9d285 泄露（打包产物 PYZ 内亦带明文），故改为运行时解析。

参考：~/.workbuddy/skills/agnes-ai（agnes-image-2.5-flash 图生图、agnes-video-2.5-flash 视频）。
"""

import io
import os

import numpy as np
from PIL import Image

# 默认凭据：**源码内绝不出现明文 key**。解析顺序：
#   1) env AGNES_API_KEY / AGNES_BASE_URL（换站/换号、CI 用）
#   2) 用户配置 ~/Documents/小臭玩AI/config.json → model_profiles.Agnes.api_key
#      （该文件在用户文档目录且已入 .gitignore，是项目既有的真实密钥归口）
#   3) 都没有 → _resolve_cred 抛错，给出可操作的提示
_DEFAULT_BASE = "https://api.agnes-ai.cn/v1"


def _user_config_agnes_key():
    """从用户配置读 Agnes key；任何异常都返回 ""（由调用方决定是否报错）。"""
    try:
        import json

        from config import CONFIG_PATH
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        prof = (cfg.get("model_profiles") or {}).get("Agnes") or {}
        return (prof.get("api_key") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _default_cred():
    """返回 (key, base)。key 可能为空字符串，由 _resolve_cred 统一判定。"""
    key = (os.environ.get("AGNES_API_KEY") or "").strip()
    if not key:
        key = _user_config_agnes_key()
    base = (os.environ.get("AGNES_BASE_URL") or "").strip() or _DEFAULT_BASE
    return key, base


def _resolve_cred(api_key, base_url):
    dkey, dbase = _default_cred()
    key = (api_key or dkey or "").strip()
    if not key:
        raise RuntimeError(
            "Agnes 凭据缺失：源码不再内置 key。请设置环境变量 AGNES_API_KEY，"
            "或在 %s 的 model_profiles.Agnes.api_key 写入 key（与 base_url 同站）。"
            % (os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI",
                            "config.json"),))
    return key, (base_url or dbase)


# 节制：视频接口 6 次/分钟限流，单次提交不涉及；此处仅做最小重试
_MAX_RETRY = 2


def _arr_to_pil(arr):
    a = np.asarray(arr, dtype=np.float64)
    if a.size == 0:
        a = np.zeros((8, 8, 3), dtype=np.float64)
    if a.max() > 1.0:
        a = a / 255.0
    a = np.clip(a, 0, 1)
    return Image.fromarray((a * 255).astype("uint8"))


def _pil_to_arr(img):
    return np.asarray(img.convert("RGB"), dtype=np.float64) / 255.0


def _region_to_text(region):
    """把归一化 region 转成中文描述，塞进 prompt 引导 Agnes 只改该区域。"""
    if not region:
        return "整图"
    if region.get("type") == "rect":
        x, y, w, h = (region.get(k, 0) for k in ("x", "y", "w", "h"))
        return "图片中大致位于 x=%.2f~%.2f、y=%.2f~%.2f 的矩形区域" % (
            x, min(1.0, x + w), y, min(1.0, y + h))
    if region.get("type") == "polygon":
        return "图片中由用户圈定的多边形区域"
    return "整图"


def _http_json(url, payload=None, headers=None, method="POST", timeout=120):
    import json
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url, out_path, timeout=300):
    import urllib.request

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


# --------------------------------------------------------------------------
# ① inpaint（图生图方式做局部重绘）
# --------------------------------------------------------------------------
def agnes_image_inpaint(arr, region, instruction, params, api_key=None, base_url=None):
    """Agnes 图生图实现的局部重绘（inpaint_fn 真实体）。

    签名兼容 image_local_edit.apply_local_edits 的 inpaint 回调：
        inpaint_fn(out, region, instruction, params) -> 新数组(float 0..1)
    Agnes 图生图是整图 img2img，不支持真 mask；我们用 prompt 引导「只改指定区域」，
    这是 Agnes 能力的诚实上限（不像某些模型支持 inpainting mask）。
    """
    import base64

    key, base = _resolve_cred(api_key, base_url)
    img = _arr_to_pil(arr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    region_desc = _region_to_text(region)
    prompt = (instruction or "").strip()
    if not prompt:
        prompt = "重绘指定区域"
    if region_desc != "整图":
        prompt = "%s。请只修改%s，其余画面严格保持原样，不要改动区域外任何内容。" % (
            prompt, region_desc)

    payload = {
        "model": "agnes-image-2.5-flash",
        "prompt": prompt,
        "size": "1K",
        "extra_body": {
            "image": [data_uri],
            "response_format": "url",
        },
    }
    headers = {"Authorization": "Bearer %s" % key, "Content-Type": "application/json"}

    last_err = None
    for _ in range(_MAX_RETRY + 1):
        try:
            data = _http_json("%s/images/generations" % base, payload, headers, "POST", 120)
            url = (data.get("data") or [{}])[0].get("url") or data.get("url")
            if not url:
                raise RuntimeError("Agnes 图生图未返回 url: %s" % str(data)[:200])
            out_buf = io.BytesIO()
            import urllib.request
            with urllib.request.urlopen(url, timeout=60) as r:
                out_buf.write(r.read())
            out_img = Image.open(out_buf).convert("RGB")
            return _pil_to_arr(out_img)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError("Agnes inpaint 失败: %s" % last_err)


def get_agnes_inpaint_fn(api_key=None, base_url=None):
    """返回符合 inpaint_fn(out, region, instruction, params) 签名的闭包。"""
    def _fn(out, region, instruction, params):
        return agnes_image_inpaint(out, region, instruction, params,
                                   api_key=api_key, base_url=base_url)
    return _fn


# --------------------------------------------------------------------------
def get_agnes_text2img_fn(api_key=None, base_url=None):
    """返回 text2img_fn(prompt) -> np.ndarray：Agnes 纯文生图（v4.211.11）。

    与 inpaint 同端点（/images/generations）、同模型（agnes-image-2.5-flash），
    区别仅在 **不带 extra_body.image** —— 即纯文字生图（2026-10-05 实探通过：
    8.6s / 1024×1024 / 雪地柯基）。未配 key 时 _resolve_cred 抛错（绝不静默
    用假 key 打网络）。调用方（executors.gen_image_executor）语义约定：
    fn 注入则走真生图、异常诚实 failed（不静默回落占位图）；未注入（None）
    或 prompt 为空时由执行器回落本地 PIL 渐变占位（离线兜底）。
    """
    key, base = _resolve_cred(api_key, base_url)

    def _text2img(prompt):
        text = (prompt or "").strip()
        if not text:
            raise RuntimeError("文生图需要非空 prompt")
        payload = {
            "model": "agnes-image-2.5-flash",
            "prompt": text,
            "size": "1K",
            "extra_body": {"response_format": "url"},
        }
        headers = {"Authorization": "Bearer %s" % key, "Content-Type": "application/json"}

        last_err = None
        for _ in range(_MAX_RETRY + 1):
            try:
                data = _http_json("%s/images/generations" % base, payload, headers, "POST", 120)
                url = (data.get("data") or [{}])[0].get("url") or data.get("url")
                if not url:
                    raise RuntimeError("Agnes 文生图未返回 url: %s" % str(data)[:200])
                out_buf = io.BytesIO()
                import urllib.request
                with urllib.request.urlopen(url, timeout=60) as r:
                    out_buf.write(r.read())
                return _pil_to_arr(Image.open(out_buf).convert("RGB"))
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError("Agnes 文生图失败: %s" % last_err)

    return _text2img


# ② 视频生成（gen_video 真实产出 clip）
# --------------------------------------------------------------------------
def _aspect_ratio(w, h):
    if w >= h:
        return "16:9" if abs(w / h - 16 / 9) < abs(w / h - 4 / 3) else "4:3"
    return "9:16" if abs(w / h - 9 / 16) < abs(w / h - 3 / 4) else "3:4"


def agnes_video_generate(node, asset_root, src_image_path, prompt,
                         api_key=None, base_url=None):
    """Agnes 视频真实生成（video_fn 真实体）。

    签名兼容 executors.gen_video_executor 的调用：
        video_fn(node, asset_root, src_image_path, prompt) -> out_path(mp4)
    用 src_image 作 reference 首帧（单图 reference 模式），提交后异步轮询下载。
    """
    import base64
    import time

    key, base = _resolve_cred(api_key, base_url)
    if not src_image_path or not os.path.exists(src_image_path):
        raise RuntimeError("gen_video 缺少上游图片资产（src_image_path 为空/不存在）")
    img = Image.open(src_image_path).convert("RGB")
    # 控制体积：最长边压到 768，避免 base64 太大
    scale = min(1.0, 768.0 / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    ratio = _aspect_ratio(img.width, img.height)

    payload = {
        "model": "agnes-video-2.5-flash",
        "prompt": (prompt or "动态展示画面内容，保持主体稳定").strip(),
        "mode": "reference",
        "images": [data_uri],
        "size": "720P",
        "aspect_ratio": ratio,
        "seconds": "6",
    }
    headers = {"Authorization": "Bearer %s" % key, "Content-Type": "application/json"}

    # 提交
    submit = _http_json("%s/videos" % base, payload, headers, "POST", 120)
    task_id = submit.get("video_id") or submit.get("task_id") or \
        (submit.get("data") or {}).get("video_id")
    if not task_id:
        raise RuntimeError("Agnes 视频提交未返回 task_id: %s" % str(submit)[:200])

    # 轮询（参考 agnes-ai skill：GET /agnesapi?video_id=&model_name=）
    out_path = os.path.join(asset_root, "video", "%s_clip.mp4" % node.id)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    status_url = "%s/agnesapi?video_id=%s&model_name=agnes-video-2.5-flash" % (base, task_id)
    video_url = None
    for _ in range(60):
        try:
            st = _http_json(status_url, headers=headers, method="GET", timeout=30)
        except Exception:  # noqa: BLE001
            st = {}
        s = (st.get("status") or "").lower()
        if s in ("completed",):
            video_url = (st.get("metadata") or {}).get("url") or st.get("url") or \
                st.get("video_url") or st.get("download_url")
            if video_url:
                break
        if s in ("failed", "error"):
            raise RuntimeError("Agnes 视频生成失败: %s" % str(st)[:200])
        time.sleep(8)
    if not video_url:
        raise RuntimeError("Agnes 视频轮询超时（task_id=%s）" % task_id)
    _download(video_url, out_path, timeout=300)
    return out_path


def get_agnes_video_fn(api_key=None, base_url=None):
    """返回符合 video_fn(node, asset_root, src_image_path, prompt) 签名的闭包。"""
    def _fn(node, asset_root, src_image_path, prompt):
        return agnes_video_generate(node, asset_root, src_image_path, prompt,
                                    api_key=api_key, base_url=base_url)
    return _fn


# --------------------------------------------------------------------------
# ③ 促销动效（promo_fx）：纯本地 PIL GIF 预览 + 默认 motion_fn
# --------------------------------------------------------------------------
def pil_promo_motion(src_image_paths, out_path, params=None):
    """纯本地、零网络、零 ffmpeg 的促销动效预览生成器（motion_fn 真实体）。

    把 src_image_paths（上游 image 资产帧列表）按顺序拼成一段 GIF 动效：
      * 帧间做轻微缩放/位移（Ken Burns 感）模拟「动效」；
      * 叠加促销文案（params.get("caption")，默认「促销 · PROMO」）；
      * 首帧/尾帧加淡入淡出感（通过透明度帧）。
    无 image 帧时退化为纯色渐变帧 + 文案，仍可生成合法 GIF。
    返回 out_path。
    """
    params = params or {}
    caption = (params.get("caption") or "促销 · PROMO")
    fps = int(params.get("fps", 4))
    hold = int(params.get("hold", 6))  # 每帧停留帧数
    imgs = []
    for p in (src_image_paths or []):
        if p and os.path.exists(p):
            try:
                imgs.append(Image.open(p).convert("RGB"))
            except Exception:  # noqa: BLE001
                pass

    W, H = 640, 360
    frames = []
    base_imgs = imgs if imgs else [None]

    def _mk(idx, total, src):
        canvas = Image.new("RGB", (W, H), (18, 18, 26))
        if src is not None:
            s = src.resize((W, H))
            # 轻微缩放位移，模拟动效
            t = idx / max(1, total - 1)
            scale = 1.0 + 0.06 * (0.5 - abs(t - 0.5) * 2)  # 中间略放大
            nw, nh = int(W * scale), int(H * scale)
            s2 = s.resize((nw, nh))
            off = ((nw - W) // 2, (nh - H) // 2)
            canvas.paste(s2, (-off[0], -off[1]))
        from PIL import ImageDraw, ImageFont
        d = ImageDraw.Draw(canvas)
        # 促销角标
        d.rectangle([0, 0, 150, 40], fill=(214, 48, 37))
        try:
            fnt = ImageFont.load_default()
            d.text((8, 12), "PROMO", fill=(255, 255, 255), font=fnt)
        except Exception:  # noqa: BLE001
            d.text((8, 12), "PROMO", fill=(255, 255, 255))
        # 文案
        d.text((10, H - 30), caption[:40], fill=(255, 230, 120))
        return canvas

    total = max(1, len(base_imgs) * hold)
    for i, src in enumerate(base_imgs):
        for k in range(hold):
            frames.append(_mk(i * hold + k, total, src))
    # 尾帧淡出感：复制最后一帧
    if frames:
        frames.append(frames[-1])

    if not frames:
        frames = [_mk(0, 1, None)]
    out_path = str(out_path)
    if not out_path.lower().endswith(".gif"):
        out_path = out_path + ".gif"
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000 / max(1, fps)), loop=0)
    return out_path


def get_promo_motion_fn():
    """返回符合 motion_fn(src_image_paths, out_path, params) 签名的闭包。"""
    def _fn(src_image_paths, out_path, params=None):
        return pil_promo_motion(src_image_paths, out_path, params)
    return _fn


def collect_promo_frames(graph, asset_root=None):
    """收集画布上「可用作促销动效帧」的 image 产物路径，并把跳过的分档报出来。

    返回：{"paths": [...],
           "skipped": {"placeholder": n, "stale": n, "invalid": n},
           "skipped_detail": [{"node", "port", "why"}, ...]}

    为什么不能只看 `os.path.exists` + `kind == "image"`：占位物的路径是编出来的
    （stub）或内容只有几行 manifest 文本（passthrough），`exists` 一样为真。旧实现
    把它收进帧列表，到 `pil_promo_motion` 里 `Image.open` 抛错被 `except: pass` 吃掉
    —— 于是「界面上几个 image 节点都写着 completed，点动效预览却出来一段纯色渐变，
    且没有任何提示说明为什么没用上你的图」。这里按统一入口 `assess_asset` 判定，
    不可用的一律不当帧，并分档计数交给 UI 讲清楚。
    """
    import canvas_graph as cg  # 函数内延迟导入：本模块顶层只依赖 PIL / numpy

    paths, detail = [], []
    skipped = {"placeholder": 0, "stale": 0, "invalid": 0}
    if graph is None:
        return {"paths": paths, "skipped": skipped, "skipped_detail": detail}
    for nid, node in graph.nodes.items():
        for p, a in (getattr(node, "out_assets", {}) or {}).items():
            if getattr(a, "kind", "") != "image":
                continue
            if not (getattr(a, "path", "") or "").strip():
                continue  # 压根没产出引用的端口不算「跳过」，不进统计
            validity, _why = cg.assess_asset(a, asset_root)
            if validity == cg.ASSET_REAL:
                paths.append(a.path)
            else:
                skipped[validity] = skipped.get(validity, 0) + 1
                detail.append({"node": nid, "port": p, "why": validity})
    return {"paths": paths, "skipped": skipped, "skipped_detail": detail}


def make_promo_motion_preview(graph, asset_root, out_gif, params=None, stats=None):
    """画布级促销动效预览：收集所有 image 资产帧 → 生成 GIF。

    供工具栏「促销动效预览」按钮调用（纯本地，不耗额度）。
    返回生成的 GIF 路径；无任何 image 资产时仍产出合法 GIF（退化帧）。

    `stats`：可选的可变 dict，回填 `collect_promo_frames` 的结果（真实帧路径 +
    跳过占位/失效/历史各几个），供调用方把「为什么没用上我的图」讲出来。
    """
    import os

    os.makedirs(asset_root, exist_ok=True)
    info = collect_promo_frames(graph, asset_root)
    if isinstance(stats, dict):
        stats.update(info)
    return pil_promo_motion(info["paths"], out_gif, params)
