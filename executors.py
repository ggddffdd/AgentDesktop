# -*- coding: utf-8 -*-
"""第 5 步执行器：把画布节点从「stub 只登记阶段路径」升级为「真正产出文件并落盘」。

设计原则：
  * 纯本地、无外部网络 / API 依赖 —— 判据可在无头沙箱运行。
  * 可插拔：CanvasGraph.use_real_executors(asset_root, inpaint_fn) 按 node_type
    分配真实执行器；gen_image 走「真实生图 + 局部编辑落盘」主链路，其余类型走
    「落盘占位」passthrough，保持 run 链路完整、资产可登记。
  * inpaint（AI 局部重绘）默认诚实抛 UnsupportedEditMode（与阶段 C 一致），运行时
    由外部注入 inpaint_fn（如 Agnes 图生图 / OpenHuman 重绘）接入真模型。
  * gen_image 产出流程：PIL 本地生成源图 → 落盘 {asset_root}/image/{node}_image.png
    → 若有 local_edits，调 image_local_edit.apply_local_edits 渲染编辑图
    → 落盘 {node}_image_edited.png（inpaint 经 inpaint_fn）→ 把编辑图作为端口产出 path。

复用 image_local_edit（阶段 C 引擎）与 canvas_graph（AssetRef / 类型常量）。
"""

import os

import numpy as np
from PIL import Image, ImageDraw

import image_local_edit as il
from image_local_edit import UnsupportedEditMode
from canvas_graph import (AssetRef, NODE_TYPES, assess_asset, ASSET_REAL)


# --------------------------------------------------------------------------
# 文件 / 路径辅助
# --------------------------------------------------------------------------
_EXT = {
    "image": ".png", "clip": ".mp4", "video": ".mp4", "final": ".mp4",
    "prompt": ".txt", "script": ".txt", "audio": ".mp3", "data": ".json",
}


def stage_path(asset_root, kind, node_id, port, ext=None):
    """真实落盘路径：{asset_root}/{kind}/{node_id}_{port}.{ext}（目录自动创建）。

    `ext` 显式传入时用它 —— 占位物走 `.node-placeholder`（见 passthrough_executor）：
    它的内容只是一段文本 manifest，若起名成 .mp4/.png，系统与外部播放器/预览器
    会把它当真媒体打开，然后报一个与真实问题毫无关系的解码错误。
    """
    d = os.path.join(asset_root, kind)
    os.makedirs(d, exist_ok=True)
    ext = ext or _EXT.get(kind, ".bin")
    return os.path.join(d, f"{node_id}_{port}{ext}")


# 占位物专用扩展名：不是任何媒体类型，一眼能看出「这不是成品」
PLACEHOLDER_EXT = ".node-placeholder"
# meta 里的内容类型标记（程序判定用；扩展名是给人看的）
PLACEHOLDER_CONTENT_TYPE = "placeholder"


# 执行器产出校验：kind → 允许的扩展名（比 stage_path 的 _EXT 宽松 —— 例如 video 允许 .gif）
_OUTPUT_EXTS = {
    "image": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"),
    "clip":  (".mp4", ".mov", ".webm", ".mkv", ".avi"),
    "video": (".mp4", ".mov", ".webm", ".mkv", ".gif"),
    "audio": (".mp3", ".wav", ".m4a", ".aac", ".flac"),
}


