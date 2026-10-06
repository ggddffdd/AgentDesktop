"""ui_msg.py —— 消息与 API 纯函数层（v4.216.0 从 ui.py 拆出）

内容：多模态消息整形（_flatten_text_content/_sanitize_msg_for_api/
_extract_file_image_parts/_normalize_image_dataurl/_compress_image_for_api）、
历史预算（_fit_history_to_budget/_build_api_history/_repair_tool_pairs）、
思考链与视觉判据（_is_thinking_channel/_ensure_reasoning_content/
_model_supports_vision）、错误文本（_api_error_text/_attach_api_body）、
审计辅助（_vision_debug/_sanitize_filename/_strip_attachment_refs/
_tool_ids_present）。

零行为变化：代码逐行搬移；ui.py 顶部 re-export 全部名字保兼容。
"""
from PySide6.QtCore import (QBuffer, QIODevice, Qt)
from PySide6.QtGui import (QImage, QPainter)
import base64
import config
import json
import logging
import mimetypes
import os
import re
from config import (WORKSPACE_DIR)
from datetime import (datetime)
from intent_guard import (strip_attachment_refs)
log = logging.getLogger("dsdesktop")

def _flatten_text_content(content_list):
    """把多模态 list content 的文本部分拼成字符串；无文本返回 ''。"""
    texts = []
    for p in content_list:
        if isinstance(p, dict) and p.get("type") == "text":
            t = p.get("text", "")
            if t:
                texts.append(t)
        elif isinstance(p, str) and p:
            texts.append(p)
    return "\n".join(texts).strip()
def _sanitize_filename(name):
    """v4.102 hotfix：清洗 Windows 文件名非法字符（\\ / : * ? " < > |），
    并裁掉首尾空格/点。返回清洗后的安全文件名；空结果回退为 'file'。
    v4.125 P2：处理 Windows 保留设备名（con/nul/aux/com1..9/lpt1..9/prn），
    模型生成此类文件名会落盘失败（即使带扩展名也拒绝）。"""
    if not name:
        return "file"
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    safe = safe.strip().strip(".")
    if not safe:
        return "file"
    stem = safe.split(".")[0].strip().lower()
    _RESERVED = {"con", "prn", "aux", "nul"} | {
        f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
    if stem in _RESERVED:
        safe = "_" + safe  # 前缀下划线绕开保留名
    return safe
def _vision_debug(msg):
    """v4.102 fix5：写视觉链路调试日志到 USER_DATA_DIR/vision_debug.log，
    用户/我们都能找得到（之前写 APP_DIR，源码与 exe 路径不同导致用户找不到）。

    v4.134.6：改走 config.append_log_line —— 与另外三条诊断日志共用同一套
    「超限就滚成 .1」逻辑。此前是裸 open(...,"a") 只增不减（实测已涨到 537 KB）。
    顺带把落点从写死的 expanduser("~/Documents/小臭玩AI") 换成 config.USER_DATA_DIR，
    这样测试子进程注入的改道变量也能生效（否则它会一直往真实目录写）。

    #755 隐私收敛：调试日志绝不落用户文本/图片 base64/密钥。无论调用方传什么，
    落盘前统一脱敏——data URI、长 base64 块、URL 内 key/token、明文 sk- 密钥
    一律抹掉，单条限长 500 字符，避免日志无限膨胀且杜绝隐私/密钥外泄。
    """
    try:
        import re
        from datetime import datetime
        s = str(msg)
        # 1) 抹掉 data: URI（图片 base64 内联）
        s = re.sub(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]+",
                   "[REDACTED_DATA_URI]", s)
        # 2) 抹掉任意长 base64 块（>=80 连续字符，覆盖裸 base64 图片/密钥）
        s = re.sub(r"[A-Za-z0-9+/]{80,}={0,2}", "[REDACTED_BLOB]", s)
        # 3) URL 内 key/token 查询参数脱敏
        s = re.sub(r"([?&](?:key|token|api[_-]?key|access[_-]?token)=)[^&\s]+",
                   r"\1[REDACTED]", s, flags=re.IGNORECASE)
        # 4) 明文 sk- 开头密钥脱敏
        s = re.sub(r"sk-[A-Za-z0-9]{8,}", "[REDACTED_KEY]", s)
        # 5) 限长，避免单条撑爆日志
        if len(s) > 500:
            s = s[:500] + f"...[truncated {len(s) - 500} chars]"
        p = os.path.join(config.USER_DATA_DIR, "vision_debug.log")
        config.append_log_line(p, f"[{datetime.now().strftime('%H:%M:%S')}] {s}\n")
    except Exception:
        pass
