"""UI-free 多镜视频导演流水线（从 video-agent 抽取内核）。

把桌面端 video-agent「一条龙导演台」的核心能力抽成无 UI 的内核，
可被小臭的导演台面板直接调用。所有与用户的交互（日志/状态/确认/完成）
都通过 callbacks 回调，便于嵌进 PySide6 面板或独立测试。

网络层：同步 urllib 直连 Agnes（对话 + 视频），与 小臭 tool_video_gen 一致；
合成层：subprocess 调用 ffmpeg。

callbacks = {
    "log":    lambda text: ...,
    "status": lambda text, err=False: ...,
    "approve": lambda stage, summary -> bool,   # 缺省则走 auto_approve
    "finish": lambda ok, msg, output_path: ...,
}
"""
import os
import re
import json
import sys
import time
import base64
import shutil
import subprocess
import urllib.request

from tools import (_agnes_creds, _build_video_prompt, tool_video_gen,
                   tool_image_gen, PRODUCTS_DIR)
# 参考图硬上限：Agnes reference 模式实测 6 张报 400，固定 5。
# （原为 tools 导出，视频统一内核后归 video_pipeline 自管，避免悬空依赖 tools）
AGNES_MAX_REF_IMAGES = 5
import vision_qc as vq

# 画面风格短语（与 video-agent 一致）
STYLE_PROMPTS = {
    "realistic": ("photorealistic, hyper-real, cinematic live-action, natural lighting, "
                  "high detail, 8k, sharp focus, realistic skin texture"),
    "cinematic": ("cinematic film still, anamorphic lens, film grain, dramatic lighting, "
                  "teal and orange color grading, shallow depth of field, moody"),
    "anime": ("anime style, hand-drawn cel shading, vibrant colors, clean lineart, "
              "studio animation quality, detailed background"),
    "watercolor": ("soft watercolor painting, delicate brush strokes, paper texture, "
                   "muted pastel palette, dreamy, artistic"),
    "neon": ("cyberpunk neon, glowing signs, rain-slick streets, high contrast, "
             "magenta and cyan, Blade Runner atmosphere, volumetric fog"),
    "documentary": ("documentary photography, handheld realism, natural color, "
                    "unstyled candid, reportage, authentic"),
}

# ---------- 运镜组合器（借鉴 Seedance2.0-Storyboard-Planner）----------
# 给每镜的 en 提示词补上专业摄影语言：景别 + 运镜 + 机位。
# 转场由「尾帧接力 / 硬切」在合成阶段处理，这里只管单镜内的镜头运动。
SHOT_SIZES = [
    "extreme wide shot", "wide shot", "full shot", "medium shot",
    "medium close-up", "close-up", "extreme close-up",
]
CAM_MOVEMENTS = [
    "static locked-off shot", "slow push-in (dolly in)", "slow pull-out (dolly out)",
    "slow pan left", "slow pan right", "tilt up", "tilt down",
    "tracking shot following the subject", "slow orbit around the subject",
    "gentle handheld", "crane up reveal", "aerial drone shot", "whip pan",
]
CAM_ANGLES = [
    "eye-level", "low angle", "high angle", "slight Dutch tilt", "bird's-eye view",
]
_CAM_KEYWORDS = ("shot", "push", "pull", "pan", "tilt", "track", "orbit",
                 "handheld", "crane", "drone", "whip", "static", "dolly", "wide",
                 "close-up", "close up", "medium")

CAM_VOCAB_TEXT = (
    "【镜头语言词汇表（请在 en 中合理使用，相邻分镜的运镜要有变化，避免全程同一运动）】\n"
    "景别：" + " / ".join(SHOT_SIZES) + "\n"
    "运镜：" + " / ".join(CAM_MOVEMENTS) + "\n"
    "机位：" + " / ".join(CAM_ANGLES) + "\n"
)


def _enrich_shots_camera(shots, portrait_mode=False):
    """给非口播分镜补上运镜组合（景别+运镜+机位）。

    - 模型已在 en 里写了镜头语言 → 尊重不动；
    - 模型给了 cam 字段 → 用它的；
    - 都没给 → 按镜序轮转一套（保证相邻镜运镜不同、有电影感）。
    口播模式 / talking head 分镜不处理（画面由程序统一锁定）。
    """
    if portrait_mode:
        return shots
    for i, s in enumerate(shots):
        en = s.get("en", "")
        if not en or "talking head" in en.lower():
            continue
        cam = (s.get("cam") or "").strip()
        if not cam:
            size = SHOT_SIZES[(i + 1) % len(SHOT_SIZES)]
            move = CAM_MOVEMENTS[(i * 3 + 1) % len(CAM_MOVEMENTS)]
            angle = CAM_ANGLES[i % len(CAM_ANGLES)]
            cam = f"{size}, {move}, {angle}"
        low = en.lower()
        if not any(k in low for k in _CAM_KEYWORDS):
            en = en.rstrip(". ").strip() + ". " + cam + "."
            s["en"] = en
        s.pop("cam", None)
    return shots


# 口播硬锁模板（有本人照片时覆盖 LLM 的 en 画面提示词）
PORTRAIT_PROMPT = (
    "The exact same person from the reference photo, identical face, "
    "hairstyle, glasses and clothing, facing the camera in a fixed "
    "medium close-up talking-head shot. Only natural lip-sync mouth "
    "movements while speaking, subtle small hand gestures and slight "
    "head nods. Static camera, no camera movement, no zoom. Background "
    "stays exactly the same as the reference photo, no scene change, "
    "no other people, no props appearing, no on-screen text."
)


def sec_to_frames(sec):
    """秒 → 帧数，须满足 8n+1 且 ≤401（Agnes 视频约束），帧率 24。"""
    nf = (sec * 24 // 8) * 8 + 1
    return min(max(nf, 41), 401)


def srt_timestamp(sec):
    ms = int(round(sec * 1000))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    msec = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def split_voiceover_script(text, n):
    """📝 口播原稿直通：把用户贴的原稿一字不改切成 n 段（只切分不改写）。"""
    text = re.sub(r"\s+", " ", str(text)).strip()
    n = max(1, int(n))
    if not text:
        return []
    if n == 1:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;…])", text) if p.strip()]
    if len(parts) < n:
        finer = []
        for p in parts:
            finer.extend(q.strip() for q in re.split(r"(?<=[，,、])", p) if q.strip())
        if len(finer) >= n:
            parts = finer
    if len(parts) < n:
        seg_len = max(1, len(text) // n)
        parts = [text[i * seg_len:(i + 1) * seg_len] for i in range(n - 1)]
        parts.append(text[(n - 1) * seg_len:])
        parts = [p for p in parts if p]
        return parts
    total = sum(len(p) for p in parts)
    target = total / n
    segs, cur = [], ""
    for i, p in enumerate(parts):
        cur += p
        rest_parts = len(parts) - i - 1
        need_segs = n - len(segs) - 1
        must_cut = rest_parts == need_segs
        if len(segs) < n - 1 and rest_parts >= need_segs and (len(cur) >= target or must_cut):
            segs.append(cur)
            cur = ""
    if cur:
        segs.append(cur)
    while len(segs) > n:
        tail = segs.pop()
        segs[-1] += tail
    return segs


def find_ffmpeg():
    """找 ffmpeg：优先系统 PATH；其次冻结环境 sys._MEIPASS 下的捆绑文件；
    再退到 imageio_ffmpeg.get_ffmpeg_exe()（含 IMAGEIO_FFMPEG_EXE 环境变量）。
    找不到返回 None。

    注意：PyInstaller 冻结环境下 imageio_ffmpeg 的 __file__ 是虚拟路径，
    get_ffmpeg_exe() 据此算出的 binaries 路径对不上磁盘（_internal 里只收集了
    binaries 数据、没收集 __init__.py），会返回不存在的路径导致 WinError 2。
    所以必须显式到 sys._MEIPASS / exe 同级 _internal 下找真实捆绑文件。
    """
    # 1) 系统 PATH
    p = shutil.which("ffmpeg")
    if p and os.path.exists(p):
        return p
    # 2) 冻结环境：直接到捆绑 binaries 目录找 ffmpeg*.exe
    cand_dirs = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cand_dirs.append(os.path.join(meipass, "imageio_ffmpeg", "binaries"))
    if getattr(sys, "frozen", False):
        cand_dirs.append(os.path.join(os.path.dirname(sys.executable),
                                      "_internal", "imageio_ffmpeg", "binaries"))
    for d in cand_dirs:
        try:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.lower().startswith("ffmpeg") and fn.lower().endswith(".exe"):
                        fp = os.path.join(d, fn)
                        if os.path.isfile(fp):
                            return fp
        except Exception:
            pass
    # 3) imageio_ffmpeg 自带（含 IMAGEIO_FFMPEG_EXE 环境变量；非冻结或正确收集时有效）
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return None


def _agnes_chat(cfg, messages, model=None, temperature=0.7):
    """同步调用 Agnes 对话接口，返回助手文本。失败抛 RuntimeError。"""
    base, key = _agnes_creds(cfg)
    if not model:
        model = (((cfg.get("model_profiles") or {}).get("Agnes", {}) or {}).get("chat_model")
                 or cfg.get("video_chat_model") or "agnes-2.5-flash")
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature},
        ensure_ascii=False).encode("utf-8")
    url = base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"Agnes 对话接口失败：{e}")