def validate_output(path, expected_kind, asset_root=None):
    """执行器产出校验：非空路径 + 文件存在 + 非空 + 扩展名相符 + 位于资产目录内。

    不合法一律抛 UnsupportedEditMode —— 宁可节点 failed，也不登记「假完成」的无效资产
    （外部回调可能返回错误文本、不存在路径或越目录路径）。返回规整后的路径。
    """
    if not isinstance(path, str) or not path.strip():
        raise UnsupportedEditMode("执行器未返回有效输出路径: %r" % (path,))
    p = path.strip()
    if not os.path.isfile(p):
        raise UnsupportedEditMode("执行器输出文件不存在: %s" % p)
    try:
        if os.path.getsize(p) <= 0:
            raise UnsupportedEditMode("执行器输出文件为空: %s" % p)
    except OSError as e:
        raise UnsupportedEditMode("执行器输出无法读取: %s (%s)" % (p, e))
    if asset_root:
        root = os.path.abspath(asset_root)
        try:
            if os.path.commonpath([os.path.abspath(p), root]) != root:
                raise UnsupportedEditMode("执行器输出越出资产目录: %s" % p)
        except ValueError:
            raise UnsupportedEditMode("执行器输出越出资产目录: %s" % p)
    exts = _OUTPUT_EXTS.get(expected_kind)
    if exts and not p.lower().endswith(exts):
        raise UnsupportedEditMode(
            "执行器输出扩展名与 %s 不符（应为 %s）: %s"
            % (expected_kind, "/".join(exts), os.path.basename(p)))
    return p


def _png_to_array(path):
    img = Image.open(path).convert("RGB")
    return np.asarray(img, dtype=np.uint8)


def _array_to_png(arr, path):
    a = np.asarray(arr, dtype=np.float64)
    if a.size and a.max() > 1.0:
        a = a / 255.0
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype("uint8"))
    img.save(path)


def _seed_hue(prompt):
    """按 prompt 文本稳定生成一个色相（占位图可复现 + 好看）。"""
    s = (prompt or "").strip()
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xffffffff
    return h % 360


def _make_source_image(node, path, size=(480, 270)):
    """用 PIL 生一张源图（水平渐变 + prompt 文字标注），落盘到 path。"""
    w, h = size
    hue = _seed_hue(node.config.get("prompt") if isinstance(node.config, dict) else None)
    base = Image.new("RGB", (w, h), (30, 30, 40))
    px = base.load()
    for x in range(w):
        t = x / max(1, w - 1)
        # 色相由 prompt 决定，明度做水平渐变，保证左右两侧像素明显不同（利于局部编辑验证）
        r = int(40 + 180 * t) % 256
        g = (hue * 2) % 256
        b = int(60 + 120 * (1 - t)) % 256
        for y in range(h):
            px[x, y] = (r, g, b)
    draw = ImageDraw.Draw(base)
    prompt = (node.config.get("prompt") if isinstance(node.config, dict) else "") or node.id
    try:
        draw.text((10, 10), prompt[:40], fill=(255, 255, 255))
    except Exception:
        pass
    base.save(path)
    return path