def _compress_image_for_api(path, max_dim=1568, quality=85):
    """v4.102 fix4+5：图片预处理。
    fix4：避免真实截图多张发图时 payload 超 DeepSeek 视觉模型单请求体限制（>10MB 返 400）。
    fix5：**无论大小先强制 convertToFormat(RGB888) 标准化**，防止 CMYK/灰度/索引色/异常
    PNG 被 DeepSeek 拒为 unsupported image（实测纯 RGB/ARGB 都接受，唯独某些剪贴板格式被拒）。

    策略：读图后立即 RGB888 标准化 → 若宽/高>max_dim 等比缩放 → 转 JPEG 条件：
    原>200KB 或需缩放 或 含 alpha（带透明的图转 JPEG 走 alpha 展平到白底）。
    失败时返 None（send 走直接读 PNG 的 fallback）。"""
    try:
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import Qt, QBuffer, QIODevice
        import base64 as _b64
        orig_kb = os.path.getsize(path) // 1024
        img = QImage(path)
        if img.isNull():
            _vision_debug(f"compress: QImage.isNull for {os.path.basename(path)}")
            return None
        # 关键：强制 RGB888 标准化，消除 CMYK/灰度/索引色等不被 DeepSeek 接受的格式
        fmt_rgb = getattr(getattr(QImage, "Format", QImage), "Format_RGB888", None) or getattr(QImage, "Format_RGB888", None)
        if fmt_rgb and img.format() != fmt_rgb:
            src_fmt = str(img.format())
            img = img.convertToFormat(fmt_rgb)
            _vision_debug(f"compress: format convert {src_fmt} -> RGB888 for {os.path.basename(path)}")
        w, h = img.width(), img.height()
        need_resize = max(w, h) > max_dim
        if need_resize:
            img = img.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # 转 JPEG 条件：原 >200KB 或需缩放 或 含 alpha（小但异常格式也强制 JPEG 更稳）
        use_jpeg = orig_kb > 200 or need_resize or img.hasAlphaChannel()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        if use_jpeg and img.hasAlphaChannel():
            # alpha 展平到白底，避免 JPEG 出现黑边
            bg = QImage(img.size(), fmt_rgb)
            bg.fill(0xFFFFFFFF)
            p = QPainter(bg)
            p.drawImage(0, 0, img)
            p.end()
            img = bg
        if use_jpeg:
            img.save(buf, "JPEG", quality)
            mime = "image/jpeg"
        else:
            img.save(buf, "PNG")
            mime = "image/png"
        raw = bytes(buf.data())
        if len(raw) < 100:  # 太小怀疑输出损坏
            _vision_debug(f"compress: output too small ({len(raw)}B) for {path}")
            return None
        final_kb = len(raw) // 1024
        b64 = _b64.b64encode(raw).decode()
        url = f"data:{mime};base64,{b64}"
        _vision_debug(f"compress OK: {os.path.basename(path)} | {w}x{h} | {orig_kb}KB->{final_kb}KB | mime={mime}")
        return (url, mime, orig_kb, final_kb, need_resize or use_jpeg)
    except Exception as e:
        _vision_debug(f"compress EXC for {os.path.basename(path)}: {type(e).__name__}: {e}")
        return None