class VideoPipeline:
    """多镜视频导演流水线（无 UI，回调驱动）。"""

    def __init__(self, cfg, app_dir, callbacks=None, auto_approve=True):
        self.cfg = cfg
        self.app_dir = app_dir
        self.cb = callbacks or {}
        self.auto_approve = auto_approve
        self.ffmpeg = find_ffmpeg()
        self.cancelled = False
        # 运行期状态
        self.shots = []
        self.width = 768
        self.height = 1152
        self.style_prompt = ""
        self.style_key = "realistic"
        self.relay = True
        self.transition = "black"
        self.transition_dur = 0.4
        self.portrait_mode = False
        self.with_dialogue = False
        self.ref_image_path = None
        self.ref_only = False
        self.project_dir = None
        self.trans_clip_path = None
        # 抗崩坏：人物三视图（角色锁定）+ 分镜关键帧/场景图
        self.characters = []          # [{"name","desc","views":[front,side,back]}]
        self.character_lock = ""      # 写入逐镜提示词的角色锁定描述（英文）
        self.characters_done = False
        self.keyframes = []           # 每镜一张「关键帧+场景图」路径（作首帧参照）
        self.keyframes_done = False
        # 抗崩坏 v2：多参考图（Agnes reference 模式最多 5 张）+ VLM 质检闭环
        self.scene_images = {}        # {scene_id: 环境概念图路径}（纯场景、无人物）
        self.vision_review = True     # VLM 质检开关（走 DeepSeek 视觉模型）
        self.review_notes = {}        # {shot_index: 最近一次质检的诊断文本}
        # 音频：分镜片段由 Agnes 直接生成「台词口型 + 背景音效」音轨，
        # 合成成片时优先沿用片段自带音轨（见 _merge / _probe_has_audio）；
        # 转场或极少数无音轨片段用静音轨补齐，确保 concat 每段音视频齐全。

    # ---------- 回调封装 ----------
    def log(self, text):
        (self.cb.get("log") or print)(text)

    def status(self, text, err=False):
        fn = self.cb.get("status")
        if fn:
            fn(text, err)

    def ask_approve(self, stage, summary):
        fn = self.cb.get("approve")
        if fn:
            return fn(stage, summary)
        return self.auto_approve

    def on_finish(self, ok, msg, path=None):
        fn = self.cb.get("finish")
        if fn:
            fn(ok, msg, path)

    # ---------- 分阶段公共接口（供面板一步步编排 + 每步人工确认） ----------
    def prepare(self, topic, n, duration, resolution, style_key, ref_image_path,
                portrait_mode, with_dialogue, relay, transition, transition_dur,
                burn_subtitles, passthrough_script, clip_dir=None):
        """锁定全部参数、建立工程目录、初始化运行期状态。"""
        self.topic = topic
        self.n = n
        self.duration = duration
        self.style_key = style_key
        self.style_prompt = STYLE_PROMPTS.get(style_key, "")
        self.ref_image_path = ref_image_path
        self.portrait_mode = portrait_mode
        self.with_dialogue = bool(with_dialogue) or portrait_mode
        self.relay = relay
        self.transition = transition
        self.transition_dur = transition_dur
        self.burn_subtitles = burn_subtitles
        self.passthrough_script = passthrough_script
        w, h = (int(x) for x in str(resolution).lower().split("x", 1))
        self.width, self.height = w, h
        ts = time.strftime("%Y%m%d_%H%M%S")
        if clip_dir is None:
            clip_dir = os.path.join(PRODUCTS_DIR, "视频", f"director_{ts}")
        os.makedirs(clip_dir, exist_ok=True)
        self.project_dir = clip_dir
        self.story = ""
        self.shots = []
        self.clip_paths = []
        return self.project_dir

    def gen_story(self, feedback=None):
        """第1步：生成（或直通）剧本。feedback 为修改意见（修订模式）。"""
        if self.portrait_mode and self.passthrough_script:
            self.story = self.passthrough_script
            return self.story
        self.story = self._gen_story(self.topic, self.n, self.with_dialogue, feedback=feedback)
        return self.story

    def set_story(self, text):
        self.story = text or ""

    def gen_shots(self, feedback=None):
        """第2步：把剧本拆成分镜。feedback 为修改意见（修订模式）。"""
        if self.portrait_mode and self.passthrough_script:
            segs = split_voiceover_script(self.passthrough_script, self.n)
            if not segs:
                raise RuntimeError("原稿为空，无法切段")
            self.shots = [{"en": "talking head", "zh": s, "line": s,
                           "line_en": "", "scene": 1} for s in segs]
            self.clip_paths = [None] * len(self.shots)
            return self.shots
        self.shots = self._gen_shots(self.story, self.n, self.with_dialogue, feedback=feedback)
        self.shots = _enrich_shots_camera(self.shots, self.portrait_mode)
        if not self.shots:
            raise RuntimeError("分镜解析为空，请重试")
        self.clip_paths = [None] * len(self.shots)
        return self.shots

    def set_shots(self, shots):
        self.shots = shots
        self.clip_paths = [None] * len(shots)

    # ---------- 第1.5步：人物三视图（角色锁定，抗崩坏） ----------
    def gen_characters(self, feedback=None):
        """从剧本抽取主要角色，生成三视图（正面/侧面/背面）。

        portrait_mode 下跳过（本人照片已锁定形象，三视图无意义）。
        生成的角色锁定描述会写进逐镜提示词，确保跨镜人物一致。
        """
        self.characters = []
        self.character_lock = ""
        if self.portrait_mode:
            self.characters_done = True
            return self.characters
        if not self.story:
            raise RuntimeError("请先生成剧本")
        specs = self._extract_character_specs(feedback=feedback)
        chars = []
        for spec in specs[:3]:
            name = spec.get("name") or "主角"
            desc = spec.get("desc") or ""
            views = self._gen_character_views(name, desc)
            chars.append({"name": name, "desc": desc, "views": views})
        self.characters = chars
        self.character_lock = self._build_character_lock(chars)
        self.characters_done = True
        return self.characters

    def set_characters(self, characters, character_lock=""):
        self.characters = characters or []
        self.character_lock = character_lock or self._build_character_lock(self.characters)
        self.characters_done = True

    def _extract_character_specs(self, feedback=None):
        """让 LLM 从剧本抽取主要角色（英文视觉描述），最多 3 个。"""
        fb = ""
        if feedback:
            fb = (f"\nRevision note: {feedback}\n"
                  "Output the revised character list.\n")
        user_prompt = (
            f"Script:\n<<<\n{self.story}\n>>>\n\n"
            "Extract the main characters that appear across multiple shots (at most 3).\n"
            "For each return a JSON object:\n"
            "- name: character name or role (e.g. '主角小明')\n"
            "- desc: a concise ENGLISH visual description (age, gender, hair, face, "
            "clothing, distinguishing features) that stays identical across shots.\n"
            "Return ONLY a JSON array like "
            '[{"name":"...","desc":"..."}, ...]. No markdown, no extra text.' + fb)
        try:
            content = _agnes_chat(self.cfg, [
                {"role": "system", "content": (
                    "You are a character designer for AI video. Read a short script and "
                    "extract the MAIN characters (at most 3) that appear across multiple "
                    "shots. For each, give a stable English visual description reusable in "
                    "every shot to keep the character consistent.")},
                {"role": "user", "content": user_prompt},
            ], temperature=0.5)
            specs = self._parse_character_specs(content)
        except Exception as e:
            self.log(f"  ⚠️ 人物设定抽取失败（{e}），改用兜底设定")
            specs = [{"name": "主角", "desc": "the main character described in the script"}]
        return specs

    @staticmethod
    def _parse_character_specs(content):
        if not content:
            return []
        txt = content.strip()
        txt = re.sub(r"^```(?:json)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
        try:
            obj = json.loads(txt)
            arr = obj.get("characters") if isinstance(obj, dict) else obj
            if isinstance(arr, list) and arr:
                out = []
                for x in arr[:3]:
                    if isinstance(x, dict):
                        out.append({"name": str(x.get("name", "主角")).strip(),
                                    "desc": str(x.get("desc", "")).strip()})
                if out:
                    return out
        except Exception:
            pass
        m = re.search(r"\[.*\]", txt, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list):
                    return [{"name": str(x.get("name", "主角")).strip(),
                             "desc": str(x.get("desc", "")).strip()}
                            for x in arr if isinstance(x, dict)]
            except Exception:
                pass
        return []

    def _gen_character_views(self, name, desc):
        """为一个角色生成 正面/侧面/背面 三视图，返回 3 个图片路径（失败的为 None）。"""
        views = []
        specs = [
            ("front", "front view, full body facing the camera"),
            ("side", "side view, profile facing left"),
            ("back", "back view, seen from behind"),
        ]
        for _vname, vpose in specs:
            prompt = (
                f"Character design turnaround sheet, {vpose}, of ONE person: {desc}. "
                f"Clean solid neutral background, even studio lighting, consistent "
                f"character design, centered, full body visible. High detail, sharp focus.")
            try:
                rel, _kind, _fname = tool_image_gen(
                    self.cfg, self.app_dir, prompt,
                    size=f"{self.width}x{self.height}")
                path = os.path.join(self.app_dir, rel)
                views.append(path if os.path.isfile(path) else None)
            except Exception as e:
                self.log(f"  ⚠️ 角色「{name}」{_vname} 视图生成失败：{e}")
                views.append(None)
        return views

    @staticmethod
    def _build_character_lock(chars):
        if not chars:
            return ""
        parts = []
        for c in chars:
            name = (c.get("name") or "").strip()
            desc = (c.get("desc") or "").strip()
            if name or desc:
                parts.append(f"{name}: {desc}" if name else desc)
        if not parts:
            return ""
        return ("[CHARACTER LOCK] Keep these characters visually IDENTICAL in every shot "
                "(same face, hair, body shape, clothing, age): " + "; ".join(parts) +
                ". Do NOT alter their appearance between shots.")

    # ---------- 第2.5步：分镜关键帧 + 场景图（每镜首帧参照，抗崩坏） ----------
    def gen_keyframes(self, feedback=None, max_review_retry=2):
        """为每一镜生成关键帧（含 VLM 质检闭环），并为每个场景生成环境图。

        portrait_mode 下跳过（本人形象口播画面统一锁定，无需场景关键帧）。

        质检闭环（参考 VideoClaw 的 VLM QA）：每生成一张关键帧就交给 DeepSeek
        视觉模型审查，不通过就把诊断意见回灌进 prompt 重生成，最多 max_review_retry
        次；仍不通过也保留最后一次结果，绝不卡死流水线。
        """
        self.keyframes = []
        if self.portrait_mode:
            self.keyframes_done = True
            return self.keyframes
        if not self.shots:
            raise RuntimeError("请先生成分镜")
        # 场景图：每个 scene 一张纯环境概念图（供逐镜作场景参照，抗场景漂移）
        self.gen_scene_images()
        lock = self.character_lock or ""
        fb = f"[修改意见：{feedback}] " if feedback else ""
        kfs = []
        for i, shot in enumerate(self.shots):
            if self.cancelled:
                kfs.append(None)
                continue
            en = shot.get("en") or shot.get("zh") or "a cinematic scene"
            qc_fix = ""
            path = None
            for attempt in range(max_review_retry + 1):
                prompt = (
                    f"Keyframe and environment concept art for ONE video shot: {en}. "
                    f"{lock} {fb}{qc_fix} "
                    f"High detail, cinematic composition, consistent lighting and visual "
                    f"style, no text, no watermark, no extra characters.")
                path = self._gen_one_keyframe(i, prompt)
                if not path:
                    break
                ok, note = self.review_keyframe(i, path)
                if ok:
                    break
                # 不通过：抽出诊断意见，回灌重生成
                issues = note
                m = re.search(r"ISSUES:\s*(.+)", note, re.S | re.I)
                if m:
                    issues = m.group(1).strip()[:300]
                if attempt < max_review_retry:
                    self.log(f"  ⚠️ 镜{i+1} 关键帧质检未通过，带诊断重生成"
                             f"（{attempt+1}/{max_review_retry}）：{issues}")
                    qc_fix = (f"[QC FIX] The previous attempt was REJECTED by quality "
                              f"control. You must fix these problems: {issues}. "
                              f"Keep every other aspect identical.")
                else:
                    self.log(f"  ⚠️ 镜{i+1} 关键帧已达重生成上限，保留最后一次结果：{issues}")
            kfs.append(path)
        self.keyframes = kfs
        self.keyframes_done = True
        return self.keyframes

    def set_keyframes(self, keyframes):
        self.keyframes = keyframes or []
        self.keyframes_done = True

    def _gen_one_image(self, prompt, tag="图"):
        """通用生图：返回本地绝对路径，失败返回 None（关键帧/场景图共用）。"""
        try:
            rel, _kind, _fname = tool_image_gen(
                self.cfg, self.app_dir, prompt,
                size=f"{self.width}x{self.height}")
            path = os.path.join(self.app_dir, rel)
            if os.path.isfile(path):
                return path
            self.log(f"  ⚠️ {tag}保存失败：{path}")
            return None
        except Exception as e:
            self.log(f"  ⚠️ {tag}生成失败：{e}")
            return None

    def _gen_one_keyframe(self, i, prompt):
        return self._gen_one_image(prompt, tag=f"镜{i+1} 关键帧")

    # ---------- 场景图（按 scene 生成纯环境概念图，无人物） ----------
    def gen_scene_images(self):
        """为每个场景生成一张「纯环境」概念图（不含人物），供逐镜作场景参照。

        与关键帧的区别：关键帧含人物与构图（作开场画面），场景图只有环境，
        模型可据此稳定「同一个地方」的陈设、光线与建筑，避免场景漂移。
        """
        self.scene_images = {}
        if self.portrait_mode or not self.shots:
            return self.scene_images
        scenes = []
        for s in self.shots:
            sc = s.get("scene") or 1
            if sc not in scenes:
                scenes.append(sc)
        for sc in scenes:
            shot = next((s for s in self.shots if (s.get("scene") or 1) == sc), None)
            if not shot:
                continue
            en = shot.get("en") or shot.get("zh") or "a location"
            prompt = (
                f"Environment concept art, an EMPTY location with NO people and NO "
                f"characters: {en}. Wide establishing shot showing only the setting — "
                f"architecture, furniture, props, ground, lighting and atmosphere. "
                f"{self.style_prompt}. Cinematic, high detail, no text, no watermark.")
            path = self._gen_one_image(prompt, tag=f"场景{sc} 环境图")
            if path:
                self.scene_images[sc] = path
        return self.scene_images

    # ---------- VLM 质检闭环（DeepSeek 视觉模型，参考 VideoClaw 的 QA 机制） ----------
    REVIEW_QUESTION = (
        "You are a strict animation QC reviewer. Compare this keyframe against the "
        "required shot description and the locked character designs.\n"
        "Check three things:\n"
        "(1) CHARACTER: do the people shown match the locked character designs "
        "(face, hairstyle, outfit, body shape, age)?\n"
        "(2) SETTING: does the environment match the required location?\n"
        "(3) DEFECTS: any extra limbs, deformed hands, distorted faces, duplicated "
        "people, garbled text, or broken objects?\n"
        "Answer in EXACTLY this format (two lines):\n"
        "VERDICT: PASS\n"
        "ISSUES: none\n"
        "...or, if it fails:\n"
        "VERDICT: FAIL\n"
        "ISSUES: <short English list of the concrete problems>"
    )

    @staticmethod
    def _vision_profile(cfg):
        # 委托公共模块（导演台与数字人共用同一套视觉质检实现）
        return vq.vision_profile(cfg)

    def _vision_review(self, image_path, question, max_tokens=400):
        """把「图片 + 问题」发给 DeepSeek 视觉模型，返回回答文本；不可用/失败返回空串。"""
        return vq.review_images(self.cfg, [image_path], question,
                                max_tokens=max_tokens, log=self.log)

    def review_keyframe(self, i, image_path):
        """审查单张关键帧，返回 (ok, note)。

        ⚠️ 质检不可用时一律放行（返回 True）——VLM 是增强手段，
        绝不能因为没配 key 或接口抖动就把整条流水线卡死。
        """
        if not image_path or not os.path.isfile(image_path):
            return True, ""
        if not self.vision_review:
            return True, ""
        shot = self.shots[i] if i < len(self.shots) else {}
        desc = shot.get("en") or shot.get("zh") or ""
        question = (f"Required shot description: {desc}\n"
                    f"{self.character_lock}\n\n{self.REVIEW_QUESTION}")
        note = self._vision_review(image_path, question)
        if not note:
            return True, ""
        self.review_notes[i] = note
        ok = "VERDICT: FAIL" not in note.upper()
        return ok, note

    # ---------- 参考图智能装配（参考 ViMax 的 reference image selection） ----------
    def _shot_characters(self, i):
        """判断本镜出场角色：用角色名在分镜文本里匹配。

        匹配不到时**只返回主角**（而不是全部角色）——否则会把没出场的配角
        参考图塞进去，诱导模型把无关人物画进画面，反而制造崩坏。
        """
        if not self.characters:
            return []
        shot = self.shots[i] if i < len(self.shots) else {}
        text = " ".join(str(shot.get(k) or "") for k in ("zh", "en", "line"))
        hits = [c for c in self.characters if c.get("name") and str(c["name"]) in text]
        return hits or self.characters[:1]

    def _assemble_ref_images(self, i):
        """为第 i 镜装配参考图，返回 [(path, role), ...]，最多 AGNES_MAX_REF_IMAGES 张。

        优先级：本镜关键帧（开场画面/构图）> 出场角色三视图正面 > 本场景环境图。
        Agnes reference 模式硬上限 5 张（实测传 6 张报 400），超出必须裁剪。
        """
        refs = []

        def _add(path, role):
            if not path or not os.path.isfile(path):
                return
            if len(refs) >= AGNES_MAX_REF_IMAGES:
                return
            if path in [r[0] for r in refs]:   # 去重（同一张图别占两个位置）
                return
            refs.append((path, role))

        # 1) 本镜关键帧：开场画面与构图的最强锚点
        if self.keyframes and i < len(self.keyframes) and self.keyframes[i]:
            _add(self.keyframes[i], "keyframe")
        elif self.portrait_mode and self.ref_image_path:
            _add(self.ref_image_path, "keyframe")
        elif i == 0 and self.ref_image_path:
            _add(self.ref_image_path, "keyframe")
        # 2) 出场角色三视图正面：人物外观锁定
        if not self.portrait_mode:
            for c in self._shot_characters(i):
                views = c.get("views") or []
                if views and views[0]:
                    _add(views[0], f"character:{c.get('name', '')}")
        # 3) 本场景环境图：场景锁定
        sc = (self.shots[i].get("scene") or 1) if i < len(self.shots) else 1
        _add(self.scene_images.get(sc), "scene")
        return refs

    @staticmethod
    def _build_ref_caption(refs):
        """把参考图列表翻译成 `<Picture N>` 指代说明，让 prompt 精确引用每张图的用途。

        Agnes 2.5 的 reference 模式支持在 prompt 里用 <Picture N> 指代第 N 张参考图。
        """
        if not refs:
            return ""
        parts = []
        for idx, (_path, role) in enumerate(refs, start=1):
            if role == "keyframe":
                parts.append(
                    f"<Picture {idx}> is the REQUIRED opening frame — start the shot "
                    f"with this exact composition, camera angle, lighting and subject "
                    f"placement")
            elif role.startswith("character:"):
                name = role.split(":", 1)[1]
                parts.append(
                    f"<Picture {idx}> shows character {name} — reproduce this exact "
                    f"face, hairstyle, body shape and outfit")
            elif role == "scene":
                parts.append(
                    f"<Picture {idx}> is the location — keep the same place, props, "
                    f"architecture and lighting")
        if not parts:
            return ""
        return ("[REFERENCE IMAGES] " + "; ".join(parts) +
                ". Do NOT ignore these references.")

    def _build_clip_prompt(self, i, feedback=None, refs=None):
        shot = self.shots[i]
        prompt = shot.get("en") or shot.get("zh") or "cinematic scene"
        if self.portrait_mode and self.ref_image_path:
            prompt = PORTRAIT_PROMPT
        if self.style_prompt:
            sp = self.style_prompt.strip()
            if sp:
                prompt = f"{prompt.rstrip('. ')}, {sp}"
        # 人物三视图锁定：把角色外观描述写进本镜提示词，确保跨镜人物一致（抗崩坏）
        if self.character_lock:
            prompt = f"{prompt.rstrip('. ')}, {self.character_lock}"
        # 多参考图指代：用 <Picture N> 明确每张参考图的用途（抗崩坏 v2）
        # 必须在 _build_clip_prompt 里拼，因为指代文本要与实际装配顺序严格一一对应
        if refs:
            caption = self._build_ref_caption(refs)
            if caption:
                prompt = f"{prompt.rstrip('. ')}. {caption}"
        line = shot.get("line") or ""
        if self.with_dialogue and line:
            prompt = _build_video_prompt(prompt, line)
        if feedback:
            prompt = (f"{prompt.rstrip('. ')}. [修改意见：{feedback} 请据此调整本镜画面，"
                      f"保持其余设定（主体 / 风格 / 机位）不变]")
        return prompt

    def generate_all_clips(self, on_clip=None):
        """第3步：顺序生成全部分镜，接力尾帧；每完成一镜回调 on_clip(i, path|None)。返回成功数。"""
        self.clip_paths = [None] * len(self.shots)
        prev_tail = None
        prev_scene = None
        ok = 0
        for i, shot in enumerate(self.shots):
            if self.cancelled:
                break
            # 抗崩坏 v2：为本镜装配最多 5 张参考图（关键帧 > 角色三视图 > 场景图）
            refs = self._assemble_ref_images(i)
            ref_paths = [r[0] for r in refs]
            prompt = self._build_clip_prompt(i, refs=refs)
            # 有参考图 → 走 reference 多图模式（与 keyframe 互斥，故首帧置空）；
            # 无参考图才退回原来的首帧/尾帧接力逻辑
            if ref_paths:
                first_frame = None
            elif self.portrait_mode and self.ref_image_path:
                first_frame = self.ref_image_path
            elif i == 0 and self.ref_image_path is not None:
                first_frame = self.ref_image_path
            elif self.relay and prev_tail is not None and shot.get("scene") == prev_scene:
                first_frame = prev_tail
            else:
                first_frame = None
            line = shot.get("line") or "" if self.with_dialogue else None
            clip = self._gen_one_clip(prompt, self.duration, f"{self.width}x{self.height}",
                                      first_frame, line if self.with_dialogue else None, i,
                                      ref_images=ref_paths)
            if clip:
                self.clip_paths[i] = clip
                ok += 1
                if self.relay:
                    prev_tail = self._tail_frame_path(clip)
                    prev_scene = shot.get("scene")
                if on_clip:
                    on_clip(i, clip)
            else:
                prev_tail = None
                if on_clip:
                    on_clip(i, None)
        return ok

    def regenerate_clip(self, i, feedback=None):
        """单独重生成某一镜（用户说「这一镜要改」）。不接力，独立生成。"""
        if self.cancelled or i < 0 or i >= len(self.shots):
            return None
        # 抗崩坏 v2：重生成同样装配多参考图，保证「改这一镜」不会把人物改崩
        refs = self._assemble_ref_images(i)
        ref_paths = [r[0] for r in refs]
        prompt = self._build_clip_prompt(i, feedback=feedback, refs=refs)
        if ref_paths:
            first_frame = None
        elif self.portrait_mode and self.ref_image_path:
            first_frame = self.ref_image_path
        elif i == 0 and self.ref_image_path is not None:
            first_frame = self.ref_image_path
        else:
            first_frame = None
        line = self.shots[i].get("line") or ""
        line = line if self.with_dialogue else None
        clip = self._gen_one_clip(prompt, self.duration, f"{self.width}x{self.height}",
                                  first_frame, line, i, ref_images=ref_paths)
        if clip:
            self.clip_paths[i] = clip
        return clip

    # ---------- 音频层：分镜自带音轨优先（合成时沿用，见 _merge / _probe_has_audio） ----------

    def merge(self):
        """第4步：把已生成的片段合成成片。返回 (out_path|None, error_msg)"""
        if not self.ffmpeg:
            return None, "未找到 ffmpeg，无法合成成片"
        clips = []
        for i, p in enumerate(self.clip_paths):
            if not p:
                self.log(f"  ⚠️ 镜{i+1}：路径为空（可能生成失败未重试）")
            elif not os.path.isfile(p):
                self.log(f"  ❌ 镜{i+1}：文件不存在（{p}）")
            else:
                clips.append(p)
        if not clips:
            return None, "没有可用的视频片段（全部生成失败或文件丢失），无法合成"
        # 传完整列表（含 None 失败镜），由 _merge 跳过空镜；
        # 这样 shot_idx 与 self.shots 全量对齐，避免索引错配。
        self.log(f"📦 合成输入：{len(clips)} 个有效片段 / 原始 {len(self.clip_paths)} 镜")
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(PRODUCTS_DIR, "视频", f"director_final_{ts}.mp4")
        ok, detail = self._merge(self.clip_paths, self.shots, out_path, self.burn_subtitles)
        if ok:
            return out_path, ""
        # 合并详细错误信息
        err_parts = ["ffmpeg 合成失败"]
        if detail:
            err_parts.append(detail)
        if os.path.exists(out_path):
            err_parts.append("（输出文件未生成）")
        return None, " | ".join(err_parts)

    def keyframe(self, clip_path):
        """抽视频首帧作关键帧预览图，返回 PNG 路径（失败返回 None）。"""
        return self._head_frame_path(clip_path)

    # ---------- 主入口（自动编排，兼容旧路径；面板改用上面分阶段接口） ----------
    def run(self, topic, n=3, duration=5, resolution="768x1152", style_key="realistic",
            ref_image_path=None, portrait_mode=False, with_dialogue=False, relay=True,
            transition="black", transition_dur=0.4, burn_subtitles=True,
            passthrough_script=None, clip_dir=None):
        if not self.ffmpeg:
            self.status("未找到 ffmpeg，无法合成成片", err=True)
            self.on_finish(False, "未找到 ffmpeg，无法合成成片")
            return None
        self.prepare(topic, n, duration, resolution, style_key, ref_image_path,
                     portrait_mode, with_dialogue, relay, transition, transition_dur,
                     burn_subtitles, passthrough_script, clip_dir)
        # 剧本
        if not (portrait_mode and passthrough_script):
            self.log("第 1 步：根据主题生成剧本…")
            self.status("正在生成剧本…")
            try:
                self.gen_story()
            except Exception as e:
                self.status(f"剧本生成失败：{e}", err=True)
                self.on_finish(False, f"剧本生成失败：{e}")
                return None
            self.log("✅ 剧本生成成功：")
            for ln in [l for l in self.story.splitlines() if l.strip()][:40]:
                self.log(f"　　{ln}")
            if not self.ask_approve("story", f"📝 剧本已生成（共 {len(self.story.splitlines())} 行）。是否满意？"):
                self.on_finish(False, "已取消（剧本未通过）")
                return None
        # 人物三视图（角色锁定，抗崩坏；口播模式跳过）
        if not (portrait_mode and passthrough_script):
            self.log("第 1.5 步：生成人物三视图（角色锁定）…")
            self.status("正在生成人物三视图…")
            try:
                chars = self.gen_characters()
                for c in chars:
                    self.log(f"  👤 {c.get('name','')}：三视图已生成")
            except Exception as e:
                self.status(f"人物三视图生成失败：{e}", err=True)
                self.on_finish(False, f"人物三视图生成失败：{e}")
                return None
        # 分镜
        self.log("第 2 步：把剧本拆成连续分镜…")
        self.status("正在拆分分镜脚本…")
        try:
            self.gen_shots()
        except Exception as e:
            self.status(f"分镜生成失败：{e}", err=True)
            self.on_finish(False, f"分镜生成失败：{e}")
            return None
        for i, s in enumerate(self.shots, 1):
            extra = f" 💬{s.get('line')}" if s.get("line") else ""
            self.log(f"  <b>镜{i}</b> [{s.get('scene',1)}场] {s.get('zh','')}{extra}")
        if not self.ask_approve("shots", f"🎞️ 分镜就绪（共 {len(self.shots)} 镜）。是否满意？"):
            self.on_finish(False, "已取消（分镜未通过）")
            return None
        # 分镜关键帧 + 场景图（每镜首帧参照，抗崩坏；口播模式跳过）
        if not (portrait_mode and passthrough_script):
            self.log("第 2.5 步：生成分镜关键帧 + 场景图…")
            self.status("正在生成关键帧与场景图…")
            try:
                kfs = self.gen_keyframes()
                ok_kf = sum(1 for x in kfs if x)
                self.log(f"  🎯 关键帧已生成 {ok_kf}/{len(self.shots)} 镜")
            except Exception as e:
                self.status(f"关键帧生成失败：{e}", err=True)
                self.on_finish(False, f"关键帧生成失败：{e}")
                return None
        # 逐镜生成
        self.log(f"第 3 步：逐镜生成视频（共 {len(self.shots)} 镜）…")
        self.status("正在生成视频片段…")
        ok = self.generate_all_clips()
        if ok == 0:
            self.status("没有成功生成的片段，任务结束", err=True)
            self.on_finish(False, "没有成功生成的片段，任务结束")
            return None
        # 合成
        self.log(f"✅ 全部 {ok} 个片段已生成，开始合成成片…")
        self.status("正在合成成片…")
        out_path, merge_err = self.merge()
        if out_path:
            size = os.path.getsize(out_path) // 1024
            self.log(f"成片完成：{out_path}（{size}KB）")
            self.status("成片完成！")
            self.on_finish(True, f"成片完成！{os.path.basename(out_path)}（{size}KB）", out_path)
            return out_path
        self.status(f"合成失败：{merge_err}", err=True)
        self.on_finish(False, f"合成失败：{merge_err}")
        return None


    # ---------- 第1步：剧本 ----------
    def _gen_story(self, topic, n, with_dialogue=False, feedback=None):
        dialogue_note = ""
        if with_dialogue:
            dialogue_note = "本片人物需要开口说中文台词，剧本要为角色写中文对白。\n"
        rev_note = ""
        if feedback:
            rev_note = (f"\n——这是修订版，请根据以下修改意见调整：\n{feedback}\n"
                        "请直接输出修订后的完整剧本。\n")
        ref_story_note = ""
        if self.ref_image_path:
            if self.ref_only:
                ref_story_note = ("（重要：用户未提供文字主题，仅上传一张参考图作为首镜首帧锁定。"
                                  "请围绕该参考图的主体/风格创作一条连贯的微故事，主角外观须与参考图一致，"
                                  "不要凭空更换主角身份或画风。）\n")
            elif self.portrait_mode:
                ref_story_note = ("（本片为【本人形象口播】模式：参考图是说话者本人的照片。"
                                  '剧本必须以第一人称"我"来写，内容就是这张照片里的人要说的口播文案。'
                                  "全片始终是同一个人在说话，不要切换视角或出现其他角色。）\n")
            else:
                ref_story_note = "（本片有参考图首镜首帧锁定，主角外观须与参考图保持一致。）\n"
        sys_prompt = ("你是专业的短视频编剧，擅长把一个主题快速扩展成有起承转合的微故事。"
                      "你的任务：根据主题，创作一部适合做成短视频的微故事剧本。")
        user_prompt = (
            f"主题：{topic}\n"
            f"请创作一部约 {n} 个镜头长度（约 {n * self.duration} 秒，科普 / 纪录片类可酌情更多）的微故事短视频剧本，用中文写。\n"
            "要求：\n"
            "1) 明确主角与关键配角（名字、外貌、性格一两句），全片同一主角贯穿；\n"
            "2) 有清晰的时间线与起承转合：开头建立情境→中间发展/冲突→结尾收束；\n"
            "3) 情节连贯，事件按先后顺序发生，不要跳切；\n"
            "4) 逐镜用一两句话描述这一镜发生了什么（不要写镜头语言或英文）；\n"
            + dialogue_note + rev_note + ref_story_note +
            "直接输出剧本正文，不要解释、不要 markdown 代码块。")
        if self.portrait_mode:
            sys_prompt = ("你是口播视频文案撰稿人。只写一个人对着镜头说的话（第一人称「我」），"
                          "口语化、自然流畅、有信息量。"
                          "绝不写故事情节、场景描写、镜头语言、动作说明，绝不出现其他角色。")
            user_prompt = (
                f"主题：{topic}\n"
                f"请写一段总时长约 {n * self.duration} 秒的中文口播文案"
                f"（第一人称「我」对镜头说话），自然分成约 {n} 个小段落，"
                f"每段约 {self.duration} 秒口语量（20~35字）。\n"
                "要求：只有说话内容本身；不要任何场景/动作/情节描写；"
                "不要出现除说话者以外的角色；不要标题。\n"
                + rev_note +
                "直接输出口播文案正文（按段落分行），不要解释、不要 markdown 代码块。")
        return _agnes_chat(self.cfg, [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ], temperature=0.7)

    # ---------- 第2步：分镜 ----------
    def _gen_shots(self, story, n, with_dialogue=False, feedback=None):
        dialogue_rule = ""
        if with_dialogue:
            dialogue_rule = ("本片需要人物开口说【中文】台词。请让分镜围绕会说话的角色设计，"
                             "每个分镜给一个字段：\n"
                             "- line：这一镜角色说的【中文】台词（口语化、带情绪，一句，且推进剧情）。\n"
                             "注意：台词必须是中文，视频模型会按中文发声，不要给英文。\n")
        rev_note = ""
        if feedback:
            rev_note = (f"\n——这是修订版，请根据以下修改意见调整分镜：\n{feedback}\n"
                        "请直接输出修订后的完整分镜 JSON 数组。\n")
        ref_note = ""
        if self.ref_image_path:
            if self.portrait_mode:
                ref_note = ("\n⚠️ 【本人形象口播】参考图是说话者本人的真实照片。"
                            "每一镜的英文提示词(en)必须以 'Same person from reference photo, "
                            "a [age]s [gender] with [glasses/hairstyle], wearing [clothing], "
                            "speaking/talking to camera' 开头——"
                            "严格锁定照片中的人物外观（脸型、发型、眼镜、衣着），"
                            "每镜都是同一个人在说话，背景可以轻微变化但人物必须一致。"
                            "不要让人物变形、换脸、或变成动画风格。\n")
            else:
                ref_note = ("\n⚠️ 本片已提供一张【参考图】（主体外观 / 视觉风格），已作为首镜首帧锁定注入视频模型。"
                            "请在每一镜的英文提示词(en)开头持续点明同一主体（与参考图一致的外观），"
                            "并确保全片视觉风格（画风、色调、光影、质感）与首镜参考图保持一致，"
                            "不要中途切换到不相关的画风或主体。\n")
        style_note = ""
        if self.style_prompt:
            style_note = (f"\n⚠️ 本片统一画面风格：{self.style_prompt}。"
                           f"请在每一镜的英文提示词(en)中持续体现这一风格"
                           f"（色调、质感、光影、镜头语言保持一致），不要中途切换到其他画风。\n")
        portrait_note = ""
        if self.portrait_mode:
            portrait_note = ("\n🎭 【本人形象口播模式特殊指令——最高优先级】：\n"
                             f"本片是口播视频。你唯一的任务是把口播文案按顺序切成 {n} 段台词：\n"
                             "- line：这一镜说的原话（直接从文案里按顺序截取，不改写、不新编）\n"
                             "- zh：与 line 相同（字幕就是台词本身）\n"
                             "- en：固定写 'talking head' 即可（画面提示词由程序统一锁定）\n"
                             "- 全片所有分镜 scene=1；不要任何情节、场景变化、动作设计、其他角色\n")
        user_prompt = (
            f"剧本如下：\n<<<\n{story}\n>>>\n\n"
            f"请把上面这部剧本拆成约 {n} 个连续分镜，串成完整短视频。"
            f"科普 / 纪录片类内容较丰富时，可酌情增加到 {max(n, 12)} 个，确保叙事完整、不遗漏要点；"
            "不要为了凑数硬加水镜。\n"
            "每一镜都必须是剧本中对应情节的【视觉化还原与延续】，不要跳切、"
            "不要换成不相关的新场景或新主角。\n"
            "每个分镜给这些字段：\n"
            "- en：英文视频生成提示词，1~2 句话，只写视觉内容（主体、动作、镜头运动、光线氛围）。\n"
            "- zh：这一镜对应的中文字幕/旁白，口语化、一句话，要体现故事推进。\n"
            "- cam：这一镜的镜头语言（景别 + 运镜 + 机位），例如 "
            "'medium shot, slow push-in, eye-level'。相邻分镜的运镜要有变化。\n"
            "- scene：这一镜所属场景编号（整数，从 1 开始）。同一连续时空的分镜用同一个 scene 值；"
            "剧情跳场时 +1。同一 scene 内的分镜会做【尾帧接力】。\n"
            + dialogue_rule + rev_note + ref_note + style_note + portrait_note +
            CAM_VOCAB_TEXT +
            "严格只返回一个 JSON 数组，形如 "
            '[{"en":"...","zh":"...","cam":"medium shot, slow push-in, eye-level","scene":1'
            + (',"line":"..."' if with_dialogue else '')
            + "}, ...]，不要任何解释文字，不要 markdown 代码块。")
        content = _agnes_chat(self.cfg, [
            {"role": "system", "content": ("你是专业的短视频分镜导演，也是 AI 视频生成提示词专家。"
                                           "把剧本拆成连续、有因果推进的完整分镜序列，全片同一主角贯穿；"
                                           "熟练运用电影摄影语言（景别、运镜、机位）设计每一镜。")},
            {"role": "user", "content": user_prompt},
        ], temperature=0.65)
        return self._parse_shots(content)

    # ---------- 分镜解析 ----------
    @staticmethod
    def _parse_shots(content):
        if not content:
            return []
        txt = content.strip()
        txt = re.sub(r"^```(?:json)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
        for candidate in (txt,):
            try:
                obj = json.loads(candidate)
                arr = obj.get("shots") if isinstance(obj, dict) else obj
                if isinstance(arr, list) and arr:
                    return [VideoPipeline._norm_shot(x) for x in arr]
            except Exception:
                pass
        m = re.search(r"\[.*\]", txt, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list) and arr:
                    return [VideoPipeline._norm_shot(x) for x in arr]
            except Exception:
                pass
        return []

    @staticmethod
    def _norm_shot(x):
        if isinstance(x, dict):
            en = x.get("en") or x.get("prompt") or x.get("english") or ""
            zh = x.get("zh") or x.get("caption") or x.get("chinese") or ""
            line = x.get("line") or ""
            cam = x.get("cam") or ""
            sc = x.get("scene") or x.get("sc") or 1
            try:
                sc = int(sc)
            except Exception:
                sc = 1
            return {"en": str(en).strip(), "zh": str(zh).strip(),
                    "line": str(line).strip(), "scene": sc, "cam": str(cam).strip()}
        return {"en": str(x).strip(), "zh": "", "line": "", "scene": 1, "cam": ""}

    # ---------- 单镜生成（带自动重试） ----------
    def _gen_one_clip(self, prompt, duration, resolution, first_frame, dialogue, idx,
                      ref_images=None):
        if not hasattr(self, "last_prompts"):
            self.last_prompts = {}
            self.last_errors = {}
        # 留存本镜实际发给模型的提示词，供 UI「查看提示词 / 修改」使用
        self.last_prompts[idx] = prompt
        max_retry = 2
        last_err = "生成失败（模型未返回视频）"
        for attempt in range(max_retry + 1):
            if self.cancelled:
                return None
            try:
                res = tool_video_gen(self.cfg, self.app_dir, prompt, duration, None,
                                     resolution=resolution, first_frame=first_frame,
                                     dialogue=dialogue, images=ref_images)
            except Exception as e:
                res = f"异常：{e}"
            if isinstance(res, tuple):
                rel, kind, name = res
                clip = os.path.join(self.app_dir, rel)
                if os.path.isfile(clip):
                    return clip
                res = f"保存路径不存在：{clip}"
            last_err = f"{res}"
            # 失败重试：仅对可恢复错误（429/5xx/超时/网络）重试；
            # 4xx(非429)是请求本身问题，重试同一请求无用，直接判失败并回显原因
            low = str(res).lower()
            permanent = any(s in low for s in (
                "http 400", "http 401", "http 403", "http 404", "http 422",
                "bad request", "unauthorized", "forbidden", "not found",
                "未提供视频描述", "保存路径不存在", "视频已完成但未返回下载地址",
            ))
            if attempt < max_retry and not permanent:
                wait = 5 + attempt * 5
                self.log(f"  ⚠️ 镜{idx+1} 失败（{res}），{wait}秒后自动重试（{attempt+1}/{max_retry}）…")
                time.sleep(wait)
            else:
                if permanent:
                    self.log(f"  ❌ 镜{idx+1} 失败（请求本身错误，不重试）：{res}")
                else:
                    self.log(f"  ❌ 镜{idx+1} 失败：{res}")
        # 全部重试仍失败，记录最终原因（UI 会回显）
        self.last_errors[idx] = last_err
        return None

    # ---------- 尾帧抽取（接力） ----------
    def _tail_frame_path(self, clip_path):
        """抽视频最后一帧 → 存为 PNG 文件，返回路径（供 next 镜作 first_frame）。"""
        try:
            out = os.path.join(self.project_dir,
                               f"tail_{len(os.listdir(self.project_dir))+1:03d}.png")
            args = [self.ffmpeg, "-y", "-sseof", "-0.1", "-i", clip_path,
                    "-frames:v", "1", "-q:v", "2", out]
            self._run_ff(args, timeout=30)
            if not os.path.exists(out) or os.path.getsize(out) < 100:
                return None
            return out
        except Exception:
            return None

    def _extract_tail_frame(self, clip_path):
        """抽尾帧 → base64 data URI（与 _tail_frame_path 同步骤，供接力判定用）。"""
        p = self._tail_frame_path(clip_path)
        if not p:
            return None
        try:
            with open(p, "rb") as f:
                b = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/png;base64,{b}"
        except Exception:
            return None

    def _head_frame_path(self, clip_path):
        """抽视频【首帧】作关键帧预览图，返回 PNG 路径（失败返回 None）。"""
        try:
            out = os.path.join(self.project_dir,
                               f"head_{abs(hash(clip_path)) % 100000:05d}.png")
            args = [self.ffmpeg, "-y", "-i", clip_path,
                    "-frames:v", "1", "-q:v", "2", out]
            self._run_ff(args, timeout=30)
            if not os.path.exists(out) or os.path.getsize(out) < 100:
                return None
            return out
        except Exception:
            return None

    # ---------- 第4步：ffmpeg 合成 ----------
    def _run_ff(self, args, timeout=60):
        """统一执行 ffmpeg 子进程，返回 (returncode, stdout_text, stderr_text)。

        ⚠️ 绝对不要用 subprocess.run(..., text=True)：
        PyInstaller 冻结环境下 locale 常为 gbk/cp936，而 ffmpeg 输出是 UTF-8。
        一旦出现 gbk 解不了的字节，UnicodeDecodeError 会抛在 subprocess 的后台
        读取线程（_readerthread）里——线程静默死亡、主线程不报错，但 stderr 会
        变成空字符串。这会让 _probe_has_audio 把"探测失败"误判成"无音轨"，
        导致成片被铺成 anullsrc 静音轨（实测 2 kb/s / -91 dB 完全无声）。
        所以这里用 bytes 模式捕获，再显式按 utf-8 解码并容错。
        """
        r = subprocess.run(args, capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out, err

    def _probe_has_audio(self, path):
        """探测片段是否带音轨。

        策略是【乐观】的：只有明确探测到"无音轨"才返回 False；
        任何探测失败（异常/超时/拿不到输出）一律返回 True。
        宁可让 ffmpeg 去尝试 map 片段音轨（真没有会报错，由 _merge 降级兜底），
        也绝不能把有声片段误判成静音——那会直接让成片失声。
        """
        # 用已知的 self.ffmpeg（通过 IMAGEIO_FFMPEG_EXE 或捆绑路径）探测音轨，
        # 避开系统 ffprobe.exe——它常被 Defender 实时扫描干扰导致 0xc0000142 崩溃。
        ff = getattr(self, "ffmpeg", None)
        if not ff or not os.path.exists(ff):
            return True
        try:
            _rc, _out, err = self._run_ff(
                [ff, "-hide_banner", "-i", path], timeout=20)
        except Exception:
            return True
        if not err:
            return True                      # 拿不到输出 → 乐观，按有音轨处理
        if "Stream #" in err:
            return "Audio:" in err           # 解析到流信息 → 精确判定
        return True                          # 输出异常（如只报错）→ 仍乐观

    def _make_transition_clip(self, kind):
        if self.trans_clip_path and os.path.exists(self.trans_clip_path):
            return self.trans_clip_path
        color = "white" if kind == "white" else "black"
        W, H = self.width, self.height
        T = self.transition_dur
        out = os.path.join(self.project_dir, "transition.mp4")
        args = [self.ffmpeg, "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:d={T}:r=24",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", str(T), "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k", out]
        self._run_ff(args, timeout=60)
        if not os.path.exists(out):
            return None
        self.trans_clip_path = out
        return out

    def _write_srt(self, path, sub_segs, shots):
        lines = []
        for k, (start, end, idx) in enumerate(sub_segs):
            s = shots[idx] if idx is not None and idx < len(shots) else None
            text = (s.get("line") or s.get("zh", "")) if s else ""
            lines.append(str(k + 1))
            lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
            lines.append(text or f"镜{(idx or 0)+1}")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _build_segs(self, clip_paths, shots):
        """把 clip_paths 展开成待拼接片段（含转场），跳过生成失败的镜。"""
        segs = []
        use_trans = self.transition in ("black", "white")
        prev_scene = None
        for i in range(len(clip_paths)):
            sc = shots[i].get("scene") if i < len(shots) else None
            # 跳过生成失败的镜（路径为 None 或文件不存在），避免 ffmpeg -i None 崩溃
            if not clip_paths[i] or not os.path.isfile(clip_paths[i]):
                prev_scene = sc  # 仍更新 scene，确保相邻成功镜的转场判定连续
                continue
            if use_trans and prev_scene is not None and sc != prev_scene:
                tpath = self._make_transition_clip(self.transition)
                if tpath:
                    segs.append({"path": tpath, "kind": "trans",
                                 "shot_idx": None, "dur": self.transition_dur})
            segs.append({"path": clip_paths[i], "kind": "clip",
                         "shot_idx": i, "dur": self.duration})
            prev_scene = sc
        return segs

    def _merge_exec(self, segs, shots, out_path, burn_subtitles, use_clip_audio=True):
        """真正执行一次 ffmpeg 拼接。

        use_clip_audio=True  → 沿用片段自带音轨（正常路径）
        use_clip_audio=False → 全部铺静音轨（仅在沿用音轨失败时兜底）
        """
        W, H = self.width, self.height
        m = len(segs)
        vparts, aparts, seg_str, sub_segs = [], [], [], []
        t = 0.0
        for k, s in enumerate(segs):
            # 每段对应一个输入文件（视频即输入 k）；分镜自带音轨优先沿用，
            # 转场/无音轨片段用静音轨补齐，确保 concat 每段音视频齐全。
            ha = self._probe_has_audio(s["path"]) if use_clip_audio else False
            vparts.append(
                f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v{k}]")
            if ha:
                # 沿用片段自带音轨（Agnes 生成的台词口型 + 背景音效）
                aparts.append(
                    f"[{k}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                    f"channel_layouts=stereo[a{k}]")
            else:
                aparts.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=44100:"
                    f"duration={s['dur']}[a{k}]")
            seg_str.append(f"[v{k}][a{k}]")
            if s["kind"] == "clip":
                sub_segs.append((t, t + s["dur"], s["shot_idx"]))
            t += s["dur"]

        filter_complex = ";".join(vparts + aparts)
        filter_complex += ";" + "".join(seg_str) + f"concat=n={m}:v=1:a=1[outv][outa]"

        final_map = "[outv]"
        if burn_subtitles and self.with_dialogue and sub_segs:
            srt_path = os.path.join(self.project_dir, "subtitles.srt")
            self._write_srt(srt_path, sub_segs, shots)
            srt_ff = srt_path.replace("\\", "/").replace(":", "\\:")
            filter_complex += (
                f";[outv]subtitles='{srt_ff}':force_style="
                "'FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=30'[outsub]")
            final_map = "[outsub]"

        args = [self.ffmpeg, "-y"]
        for s in segs:
            args += ["-i", s["path"]]
        args += ["-filter_complex", filter_complex,
                 "-map", final_map, "-map", "[outa]",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-preset", "medium", "-crf", "20",
                 "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", out_path]
        # 记录完整命令（调试用）
        mode = "沿用片段音轨" if use_clip_audio else "静音轨兜底"
        self.log(f"🔧 ffmpeg 合成（{mode}）：{m} 路输入，共 {len(args)} 参数")
        try:
            rc, out, err = self._run_ff(args, timeout=600)
            if rc != 0:
                # 提取关键错误信息（ffmpeg stderr 通常很长，取最后几行）
                err_lines = err.strip().splitlines()
                err_summary = "\n".join(err_lines[-5:]) if err_lines else "(无 stderr 输出)"
                if out and out.strip():
                    err_summary += "\nstdout: " + out.strip()[-200:]
                self.log(f"❌ ffmpeg 返回码 {rc}：\n{err_summary}")
                return False, f"ffmpeg 返回码 {rc}"
            if not os.path.exists(out_path):
                self.log("❌ ffmpeg 返回成功但输出文件不存在")
                return False, "ffmpeg 成功但输出文件缺失"
            size = os.path.getsize(out_path) // 1024
            self.log(f"✅ 合成完成：{os.path.basename(out_path)}（{size}KB）")
            return True, ""
        except subprocess.TimeoutExpired:
            self.log("❌ ffmpeg 合成超时（>10分钟）")
            return False, "ffmpeg 合成超时"
        except Exception as e:
            self.log(f"❌ ffmpeg 合成异常：{e}")
            return False, str(e)

    @staticmethod
    def _is_stream_error(detail):
        """判断失败原因是否属于「片段音轨不可用」——这类可降级为静音轨重试。"""
        d = (detail or "").lower()
        return ("matches no streams" in d
                or "invalid stream specifier" in d
                or "does not contain any stream" in d)

    def _check_audio_level(self, path):
        """合成后自检成片音量；静音则明确告警，防止无声问题再次静默发生。"""
        try:
            _rc, _o, err = self._run_ff(
                [self.ffmpeg, "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                timeout=300)
            mt = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err)
            if not mt:
                return
            mx = float(mt.group(1))
            if mx < -60.0:
                self.log(f"  ⚠️ 成片自检：音轨峰值 {mx} dB，疑似静音！"
                         f"（分镜有声却合成无声时请查 _probe_has_audio）")
            else:
                self.log(f"  🔊 成片自检：音轨峰值 {mx} dB（正常）")
        except Exception:
            pass

    def _merge(self, clip_paths, shots, out_path, burn_subtitles=True):
        segs = self._build_segs(clip_paths, shots)
        if not segs:
            return False, "没有可用的视频片段（全部生成失败或文件丢失）"
        # 1) 正常路径：沿用分镜自带音轨
        ok, detail = self._merge_exec(segs, shots, out_path, burn_subtitles,
                                      use_clip_audio=True)
        if ok:
            self._check_audio_level(out_path)
            return True, ""
        # 2) 若失败源于「片段音轨不可用」→ 降级为静音轨重试，保证至少能出片
        if self._is_stream_error(detail):
            self.log("  ⚠️ 沿用分镜音轨失败，降级为静音轨重试（成片将无声）…")
            ok2, detail2 = self._merge_exec(segs, shots, out_path, burn_subtitles,
                                            use_clip_audio=False)
            if ok2:
                self.log("  ⚠️ 已按静音轨降级合成：成片无声（片段无可用音轨）")
                return True, ""
            return False, detail2
        return False, detail