# --------------------------------------------------------------------------
# gen_image 真实执行器（含局部编辑落盘）
# --------------------------------------------------------------------------
def gen_image_executor(node, asset_root, inpaint_fn=None, text2img_fn=None, graph=None):
    """gen_image 节点的真实执行器。返回 (state) -> dict 闭包。

    闭包捕获 node，run 时实时读 node.config["local_edits"]（最新值）。
    源图来源（v4.211.11）：text2img_fn 注入且 prompt 非空 → Agnes 纯文生图
    （异常诚实 failed，不静默回落）；否则本地 PIL 渐变占位（离线兜底）。
    prompt 解析：① 节点自身 config（属性面板直填，显式优先）② 上游
    prompt 端口（数据边 src.prompt → img.prompt，示例图形态）。
    """
    out_ports = [p for p, pt in node.outputs.items() if pt.port_type == "image"]

    def _resolve_prompt():
        cfg = node.config if isinstance(node.config, dict) else {}
        p = (cfg.get("prompt") or "").strip()
        if p:
            return p
        if graph is not None:
            for e in graph.data_edges:
                if e.to_node == node.id and e.to_port == "prompt":
                    up = graph.nodes.get(e.from_node)
                    if up is not None:
                        p = ((up.config or {}).get("prompt") or "").strip()
                        if p:
                            return p
        return ""

    def _exec(state):
        src_path = stage_path(asset_root, "image", node.id, "source")
        prompt = _resolve_prompt()
        if text2img_fn is not None and prompt:
            # 真·AI 文生图：注入即走网络；异常向上抛 → TaskGraph 吸收成节点
            # failed（诚实失败），绝不静默回落占位图 —— 网络错必须让人看见。
            arr = text2img_fn(prompt)
            _array_to_png(arr, src_path)
        else:
            # 未注入（无 key / 无 prompt）→ 本地 PIL 渐变占位（离线兜底）。
            _make_source_image(node, src_path)
        arr = _png_to_array(src_path).astype(np.float64) / 255.0
        edits = (node.config or {}).get("local_edits") or []
        if edits:
            out_arr = il.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)
            out_path = stage_path(asset_root, "image", node.id, "edited")
            _array_to_png(out_arr, out_path)
        else:
            out_path = src_path
        out_path = validate_output(out_path, "image", asset_root)
        produced = None
        for p in out_ports:
            ref = AssetRef(
                kind="image",
                name="%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type), node.id),
                path=out_path,
                meta={"tags": [node.node_type, "image"],
                      "project": "canvas_runtime",
                      "task": "节点 %s 生图（第5步执行器）" % node.id},
            )
            node.out_assets[p] = ref
            produced = ref
        return {
            "node": node.id, "type": node.node_type,
            "out_kind": "image", "source": src_path,
            "out_path": out_path, "applied": len(edits),
            "produced": produced.to_dict() if produced else None,
        }
    return _exec


# --------------------------------------------------------------------------
# 非图片节点：落盘占位 passthrough（保持 run 链路完整 + 资产可登记）
# --------------------------------------------------------------------------
def passthrough_executor(node, asset_root):
    """非图片节点的真实落盘占位：写一个小 manifest 文件 + 登记 AssetRef。"""

    def _exec(state):
        outs = []
        for p, pt in node.outputs.items():
            kind = pt.port_type
            if kind not in _EXT:
                kind = "data"
            path = stage_path(asset_root, kind, node.id, p, ext=PLACEHOLDER_EXT)
            with open(path, "w", encoding="utf-8") as f:
                f.write("# canvas runtime placeholder\nnode=%s\nport=%s\nkind=%s\n"
                        % (node.id, p, kind))
            ref = AssetRef(
                kind=kind,
                name="%s:%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type),
                                    node.id, p),
                path=path,
                # Wave D（复审 #7）：passthrough 写的是一个「流程占位物」——
                # 内容只是几行 manifest。
                # 复审第 3 条：扩展名不再借真实媒体后缀（.mp4/.png），
                # 改为 .node-placeholder，避免外部程序/媒体预览器尝试打开它；
                # 同时在 meta 里给出 content_type 供程序判定（扩展名是给人看的，
                # 元数据才是给下游代码看的）。
                placeholder=True,
                meta={"tags": [node.node_type, kind],
                      "project": "canvas_runtime",
                      "content_type": PLACEHOLDER_CONTENT_TYPE,
                      "task": "节点 %s 产出 %s（第5步 passthrough）" % (node.id, kind)},
            )
            node.out_assets[p] = ref
            outs.append(ref.to_dict())
        return {"node": node.id, "type": node.node_type, "outputs": outs}
    return _exec


# --------------------------------------------------------------------------
# 上游资产收集（供 gen_video / promo_fx 取参考图 / 动效帧）
# --------------------------------------------------------------------------
def _upstream_asset_paths(graph, node, want_port_type, _seen=None):
    """递归收集 node 上游所有 kind==want_port_type 的资产路径。

    依赖 task_graph 的拓扑序（上游先 completed、out_assets 先落盘），因此运行时稳定。
    """
    if graph is None or node is None:
        return []
    _seen = _seen if _seen is not None else set()
    found = []
    for e in getattr(graph, "data_edges", []) or []:
        if getattr(e, "to_node", None) != node.id:
            continue
        up = graph.nodes.get(e.from_node)
        if up is None:
            continue
        ref = (up.out_assets or {}).get(e.from_port)
        if ref is not None and getattr(ref, "kind", "") == want_port_type \
                and getattr(ref, "path", ""):
            if ref.path not in found:
                found.append(ref.path)
        if e.from_node not in _seen:
            _seen.add(e.from_node)
            found.extend(_upstream_asset_paths(graph, up, want_port_type, _seen))
    return found