def _normalize_image_dataurl(url):
    """v4.102 fix6：把任意 image data URL 重编码为 DeepSeek 视觉模型稳接受的 RGB JPEG。
    作为「最后一道关卡」覆盖所有发图来源（贴图/附件/历史消息/兜底回退）——只要进 API
    前统一过一遍，即可规避特殊格式/超大图被拒（HTTP 400 无正文）的情况。

    v4.175.0 改语义：**解不开就返回空串（＝判为无效，由调用方丢掉）**。
    原实现「无法解码则原样返回」，等于把坏图原封不动送给 API ——
    实测事故：会话历史里一张 71 字节的坏 PNG（IDAT 校验和错，libpng 拒收）
    被原样发出 → DeepSeek 判 `.messages[1].image[0]: unsupported image` 400
    → **该会话此后每次带图都失败**（等于整个会话被一张坏图毒死），
    而界面只显示「HTTP Error 400: Bad Request」，完全看不出原因。
    一张坏图顶多让模型少看一张图（还能用文字回答），远比整轮失败轻。"""
    if not isinstance(url, str) or not url.startswith("data:image/"):
        return url
    try:
        import base64 as _b64
        from PySide6.QtGui import QImage
        from PySide6.QtCore import Qt, QBuffer, QIODevice
        header, _, b64 = url.partition(",")
        if "base64" not in header:
            return url
        raw = _b64.b64decode(b64)
        img = QImage.fromData(raw)
        if img.isNull():
            _vision_debug("normalize: 图片无法解码，判为无效（将被丢弃）")
            return ""            # v4.175.0：解不开 ⇒ 无效
        fmt_rgb = QImage.Format.Format_RGB888
        if img.format() != fmt_rgb:
            img = img.convertToFormat(fmt_rgb)
        buf = QBuffer(); buf.open(QIODevice.WriteOnly)
        img.save(buf, "JPEG", 85)
        out = bytes(buf.data())
        return f"data:image/jpeg;base64,{_b64.b64encode(out).decode()}"
    except Exception as e:
        _vision_debug(f"_normalize_image_dataurl EXC: {e}")
        return ""                # v4.175.0：异常同样判为无效（原来会原样放行）
# 视觉模型识别词表（子串匹配，小写）。
#
# ⚠️ 这张表与「图像链路路由」是**一对**，必须一起改：
#   `_start_stream` 检测到带图 → force_complex=True, reason="image"
#     → 走 `model_routing.complex_model`（默认 profile「DeepSeek 官方」= deepseek-flash）
#   → 再用 `_model_supports_vision(_m)` 决定「图要不要保留成 image_url」
#   → 认不出 → `_flatten_text_content()` 把图归一化成纯文本 → 模型说"我没收到图"
#
# v4.174.0 修（实测事故）：表里**没有** `deepseek-flash`，而路由偏偏往它发图 ——
# 于是整条图像链路被自己关掉：图进了会话、升舱也发生了（route_log 的
# reason="image"），但发给模型的 payload 里图已被抹成文字，模型只能回
# 「我这边没有收到任何图片」。**实测定案：deepseek-flash 认图** ——
# 128×128 纯红 PNG → 答「红色」，且 reasoning 里明确写着看图过程
# （同一实测也确认 deepseek-v4-flash-vision-exp 认图）。
VISION_MODEL_KW = (
    "vision", "vl", "gpt-4o", "gpt-4v", "gpt-4.1", "qwen-vl", "qwen2-vl",
    "qwen2.5-vl", "qwen2_5-vl", "glm-4v", "glm-4v-plus", "yi-vl",
    "internvl", "minicpm-v", "deepseek-vl", "step-1v", "moondream",
    "cogvlm", "fuyu", "idefics", "kosmos",
    # v4.174.0：图像链路实际使用的 DeepSeek 通道模型（实测支持视觉）
    "deepseek-flash",
)
def _model_supports_vision(model):
    """模块级：判断模型是否支持图像输入（多模态视觉）。仅这些模型才在
    `_sanitize_msg_for_api` 中保留 image_url；其余模型图像被归一化为纯文本标签，
    避免把 list content 原样发给不支持视觉的接口导致 400。

    v4.174.0：除内置词表外，额外接受 `config.VISION_MODEL_EXTRA_HINTS`
    —— 换视觉模型时改配置即可，不必改代码。
    **改完请跑 `tests/test_vision_channel.py`**（它会拿配置里图像链路实际指向的
    模型来核对本函数认不认，正是这次漏掉的那道守护）。
    """
    if not model:
        return False
    m = str(model).lower()
    if any(k in m for k in VISION_MODEL_KW):
        return True
    try:
        import config
        extra = getattr(config, "VISION_MODEL_EXTRA_HINTS", ()) or ()
        return any(str(k).lower() in m for k in extra)
    except Exception:
        return False
def _strip_attachment_refs(text):
    """剥掉附件 / 文件引用标记（v4.173.0）。

    **实现唯一来源在 `intent_guard.strip_attachment_refs`** —— 同一份剥法服务两处：
      ① 本文件的导演台判据（`_is_director_command`）；
      ② `intent_guard` 的全部判据（归一化入口 `_norm` 里调用）。
    分两份写迟早漂移，而漂移的后果正是「一处的文件名能点着判据、另一处不能」。
    这里只转发，不重复实现。

    剥掉的行：`[文件: x]` / `[文件不存在: x]` / `[非图片文件: x]` / `[file: x]`
    / `[图片已粘贴 N]`。只剥标记本身，正文一个字不动。
    """
    from intent_guard import strip_attachment_refs
    return strip_attachment_refs(text)