# --------------------------------------------------------------------------
# 上游选择语义（Wave D · 复审 #8）：谁当参考帧，必须说得清、且可复现
# --------------------------------------------------------------------------
def _direct_upstream(graph, node, want_kind):
    """直接上游里 kind 相符的资产，按 node 的**输入端口声明顺序**返回。

    每项：{"path", "port"(下游输入端口名), "from"("上游节点.端口")}。

    为什么强调顺序：旧实现遍历 data_edges 取第一个 —— 谁在前取决于「这条边是什么
    时候连的」，同一个工程文件换个连线次序就能得到不同的参考帧，用户无法预期也
    无法复现。端口声明顺序是写进工程文件的，重开、重导入都一致。
    """
    out = []
    if graph is None or node is None:
        return out
    for port_name in (node.inputs or {}):
        for e in getattr(graph, "data_edges", []) or []:
            if e.to_node != node.id or e.to_port != port_name:
                continue
            up = graph.nodes.get(e.from_node)
            ref = (up.out_assets or {}).get(e.from_port) if up is not None else None
            if ref is not None and getattr(ref, "kind", "") == want_kind \
                    and getattr(ref, "path", ""):
                # 带上 ref 本体（2026-10-04）：消费侧要按统一入口判「这个产物能不能
                # 当真素材用」（占位物路径是编出来的，只看 kind + path 挡不住）。
                out.append({"path": ref.path, "port": e.to_port,
                            "from": "%s.%s" % (e.from_node, e.from_port),
                            "ref": ref})
    return out


def _ref_by_path(graph, path):
    """按路径反查 AssetRef。

    递归上游通道（`_upstream_asset_paths`）只传路径、拿不到 ref，而消费侧要按
    统一入口判「这个产物能不能当真素材用」。不反查的话，像 promo 这种**帧全来自
    间接上游**的节点，过滤就成了死代码（每个 f["ref"] 都是 None，一律放行）。
    """
    for _nid, n in (getattr(graph, "nodes", {}) or {}).items():
        for _p, a in (getattr(n, "out_assets", {}) or {}).items():
            if isinstance(a, AssetRef) and a.path == path:
                return a
    return None


def _upstream_assets(graph, node, want_kind):
    """收集上游 kind 相符的资产 -> [{"path", "port", "from", "ref"}]，顺序**确定**。

    顺序规则（写死，不依赖连线时序）：
      1. 直接上游按输入端口声明顺序；
      2. 再补递归上游（`_upstream_asset_paths` 的老行为），兼顾间接来源；
      3. 同一路径只保留靠前的那次出现。
    """
    if graph is None or node is None:
        return []
    found, seen = [], set()
    for c in _direct_upstream(graph, node, want_kind):
        if c["path"] in seen:
            continue
        seen.add(c["path"])
        found.append(c)
    for p in _upstream_asset_paths(graph, node, want_kind):
        if p in seen:
            continue
        seen.add(p)
        # 递归上游走的是只带路径的老通道 —— 反查回 ref，让消费侧的四态判定同样生效。
        found.append({"path": p, "port": "", "from": "递归上游",
                      "ref": _ref_by_path(graph, p)})
    return found