def _extract_file_image_parts(text, app_dir):
    """v4.102 hotfix：从文本中的 [文件: path] / [file: path] 标记提取图片，
    转为 OpenAI 兼容的 image_url content parts。

    返回 (clean_text, image_parts)。只处理真实存在的图片文件；不存在或非图片文件
    会在原处保留提示文本，避免模型误解。clean_text 已去掉被成功加载图片的标记。

    v4.173.0 修路径基准（实测事故）：附件由发送侧复制到 **WORKSPACE_DIR**/incoming
    （v4.164.0 运行数据归口），但本函数此前只按 `app_dir` 解析 —— 于是附件明明在
    `~/Documents/小臭玩AI/incoming/`，这里却判「文件不存在」，模型既收不到图、
    也拿不到可用路径。与 `tools.tool_read_file` 统一口径：**工作区优先，程序目录回退**。
    """
    import base64, mimetypes
    pattern = re.compile(r"\[(?:\u6587\u4ef6|file):\s*([^\]\n]+)\]")
    image_parts = []

    def _resolve(rel):
        """相对路径：先按工作区解析，未命中回退程序目录（兼容历史相对路径）。"""
        if os.path.isabs(rel):
            return os.path.normpath(rel)
        for _base in (WORKSPACE_DIR, app_dir):
            try:
                cand = os.path.normpath(os.path.join(_base, rel))
            except Exception:
                continue
            if os.path.isfile(cand):
                return cand
        # 都不存在：给工作区下的路径（提示里展示的是 rel，这里只用于判断）
        return os.path.normpath(os.path.join(WORKSPACE_DIR, rel))

    def _replace(m):
        rel = m.group(1).strip()
        path = _resolve(rel)
        if not os.path.isfile(path):
            return f"\n[文件不存在: {rel}]\n"
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = mimetypes.guess_type(path)[0] or ("image/png" if ext == "png" else "image/jpeg")
        if not mime.startswith("image/"):
            return f"\n[非图片文件: {rel}]\n"
        try:
            # v4.102 fix6：附件图片也走 _compress_image_for_api，强制 RGB888 标准化 +
            # JPEG 体积控制，与贴图路径统一，避免特殊格式/超大图被 DeepSeek 视觉模型拒（HTTP 400）。
            comp = _compress_image_for_api(path)
            if comp:
                image_parts.append({"type": "image_url", "image_url": {"url": comp[0]}})
                _vision_debug(f"attach 图片压缩 OK: {os.path.basename(path)} -> {comp[1]} {comp[3]}KB")
            else:
                # 压缩失败回退原始读取（保证不阻断发送）
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                image_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            return ""  # 成功加载的图片标记从文本中移除
        except Exception as e:
            return f"\n[图片加载失败: {rel} ({e})]\n"

    clean_text = pattern.sub(_replace, text)
    return clean_text.strip(), image_parts
def _sanitize_msg_for_api(m, vision_ok=False):
    """把一条 session 消息清洗为 OpenAI 兼容接口可接受的 {"role","content"}；不可接受返回 None。

    v4.79 hotfix：session.messages 里混有 UI 展示用的 tool / tool_log 角色、
    content=None、以及多模态 list content——直接发给 DeepSeek 等接口会 400
    （unknown variant `tool_log` / invalid content）。历史消息必须经此过滤。

    vision_ok=True（目标模型支持视觉，如 deepseek-flash）时，若
    content 是含 image_url 的 list，则保留 list 结构（仅 text + image_url 两种合法
    part）原样发视觉模型，让模型真正"看图"；否则仍归一化为纯文本（兼容旧逻辑）。
    """
    if not isinstance(m, dict):
        return None
    role = m.get("role")
    if role not in ("user", "assistant"):
        return None
    c = m.get("content")
    if isinstance(c, list):
        has_img = any(isinstance(p, dict) and p.get("type") == "image_url" for p in c)
        if has_img and vision_ok:
            # 视觉模型：保留 list 结构，只留 text / image_url 两种合法 part
            cleaned = []
            _dropped_img = 0
            for p in c:
                if not isinstance(p, dict):
                    continue
                t = p.get("type")
                if t == "text":
                    if p.get("text", "").strip():
                        cleaned.append(p)
                elif t == "image_url":
                    # v4.102 fix6：统一重编码图片为 RGB JPEG（兜底所有来源）
                    # v4.175.0：**解不开的图必须丢掉** —— 一张坏图会让整个请求被判
                    # 「unsupported image」400，且该会话此后每次带图都失败。
                    u = (p.get("image_url") or {}).get("url", "")
                    _nu = _normalize_image_dataurl(u)
                    if _nu:
                        cleaned.append({"type": "image_url",
                                        "image_url": {"url": _nu}})
                    else:
                        _dropped_img += 1
            # v4.175.0：占位要在 `if cleaned` **之前**补 —— 否则"整条消息只有图、
            # 且图全被丢掉"时会掉到下面的纯文本分支，退化成一句 `[图片]`，
            # 既看不出发生了什么，也丢掉了"这里原本有图"的信息。
            if _dropped_img and not any(
                    isinstance(x, dict) and x.get("type") == "text"
                    for x in cleaned):
                cleaned.insert(0, {"type": "text",
                                   "text": "[图片无法解析，已忽略]"})
            if cleaned:
                return {"role": role, "content": cleaned}
        # 非视觉（默认或视觉模型但无图）：归一化为纯文本
        c = _flatten_text_content(c)
        if has_img:
            c = (c + "\n[图片]") if c else "[图片]"
    if not isinstance(c, str) or not c.strip():
        # v4.168.2：纯工具调用的 assistant（content 为空）也要放行 ——
        # 它可能是思考模式的 tool_calls 载体，丢掉会让配对断裂（400）。
        if role == "assistant" and m.get("tool_calls"):
            sm = {"role": role, "content": ""}
            if "reasoning_content" in m:
                sm["reasoning_content"] = m.get("reasoning_content") or ""
            return sm
        return None
    sm = {"role": role, "content": c}
    # v4.168.2：`reasoning_content` 必须原样带回去（思考模式硬要求，缺则 400）。
    # 空串也算"带上"（实测通过），所以这里不做 truthy 判断。
    if "reasoning_content" in m:
        sm["reasoning_content"] = m.get("reasoning_content") or ""
    return sm
def _repair_tool_pairs(msgs):
    """v4.125 N-01 内部：修复 tool_calls/tool 配对，杜绝 API 400 死局。

    - 孤儿 tool 消息（前面无对应 assistant.tool_calls）→ 丢弃；
    - assistant.tool_calls 未全部配到 tool 结果 → 只留配到的；
      一个都没配到 → 剥掉 tool_calls 只留正文，正文也空则整条丢弃。
    """
    # 先收集所有 assistant 声明过的 tool_call_id——tool 消息只有配得上
    # 其中之一才算"有主"（不能拿 tool 消息自己的 id 集合自证，孤儿会恒真）。
    need = set()
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                if tc.get("id"):
                    need.add(tc["id"])
    out = []
    for m in msgs:
        if m.get("role") == "tool":
            if m.get("tool_call_id") in need:
                out.append(m)
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = [tc for tc in (m.get("tool_calls") or [])
                   if tc.get("id") in _tool_ids_present(msgs)]
            m = dict(m)
            if tcs:
                m["tool_calls"] = tcs
            else:
                m.pop("tool_calls", None)
                if not str(m.get("content") or "").strip():
                    continue
        out.append(m)
    return out