def select_upstream(graph, node, want_kind):
    """选「哪个上游资产当参考」，返回 (path, desc)；没有则 (None, "")。

    优先级（显式 > 隐式；隐式也必须确定）：
      1. node.config["ref_source"] = "上游节点.端口" —— 显式指定。指不到就**诚实抛错**，
         不静默回落：用户点名要 A 却拿到 B，比直接报错难查得多。
      2. node.config["ref_port"] = "输入端口名" —— 按端口指定，该端口没货同样抛错。
      3. 按输入端口声明顺序取第一个，来源（desc）回显出去，便于 UI/日志核对。

    旧行为是「默默取第一个」——多入节点（如成片并合 main/promo）到底用了哪一路
    谁也说不清，出片不对时无从排查。这里把选择变成显式契约。
    """
    cfg = getattr(node, "config", None) or {}
    explicit = str(cfg.get("ref_source") or "").strip()
    if explicit:
        for c in _direct_upstream(graph, node, want_kind):
            if c["from"] == explicit:
                return c["path"], c["from"]
        raise UnsupportedEditMode(
            "ref_source=%r 指不到 kind=%s 的上游资产（可用: %s）"
            % (explicit, want_kind,
               [c["from"] for c in _direct_upstream(graph, node, want_kind)] or "无"))
    only_port = str(cfg.get("ref_port") or "").strip()
    cands = _upstream_assets(graph, node, want_kind)
    if only_port:
        for c in cands:
            if c["port"] == only_port:
                return c["path"], c["from"]
        raise UnsupportedEditMode(
            "ref_port=%r 上没有 kind=%s 的上游资产（可用端口: %s）"
            % (only_port, want_kind, [c["port"] for c in cands if c["port"]] or "无"))
    if not cands:
        return None, ""
    return cands[0]["path"], cands[0]["from"]


# --------------------------------------------------------------------------
# gen_video 真实执行器（接 Agnes 视频，或注入的 video_fn）
# --------------------------------------------------------------------------
def gen_video_executor(node, asset_root, inpaint_fn=None, video_fn=None, graph=None):
    """gen_video 节点的真实执行器：取上游 image 作参考首帧，调 video_fn 生成 clip。"""
    out_ports = [p for p, pt in node.outputs.items() if pt.port_type == "clip"]

    def _exec(state):
        # Wave D #8：参考帧走显式选择（config.ref_source / ref_port 优先，
        # 否则按输入端口声明顺序），不再「默默取递归收集的第一个」。
        src_path, src_from = select_upstream(graph, node, "image")
        prompt = (node.config or {}).get("prompt") or "动态展示画面内容"
        if video_fn is None:
            raise UnsupportedEditMode("gen_video 需 video_fn（未注入 Agnes 视频回调）")
        out_path = video_fn(node, asset_root, src_path, prompt)
        out_path = validate_output(out_path, "clip", asset_root)
        produced = None
        for p in out_ports:
            ref = AssetRef(
                kind="clip",
                name="%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type), node.id),
                path=out_path,
                meta={"tags": [node.node_type, "clip"],
                      "project": "canvas_runtime",
                      "task": "节点 %s 生视频（第5步执行器）" % node.id},
            )
            node.out_assets[p] = ref
            produced = ref
        return {"node": node.id, "type": node.node_type, "out_kind": "clip",
                "out_path": out_path, "src_image": src_path, "src_from": src_from,
                "produced": produced.to_dict() if produced else None}
    return _exec


# --------------------------------------------------------------------------
# promo_fx 真实执行器（促销动效：接注入的 motion_fn，默认本地 PIL GIF）
# --------------------------------------------------------------------------
def promo_fx_executor(node, asset_root, inpaint_fn=None, video_fn=None, graph=None,
                      motion_fn=None):
    """promo_fx 节点的真实执行器：取上游 image 资产帧，调 motion_fn 生成促销动效。

    motion_fn(src_image_paths, out_path, params) -> out_path；未注入时回落本地
    PIL 实现（零网络、零 ffmpeg），保证「促销动效」随时可跑通。
    """
    if motion_fn is None:
        from agnes_bridge import pil_promo_motion  # 本地零网络降级
        motion_fn = pil_promo_motion
    out_ports = [p for p, pt in node.outputs.items() if pt.port_type == "video"]

    def _exec(state):
        # Wave D #8：动效帧来源同样确定化（输入端口声明顺序 + 递归补齐），
        # 并把实际用的来源回显出去（旧实现只给路径，出片不对时无从排查）。
        #
        # 消费侧四态过滤（2026-10-04）：只把**真实**产物当帧喂给 motion_fn。
        # 占位物的路径是编出来的（stub）或内容只有几行 manifest 文本（passthrough），
        # 旧实现照单全收 —— 本地 PIL 实现会在 Image.open 失败后静默跳过（帧没了却
        # 没有任何提示），换成 ffmpeg 实现则可能报一个与真因无关的解码错误。
        frames = _upstream_assets(graph, node, "image")
        usable, skipped = [], {"placeholder": 0, "stale": 0, "invalid": 0}
        for f in frames:
            ref = f.get("ref")
            if ref is not None:
                validity, _why = assess_asset(ref, asset_root)
                if validity != ASSET_REAL:
                    skipped[validity] = skipped.get(validity, 0) + 1
                    continue
            usable.append(f)
        img_paths = [f["path"] for f in usable]
        out_path = os.path.join(asset_root, "video", "%s_promo" % node.id)  # 后缀由 motion_fn 决定
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        params = (node.config or {}).get("motion_params") or {}
        res = motion_fn(img_paths, out_path, params)
        res = validate_output(res, "video", asset_root)
        produced = None
        for p in out_ports:
            ref = AssetRef(
                kind="video",
                name="%s:%s:%s" % (NODE_TYPES.get(node.node_type, node.node_type),
                                    node.id, p),
                path=res,
                meta={"tags": [node.node_type, "video", "promo"],
                      "project": "canvas_runtime",
                      "task": "节点 %s 促销动效（第6步）" % node.id},
            )
            node.out_assets[p] = ref
            produced = ref
        return {"node": node.id, "type": node.node_type, "out_kind": "video",
                "out_path": res, "frame_count": len(img_paths),
                "frames_from": [f["from"] for f in usable],
                "skipped_frames": skipped,
                "produced": produced.to_dict() if produced else None}
    return _exec


# --------------------------------------------------------------------------
# 注册表 + 装配
# --------------------------------------------------------------------------
# node_type -> factory(node, asset_root, inpaint_fn=None, video_fn=None, graph=None, text2img_fn=None)
DEFAULT_EXECUTORS = {
    "gen_image": lambda node, asset_root, inpaint_fn=None, video_fn=None, graph=None, motion_fn=None, text2img_fn=None:
        gen_image_executor(node, asset_root, inpaint_fn, text2img_fn=text2img_fn, graph=graph),
    "gen_video": lambda node, asset_root, inpaint_fn=None, video_fn=None, graph=None, motion_fn=None, text2img_fn=None:
        gen_video_executor(node, asset_root, inpaint_fn, video_fn, graph),
    "promo_fx": lambda node, asset_root, inpaint_fn=None, video_fn=None, graph=None, motion_fn=None, text2img_fn=None:
        promo_fx_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn),
}


def build_executor(node, asset_root, inpaint_fn=None, video_fn=None, graph=None, motion_fn=None, text2img_fn=None):
    """按 node_type 选真实执行器工厂；未知类型回落 passthrough。"""
    factory = DEFAULT_EXECUTORS.get(node.node_type)
    if factory is not None:
        return factory(node, asset_root, inpaint_fn, video_fn, graph, motion_fn, text2img_fn)
    return passthrough_executor(node, asset_root)


def apply_real_executors(graph, asset_root, inpaint_fn=None, video_fn=None, motion_fn=None, text2img_fn=None):
    """给整张图的所有节点装上真实执行器（run 前调用）。返回被替换的节点 id 列表。"""
    replaced = []
    for nid, node in graph.nodes.items():
        node.executor = build_executor(node, asset_root, inpaint_fn, video_fn, graph, motion_fn, text2img_fn)
        replaced.append(nid)
    return replaced