def _tool_ids_present(msgs):
    """当前消息序列里实际存在的 tool 结果 id 集合。"""
    return {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
def _api_error_text(exc, limit=800):
    """从 HTTP 错误里读出 **API 的真实报错文本**（v4.168.2）。

    为什么必须读：`str(HTTPError)` 只有「HTTP Error 400: Bad Request」这一句，
    API 真正的原因全在响应体里。此前出站 400 时日志只有那句废话，
    「为什么 400」完全无从查 —— 本次事故就是靠它才定位到 reasoning_content。
    `HTTPError.read()` 只能读一次，读完即丢弃（我们要重试就重新发新请求）。
    """
    try:
        body = exc.read()
    except Exception:
        return ""
    try:
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", "replace")
    except Exception:
        pass
    return str(body or "")[:limit]
def _attach_api_body(exc, limit=800):
    """把 API 真实报文挂到异常对象上（v4.175.0）。

    为什么需要：`HTTPError.read()` **只能读一次**，而 `_api_error_text` 在日志那层
    已经把正文读掉；等异常上抛到上层（agent 弹给用户）时，报文已经没了，
    用户只看到「HTTP Error 400: Bad Request」这种毫无信息量的句子
    （实测事故：真正的 `unsupported image` 只躺在 debug.log 里，界面啥都不说）。

    做法：**读它的那一处顺手挂到异常上**，上层用 `getattr(e, "_api_body", "")` 取。
    """
    try:
        _b = _api_error_text(exc, limit=limit)
        if _b:
            try:
                exc._api_body = _b
            except Exception:
                pass
        return _b
    except Exception:
        return ""
def _is_thinking_channel(base_url, model):
    """该通道是否处于「思考模式」——即本轮必须回传 `reasoning_content`。

    实测（2026-09-27，api.deepseek.com / deepseek-flash）：
      只带 role/content 的 assistant 消息 → 400
        「The `reasoning_content` in the thinking mode must be passed back to the API.」
      带 `reasoning_content`（**空串也算**）→ 200。
    所以只要走思考通道，出站前必须给每条 assistant 消息补上该字段。

    刻意收窄到"确实要求它"的通道（DeepSeek 官方 + 名字里带 reason/think 的模型），
    避免给不需要的通道塞未知字段。
    """
    b = (base_url or "").lower()
    m = (model or "").lower()
    if "api.deepseek.com" in b:
        return True
    if re.search(r"(reasoner|reasoning|thinking|think|-r1)", m):
        return True
    return False
def _ensure_reasoning_content(messages, required=True):
    """思考模式下，给每条 assistant 消息补上 `reasoning_content`（缺则补空串）。

    只补不改：返回**新列表**，原对象一字不动（AgentWorker 靠 `self.messages`
    的对象身份做 `_seq` 回写对齐，绝不能就地改）。
    """
    if not required:
        return messages
    out = []
    for m in (messages or []):
        if (isinstance(m, dict) and m.get("role") == "assistant"
                and "reasoning_content" not in m):
            m = {**m, "reasoning_content": ""}
        out.append(m)
    return out
def _fit_history_to_budget(msgs, budget_chars, min_keep=1):
    """v4.177.0：按**字符预算**从最旧开始整条丢弃，直到总量装得下。

    为什么要有它：既有的 `max_history` 是**按条数**截断的 —— 条数相同、体量可以差
    几十倍：30 条纯文本 ≈ 几十 KB，30 条带图/带工具结果的消息能到几百 KB。
    实测事故：一条带 3 张图的历史让单次 payload 到 **264KB**。条数闸管不住这种情况。

    ⚠️ **刻意用「字符数」而不是真 token**：真 token 要引 tokenizer（新依赖 + 打包体积
    + 版本漂移），而中英混排下字符数已经是个够用的代理（中文约 1 字符 ≈ 0.6~1 token）。
    **宁可估得粗，也不引新依赖 —— 好维护优先。**

    ⚠️ **不自己写配对逻辑**：整条丢完可能切断 assistant.tool_calls ↔ tool 的配对，
    交给既有的 `_repair_tool_pairs` 收尾（分两份实现迟早漂移）。

    规则：
      · `budget_chars <= 0` → 关闭，原样返回（默认行为不变，可逆）
      · 从**最旧**开始丢；永远保住最后 `min_keep` 条（默认 1 = 本轮提问，
        它再怎么大也不能丢，否则等于答非所问）
      · 单条体量 = 序列化后的字符数（图上 base64 占多少就算多少，与上线的量级一致）

    返回 `(kept, dropped_count, kept_chars)`。纯函数，不改传入的 list。
    """
    msgs = list(msgs or [])
    budget = int(budget_chars or 0)
    if budget <= 0 or not msgs:
        return msgs, 0, 0
    sizes = [len(json.dumps(m, ensure_ascii=False)) for m in msgs]
    total = sum(sizes)
    keep_from = max(0, len(msgs) - max(1, int(min_keep)))
    i = 0
    while total > budget and i < keep_from:
        total -= sizes[i]
        i += 1
    if i == 0:
        return msgs, 0, total
    return msgs[i:], i, total
# ============================================================
# v4.224：单条消息硬上限（P2 单条消息预算）
# ============================================================
# 为什么要有它：v4.177 的字符预算闸 `_fit_history_to_budget` 是**整条丢**——
# 为了不丢掉本轮提问，它刻意保住最后 `min_keep` 条。于是：一条 264KB 的巨消息
# （实测事故）只要它是最后一条，就**绕过全部预算**——预算闸形同虚设。
# 本层补的是「单条内部」的上限：**最后一条照样截断内容，只是不整条丢**。
#
# 各维度 0 = 该维度关闭；传空 dict = 全部关闭（可逆）。
MSG_BUDGET_DEFAULTS = {
    # 单条正文（user/assistant 文本、vision 里的 text part）上限
    "text_max_chars": 12000,
    # 单条工具结果上限（Agent 长循环里一条结果能到几十 KB）
    "tool_result_max_chars": 12000,
    # 单条 assistant.tool_calls 里 arguments 的上限
    "args_max_chars": 8000,
    # 单条消息最多带几张图
    "max_images_per_msg": 4,
    # 单张图的 base64 长度上限（超了直接丢图，不截断——截断的图是废字节）
    "max_image_chars": 900000,
}

_TRUNC_SUFFIX = "\u2026[\u5df2\u622a\u65ad {n} \u5b57\u7b26]"
_IMG_DROP_NOTE = "[\u5df2\u7701\u7565 {n} \u5f20\u56fe]"

def _cap_text(s, cap):
    """把字符串截到 cap，并**留可见的截断标记**（模型得知道内容被砍过）。"""
    if not isinstance(s, str) or not cap or cap <= 0 or len(s) <= cap:
        return s, False
    n = len(s) - cap
    return s[:cap] + _TRUNC_SUFFIX.format(n=n), True

def _cap_message_to_budget(m, caps=None):
    """v4.224：对**单条**消息施加硬上限（含最后一条，见上方说明）。

    覆盖四个维度：正文 / 工具结果 / tool_calls 的 arguments / 图片（张数与单张体积）。
    只削内容、**不删消息**（删消息仍归 `_fit_history_to_budget`），所以不破坏
    assistant.tool_calls ↔ tool 的配对。

    返回 (new_msg, changed)。纯函数，不改传入对象。
    """
    if not isinstance(m, dict):
        return m, False
    caps = caps or {}
    changed = False
    out = dict(m)
    role = m.get("role")
    text_cap = int(caps.get("text_max_chars") or 0)

    # 1) 工具结果：单条 tool 消息的 content
    if role == "tool":
        c = out.get("content")
        if isinstance(c, str):
            nc, ch = _cap_text(c, int(caps.get("tool_result_max_chars") or 0))
            if ch:
                out["content"] = nc
                changed = True
        return out, changed

    # 2) 正文：纯字符串，或视觉模型的 parts list
    c = out.get("content")
    if isinstance(c, str):
        nc, ch = _cap_text(c, text_cap)
        if ch:
            out["content"] = nc
            changed = True
    elif isinstance(c, list):
        parts = []
        imgs = 0
        dropped_imgs = 0
        max_imgs = int(caps.get("max_images_per_msg") or 0)
        max_img_chars = int(caps.get("max_image_chars") or 0)
        for p in c:
            if isinstance(p, str):
                np_, ch = _cap_text(p, text_cap)
                if ch:
                    changed = True
                parts.append(np_)
                continue
            if not isinstance(p, dict):
                parts.append(p)
                continue
            if p.get("type") == "image_url":
                url = ((p.get("image_url") or {}).get("url") or "")
                if max_img_chars and len(url) > max_img_chars:
                    dropped_imgs += 1
                    changed = True
                    continue
                if max_imgs and imgs >= max_imgs:
                    dropped_imgs += 1
                    changed = True
                    continue
                imgs += 1
                parts.append(p)
                continue
            if p.get("type") == "text":
                nt, ch = _cap_text(p.get("text") or "", text_cap)
                if ch:
                    p = dict(p)
                    p["text"] = nt
                    changed = True
            parts.append(p)
        if dropped_imgs:
            parts.append({"type": "text",
                          "text": _IMG_DROP_NOTE.format(n=dropped_imgs)})
        out["content"] = parts

    # 3) assistant.tool_calls 里的 arguments（模型拼超长参数同样要封顶）
    tcs = out.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        acap = int(caps.get("args_max_chars") or 0)
        if acap > 0:
            new_tcs = []
            for tc in tcs:
                if isinstance(tc, dict):
                    fn = tc.get("function")
                    if isinstance(fn, dict):
                        a = fn.get("arguments")
                        if isinstance(a, str) and len(a) > acap:
                            fn = dict(fn)
                            fn["arguments"], _ = _cap_text(a, acap)
                            tc = dict(tc)
                            tc["function"] = fn
                            changed = True
                new_tcs.append(tc)
            out["tool_calls"] = new_tcs
    return out, changed

def _build_api_history(messages, vision_ok=False, max_history=None,
                        char_budget=None, msg_budget=None):
    """v4.125 N-01：会话保真压缩——把 session 历史构造成 API 可接受的 messages。

    与 _sanitize_msg_for_api 的区别（为什么要有这个函数）：
    后者只收 user/assistant 纯文本，丢掉全部 tool 消息和 assistant.tool_calls——
    普通聊天无伤，但 Agent 断点续跑/「继续」时模型因此看不到任何工具调用记录，
    等于失忆重干（重复调工具、重复扣费、编造进度）——v4.108 花大力气建的
    checkpoint 机制（快照/心跳/paused 状态机）全部空转。

    保真规则：
    1. 保留 user / assistant / tool 三种 role；assistant.tool_calls 原样保留；
    2. 排除 system / _internal（nudge、伪造工具指令等由回写端保证不入 session）；
    3. 配对修复（见 _repair_tool_pairs）——H-01/H-02/H-03 修过的 400 死局不回潮；
    4. 截断按消息条数，截断后再修一遍配对（截断可能把 assistant 与 tool 结果
       切成两半，孤儿立即剥离）；
    5. 只输出 role/content/tool_calls/tool_call_id 四个白名单字段——session
       里的 _seq 等存储字段绝不能发给 API。
    """
    cleaned = []
    for m in (messages or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        if m.get("_internal"):
            continue
        if role == "tool":
            c = m.get("content")
            c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False) if c else ""
            cleaned.append({"role": "tool",
                            "tool_call_id": str(m.get("tool_call_id", "")),
                            "content": c})
            continue
        # user / assistant：content 归一化复用 _sanitize（视觉保留/文本展平）
        sm = _sanitize_msg_for_api(m, vision_ok=vision_ok)
        if not sm:
            # 纯工具调用（正文为空）的 assistant 在 sanitize 里会被丢——这里保住
            if role == "assistant" and m.get("tool_calls"):
                sm = {"role": "assistant", "content": ""}
            else:
                continue
        if role == "assistant" and m.get("tool_calls"):
            sm = dict(sm)
            sm["tool_calls"] = [tc for tc in m["tool_calls"]
                                if isinstance(tc, dict) and tc.get("id")]
        # v4.168.2：白名单原为「role/content/tool_calls/tool_call_id」四项，
        # 但 `reasoning_content` 是**思考模式的硬要求**（DeepSeek 官方实测：
        # assistant 消息缺该字段 → 400「must be passed back to the API」）。
        # 把它加进白名单：有就带着，没有就由出站门 `_ensure_reasoning_content` 补空串。
        if role == "assistant":
            sm = dict(sm)
            sm.setdefault("reasoning_content", m.get("reasoning_content") or "")
        cleaned.append(sm)
    # v4.224：单条硬上限（**含最后一条**）—— 整条丢弃保不住的那条巨消息，
    # 在这一层被削内容。只削不删，配对不受影响，后面照旧修一遍。
    if msg_budget:
        _new_cleaned = []
        _capped = 0
        for _m in cleaned:
            _nm, _ch = _cap_message_to_budget(_m, msg_budget)
            if _ch:
                _capped += 1
            _new_cleaned.append(_nm)
        cleaned = _new_cleaned
        if _capped:
            try:
                log.info("单条消息超预算已裁剪：%d 条", _capped)
            except Exception:
                pass
    cleaned = _repair_tool_pairs(cleaned)
    if max_history and len(cleaned) > int(max_history):
        cleaned = _repair_tool_pairs(cleaned[-int(max_history):])
    # v4.177.0：条数闸之后再过一道**字符预算**闸（兜住"条数不多但每条巨大"的灾难：
    # 带图历史、Agent 长循环的工具结果）。同样，截断后必须重修配对。
    if char_budget and int(char_budget) > 0:
        cleaned, _dropped, _chars = _fit_history_to_budget(cleaned, int(char_budget))
        if _dropped:
            cleaned = _repair_tool_pairs(cleaned)
            try:
                log.info("历史按字符预算裁剪：丢最旧 %d 条，留 %d 条 / %d 字符（预算 %d）",
                         _dropped, len(cleaned), _chars, int(char_budget))
            except Exception:
                pass
    return cleaned
