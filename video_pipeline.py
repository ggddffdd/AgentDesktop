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
import threading
# v4.125 M-14：windowed 打包下调 ffmpeg 不闪黑窗
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
import urllib.request

from tools import (_agnes_creds, _build_video_prompt, tool_video_gen,
                   tool_image_gen, PRODUCTS_DIR, is_silent_clip)
# 参考图硬上限：Agnes reference 模式实测 6 张报 400，固定 5。
# （原为 tools 导出，视频统一内核后归 video_pipeline 自管，避免悬空依赖 tools）
AGNES_MAX_REF_IMAGES = 5


def _vp_products_dir(cfg=None, kind="video"):
    """v4.129：产物目录（dated 时含日期+项目层）。product_layout 不可用时退回旧路径。"""
    try:
        import product_layout
        layout = "dated" if product_layout.is_dated_layout_enabled(cfg) else "flat"
        return product_layout.product_dir(PRODUCTS_DIR, kind=kind, layout=layout)
    except Exception:
        d = os.path.join(PRODUCTS_DIR, "视频")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d
import vision_qc as vq

# 画面风格短语（与 video-agent 一致）
# ---------- 画风（单一来源：面板下拉与生图提示词都从这里派生）----------
# v4.151：原先 STYLE_PROMPTS 在 video_pipeline、STYLE_ITEMS 在 director_panel **各存一份**，
#   两处表一旦漂移就会出现「下拉框里有、提示词里没有」的静默失效 —— 本轮大哥的
#   「我要水墨动画，它把主角三视图干成真人」就有一部分是这么来的（词表里**根本没有水墨**，
#   他在主题里写的"水墨"两个字只喂给了剧本模型，一个画面提示词都没吃到）。
#   现在只此一份：STYLE_ORDER 定顺序、STYLE_LABELS 定中文名、STYLE_PROMPTS 定英文提示词。
STYLE_ORDER = ("realistic", "cinematic", "anime", "inkwash",
               "watercolor", "neon", "documentary")

STYLE_LABELS = {
    "realistic": "写实",
    "cinematic": "电影感",
    "anime": "动画",
    "inkwash": "水墨",
    "watercolor": "水彩",
    "neon": "霓虹",
    "documentary": "纪录片",
}

STYLE_PROMPTS = {
    "realistic": ("photorealistic, hyper-real, cinematic live-action, natural lighting, "
                  "high detail, 8k, sharp focus, realistic skin texture"),
    "cinematic": ("cinematic film still, anamorphic lens, film grain, dramatic lighting, "
                  "teal and orange color grading, shallow depth of field, moody"),
    "anime": ("anime style, hand-drawn cel shading, vibrant colors, clean lineart, "
              "studio animation quality, detailed background"),
    # v4.151 新增「水墨」（文本借自同机 video-agent 项目的 ink 风格并扩写）
    "inkwash": ("Chinese ink wash painting style (shui-mo), minimalist black ink on rice "
                "paper, elegant negative space, soft brush texture, expressive dry-brush "
                "strokes, monochrome with subtle gray washes, traditional Chinese "
                "painting aesthetic"),
    "watercolor": ("soft watercolor painting, delicate brush strokes, paper texture, "
                   "muted pastel palette, dreamy, artistic"),
    "neon": ("cyberpunk neon, glowing signs, rain-slick streets, high contrast, "
             "magenta and cyan, Blade Runner atmosphere, volumetric fog"),
    "documentary": ("documentary photography, handheld realism, natural color, "
                    "unstyled candid, reportage, authentic"),
}


def style_items():
    """风格下拉项 [(显示名, key), ...]——从上面那份唯一来源派生，杜绝两处表格漂移。"""
    return [(f"{STYLE_LABELS.get(k, k)} {k}", k) for k in STYLE_ORDER]


def style_label(key):
    return STYLE_LABELS.get(key, key or "")


# 主题里出现的画风词 → style_key。命中就自动切下拉框并**明确告知**用户
# （大哥 2026-09-15 定下的规则：**主题优先于下拉框**，但要说明我改了什么、允许改回）。
# 刻意用确定性关键词而不是调模型：不花钱、结果可预期、可单元测试。
STYLE_KEYWORDS = (
    ("inkwash", ("水墨动画", "水墨画", "水墨", "国画", "写意", "墨色", "宣纸", "泼墨",
                 "ink wash", "inkwash", "shui-mo", "shuimo", "chinese ink")),
    ("neon", ("赛博朋克", "赛博", "霓虹", "蒸汽波", "cyberpunk", "neon")),
    ("watercolor", ("水彩画", "水彩", "watercolor", "watercolour")),
    ("anime", ("动漫", "动画", "二次元", "日漫", "吉卜力", "宫崎骏", "anime", "ghibli")),
    ("cinematic", ("电影感", "电影质感", "胶片感", "影调", "cinematic")),
    ("documentary", ("纪录片", "纪实", "科普片", "documentary")),
    ("realistic", ("写实", "真人", "实拍", "照片级", "realistic", "photoreal")),
)


def style_from_topic(topic, default=None):
    """从主题里识别画风，返回 style_key；识别不到返回 default（默认 None）。

    排序规则（可解释、可测）：
      1. 匹配词**越长越优先** —— "水墨动画" 必须压过 "动画"，否则水墨诉求会被 anime 抢走；
      2. 长度相同时，**在主题里出现越早越优先** —— "水调歌头水墨动画" 里"水墨"在"动画"之前。
    """
    t = (topic or "").lower()
    if not t.strip():
        return default
    hits = []                       # (匹配长度, 出现位置, key)
    for key, words in STYLE_KEYWORDS:
        for w in words:
            pos = t.find(w.lower())
            if pos >= 0:
                hits.append((len(w), pos, key))
    if not hits:
        return default
    hits.sort(key=lambda x: (-x[0], x[1]))
    return hits[0][2]


# ---------- 人物 / 物件词表（v4.151：把「酒樽」这类道具挡在角色管道之外）----------
# 背景：大哥的主题「水调歌头水墨动画，主角苏轼」跑出来 characters = [苏轼, 酒樽]，
#   于是「酒樽」被拿去生成人物三视图 → 真人捧着酒壶。根子在**剧本把道具写成了「关键配角」**，
#   抽取器照剧本办；这里再加一道机器闸门兜底，即使提示词漏了也不会烧钱画错东西。
#
# 设计原则：**宁可漏判，不可误杀**。「小花猫」「狐妖」「机器人」这类拟人/动物角色
#   必须照常出三视图（既有回归脚本正好拿 `a cat` 做断言，天然守着这条）。
#
# 强线索 = 一眼就是活物；弱线索 = 活物常见但也会出现在物件描述里。
# 分强弱是为了让「有物件词 + 无强线索」能安全地判成物件，而不被 "aged bronze"
# （陈年青铜，撞上 aged=年迈）、"rounded body"（器身，撞上 body=身体）这种假线索挡住。
_NP_STRONG_CUES = (
    # 英文（词边界匹配）
    "male", "female", "man", "woman", "boy", "girl", "lady", "gentleman",
    "person", "human", "figure", "face", "hair", "eyes", "beard", "moustache",
    "skin", "wearing", "dressed", "robe", "dress", "suit", "jacket", "armor",
    "armour", "cloak", "hat", "crown", "child", "kid", "teenager", "warrior",
    "monk", "scholar", "official", "emperor", "king", "queen", "prince", "princess",
    "general", "soldier", "servant", "maid", "priest", "wizard", "hero", "heroine",
    # 拟人 / 动物 / 精灵鬼怪（这些**是**角色，不能当物件剔掉）
    "animal", "cat", "dog", "bird", "fox", "wolf", "tiger", "lion", "horse", "ox",
    "cow", "rabbit", "mouse", "deer", "monkey", "snake", "dragon", "fish", "beast",
    "creature", "spirit", "ghost", "demon", "deity", "god", "goddess", "immortal",
    "fairy", "monster", "alien", "robot", "android", "anthropomorphic",
    # 中文（子串匹配；中文无词边界）
    "男", "女", "少年", "少女", "老人", "老者", "中年", "青年", "孩子", "孩童", "儿童",
    "面容", "脸庞", "长发", "短发", "胡须", "眼睛", "眉毛", "身穿", "身披", "身着",
    "长袍", "衣裳", "盔甲", "花白", "清癯", "面相",
    "猫", "狗", "鸟", "狐", "狼", "虎", "狮", "马", "兔", "猴", "蛇", "龙", "鱼",
    "兽", "精怪", "妖精", "精灵", "妖怪", "鬼", "神仙", "仙", "妖魔", "机器人", "拟人",
)

_NP_WEAK_CUES = (
    "aged", "old", "young", "elderly", "tall", "short", "slim", "thin", "sturdy",
    "body", "character", "protagonist", "role",
    # 中文弱线索**只用双字词**。单字（人/身/年/岁/神/灵/怪）会误命中
    # 「令**人**」「器**身**」「陈**年**」「岁**月**」「**神**秘」「**灵**动」「奇**怪**」
    # 这类**非活物**语境 —— 本脚本第一版就吃了这个亏：「神秘光球」被「神秘」里的"神"
    # 误判成活物，于是躲过了闸门。宁可少一条弱线索，也不留假阳性。
    "年迈", "年纪", "岁数", "身材", "人形", "人类", "人物",
)

_NP_OBJECT_CUES = (
    # 英文（词边界匹配）
    "object", "prop", "item", "vessel", "pot", "teapot", "kettle", "cup", "bowl",
    "jar", "bottle", "vase", "urn", "goblet", "chalice", "flask", "sword", "blade",
    "knife", "dagger", "spear", "halberd", "axe", "bow", "arrow", "weapon", "tool",
    "device", "machine", "engine", "vehicle", "car", "boat", "ship", "cart", "wheel",
    "building", "house", "temple", "pavilion", "tower", "palace", "gate", "door",
    "window", "wall", "roof", "bridge", "sign", "plaque", "banner", "flag", "lantern",
    "lamp", "candle", "mirror", "ring", "necklace", "bracelet", "scroll", "letter",
    "book", "tome", "coin", "statue", "sculpture", "idol", "painting", "mural",
    "screen", "fan", "umbrella", "box", "chest", "cabinet", "table", "chair", "bed",
    "altar", "censer", "artifact", "artefact", "set piece", "furniture", "ornament",
    "orb", "sphere", "gemstone",
    # 中文（子串匹配）
    "樽", "壶", "杯", "碗", "罐", "瓶", "瓮", "皿", "器皿", "器具", "物件", "道具",
    "光球", "球体", "珠",
    "剑", "刀", "枪", "矛", "戈", "戟", "弓", "箭", "兵器", "工具", "器械",
    "车", "船", "舟", "屋", "楼", "亭", "阁", "塔", "殿", "门", "窗", "墙", "瓦",
    "桥", "匾", "牌", "旗", "幡", "灯笼", "油灯", "烛台", "铜镜", "玉佩", "玉环",
    "书卷", "书籍", "钱币", "雕像", "塑像", "画像", "屏风", "折扇", "箱", "盒", "柜",
    "桌", "椅", "床", "香炉", "器物", "陈设", "摆件", "盛器", "青铜",
)


def _cue_hits(text, cues):
    """命中的线索词个数。

    英文线索必须用**词边界**匹配，否则 "man" 会命中 "mansion"（宅院被当成人）、
    "body" 会命中 "vessel body"（器身被当成身体）。中文没有词边界概念，直接子串匹配。
    """
    n = 0
    for w in cues:
        wl = w.lower()
        if wl.isascii():
            if re.search(r"\b" + re.escape(wl) + r"\b", text):
                n += 1
        elif wl in text:
            n += 1
    return n


def style_sfx(obj):
    """从任意持有 `style_prompt` 的对象上取**画风片段**：有画风则前方带一个空格，否则空串。

    刻意做成**模块级函数而不是实例方法**：`_gen_character_views` / `_gen_clue_image`
    会被喂各种桩对象（测试替身只实现 log/_sv/cfg/app_dir/width/height），
    给它新增一个实例方法就会把既有调用方打挂（本轮就因此挂了一次回归）。
    这里只用 getattr 取一个属性，不要求实例上有这个方法。

    两个目的：
    ① **空画风零副作用** —— 没设画风时拼出来的 prompt 与旧版逐字节相同；
    ② **不硬依赖 `style_prompt` 存在** —— 缺属性时退化为空串而不是抛异常。
    """
    s = (getattr(obj, "style_prompt", "") or "").strip()
    return (" " + s) if s else ""


def looks_non_person(name, desc):
    """这条「角色」是不是其实是个物件？（v4.151）

    两条判据，任一命中即判非人物：
      A. 有**物件词**、且**没有任何强活物线索** → 是道具/陈设；
      B. **连一条活物线索都没有**（强弱都没有）→ 不是角色。
    真实角色描述按抽取提示词的规格必然带 age/gender/hair/face/clothing，
    所以不会踩到 B；而「酒樽」的 desc 只有 "aged bronze / rounded body" 这类
    也会出现在器物上的**弱**线索，因此走 A 被安全拦下。
    """
    text = f"{name or ''} {desc or ''}".lower()
    if not text.strip():
        return False                    # 空描述不下判断，交给上游兜底，别误杀
    strong = _cue_hits(text, _NP_STRONG_CUES)
    weak = _cue_hits(text, _NP_WEAK_CUES)
    obj = _cue_hits(text, _NP_OBJECT_CUES)
    if strong == 0 and weak == 0:
        return True                     # B：一点活物线索都没有
    if obj > 0 and strong == 0:
        return True                     # A：有物件词又没有强活物线索
    return False


# ---------- 需求理解（v4.151：写主题后先对齐需求，再开写剧本）----------
# 大哥反馈：「每次我写入主题，它首先给我编造个故事」「它对我的需求理解太差」。
# 根因是「填主题 → 直接开写」中间**没有任何确认环节**，系统的定位又是
# 「把主题快速扩展成微故事」，于是必然自己编。这里补上动工前的澄清。
UNDERSTAND_SOURCE_CHOICES = ("不引用原文", "引用原句作字幕", "用原句作旁白念白")
UNDERSTAND_AUDIO_CHOICES = ("无台词（纯画面 + 字幕）", "人物说中文台词", "旁白解说（后期配音）")


def understand_topic(cfg, topic, style_key="realistic", n=6, duration=5, spec=None):
    """把主题整理成一份「需求理解」草稿（供导演台需求确认卡）。

    只做**理解与复述**，不写剧本、不分镜、不生图 —— 用户确认之后才动工。
    任何异常或解析失败一律返回 None；调用方据此**降级为原行为（直接开写）**，
    绝不因为这一步把流程卡死。
    """
    t = (topic or "").strip()
    if not t:
        return None
    style_hint = STYLE_LABELS.get(style_key, style_key or "")
    try:
        content = _agnes_chat(cfg, [
            {"role": "system", "content": (
                "你是短片制片人，负责在动工前把客户的一句话主题**复述成可执行的需求**。"
                "你只做理解与澄清：不写剧本、不设计分镜、不编造情节。")},
            {"role": "user", "content": (
                f"用户给的主题：{t}\n"
                f"（当前画风设定：{style_hint}；计划 {n} 个镜头、每镜约 {duration} 秒）\n\n"
                "请整理成 JSON：\n"
                "- genre: 一句话说清「我以为你要拍的是什么」——题材 / 立意 / 体裁；"
                "主题若点了某首诗词或某位历史人物，必须写出来\n"
                "- narrative: 建议的叙事方式（一句话，例如「按原作词意逐句展开意境」"
                "或「围绕主角虚构一条微故事线」）\n"
                "- source: 原文使用方式，只能从这三个里选一个："
                + json.dumps(list(UNDERSTAND_SOURCE_CHOICES), ensure_ascii=False) + "\n"
                "- audio: 声音形式，只能从这三个里选一个："
                + json.dumps(list(UNDERSTAND_AUDIO_CHOICES), ensure_ascii=False) + "\n"
                "- notes: 给编剧的补充要求（一句话；没有就填空字符串）\n"
                "只输出 JSON，不要 markdown、不要解释。")},
        ], temperature=0.3)
    except Exception:
        return None
    if not content:
        return None
    txt = content.strip()
    txt = re.sub(r"^```(?:json)?", "", txt).strip()
    txt = re.sub(r"```$", "", txt).strip()
    obj = None
    try:
        obj = json.loads(txt)
    except Exception:
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return None
    out = {
        "genre": str(obj.get("genre") or "").strip(),
        "narrative": str(obj.get("narrative") or "").strip(),
        "source": str(obj.get("source") or "").strip(),
        "audio": str(obj.get("audio") or "").strip(),
        "notes": str(obj.get("notes") or "").strip(),
    }
    # 选项字段必须落在白名单内，否则回退到第一个（防模型自由发挥出奇怪文案）
    if out["source"] not in UNDERSTAND_SOURCE_CHOICES:
        out["source"] = UNDERSTAND_SOURCE_CHOICES[0]
    if out["audio"] not in UNDERSTAND_AUDIO_CHOICES:
        out["audio"] = UNDERSTAND_AUDIO_CHOICES[0]
    if not (out["genre"] or out["narrative"]):
        return None                     # 什么都没理解出来 → 当作失败，走降级
    out["style_key"] = style_key
    return out


def build_setting_block(genre="", narrative="", source="", audio="", notes="", extra=""):
    """把需求卡的字段拼成 `spec["setting"]` 的中文硬约束段（空字段自动省略）。

    返回空串表示什么都没填 —— 调用方据此**不写 spec**，与旧行为完全一致。
    """
    lines = []
    for label, val in (("题材/立意", genre), ("叙事方式", narrative),
                       ("原文使用", source), ("声音形式", audio),
                       ("补充意见", notes), ("额外要求", extra)):
        v = (val or "").strip()
        if v:
            lines.append(f"{label}：{v}")
    return "\n".join(lines)


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


def _agnes_chat(cfg, messages, model=None, temperature=0.7, thinking=False, max_tokens=None):
    """同步调用 Agnes 对话接口，返回助手文本。失败抛 RuntimeError。

    v4.128：改走统一文本调用层 agnes_text——文本模型可配置（3.0/2.5 两档，默认 2.5），
    选用 3.0 时带 2.5 回退链（超时/5xx/429/空响应/结构异常自动回退一次）。
    模型解析顺序：显式入参 > cfg.agnes_text_model > Agnes 档位 chat_model > 档位 model > 2.5。
    """
    try:
        import agnes_text
    except Exception:
        # 调用层缺失（极端情况）时退回直连，保证导演台不至于整体不可用。
        base, key = _agnes_creds(cfg)
        if not model:
            model = (((cfg.get("model_profiles") or {}).get("Agnes", {}) or {}).get("chat_model")
                     or cfg.get("video_chat_model") or "agnes-2.5-flash")
        payload = json.dumps(
            {"model": model, "messages": messages, "temperature": temperature},
            ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                     data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise RuntimeError(f"Agnes 对话接口失败：{e}")
    if not model:
        # 兼容旧口径：video_chat_model 是导演台专用覆盖项，优先级高于全局文本模型配置
        model = ((cfg or {}).get("video_chat_model") or "").strip() or None
    return agnes_chat_module(cfg, messages, model=model, temperature=temperature,
                             thinking=thinking, max_tokens=max_tokens)


def agnes_chat_module(cfg, messages, model=None, temperature=0.7,
                      thinking=False, max_tokens=None):
    """转调 agnes_text.agnes_chat（独立成函数：验证脚本可直接 stub 本函数抓调用参数）。"""
    import agnes_text
    return agnes_text.agnes_chat(cfg, messages, model=model, temperature=temperature,
                                 thinking=thinking, max_tokens=max_tokens)


def _version_ts(p):
    """从版本备份文件名里抠时间戳（clip_3_20260910_163000_012.mp4 → 20260910_163000_012）。"""
    if not p or not os.path.isfile(p):
        return ""
    m = re.search(r"_(\d{8}_\d{6}(?:_\d{3})?)\.", os.path.basename(p))
    return m.group(1) if m else ""


class VideoPipeline:
    """多镜视频导演流水线（无 UI，回调驱动）。"""

    def __init__(self, cfg, app_dir, callbacks=None, auto_approve=True):
        self.cfg = cfg
        self.app_dir = app_dir
        self.cb = callbacks or {}
        self.auto_approve = auto_approve
        self.ffmpeg = find_ffmpeg()
        self.cancelled = False

        # ---- v4.167.0（审查 §1）：任务级取消令牌，取代「永久布尔」 ----
        # 原实现只有 self.cancelled 一个全局永久布尔：停止置 True 后**永不恢复**，
        # 于是 regenerate_clip() 一看到 True 就直接返回 None —— 用户点了停止
        # 就只能「重置项目」，前面写好的剧本/分镜/关键帧被迫全丢。
        # 现在：停止只取消**当前这一轮**；重试/继续会 begin_job() 换发新令牌。
        self._job_token = None
        self._job_seq = 0
        self._job_name = ""
        self._job_lock = threading.Lock()
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
        # 抗崩坏 v3（参考 ArcReel 的 clues）：跨镜复用的「关键道具 / 场景资产」追踪。
        # 人物锁只锁人，道具与陈设照样会跨镜漂移（同一把剑换了造型、同一块招牌换了字）。
        # clues 为空时全链路不注入任何文本/参考图 —— Auto 默认行为与旧版完全一致。
        self.clues = []               # [{"name","desc","kind":"prop|set","image":path|None}]
        self.clue_lock = ""           # 写入逐镜提示词的道具/资产锁定描述（英文）
        self.clues_done = False
        self.clue_versions = {}       # {idx: [{"image":path,"desc":str}, ...]}
        # v4.127 多参考图增强：默认开。关掉后逐镜只走旧的单首帧/单关键帧链路。
        self.multiref = True
        # v4.132 项目制作规格（Flova「文档区」同款）：故事设定 / 视觉风格 / 镜头语言 /
        # 禁用内容 / 输出要求。写一次，之后每次生成（剧本·分镜·人物·道具·关键帧·视频）
        # 都自动带上，免去每轮重说。空字典 = 与旧版完全一致，零副作用。
        self.spec = {}
        # v4.132 Prompt 草稿预审：用户改过的 prompt 存这里（key=镜号）。
        # 为空表示没预审过，走原逻辑——保证不预审的链路字节级不变。
        self.keyframe_prompt_override = {}
        self.clip_prompt_override = {}
        # v4.133 可视化时间线：None = 没开过时间线面板（全链路与旧版完全一致）；
        # 用户在面板里保存后才变成 dict，此时 _build_segs 改走 build_timeline_segs()。
        # 结构见 timeline_default()；trim/trans 的键是 str（JSON 只认字符串键）。
        self.timeline = None
        self._trans_cache = {}        # {(kind, 时长): 转场片段路径} 逐镜不同转场时长要分开缓存
        # 版本回滚（v后续）：每次 revise 覆盖前把当前文件备份到 versions/，支持一键回退上一版
        self.versions_dir = None
        self.clip_versions = {}       # {i: [version_file_path, ...]} 历史栈（旧→新）
        self.keyframe_versions = {}   # {i: [version_file_path, ...]}
        self.character_versions = {}  # {idx: [{"views":[3 png], "desc":str}, ...]}
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
                burn_subtitles, passthrough_script, clip_dir=None, spec=None,
                smart=True):
        """锁定全部参数、建立工程目录、初始化运行期状态。

        v4.132：spec = 项目制作规格字典（见 set_spec）。传 None 与旧版一致。
        v4.154：smart = 「AI 智能分镜」开关。开启时 AI 按剧本自主决定分镜数量与
                每镜时长（4~12 秒），下拉框降为兜底默认；关闭时严格按下拉框硬执行。
        """
        self.topic = topic
        self.n = n
        self.duration = duration
        self.smart = smart
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
            # v4.129：走分层目录（dated 时 产物/YYYY-MM-DD/<项目>/视频/director_xxx）
            clip_dir = os.path.join(_vp_products_dir(getattr(self, "cfg", None), "video"),
                                    f"director_{ts}")
        os.makedirs(clip_dir, exist_ok=True)
        self.project_dir = clip_dir
        # 版本回滚：建 versions/ 子目录 + 清空历史栈（保证每次新建工程干净起步）
        self.versions_dir = os.path.join(clip_dir, "versions")
        os.makedirs(self.versions_dir, exist_ok=True)
        self.clip_versions = {}
        self.keyframe_versions = {}
        self.character_versions = {}
        self.clue_versions = {}
        self.story = ""
        self.shots = []
        self.clip_paths = []
        self.spec = spec if isinstance(spec, dict) else {}
        self.keyframe_prompt_override = {}
        self.clip_prompt_override = {}
        # 新工程干净起步：时间线/转场缓存都不继承上一次的
        self.timeline = None
        self._trans_cache = {}
        return self.project_dir

    # ---------- v4.132 项目制作规格（文档区） ----------
    # 五个字段都是「人写给 AI 看」的中文短文，空着就不注入任何东西。
    SPEC_FIELDS = (
        ("setting", "故事设定"),
        ("style", "视觉风格"),
        ("camera", "镜头语言"),
        ("forbid", "禁用内容"),
        ("output", "输出要求"),
    )

    def set_spec(self, spec):
        """热更新项目制作规格（保存即可生效，不必重开工程）。"""
        self.spec = spec if isinstance(spec, dict) else {}
        return self.spec

    def _spec_val(self, key):
        v = str((self.spec or {}).get(key) or "").strip()
        return v

    def spec_text(self):
        """给「中文文本生成」（剧本 / 分镜 / 角色 / 道具）的项目规格段。

        空规格返回 ""——调用方拼接时零附加字符，旧行为不变。
        """
        if not self.spec:
            return ""
        lines = [f"- {label}：{self._spec_val(k)}"
                 for k, label in self.SPEC_FIELDS if self._spec_val(k)]
        if not lines:
            return ""
        return ("\n【项目制作规格（全片硬约束，任何一步都不得违背）】\n"
                + "\n".join(lines) + "\n")

    def spec_visual(self):
        """给「画面生成」（关键帧 / 视频 / 三视图 / 道具图）的规格约束（附在 prompt 尾）。"""
        if not self.spec:
            return ""
        parts = []
        for k, en in (("style", "Style"), ("camera", "Camera"), ("forbid", "Avoid")):
            v = self._spec_val(k)
            if v:
                parts.append(f"{en}: {v}")
        if not parts:
            return ""
        return "[PROJECT SPEC] " + " | ".join(parts)

    def _sv(self):
        """画面 prompt 里用的规格片段：有内容则前方带空格，无内容返回空串。

        ——保证「没写规格」时拼出来的 prompt 与旧版逐字节相同。
        """
        v = self.spec_visual()
        return (" " + v) if v else ""

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
        # v4.154：把兜底每镜时长补进每个 shot（智能模式 AI 已给 dur，这里只兜底缺失项）
        for _s in self.shots:
            if not isinstance(_s, dict) or not _s.get("dur"):
                _s["dur"] = max(4, min(12, int(self.duration or 5)))
        if not self.shots:
            raise RuntimeError("分镜解析为空，请重试")
        self.clip_paths = [None] * len(self.shots)
        return self.shots

    def set_shots(self, shots):
        self.shots = shots
        self.clip_paths = [None] * len(shots)

    def _shot_dur(self, shot):
        """v4.154：取某一镜的实际生成时长（秒），强制夹到 Agnes 合法区间 4~12。

        智能模式 shot 自带 AI 决定的 dur（越界夹边界、保留意图）；
        手动模式 / 缺失 → 回落 self.duration。
        """
        d = shot.get("dur") if isinstance(shot, dict) else None
        try:
            d = int(round(float(d)))
            return max(4, min(12, d))
        except (TypeError, ValueError):
            return max(4, min(12, int(self.duration or 5)))

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
        dropped = []
        for spec in specs[:3]:
            name = spec.get("name") or "主角"
            desc = spec.get("desc") or ""
            # v4.151：机器闸门 —— 剧本里若把道具写成了「关键配角」（大哥那次就是
            # 「酒樽，一只古朴的青铜酒壶」），这里直接拦掉，绝不让它去生成"人物"三视图。
            if looks_non_person(name, desc):
                dropped.append(name)
                self.log(f"  ⏭️ 已剔除「{name}」：它看起来是道具/陈设而不是人物，"
                         f"不生成人物三视图。")
                continue
            views = self._gen_character_views(name, desc)
            chars.append({"name": name, "desc": desc, "views": views})
        if dropped:
            self.log("  ℹ️ 被剔除的条目请用「🔎 抽取关键道具/场景资产」立档，"
                     "让它们走道具锁定而不是人物锁定：" + "、".join(dropped))
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
            "A character means a PERSON or an ANTHROPOMORPHIC living being "
            "(e.g. a talking fox, a spirit in human form).\n"
            "NEVER include props, vessels, weapons, tools, costumes, buildings, set "
            "pieces, scenery or any inanimate object — even if the script gives them a "
            "name or treats them as symbolic. Those belong to a separate props list.\n"
            "For each return a JSON object:\n"
            "- name: character name or role (e.g. '主角小明')\n"
            "- desc: a concise ENGLISH visual description (age, gender, hair, face, "
            "clothing, distinguishing features) that stays identical across shots.\n"
            "Every desc MUST describe a living being; if you cannot describe age / "
            "gender / face / clothing for it, it is NOT a character — leave it out.\n"
            "Return ONLY a JSON array like "
            '[{"name":"...","desc":"..."}, ...]. No markdown, no extra text.'
            + fb + self.spec_text())
        try:
            content = _agnes_chat(self.cfg, [
                {"role": "system", "content": (
                    "You are a character designer for AI video. Read a short script and "
                    "extract the MAIN characters (at most 3) that appear across multiple "
                    "shots. A character is strictly a person or an anthropomorphic living "
                    "being — never an object, prop, vessel, weapon, building or set piece. "
                    "For each character, give a stable English visual description reusable "
                    "in every shot to keep the character consistent. When in doubt, return "
                    "fewer characters: a wrong entry (an object) is far worse than a "
                    "missing one, because it will be rendered as a human being.")},
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

    @staticmethod
    def _looks_non_person(name, desc):
        """这条「角色」是不是其实是个物件？——见模块级 looks_non_person() 的判据说明。

        保留这个薄别名只为兼容既有调用点；**新代码请直接用模块级 `looks_non_person()`**，
        它不依赖实例，对测试替身（只实现部分方法的桩对象）也友好。
        """
        return looks_non_person(name, desc)

    def _gen_character_views(self, name, desc):
        """为一个角色生成 正面/侧面/背面 三视图，返回 3 个图片路径（失败的为 None）。

        v4.151 两处修复（大哥反馈「我要的是水墨动画，它把主角三视图干成真人」）：
        ① **注入画风**：本函数此前是全项目**唯一不拼 `style_prompt` 的生图环节**
           （道具图/场景图/关键帧/分镜全都拼了），所以无论把风格设成什么、哪怕主题里
           明写"水墨动画"，三视图永远是写实真人。现改为与其它环节一致地拼上画风。
        ② **非人物前置守卫**：desc 若显示这是件物件（如「青铜酒樽」），直接报错——
           旧行为会把它喂进 `of ONE person` 的模板，画出「一个人捧着酒樽」的三视图，
           既离谱又白花钱。守卫在调接口**之前**，所以拦下时零费用。
        """
        if looks_non_person(name, desc):
            raise RuntimeError(
                f"「{name}」看起来不是人物（道具 / 陈设 / 物件），已拒绝生成人物三视图。"
                "它应该走「🔎 抽取关键道具/场景资产」立档，而不是当角色。")
        views = []
        specs = [
            ("front", "front view, full body facing the camera"),
            ("side", "side view, profile facing left"),
            ("back", "back view, seen from behind"),
        ]
        for _vname, vpose in specs:
            # 中性背景与均匀布光**保留**（为了看清形体结构），但笔触/色彩/质感跟随全片画风。
            prompt = (
                f"Character design turnaround sheet, {vpose}, of ONE person: {desc}. "
                f"Consistent character design, centered, full body visible. "
                f"Clean solid neutral background, even studio lighting. "
                f"High detail, sharp focus.{style_sfx(self)}{self._sv()}")
            try:
                # v4.134.10 修复：tool_image_gen 契约是「成功=3元组 / 失败=错误字符串」，
                # 直接 a,b,c= 解包会把失败时的错误字符串按字符拆开 →
                # "too many values to unpack (expected 3)"，掩盖真实原因。
                res = tool_image_gen(
                    self.cfg, self.app_dir, prompt,
                    size=f"{self.width}x{self.height}")
                if not isinstance(res, tuple):
                    self.log(f"  ⚠️ 角色「{name}」{_vname} 视图生成失败：{res}")
                    views.append(None)
                    continue
                rel = res[0]
                path = os.path.join(self.app_dir, rel)
                views.append(path if os.path.isfile(path) else None)
            except Exception as e:
                self.log(f"  ⚠️ 角色「{name}」{_vname} 视图生成失败：{e}")
                views.append(None)
        return views

    @staticmethod
    def _build_character_lock(chars):
        """把角色列表拼成英文锁定串；空列表返回空串（→ 全链路不注入）。

        v4.151：拼之前先过一遍非人物闸门。人物锁会被注入**每一镜**的画面提示词，
        一旦混进道具（酒樽那种），全片都会被误导「这件器物有脸有头发有身材」——
        所以这里必须再过滤一次，不能只依赖上游。
        """
        if not chars:
            return ""
        parts = []
        for c in chars:
            name = (c.get("name") or "").strip()
            desc = (c.get("desc") or "").strip()
            if looks_non_person(name, desc):
                continue
            if name or desc:
                parts.append(f"{name}: {desc}" if name else desc)
        if not parts:
            return ""
        return ("[CHARACTER LOCK] Keep these characters visually IDENTICAL in every shot "
                "(same face, hair, body shape, clothing, age): " + "; ".join(parts) +
                ". Do NOT alter their appearance between shots.")

    # ---------- 第1.6步：关键道具 / 场景资产追踪（clue，抗崩坏 v3，参考 ArcReel） ----------
    def gen_clues(self, feedback=None):
        """从剧本抽取跨镜复用的「关键道具 / 场景资产」，每个生成一张参考图。

        人物锁只锁人；道具与陈设一样会跨镜漂移（同一把剑换造型、同一块招牌换字）。
        clue 就是 ArcReel 里的 clues：把这些「视觉资产」也单独立档 + 生参考图 + 写锁定串。

        portrait_mode 下跳过（口播画面没有跨镜道具叙事）。
        失败/抽不出来时保持 clues 为空 —— 后续全链路不注入，行为与旧版一致。
        """
        self.clues = []
        self.clue_lock = ""
        if self.portrait_mode:
            self.clues_done = True
            return self.clues
        if not self.story:
            raise RuntimeError("请先生成剧本")
        specs = self._extract_clue_specs(feedback=feedback)
        items = []
        for spec in specs[:4]:
            name = (spec.get("name") or "").strip()
            desc = (spec.get("desc") or "").strip()
            if not (name or desc):
                continue
            kind = "set" if str(spec.get("kind", "")).lower().startswith("s") else "prop"
            image = self._gen_clue_image(name or "prop", desc, kind)
            items.append({"name": name or "prop", "desc": desc,
                          "kind": kind, "image": image})
        self.clues = items
        self.clue_lock = self._build_clue_lock(items)
        self.clues_done = True
        return self.clues

    def set_clues(self, clues, clue_lock=""):
        self.clues = clues or []
        self.clue_lock = clue_lock or self._build_clue_lock(self.clues)
        self.clues_done = True

    def _extract_clue_specs(self, feedback=None):
        """让 LLM 从剧本抽取跨镜复用的关键道具/场景资产（英文视觉描述），最多 4 个。"""
        fb = ""
        if feedback:
            fb = (f"\nRevision note: {feedback}\n"
                  "Output the revised list.\n")
        user_prompt = (
            f"Script:\n<<<\n{self.story}\n>>>\n\n"
            "List the KEY VISUAL ASSETS that appear in MORE THAN ONE shot and must stay "
            "identical every time they appear. Two kinds:\n"
            "- prop: an object the characters carry or use (weapon, letter, phone, cup, "
            "necklace, vehicle...)\n"
            "- set: a signature part of the location (a shop sign, a specific door, a "
            "painting on the wall, a landmark building...)\n"
            "Ignore anything that shows up in only one shot, and ignore the characters "
            "themselves (people are handled separately). At most 4 items.\n"
            "For each return a JSON object:\n"
            "- name: short label, may be Chinese (e.g. '青铜短剑')\n"
            "- kind: \"prop\" or \"set\"\n"
            "- desc: a concise ENGLISH visual description (shape, size, color, material, "
            "wear, markings) precise enough to redraw it identically.\n"
            "Return ONLY a JSON array like "
            '[{"name":"...","kind":"prop","desc":"..."}, ...]. '
            "If the script has no such recurring asset, return []. "
            "No markdown, no extra text." + fb + self.spec_text())
        try:
            content = _agnes_chat(self.cfg, [
                {"role": "system", "content": (
                    "You are a props and set-dressing supervisor for AI video. Read a "
                    "short script and list only the recurring props / set pieces that "
                    "must look identical across shots. Be conservative: fewer, truly "
                    "recurring items beat a long speculative list.")},
                {"role": "user", "content": user_prompt},
            ], temperature=0.4)
            specs = self._parse_clue_specs(content)
        except Exception as e:
            self.log(f"  ⚠️ 道具/场景资产抽取失败（{e}），本次不做道具锁定")
            specs = []
        return specs

    @staticmethod
    def _parse_clue_specs(content):
        """解析 LLM 返回的 clue JSON；抽不出来一律返回 []（宁缺勿滥，空=不注入）。"""
        if not content:
            return []
        txt = content.strip()
        txt = re.sub(r"^```(?:json)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()

        def _norm(arr):
            out = []
            for x in arr[:4]:
                if isinstance(x, dict):
                    out.append({"name": str(x.get("name", "")).strip(),
                                "kind": str(x.get("kind", "prop")).strip(),
                                "desc": str(x.get("desc", "")).strip()})
            return out

        try:
            obj = json.loads(txt)
            arr = obj.get("clues") if isinstance(obj, dict) else obj
            if isinstance(arr, list):
                return _norm(arr)
        except Exception:
            pass
        m = re.search(r"\[.*\]", txt, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list):
                    return _norm(arr)
            except Exception:
                pass
        return []

    def _gen_clue_image(self, name, desc, kind="prop"):
        """为一个道具/场景资产生成一张参考图（道具=白底特写，资产=局部环境图）。

        v4.151：`prop` 分支此前漏了 `style_prompt`（只有 `set` 分支有）——
        水墨片会拿到一张照片级写实的道具参考图，与全片画风割裂。现两条分支都拼画风，
        并统一用 `_style_sfx()` / `getattr` 兜底：空画风时与旧版逐字节相同。
        """
        _st = getattr(self, "style_prompt", "") or ""
        if kind == "set":
            prompt = (
                f"Reference photo of ONE distinctive set piece with NO people: {desc}. "
                f"Medium shot centered on this element, natural lighting, "
                f"{_st}. High detail, sharp focus, no text overlay, "
                f"no watermark.{self._sv()}")
        else:
            prompt = (
                f"Product-style reference photo of ONE single object, isolated: {desc}. "
                f"Centered on a clean solid neutral background, even studio lighting, "
                f"no hands, no people, no other objects.{style_sfx(self)} "
                f"High detail, sharp focus, no text overlay, no watermark.{self._sv()}")
        return self._gen_one_image(prompt, tag=f"道具/资产「{name}」参考图")

    @staticmethod
    def _build_clue_lock(clues):
        """把 clue 列表拼成英文锁定串；空列表返回空串（→ 全链路不注入）。"""
        if not clues:
            return ""
        parts = []
        for c in clues or []:
            name = (c.get("name") or "").strip()
            desc = (c.get("desc") or "").strip()
            if not (name or desc):
                continue
            tag = "set piece" if str(c.get("kind", "")).lower() == "set" else "prop"
            parts.append(f"{name} ({tag}): {desc}" if name else f"{tag}: {desc}")
        if not parts:
            return ""
        return ("[PROP & SET LOCK] These recurring items must look IDENTICAL every time "
                "they appear (same shape, size, color, material, wear and markings): " +
                "; ".join(parts) +
                ". Do NOT redesign, recolor or replace them between shots.")

    def _locks_text(self):
        """把人物锁 + 道具/资产锁拼成一段（任一为空自动省略）。"""
        return " ".join(x for x in (self.character_lock or "", self.clue_lock or "") if x)

    def _shot_clues(self, i):
        """判断本镜出现的 clue：用名字在分镜文本里匹配。

        ⚠️ 与 _shot_characters 不同：匹配不到时返回空列表（**不兜底塞主道具**）——
        把没出现的道具参考图塞进去，会诱导模型把无关物件画进画面，反而制造崩坏。
        """
        if not self.clues:
            return []
        shot = self.shots[i] if i < len(self.shots) else {}
        text = " ".join(str(shot.get(k) or "") for k in ("zh", "en", "line"))
        if not text.strip():
            return []
        hits = []
        for c in self.clues:
            name = (c.get("name") or "").strip()
            if name and name in text:
                hits.append(c)
        return hits

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
        lock = self._locks_text()   # 人物锁 + 道具/资产锁（clue 为空时等于原来的人物锁）
        fb = f"[修改意见：{feedback}] " if feedback else ""
        kfs = []
        for i, shot in enumerate(self.shots):
            if self._is_cancelled():
                kfs.append(None)
                continue
            qc_fix = ""
            path = None
            base = self.keyframe_prompt_override.get(i) or ""
            for attempt in range(max_review_retry + 1):
                if base:
                    # 预审改过稿：以用户那版为准，质检诊断仍追加在后面（不覆盖用户文字）
                    prompt = f"{base} {qc_fix}".strip()
                else:
                    prompt = self._build_keyframe_prompt(i, qc_fix=qc_fix,
                                                         feedback=feedback)
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

    # ---------- v4.132 Prompt 草稿预审 ----------
    def _build_keyframe_prompt(self, i, qc_fix="", feedback=None):
        """构造第 i 镜关键帧 prompt。预审界面与真实生成共用同一段代码，
        避免「看到的草稿」和「实际烧钱的 prompt」不一致。"""
        shot = self.shots[i] if i < len(self.shots) else {}
        en = shot.get("en") or shot.get("zh") or "a cinematic scene"
        lock = self._locks_text()
        fb = f"[修改意见：{feedback}] " if feedback else ""
        return (
            f"Keyframe and environment concept art for ONE video shot: {en}. "
            f"{lock} {fb}{qc_fix} "
            f"High detail, cinematic composition, consistent lighting and visual "
            f"style, no text, no watermark, no extra characters.{self._sv()}")

    def build_keyframe_drafts(self):
        """返回每镜关键帧 prompt 草稿 [(镜号, prompt), ...]，供预审界面编辑。"""
        return [(i + 1, self.keyframe_prompt_override.get(i)
                 or self._build_keyframe_prompt(i))
                for i in range(len(self.shots))]

    def build_clip_drafts(self):
        """返回每镜视频 prompt 草稿 [(镜号, prompt), ...]。

        ⚠️ 草稿不含「上一镜尾帧」那张参考图的指代（尾帧要等上一镜生成完才有），
        生成时会动态追加一句 Continue from <Picture N>——草稿只保证主体文字一致。
        """
        out = []
        for i in range(len(self.shots)):
            refs = self._assemble_ref_images(i)
            out.append((i + 1, self.clip_prompt_override.get(i)
                        or self._build_clip_prompt(i, refs=refs)))
        return out

    def set_prompt_overrides(self, kind, drafts):
        """存下预审改过的 prompt。drafts = {镜号(1基): prompt文本}。
        空字符串表示该镜没改动 → 不覆盖（走原逻辑）。"""
        store = (self.keyframe_prompt_override if kind == "keyframe"
                 else self.clip_prompt_override)
        n = 0
        for idx, text in (drafts or {}).items():
            try:
                i = int(idx) - 1
            except Exception:
                continue
            if i < 0 or i >= len(self.shots):
                continue
            t = str(text or "").strip()
            if t:
                store[i] = t
                n += 1
            else:
                store.pop(i, None)
        return n

    def _gen_one_image(self, prompt, tag="图"):
        """通用生图：返回本地绝对路径，失败返回 None（关键帧/场景图共用）。"""
        try:
            # v4.134.10 修复：同 _gen_character_views，遵守 tool_image_gen
            # 「成功=元组 / 失败=错误字符串」契约，避免解包错误字符串崩溃。
            res = tool_image_gen(
                self.cfg, self.app_dir, prompt,
                size=f"{self.width}x{self.height}")
            if not isinstance(res, tuple):
                self.log(f"  ⚠️ {tag}生成失败：{res}")
                return None
            rel = res[0]
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
                f"{self.style_prompt}. Cinematic, high detail, no text, no watermark."
                f"{self._sv()}")
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
        extra = ""
        if self.clue_lock:
            extra = ("\nAlso verify the recurring props / set pieces listed above look "
                     "identical to their locked descriptions (shape, color, material); "
                     "report any mismatch under ISSUES.")
        question = (f"Required shot description: {desc}\n"
                    f"{self._locks_text()}{extra}\n\n{self.REVIEW_QUESTION}")
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

    # ---------- v4.127：资产库反喂（本管线没生成过的角色/场景，去库里找存货） ----------
    def _asset_character_refs(self, character, top=2):
        """从资产库取角色三视图补位。任何异常/找不到都返回 []——少一张图不致命。

        character 为空 = 本项目唯一主角兜底（不按名字过滤，取该项目最新的三视图）。
        """
        if not self.multiref:
            return []
        try:
            import asset_store
            paths = asset_store.pick_character_refs(
                project=getattr(self, "topic", "") or "",
                character=(character or None), top=top)
        except Exception:
            return []
        out = [p for p in (paths or []) if p and os.path.isfile(p)]
        if out:
            self.log(f"  🗃️ 角色「{character or '本项目主角'}」参考图取自资产库（{len(out)} 张）")
        return out

    def _asset_scene_ref(self, sc):
        """从资产库取场景图补位。找不到返回 None。"""
        if not self.multiref:
            return None
        try:
            import asset_store
            p = asset_store.pick_scene_ref(
                project=getattr(self, "topic", "") or "",
                scene_tag=self._scene_tag(sc))
        except Exception:
            return None
        if p and os.path.isfile(p):
            self.log(f"  🗃️ 场景 {sc} 参考图取自资产库")
            return p
        return None

    @staticmethod
    def _scene_tag(sc):
        """场景检索词。分镜表里只有场景编号没有名字，返回 None 交给 project 收窄。"""
        return None

    def _assemble_ref_images(self, i, prev_tail=None):
        """为第 i 镜装配参考图，返回 [(path, role), ...]，最多 AGNES_MAX_REF_IMAGES 张。

        v4.127 五槽（工单）：凑满 5 张优先，不足有几张给几张，同一张图不重复占槽。

          1 本镜关键帧（开场画面 / 构图锚点）
          2 各出场角色三视图·正面（锁脸 + 服装）
          3 主角三视图·侧面（锁多角度，防转头崩）
          4 本场景环境图（锁环境）
          5 本镜关键道具 clue（抗崩坏 v3）
          6 上一镜尾帧（reference 模式下的尾帧接力等价物）
          7 其余角色侧面（还有余额才给）

        ⚠️ 截断顺序沿用既有：**关键帧（开场锚点）最优先**。工单表格里的「槽位」
        说的是「要凑哪些图」，不是截断优先级；每张图的作用由 role 决定，
        <Picture N> 指代与顺序无关，谁在第几位不影响效果。

        资产缺失（本管线没生成过 + 库里也没有）→ **降级少一张 + 日志写明缺什么**，
        绝不中断生成。
        """
        refs = []

        def _add(path, role):
            if not path or not os.path.isfile(path):
                return False
            if len(refs) >= AGNES_MAX_REF_IMAGES:
                return False
            if path in [r[0] for r in refs]:   # 去重（同一张图别占两个位置）
                return False
            refs.append((path, role))
            return True

        _kf = None
        if self.keyframes and i < len(self.keyframes) and self.keyframes[i]:
            _kf = self.keyframes[i]
        elif self.portrait_mode and self.ref_image_path:
            _kf = self.ref_image_path
        elif i == 0 and self.ref_image_path:
            _kf = self.ref_image_path
        if not self.multiref:
            # 关闭「多参考图增强」：退回旧的单参考图（本镜关键帧）链路
            return [(_kf, "keyframe")] if _kf and os.path.isfile(_kf) else []

        chars = [] if self.portrait_mode else self._shot_characters(i)
        # 1) 本镜关键帧：开场画面与构图的最强锚点
        _add(_kf, "keyframe")
        # 2) 出场角色三视图正面：人物外观锁定
        for c in chars:
            views = [v for v in (c.get("views") or []) if v]
            if views:
                _add(views[0], f"character:{c.get('name', '')}")
            elif self.multiref:      # 本管线没生成过 → 资产库补位
                for p in self._asset_character_refs(c.get("name") or "", top=2):
                    _add(p, f"character:{c.get('name', '')}")
        # 3) 主角侧面：防转头崩（只给主角，避免挤掉场景/尾帧）
        if chars:
            lead = chars[0]
            views = [v for v in (lead.get("views") or []) if v]
            if len(views) > 1:
                _add(views[1], f"character:{lead.get('name', '')}")
        # 4) 本场景环境图：场景锁定
        sc = (self.shots[i].get("scene") or 1) if i < len(self.shots) else 1
        # ⚠️ 只在**压根没有场景图**时才去资产库补位。若 _add 失败是因为与关键帧
        # 撞图（去重），不能再去库里拉一张 —— 那会把无关场景塞进本镜。
        if self.scene_images.get(sc):
            _add(self.scene_images.get(sc), "scene")
        else:
            _add(self._asset_scene_ref(sc), "scene")
        # 5) 本镜出现的关键道具/场景资产（clue，抗崩坏 v3）
        if not self.portrait_mode:
            for c in self._shot_clues(i):
                _add(c.get("image"), f"clue:{c.get('name', '')}")
        # 6) 上一镜尾帧：reference 模式下尾帧接力的等价物（有参考图时首帧被置空，
        #    不补这一张，跨镜动作连贯性就断了）
        if self.relay:
            _add(prev_tail, "tail")
        # 7) 还有余额才给其余角色侧面
        for c in chars[1:]:
            views = [v for v in (c.get("views") or []) if v]
            if len(views) > 1:
                _add(views[1], f"character:{c.get('name', '')}")
        # 注：本任务压根没生成过角色（characters 空）时**不去资产库兜底**——
        # 那等于拿别的项目的角色图硬塞进来（记忆铁律：禁止全局扫描自动注入）。
        # 「没有角色字段则取本项目唯一主角」由 _shot_characters 兜底回主角实现。
        return refs

    def _ref_missing_text(self, i, refs):
        """装配后还缺哪些槽位（用于「本镜参考图 N/5，缺：…」降级日志）。"""
        roles = [r[1] for r in refs]
        miss = []
        if not any(r == "keyframe" for r in roles):
            miss.append("关键帧")
        if not self.portrait_mode and not any(r.startswith("character:") for r in roles):
            miss.append("角色图")
        if not any(r == "scene" for r in roles):
            miss.append("场景图")
        if not self.portrait_mode and not any(r.startswith("clue:") for r in roles) \
                and self._shot_clues(i):
            miss.append("道具图")
        if self.relay and not any(r == "tail" for r in roles) and i > 0:
            miss.append("尾帧")
        return miss

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
                name = role.split(":", 1)[1] or "the main character"
                parts.append(
                    f"<Picture {idx}> shows character {name} — reproduce this exact "
                    f"face, hairstyle, body shape and outfit")
            elif role == "scene":
                parts.append(
                    f"<Picture {idx}> is the location — keep the same place, props, "
                    f"architecture and lighting")
            elif role == "tail":
                parts.append(
                    f"<Picture {idx}> is the exact final frame of the PREVIOUS shot — "
                    f"continue the action and motion continuously from it, keep the same "
                    f"subject position, lighting and camera direction")
            elif role.startswith("clue:"):
                cname = role.split(":", 1)[1]
                parts.append(
                    f"<Picture {idx}> shows the recurring item {cname} — reproduce it "
                    f"exactly (same shape, color, material and markings), do not "
                    f"redesign it")
        if not parts:
            return ""
        return ("[REFERENCE IMAGES] " + "; ".join(parts) +
                ". Do NOT ignore these references.")

    # ---------- 台词清洗（v4.126.1 中文锁死）----------
    _DLG_BRACKET_RE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]|\*[^*]{1,10}\*")
    _DLG_LEAD_RE = re.compile(r"^\s*(?:台词|对白|旁白|line|dialogue)\s*[:：]\s*", re.I)
    _DLG_JUNK_RE = re.compile(r"[\"'“”‘’*#`《》<>｜|]")

    @classmethod
    def clean_dialogue(cls, line, max_len=60):
        """把台词洗成「能安全念出来的纯中文单句」，洗不出来返回 ""。

        2026-09-09 实测：分镜 line 里混着引号、换行、（叹气）这类动作描写、
        以及英文提示词残留，Agnes 会照本宣科全念出来 → 音轨乱七八糟。
        这里统一剥掉，并做中文占比校验（非中文为主 = 提示词残留，直接不念）。
        """
        if not line:
            return ""
        d = str(line).strip()
        if not d:
            return ""
        d = d.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        d = cls._DLG_LEAD_RE.sub("", d)          # 剥「台词：」这类前缀
        d = cls._DLG_BRACKET_RE.sub("", d)       # 剥（叹气）[动作] 等描写
        d = cls._DLG_JUNK_RE.sub("", d)          # 剥引号/markdown/书名号
        d = re.sub(r"\s+", " ", d).strip(" 　:：，,。.、;；-—")
        if not d:
            return ""
        han = len(re.findall(r"[\u4e00-\u9fff]", d))
        # 中文不足两成或压根没汉字 → 判定为英文提示词残留，别念
        if han < 2 or han / max(len(d), 1) < 0.20:
            return ""
        if len(d) > max_len:
            cut = d[:max_len]
            for p in ("。", "！", "？", "，", "、"):
                k = cut.rfind(p)
                if k >= max_len // 2:
                    cut = cut[:k + 1]
                    break
            d = cut
        return d

    # v4.147.3：某镜彻底没有任何画面描述时的中性兜底
    NEUTRAL_PROMPT = "cinematic scene, continuous visual, no text overlay"

    def _recover_missing_en(self, zh, idx):
        """v4.147.3：某镜缺英文画面提示词(en)时的补救。

        历史写法 `shot.get("en") or shot.get("zh")` 会把 zh —— 它在本项目里的定义是
        「这一镜的**中文字幕/旁白**」——直接拿去当画面提示词，视频模型于是把旁白
        念出来或渲染成字幕，成品凭空多一条旁白（大哥实测现象）。这里改成：
          ① 用 LLM 从中文旁白反推一句英文视觉描述（只写画面，不含台词）
          ② 失败/结果不可用 → 中性兜底 + **显式日志**，绝不静默降级
        """
        name = f"镜{idx + 1}"
        zh = (zh or "").strip()
        if not zh:
            self.log(f"  ⚠️ {name} 缺 en 且无旁白可依，改用中性画面兜底（画面可能与剧本不符）")
            return self.NEUTRAL_PROMPT
        try:
            sysmsg = ("You are an AI video prompt expert. Convert the given Chinese scene "
                      "description into ONE English visual prompt sentence: write only visual "
                      "content (subject, action, camera movement, lighting, mood). "
                      "Do NOT include any spoken line, narration or caption text. "
                      "Output only that one English sentence.")
            umsg = f"Chinese scene: {zh}"
            if self.style_prompt:
                umsg += f"\nKeep this art style: {self.style_prompt}"
            out = _agnes_chat(self.cfg, [
                {"role": "system", "content": sysmsg},
                {"role": "user", "content": umsg},
            ], temperature=0.3)
            out = (out or "").strip().strip('"').strip()
            # 兜底校验：必须是英文为主，防 LLM 又吐回中文（那样等于没修）
            if out and len(re.findall(r"[A-Za-z]", out)) > len(out) * 0.5:
                self.log(f"  ✅ {name} 缺 en，已据中文旁白补写英文画面描述")
                return out
            self.log(f"  ⚠️ {name} 缺 en，补写结果非英文，改用中性画面兜底")
        except Exception as e:
            self.log(f"  ⚠️ {name} 缺 en，补写失败（{e}），改用中性画面兜底")
        return self.NEUTRAL_PROMPT

    @staticmethod
    def _has_latin(text):
        """台词里是否混入了拉丁字母（会被配音照本宣科念出来）。"""
        return bool(re.search(r"[A-Za-z]", text or ""))

    def _localize_line(self, line, idx):
        """v4.147.3：把混了英文的台词改写回纯中文口语。

        分镜 prompt 已硬性要求 line 纯中文，但 LLM（尤其小模型）仍会漏出
        "AI Agent" / "Hi 大家好" / "powerful" 之类。clean_dialogue 只剥杂质、
        不做语言修复：含少量英文时照样放行 → 配音真把英文念出来
        （大哥实测「有的分镜台词是英文」）。这里补一步改写；改写失败先后退到
        「剥离英文片段」，仍不行则本镜不配音 —— 绝不把英文原样送去让模型照念。
        """
        last = ""
        try:
            out = _agnes_chat(self.cfg, [
                {"role": "system", "content": (
                    "你是中文台词改写助手。把用户给的台词改写成【纯中文口语】："
                    "保留原意；把英文单词/缩写改成中文说法（如 AI→人工智能，"
                    "Agent→智能体，Hi→嗨，powerful→给力）；不要添加任何新内容、"
                    "不要写成旁白式书面语、不要动作描写、不要引号；30 字以内；"
                    "只输出改写后的这一句中文。"
                )},
                {"role": "user", "content": line},
            ], temperature=0.3)
            out = self.clean_dialogue((out or "").strip().strip('"').strip())
            if out and not self._has_latin(out):
                self.log(f"  ✅ 镜{idx + 1} 台词已转纯中文：{out}")
                return out
        except Exception as e:
            last = str(e)
        # 加固：LLM 偶发超时会导致转写失败，重试一次（实测确实会超时）
        for _attempt in range(1):
            try:
                out = _agnes_chat(self.cfg, [
                    {"role": "system", "content": (
                        "把台词改写成纯中文口语，英文词一律换成中文说法，"
                        "不新增内容，30 字以内，只输出这一句中文。"
                    )},
                    {"role": "user", "content": line},
                ], temperature=0.3)
                out = self.clean_dialogue((out or "").strip().strip('"').strip())
                if out and not self._has_latin(out):
                    self.log(f"  ✅ 镜{idx + 1} 台词已转纯中文（重试）：{out}")
                    return out
            except Exception as e:
                last = str(e)
        # 仍失败：宁缺毋滥 —— 剥掉英文片段保中文；剩余过短就整句不配音，
        # 绝不把英文原样送去让模型照念（那正是「台词变英文」的现象）。
        stripped = self.clean_dialogue(re.sub(r"[A-Za-z][A-Za-z0-9'’\-]*", "", line))
        han = len(re.findall(r"[\u4e00-\u9fff]", stripped))
        if stripped and not self._has_latin(stripped) and han >= 4:
            self.log(f"  ⚠️ 镜{idx + 1} 台词转写失败（{last}），已剥离英文片段：{stripped}")
            return stripped
        self.log(f"  ⚠️ 镜{idx + 1} 台词转写失败（{last}）且无法安全清理，本镜不配音")
        return None


    def _build_clip_prompt(self, i, feedback=None, refs=None, base=None):
        """base = 预审改过的 prompt 草稿（已含风格/锁定词），有则整段沿用不重拼。"""
        shot = self.shots[i]
        if base:
            prompt = base
        else:
            # v4.147.3：en 缺失时【绝不退回 zh】。zh 是这一镜的中文字幕/旁白，
            # 拿它当画面提示词会让视频模型把旁白念出来 / 当字幕渲染，
            # 成品凭空多一条旁白（大哥实测现象）。改为据 zh 反推英文画面描述。
            _en = (shot.get("en") or "").strip()
            prompt = _en if _en else self._recover_missing_en(shot.get("zh"), i)
            if self.portrait_mode and self.ref_image_path:
                prompt = PORTRAIT_PROMPT
            if self.style_prompt:
                sp = self.style_prompt.strip()
                if sp:
                    prompt = f"{prompt.rstrip('. ')}, {sp}"
            # 人物三视图锁定：把角色外观描述写进本镜提示词，确保跨镜人物一致（抗崩坏）
            if self.character_lock:
                prompt = f"{prompt.rstrip('. ')}, {self.character_lock}"
            # 道具/场景资产锁定（clue，抗崩坏 v3）：clue 为空时不注入，行为与旧版一致
            if self.clue_lock:
                prompt = f"{prompt.rstrip('. ')}, {self.clue_lock}"
            # v4.132 项目制作规格（视觉部分）：风格 / 镜头 / 禁用
            if self.spec_visual():
                prompt = f"{prompt.rstrip('. ')}. {self.spec_visual()}"
        # 多参考图指代：用 <Picture N> 明确每张参考图的用途（抗崩坏 v2）
        # 必须在 _build_clip_prompt 里拼，因为指代文本要与实际装配顺序严格一一对应
        if refs:
            caption = self._build_ref_caption(refs)
            if caption:
                prompt = f"{prompt.rstrip('. ')}. {caption}"
        # v4.126.1 修「台词念出乱码」：台词不再在这里拼进 prompt。
        # 旧逻辑这里注入一次，generate_all_clips 又通过 dialogue= 参数让内核
        # _inject_dialogue 再注入一次 → 模型看到两段 "Only the quoted line above"，
        # 指代不明，念出来就是乱的。现在台词统一走 dialogue 参数由内核单一注入。
        if feedback:
            prompt = (f"{prompt.rstrip('. ')}. [修改意见：{feedback} 请据此调整本镜画面，"
                      f"保持其余设定（主体 / 风格 / 机位）不变]")
        return prompt

    # ---------- v4.167.0（审查 §1）：任务级取消与「停止后可重试」 ----------

    def _is_cancelled(self):
        """本轮是否被取消（兼容旧读点 + 新令牌）。

        两处都要看：`self.cancelled` 是历史遗留的全局标志（外部可能仍在置位），
        `self._job_token` 是本轮的取消令牌。任一为真即视为取消。
        """
        if getattr(self, "cancelled", False):
            return True
        tk = getattr(self, "_job_token", None)
        try:
            return bool(tk is not None and tk.is_cancelled)
        except Exception:
            return False

    def begin_job(self, name="", reset_cancel=True):
        """开始新一轮生成：**换发新令牌**（这就是「停止后还能重试」的关键）。

        为什么必须有这一步：原来的 `cancelled` 一置 True 就永不复位，
        重试路径 `regenerate_clip()` 第一行就 `return None`。
        换发令牌后，新一轮从"未取消"开始，而上一轮的取消状态不会污染它。
        """
        try:
            from cancel_token import CancellationToken
        except Exception:
            CancellationToken = None
        with self._job_lock:
            self._job_seq += 1
            self._job_name = name or f"job{self._job_seq}"
            if CancellationToken is not None:
                self._job_token = CancellationToken(
                    name=f"director/{self._job_name}#{self._job_seq}")
            if reset_cancel:
                # 复位历史全局标志 —— 否则旧读点仍会认为"已取消"
                self.cancelled = False
            token = self._job_token
        return token

    def cancel_job(self, reason="用户点了停止", stage=""):
        """取消**当前这一轮**（幂等）。不动已完成产出，也不影响下一轮。

        与旧行为的差别：以前是给一个永不复位的全局布尔置 True；现在只终结
        当前令牌，`begin_job()` 一换发就恢复可用 → 用户能"从第 X 镜继续"。
        """
        with self._job_lock:
            self.cancelled = True          # 兼容仍在读它的旧路径
            token = self._job_token
        if token is not None:
            try:
                return token.cancel(reason, stage=stage)
            except Exception:
                return False
        return False

    def job_state(self):
        """本轮任务状态快照（供 UI 显示「已停止，可从第 X 镜继续」）。"""
        tk = getattr(self, "_job_token", None)
        done = len([p for p in (getattr(self, "clip_paths", None) or []) if p])
        return {
            "job": getattr(self, "_job_name", ""),
            "seq": getattr(self, "_job_seq", 0),
            "cancelled": self._is_cancelled(),
            "reason": getattr(tk, "reason", "") if tk is not None else "",
            "stage": getattr(tk, "stage", "") if tk is not None else "",
            "done_clips": done,
            "total_shots": len(getattr(self, "shots", None) or []),
        }

    def generate_all_clips(self, on_clip=None):
        """第3步：顺序生成全部分镜，接力尾帧；每完成一镜回调 on_clip(i, path|None)。返回成功数。"""
        # v4.167.0：整批生成 = 一个新任务（换发令牌 → 上一轮的"停止"不再粘住本轮）
        self.begin_job("generate_all_clips")
        self.clip_paths = [None] * len(self.shots)
        prev_tail = None
        prev_scene = None
        ok = 0
        for i, shot in enumerate(self.shots):
            if self._is_cancelled():
                break
            # 抗崩坏 v2：为本镜装配最多 5 张参考图（关键帧 > 角色三视图 > 场景图）
            # v4.127：带上一镜尾帧入槽（reference 模式下首帧被置空，靠它续连贯）
            refs = self._assemble_ref_images(i, prev_tail=prev_tail)
            ref_paths = [r[0] for r in refs]
            if len(refs) < AGNES_MAX_REF_IMAGES:
                miss = self._ref_missing_text(i, refs)
                self.log(f"  🖼️ 镜{i+1} 参考图 {len(refs)}/{AGNES_MAX_REF_IMAGES}"
                         + (f"，缺：{'/'.join(miss)}" if miss else "（无更多可用）"))
            prompt = self._build_clip_prompt(i, refs=refs,
                                             base=self.clip_prompt_override.get(i))
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
            # v4.126.1：台词先清洗再交给内核，剥掉括号动作描写/英文残留
            line = self.clean_dialogue(line) if line else None
            # v4.147.3：台词里混了英文会被照本宣科念出来，这里先转写成纯中文口语
            if line and self._has_latin(line):
                line = self._localize_line(line, i)
            if shot.get("line") and not line and self.with_dialogue:
                self.log(f"  ⚠️ 镜{i+1} 台词非中文/为空，本镜不配音（原文已被过滤）")
            clip = self._gen_one_clip(prompt, self._shot_dur(shot), f"{self.width}x{self.height}",
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
        # v4.167.0（审查 §1）：单镜重生成 = 新任务。原来这里第一行就检查
        # 全局永久 cancelled，一旦停止过就永远 return None，只能重置整个项目。
        # 换发令牌后「停止 → 重试这一镜」成立。
        self.begin_job(f"regenerate_clip#{i}")
        if i < 0 or i >= len(self.shots):
            return None
        # 抗崩坏 v2：重生成同样装配多参考图，保证「改这一镜」不会把人物改崩
        # v4.127：重生成也带上一镜尾帧（有的话），保持与整批生成一致
        _pt = None
        if self.relay and i > 0 and i - 1 < len(self.clip_paths) and self.clip_paths[i - 1]:
            _pt = self._tail_frame_path(self.clip_paths[i - 1])
        refs = self._assemble_ref_images(i, prev_tail=_pt)
        ref_paths = [r[0] for r in refs]
        if len(refs) < AGNES_MAX_REF_IMAGES:
            miss = self._ref_missing_text(i, refs)
            self.log(f"  🖼️ 镜{i+1} 参考图 {len(refs)}/{AGNES_MAX_REF_IMAGES}"
                     + (f"，缺：{'/'.join(miss)}" if miss else "（无更多可用）"))
        prompt = self._build_clip_prompt(i, feedback=feedback, refs=refs,
                                         base=self.clip_prompt_override.get(i))
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
        line = self.clean_dialogue(line) if line else None
        # v4.147.3：同上，混英文的台词先转写成纯中文再配音
        if line and self._has_latin(line):
            line = self._localize_line(line, i)
        clip = self._gen_one_clip(prompt, self._shot_dur(self.shots[i]), f"{self.width}x{self.height}",
                                  first_frame, line, i, ref_images=ref_paths)
        if clip:
            # 版本回滚：覆盖前把旧片段备份到 versions/，入历史栈
            old = self.clip_paths[i] if (i < len(self.clip_paths) and self.clip_paths[i]) else None
            self.clip_paths[i] = clip
            v = self._backup_file(old, "clip", i)
            if v:
                self.clip_versions.setdefault(i, []).append(v)
            # v4.125 P2：重生成成功即清错误记录——否则已修复的镜重启后
            # 仍显示失败卡片，且快照 ok 判定被旧错误污染。
            self.last_errors.pop(i, None)
        return clip

    def regenerate_keyframe(self, i, feedback=None):
        """只重生成第 i 镜的关键帧（v4.106 对话框指令用，其余镜/场景图不动）。

        复用 gen_keyframes 的 prompt 装配（人物锁定 + 修改意见），成功后
        就地更新 self.keyframes[i] 并返回新路径；失败返回 None。
        """
        if self.portrait_mode or not (0 <= i < len(self.shots)):
            return None
        # v4.132：统一走 _build_keyframe_prompt（预审改过的稿子这里也认），
        # 避免「预审改了、重生成又用回老 prompt」这种两头不一致。
        base = self.keyframe_prompt_override.get(i) or ""
        if base:
            fb_tail = f"[Revision note: {feedback}] " if feedback else ""
            prompt = f"{base} {fb_tail}".strip()
        else:
            prompt = self._build_keyframe_prompt(i, feedback=feedback)
        self.log(f"  ↻ 重生成 镜{i+1} 关键帧…")
        path = self._gen_one_keyframe(i, prompt)
        if path:
            if self.keyframes is None:
                self.keyframes = []
            while len(self.keyframes) <= i:
                self.keyframes.append(None)
            # 版本回滚：覆盖前把旧关键帧备份到 versions/，入历史栈
            old = self.keyframes[i]
            self.keyframes[i] = path
            v = self._backup_file(old, "keyframe", i)
            if v:
                self.keyframe_versions.setdefault(i, []).append(v)
        return path

    def regenerate_character(self, idx, feedback=None):
        """只重生成第 idx 个角色的三视图（v4.106 对话框指令用，其他角色不动）。

        若带修改意见且涉及外观，意见会追加进角色描述并同步刷新 character_lock，
        保证后续逐镜提示词里的角色锁定跟新形象一致。
        返回 (角色名, views列表或None)。
        """
        if self.portrait_mode or not self.characters:
            return (None, None)
        if not (0 <= idx < len(self.characters)):
            return (None, None)
        c = self.characters[idx]
        name = c.get("name") or "主角"
        desc = c.get("desc") or ""
        old_views = c.get("views")
        old_desc = c.get("desc")
        if feedback:
            # v4.108 M-08：替换而非追加——多次修改同一角色时旧意见会残留累积
            # （"updated appearance: A | updated appearance: B"），早期过时意见
            # 持续影响后续生成。每次只保留最新一条修改意见。
            desc = re.sub(r"\s*\|\s*updated appearance:.*$", "", desc)
            desc = f"{desc} | updated appearance: {feedback}"
            c["desc"] = desc
            self.character_lock = self._build_character_lock(self.characters)
        self.log(f"  ↻ 重生成角色「{name}」三视图…")
        views = self._gen_character_views(name, desc)
        ok = any(views)
        if ok:
            # 版本回滚：备份旧三视图（复制 png 到 versions/）+ 旧描述
            backed = [b for b in (self._backup_file(vp, "char", idx)
                                   for vp in (old_views or []) if vp) if b]
            self.character_versions.setdefault(idx, []).append({
                "views": backed if backed else old_views,
                "desc": old_desc,
            })
            c["views"] = views
        return (name, views if ok else None)

    def regenerate_clue(self, idx, feedback=None):
        """只重生成第 idx 个道具/场景资产的参考图（其他 clue 不动）。

        带修改意见时意见会替换进描述并同步刷新 clue_lock，保证后续逐镜提示词
        里的道具锁定跟新造型一致。返回 (名称, 图片路径或None)。
        """
        if self.portrait_mode or not self.clues:
            return (None, None)
        if not (0 <= idx < len(self.clues)):
            return (None, None)
        c = self.clues[idx]
        name = c.get("name") or "道具"
        desc = c.get("desc") or ""
        kind = c.get("kind") or "prop"
        old_image = c.get("image")
        old_desc = c.get("desc")
        if feedback:
            # 与 regenerate_character 同策略：替换而非追加，避免旧意见累积残留
            desc = re.sub(r"\s*\|\s*updated look:.*$", "", desc)
            desc = f"{desc} | updated look: {feedback}"
            c["desc"] = desc
            self.clue_lock = self._build_clue_lock(self.clues)
        self.log(f"  ↻ 重生成道具/资产「{name}」参考图…")
        image = self._gen_clue_image(name, desc, kind)
        if image:
            # 版本回滚：备份旧参考图 + 旧描述
            backed = self._backup_file(old_image, "clue", idx)
            self.clue_versions.setdefault(idx, []).append({
                "image": backed or old_image,
                "desc": old_desc,
            })
            c["image"] = image
        return (name, image)

    # ---------- 版本回滚（v后续）：revise 覆盖前已备份到 versions/，此处恢复指针 ----------
    def _backup_file(self, src, kind, idx):
        """把当前文件复制到 versions/ 作版本备份，返回版本路径（失败返回 None）。"""
        if not src or not os.path.isfile(src) or not self.versions_dir:
            return None
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            ms = f"_{int(time.time() * 1000) % 1000:03d}"  # 毫秒尾缀，避免同秒内多次 revise 互相覆盖丢版本
            base = os.path.basename(src)
            name, ext = os.path.splitext(base)
            dst = os.path.join(self.versions_dir, f"{kind}_{idx + 1}_{ts}{ms}{ext}")
            shutil.copy2(src, dst)
            return dst
        except Exception:
            return None

    def _snapshot_current(self, kind, idx):
        """v4.133.1：换版前给「当前正在用的这一版」留个能找回来的备份。

        与 _backup_file 的区别：这里会**同时入历史栈**，所以界面（版本对比）里
        立刻看得到、点得回来。只 _backup_file 不入栈 = 磁盘上多个孤儿文件，
        用户切走就找不回——那不叫备份。
        返回备份路径（失败/无需备份返回 None）。
        """
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return None
        if idx < 0:
            return None
        if kind == "clip":
            cur = self.clip_paths[idx] if idx < len(self.clip_paths or []) else None
            if cur and os.path.isfile(cur):
                v = self._backup_file(cur, "clip", idx)
                if v:
                    self.clip_versions.setdefault(idx, []).append(v)
                    return v
        elif kind == "keyframe":
            cur = self.keyframes[idx] if idx < len(self.keyframes or []) else None
            if cur and os.path.isfile(cur):
                v = self._backup_file(cur, "keyframe", idx)
                if v:
                    self.keyframe_versions.setdefault(idx, []).append(v)
                    return v
        elif kind == "character":
            if idx < len(self.characters or []):
                c = self.characters[idx] or {}
                vs = [v for v in (c.get("views") or []) if v and os.path.isfile(v)]
                if vs:
                    backed = [b for b in (self._backup_file(vp, "char", idx)
                                          for vp in vs) if b]
                    if backed:
                        self.character_versions.setdefault(idx, []).append(
                            {"views": backed, "desc": c.get("desc") or ""})
                        return backed[0]
        elif kind == "clue":
            if idx < len(self.clues or []):
                c = self.clues[idx] or {}
                img = c.get("image")
                if img and os.path.isfile(img):
                    b = self._backup_file(img, "clue", idx)
                    if b:
                        self.clue_versions.setdefault(idx, []).append(
                            {"image": b, "desc": c.get("desc") or ""})
                        return b
        return None

    def rollback_clip(self, i, version=-1):
        """把第 i 镜的片段回滚到历史某一版本（默认上一版）。返回恢复后的路径或 None。"""
        if not (0 <= i < len(self.shots)):
            return None
        hist = self.clip_versions.get(i, [])
        if not hist:
            return None
        try:
            v = hist[version]   # 支持正索引（绝对第几版）与负索引（-1 上一版，-2 更早）
        except (IndexError, TypeError):
            return None
        if not v or not os.path.isfile(v):
            return None
        self.clip_paths[i] = v
        return v

    def rollback_keyframe(self, i, version=-1):
        """把第 i 镜的关键帧回滚到历史某一版本。返回恢复后的路径或 None。"""
        if not (0 <= i < len(self.shots)):
            return None
        hist = self.keyframe_versions.get(i, [])
        if not hist:
            return None
        try:
            v = hist[version]   # 支持正索引（绝对第几版）与负索引（-1 上一版，-2 更早）
        except (IndexError, TypeError):
            return None
        if not v or not os.path.isfile(v):
            return None
        if self.keyframes is None:
            self.keyframes = []
        while len(self.keyframes) <= i:
            self.keyframes.append(None)
        self.keyframes[i] = v
        return v

    def rollback_character(self, idx, version=-1):
        """把第 idx 个角色的三视图+描述回滚到历史某一版本。返回角色名或 None。"""
        if not (0 <= idx < len(self.characters)):
            return None
        hist = self.character_versions.get(idx, [])
        if not hist:
            return None
        try:
            v = hist[version]   # 支持正索引（绝对第几版）与负索引（-1 上一版，-2 更早）
        except (IndexError, TypeError):
            return None
        if not isinstance(v, dict):
            return None
        c = self.characters[idx]
        if v.get("views"):
            c["views"] = v["views"]
        if v.get("desc") is not None:
            c["desc"] = v["desc"]
            self.character_lock = self._build_character_lock(self.characters)
        return c.get("name")

    def rollback_clue(self, idx, version=-1):
        """把第 idx 个道具/资产的参考图+描述回滚到历史某一版本。返回名称或 None。"""
        if not (0 <= idx < len(self.clues)):
            return None
        hist = self.clue_versions.get(idx, [])
        if not hist:
            return None
        try:
            v = hist[version]   # 支持正索引（绝对第几版）与负索引（-1 上一版，-2 更早）
        except (IndexError, TypeError):
            return None
        if not isinstance(v, dict):
            return None
        c = self.clues[idx]
        if v.get("image"):
            c["image"] = v["image"]
        if v.get("desc") is not None:
            c["desc"] = v["desc"]
            self.clue_lock = self._build_clue_lock(self.clues)
        return c.get("name")

    def list_versions(self):
        """返回各镜/角色的历史版本时间戳清单（供 UI / 对话显示）。"""
        def _ts(p):
            if not p or not os.path.isfile(p):
                return None
            m = re.search(r"_(\d{8}_\d{6}(?:_\d{3})?)\.", os.path.basename(p))
            return m.group(1) if m else None
        out = {"clip": {}, "keyframe": {}, "character": {}, "clue": {}}
        for i, hist in self.clip_versions.items():
            ts = [_ts(p) for p in hist]
            if any(ts):
                out["clip"][i] = ts
        for i, hist in self.keyframe_versions.items():
            ts = [_ts(p) for p in hist]
            if any(ts):
                out["keyframe"][i] = ts
        for idx, hist in self.character_versions.items():
            ts = []
            for v in hist:
                # views 可能是 None 或 []（三视图全失败时），不能直接下标取 [0]
                vs = (v.get("views") or []) if isinstance(v, dict) else []
                ts.append(_ts(vs[0]) if vs else None)
            if any(ts):
                out["character"][idx] = ts
        for idx, hist in self.clue_versions.items():
            ts = [_ts(v.get("image")) if isinstance(v, dict) else None for v in hist]
            if any(ts):
                out["clue"][idx] = ts
        return out

    def version_items(self, kind, idx):
        """v4.133「单镜多版本对比」数据源：某镜/角色/道具的全部历史版本 + 当前版。

        kind ∈ clip / keyframe / character / clue，idx 从 0 计。
        返回 [{"no","path","ts","size_kb","current","desc"}]，按时间从旧到新，
        当前版本永远排在最后（标 current=True）。文件不存在的一律剔除——回滚
        到不存在的文件只会让人干瞪眼。
        """
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return []
        if idx < 0:
            return []
        hist = []      # [(路径, 描述)]
        cur, cur_desc = None, ""
        if kind == "clip":
            hist = [(p, "") for p in (self.clip_versions.get(idx, []) or [])]
            cur = self.clip_paths[idx] if idx < len(self.clip_paths or []) else None
        elif kind == "keyframe":
            hist = [(p, "") for p in (self.keyframe_versions.get(idx, []) or [])]
            cur = self.keyframes[idx] if idx < len(self.keyframes or []) else None
        elif kind == "character":
            if idx < len(self.characters or []):
                vs = self.characters[idx].get("views") or []
                cur = vs[0] if vs else None
                cur_desc = self.characters[idx].get("desc") or ""
            for v in (self.character_versions.get(idx, []) or []):
                if not isinstance(v, dict):
                    continue
                vs = v.get("views") or []
                hist.append((vs[0] if vs else None, v.get("desc") or ""))
        elif kind == "clue":
            if idx < len(self.clues or []):
                cur = self.clues[idx].get("image")
                cur_desc = self.clues[idx].get("desc") or ""
            for v in (self.clue_versions.get(idx, []) or []):
                if not isinstance(v, dict):
                    continue
                hist.append((v.get("image"), v.get("desc") or ""))
        else:
            return []
        out = []
        for vi, (p, d) in enumerate(hist):
            if p and os.path.isfile(p):
                # vi = 在历史栈里的真实下标（文件缺失会错位，回滚时必须按它来）
                out.append({"no": len(out) + 1, "vi": vi, "path": p,
                            "ts": _version_ts(p),
                            "size_kb": os.path.getsize(p) // 1024,
                            "current": False, "desc": d or ""})
        if cur and os.path.isfile(cur):
            # 当前版没有历史下标（vi=-1 只是占位，UI 里当前版本来就不给回滚）
            out.append({"no": len(out) + 1, "vi": -1, "path": cur,
                        "ts": _version_ts(cur),
                        "size_kb": os.path.getsize(cur) // 1024,
                        "current": True, "desc": cur_desc})
        return out

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
        # v4.129：同上，成片也落分层目录
        out_path = os.path.join(_vp_products_dir(getattr(self, "cfg", None), "video"),
                                f"director_final_{ts}.mp4")
        ok, detail = self._merge(self.clip_paths, self.shots, out_path, self.burn_subtitles)
        if ok:
            return out_path, ""
        # 合并详细错误信息
        err_parts = ["ffmpeg 合成失败"]
        if detail:
            err_parts.append(detail)
        # v4.108 M-05：条件写反修复——此处是【失败分支】，输出文件没生成才该提示；
        # 原 os.path.exists(...) 导致失败时报"未生成"（反了），输出正常却追文案。
        if not os.path.exists(out_path):
            err_parts.append("（输出文件未生成）")
        return None, " | ".join(err_parts)

    def keyframe(self, clip_path):
        """抽视频首帧作关键帧预览图，返回 PNG 路径（失败返回 None）。"""
        return self._head_frame_path(clip_path)

    # ---------- v4.132 导出三件套（成片 / 工程文件 / 素材包） ----------
    # Flova 的「导出」给三种：成片、PR 工程、全部素材。小臭这边对应：
    #   成片  → 面板里另存（UI 层做，无需 pipeline）
    #   工程  → EDL（剪映/PR/达芬奇通吃）+ FCPXML（Final Cut / 达芬奇）
    #   素材  → zip 打包：片段 + 关键帧 + 三视图 + 道具图 + 分镜表 + 剧本
    FPS = 24

    def _export_segs(self):
        """导出用的片段清单，与合成同源（含转场片段），保证工程与成片一致。"""
        try:
            segs = self._build_segs(self.clip_paths, self.shots)
        except Exception:
            segs = []
        if not segs:
            segs = []
            for i, p in enumerate(self.clip_paths or []):
                if p and os.path.isfile(p):
                    segs.append({"path": p, "kind": "clip", "shot_idx": i,
                                 "dur": self._probe_duration(p) or self.duration})
        return segs

    @staticmethod
    def _tc(sec, fps=24):
        f = max(0, int(round(float(sec or 0) * fps)))
        return f"{f // (fps * 3600):02d}:{f // (fps * 60) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"

    @staticmethod
    def _xml_esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def export_edl(self, out_path):
        """导出 CMX3600 EDL（剪映/PR/达芬奇都能导入的时间线清单）。"""
        segs = self._export_segs()
        if not segs:
            return False, "没有可导出的片段"
        lines = [f"TITLE: {os.path.basename(self.project_dir or 'director')}",
                 "FCM: NON-DROP FRAME", ""]
        rec = 0.0
        for n, sg in enumerate(segs, 1):
            dur = float(sg.get("dur") or self.duration or 5)
            # v4.133.1：裁过入点的话，源入点必须跟着走——否则 PR 里从头播，
            # 与成片对不上（此前无论怎么裁，source in 恒为 0）。
            ss = float(sg.get("ss") or 0)
            name = os.path.basename(sg.get("path") or "")
            lines.append(
                f"{n:03d}  AX       V     C        "
                f"{self._tc(ss)} {self._tc(ss + dur)} {self._tc(rec)} {self._tc(rec + dur)}")
            lines.append(f"* FROM CLIP NAME: {name}")
            idx = sg.get("shot_idx")
            if idx is not None:
                zh = ""
                try:
                    zh = str(self.shots[idx].get("zh") or "")[:60]
                except Exception:
                    pass
                if zh:
                    lines.append(f"* SHOT {idx + 1}: {zh}")
            lines.append("")
            rec += dur
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return True, out_path
        except Exception as e:
            return False, f"EDL 写入失败：{e}"

    def export_fcpxml(self, out_path):
        """导出 FCPXML 1.9（Final Cut / 达芬奇可直接打开的时间线工程）。"""
        import pathlib
        segs = self._export_segs()
        if not segs:
            return False, "没有可导出的片段"
        fps = self.FPS
        fr = lambda sec: max(1, int(round(float(sec or 0) * fps)))
        total = sum(fr(sg.get("dur") or self.duration or 5) for sg in segs)
        fmt = (f'<format id="r0" name="FFVideoFormatCustom" '
               f'frameDuration="1/{fps}s" width="{self.width}" height="{self.height}"/>')
        assets, spine, off = [], [], 0
        for n, sg in enumerate(segs, 1):
            p = sg.get("path") or ""
            dur_f = fr(sg.get("dur") or self.duration or 5)
            # v4.133.1：asset 记素材全长，asset-clip 用 start 指素材内入点；
            # 此前 start 恒为 0，裁过的镜导进 Final Cut 会从头播。
            ss_f = fr(sg.get("ss") or 0)
            src_f = max(dur_f + ss_f,
                        fr(sg.get("src_dur") or self.duration or 5))
            try:
                src = pathlib.Path(os.path.abspath(p)).as_uri()
            except Exception:
                src = "file:///" + os.path.abspath(p).replace("\\", "/")
            name = os.path.basename(p)
            assets.append(
                f'<asset id="a{n}" name="{self._xml_esc(name)}" start="0s" '
                f'duration="{src_f}/{fps}s" hasVideo="1" hasAudio="1" format="r0">\n'
                f'  <media-rep kind="original-media" src="{self._xml_esc(src)}"/>\n'
                f'</asset>')
            spine.append(
                f'<asset-clip name="{self._xml_esc(name)}" ref="a{n}" '
                f'offset="{off}/{fps}s" duration="{dur_f}/{fps}s" '
                f'start="{ss_f}/{fps}s" format="r0"/>')
            off += dur_f
        title = self._xml_esc(os.path.basename(self.project_dir or "director"))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
            '<fcpxml version="1.9">\n'
            f'  <resources>\n    {fmt}\n    '
            + "\n    ".join(assets) + '\n  </resources>\n'
            '  <library>\n'
            f'    <event name="Director">\n      <project name="{title}">\n'
            f'        <sequence format="r0" duration="{total}/{fps}s" tcStart="0s" '
            'tcFormat="NDF">\n          <spine>\n            '
            + "\n            ".join(spine) + '\n          </spine>\n'
            '        </sequence>\n      </project>\n    </event>\n'
            '  </library>\n</fcpxml>\n')
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(xml)
            return True, out_path
        except Exception as e:
            return False, f"FCPXML 写入失败：{e}"

    def export_bundle(self, out_path, final_path=None):
        """把本片全部素材 + 分镜表 + 剧本打包成 zip（给剪辑 / 归档 / 交接用）。"""
        import zipfile
        import csv as _csv
        import io as _io

        def _add_unique(z, arc, path):
            """同名文件自动加序号，避免 zip 里互相覆盖（素材经常重名）。"""
            if not path or not os.path.isfile(path):
                return False
            base, ext = os.path.splitext(arc)
            name, k = arc, 1
            try:
                names = set(z.namelist())
            except Exception:
                names = set()
            while name in names:
                k += 1
                name = f"{base}_{k}{ext}"
            try:
                z.write(path, name)
                return True
            except Exception:
                return False

        n = 0
        try:
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("剧本.txt", self.story or "")
                # 分镜表（json + csv 双份，人读机读都方便）
                try:
                    z.writestr("分镜表.json", json.dumps(
                        self.shots or [], ensure_ascii=False, indent=1))
                except Exception:
                    pass
                try:
                    buf = _io.StringIO()
                    w = _csv.writer(buf)
                    w.writerow(["镜号", "场景", "中文字幕", "英文提示词", "镜头语言", "台词"])
                    for i, s in enumerate(self.shots or []):
                        w.writerow([i + 1, s.get("scene") or "", s.get("zh") or "",
                                    s.get("en") or "", s.get("cam") or "",
                                    s.get("line") or ""])
                    z.writestr("分镜表.csv", buf.getvalue())
                except Exception:
                    pass
                if (self.spec or {}):
                    try:
                        z.writestr("项目制作规格.json", json.dumps(
                            self.spec, ensure_ascii=False, indent=1))
                    except Exception:
                        pass
                for i, p in enumerate(self.clip_paths or []):
                    if _add_unique(z, f"片段/shot{i + 1:02d}{os.path.splitext(p or '')[1] or '.mp4'}", p):
                        n += 1
                for i, p in enumerate(self.keyframes or []):
                    if _add_unique(z, f"关键帧/shot{i + 1:02d}{os.path.splitext(p or '')[1] or '.png'}", p):
                        n += 1
                for c in self.characters or []:
                    nm = str(c.get("name") or "角色")
                    for j, p in enumerate(c.get("views") or []):
                        tag = ("正面", "侧面", "背面")[j] if j < 3 else f"视图{j + 1}"
                        ext = os.path.splitext(p or "")[1] or ".png"
                        if _add_unique(z, f"人物/{nm}_{tag}{ext}", p):
                            n += 1
                for k, c in enumerate(self.clues or []):
                    nm = str(c.get("name") or f"道具{k + 1}")
                    ext = os.path.splitext(c.get("image") or "")[1] or ".png"
                    if _add_unique(z, f"道具/{nm}{ext}", c.get("image")):
                        n += 1
                for sc, p in (self.scene_images or {}).items():
                    ext = os.path.splitext(p or "")[1] or ".png"
                    if _add_unique(z, f"场景/场景{sc}{ext}", p):
                        n += 1
                fp = final_path or getattr(self, "final_path", None)
                if fp and os.path.isfile(fp):
                    if _add_unique(z, f"成片/{os.path.basename(fp)}", fp):
                        n += 1
            return True, f"{out_path}（{n} 个素材文件）"
        except Exception as e:
            return False, f"素材包打包失败：{e}"

    # ---------- 主入口（自动编排，兼容旧路径；面板改用上面分阶段接口） ----------
    def run(self, topic, n=3, duration=5, resolution="768x1152", style_key="realistic",
            ref_image_path=None, portrait_mode=False, with_dialogue=False, relay=True,
            transition="black", transition_dur=0.4, burn_subtitles=True,
            passthrough_script=None, clip_dir=None, smart=True):
        if not self.ffmpeg:
            self.status("未找到 ffmpeg，无法合成成片", err=True)
            self.on_finish(False, "未找到 ffmpeg，无法合成成片")
            return None
        self.prepare(topic, n, duration, resolution, style_key, ref_image_path,
                     portrait_mode, with_dialogue, relay, transition, transition_dur,
                     burn_subtitles, passthrough_script, clip_dir, smart=smart)
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
        sys_prompt = ("你是专业的短视频编剧。你的任务：**严格按用户给的主题**创作一部微故事剧本。"
                      "主题已经限定了体裁 / 人物 / 时代 / 原作，你只能在主题给定的框架里补全情节，"
                      "不得替换成另一个故事，也不要凭空添置与主题无关的设定。")
        if getattr(self, "smart", True):
            story_len_note = ("请创作一部微故事短视频剧本，用中文写。"
                              "镜头数量由你根据情节的起伏、场景转换自行决定"
                              "（通常 6~18 镜，内容丰富的科普 / 纪录片可到 24，"
                              "但务必为叙事服务，不要为了凑数硬加水镜）。\n")
        else:
            story_len_note = (f"请创作一部约 {n} 个镜头长度（约 {n * self.duration} 秒，"
                              f"科普 / 纪录片类可酌情更多）的微故事短视频剧本，用中文写。\n")
        user_prompt = (
            f"主题：{topic}\n"
            + story_len_note
            + "要求：\n"
            "1) **忠于主题**：主题点明的体裁、人物、时代、原作（例如某首诗词、某位历史人物）"
            "必须保留，你只补全情节，不另起炉灶；主题没提的才由你发挥；\n"
            "2) 明确**人物角色**（名字 + 年龄 / 性别 / 发型 / 面容 / 衣着，一两句），全片同一主角贯穿。"
            "人物角色**只能是「人」或拟人化的活物**（如会说话的狐狸、人形精怪）；"
            "**道具、器皿（酒壶 / 酒杯）、武器、建筑、陈设一律不是角色**，不要写进人物角色里；\n"
            "3) 有清晰的时间线与起承转合：开头建立情境→中间发展/冲突→结尾收束；\n"
            "4) 情节连贯，事件按先后顺序发生，不要跳切；\n"
            "5) 逐镜用一两句话描述这一镜发生了什么（不要写镜头语言或英文）；\n"
            "6) 剧本最后**另起一行**写「关键道具/陈设：」，列出跨镜反复出现、需要保持一致的"
            "**物件**（只写名称与外观一两句；并记住它们是物件、不是角色）。没有就写「无」。\n"
            + dialogue_note + rev_note + ref_story_note + self.spec_text() +
            "直接输出剧本正文，不要解释、不要 markdown 代码块。")
        if self.portrait_mode:
            sys_prompt = ("你是口播视频文案撰稿人。只写一个人对着镜头说的话（第一人称「我」），"
                          "口语化、自然流畅、有信息量。"
                          "绝不写故事情节、场景描写、镜头语言、动作说明，绝不出现其他角色。")
            if getattr(self, "smart", True):
                voice_len_note = ("请写一段中文口播文案（第一人称「我」对镜头说话），"
                                  "自然分段，每段口播 20~35 字，按各镜时长自然分配，"
                                  "总时长由分段数与每段长度自然决定。\n")
            else:
                voice_len_note = (f"请写一段总时长约 {n * self.duration} 秒的中文口播文案"
                                  f"（第一人称「我」对镜头说话），自然分成约 {n} 个小段落，"
                                  f"每段约 {self.duration} 秒口语量（20~35字）。\n")
            user_prompt = (
                f"主题：{topic}\n"
                + voice_len_note
                + "要求：只有说话内容本身；不要任何场景/动作/情节描写；"
                "不要出现除说话者以外的角色；不要标题。\n"
                + rev_note + self.spec_text() +
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
                             "🔴 line 硬性约束（违反会导致配音变成乱码，必须严格遵守）：\n"
                             "  1. 只能是纯中文口语，禁止出现任何英文单词、拼音或英文标点；\n"
                             "  2. 禁止写动作/情绪描写，如（叹气）、（转身）、*笑* 之类一律不要；\n"
                             "  3. 不要用引号包裹，不要加「台词：」前缀，不要换行；\n"
                             "  4. 一句话控制在 30 字以内，要像真人说话，不像旁白书面语。\n")
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
        smart = getattr(self, "smart", True)
        if smart:
            shot_count_note = ("请根据剧本情节自行决定分镜数量，串成完整短视频："
                               "通常 6~18 镜，内容丰富的科普 / 纪录片可到 24，"
                               "但务必为叙事服务，不要硬凑水镜。\n")
            dur_field = ("- dur：这一镜的时长（秒），根据内容在 4~12 之间取值："
                         "动作 / 快节奏偏短（4~6），抒情 / 空镜偏长（8~12），对白适中（5~7）。\n")
        else:
            shot_count_note = (f"请把上面这部剧本拆成约 {n} 个连续分镜，串成完整短视频。"
                               f"科普 / 纪录片类内容较丰富时，可酌情增加到 {max(n, 12)} 个，"
                               f"确保叙事完整、不遗漏要点；不要为了凑数硬加水镜。\n")
            dur_field = ""
        user_prompt = (
            f"剧本如下：\n<<<\n{story}\n>>>\n\n"
            + shot_count_note
            + "每一镜都必须是剧本中对应情节的【视觉化还原与延续】，不要跳切、"
            "不要换成不相关的新场景或新主角。\n"
            "每个分镜给这些字段：\n"
            "- en：英文视频生成提示词，1~2 句话，只写视觉内容（主体、动作、镜头运动、光线氛围）。\n"
            "- zh：这一镜对应的中文字幕/旁白，口语化、一句话，要体现故事推进。\n"
            "- cam：这一镜的镜头语言（景别 + 运镜 + 机位），例如 "
            "'medium shot, slow push-in, eye-level'。相邻分镜的运镜要有变化。\n"
            "- scene：这一镜所属场景编号（整数，从 1 开始）。同一连续时空的分镜用同一个 scene 值；"
            "剧情跳场时 +1。同一 scene 内的分镜会做【尾帧接力】。\n"
            + dur_field
            + dialogue_rule + rev_note + ref_note + style_note + portrait_note
            + self.spec_text()
            + CAM_VOCAB_TEXT
            + "严格只返回一个 JSON 数组，形如 "
            '[{"en":"...","zh":"...","cam":"medium shot, slow push-in, eye-level","scene":1'
            + (',"line":"..."' if with_dialogue else '')
            + (',"dur":6' if smart else '')
            + "}, ...]，不要任何解释文字，不要 markdown 代码块。")
        content = _agnes_chat(self.cfg, [
            {"role": "system", "content": ("你是专业的短视频分镜导演，也是 AI 视频生成提示词专家。"
                                           "把剧本拆成连续、有因果推进的完整分镜序列，全片同一主角贯穿；"
                                           "熟练运用电影摄影语言（景别、运镜、机位）设计每一镜。")},
            {"role": "user", "content": user_prompt},
        ], temperature=0.65)
        return self._parse_shots(content, self.duration)

    # ---------- 分镜解析 ----------
    @staticmethod
    def _parse_shots(content, def_dur=5):
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
                    return [VideoPipeline._norm_shot(x, def_dur) for x in arr]
            except Exception:
                pass
        m = re.search(r"\[.*\]", txt, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list) and arr:
                    return [VideoPipeline._norm_shot(x, def_dur) for x in arr]
            except Exception:
                pass
        return []

    @staticmethod
    def _norm_shot(x, def_dur=5):
        if isinstance(x, dict):
            en = x.get("en") or x.get("prompt") or x.get("english") or ""
            zh = x.get("zh") or x.get("caption") or x.get("chinese") or ""
            # v4.126.1：台词入库即清洗（剥括号描写/英文残留），避免脏台词一路流到配音
            line = VideoPipeline.clean_dialogue(x.get("line") or "")
            cam = x.get("cam") or ""
            sc = x.get("scene") or x.get("sc") or 1
            try:
                sc = int(sc)
            except Exception:
                sc = 1
            # v4.154：每镜时长（智能分镜由 AI 给出）。Agnes 视频硬约束 4~12 秒。
            # 有值但越界 → 夹到最近边界（保留「想短/想长」的 AI 意图）；
            # 缺失 / 非数字 → 回落兜底默认并夹到合法区间。
            raw_dur = x.get("dur")
            try:
                d = int(round(float(raw_dur)))
                d = max(4, min(12, d))
            except (TypeError, ValueError):
                d = max(4, min(12, int(def_dur)))
            return {"en": str(en).strip(), "zh": str(zh).strip(),
                    "line": str(line).strip(), "scene": sc, "cam": str(cam).strip(),
                    "dur": d}
        return {"en": str(x).strip(), "zh": "", "line": "", "scene": 1, "cam": "",
                "dur": max(4, min(12, int(def_dur)))}

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
            if self._is_cancelled():
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
                    # 天生无声的素材（智谱兜底等）登记下来：合成自检据此区分
                    # 「设计无声（空镜）」与「哑弹 BUG（有声素材被合成没声）」。
                    try:
                        if is_silent_clip(rel):
                            if not hasattr(self, "silent_shots"):
                                self.silent_shots = set()
                            self.silent_shots.add(idx)
                    except Exception:
                        pass
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
            # v4.125 P2：原「目录文件数+1」命名在删文件后计数回退 → 覆盖旧帧，
            # 接力锁脸拿到错帧。改按 clip 路径 md5 稳定命名（同 head 帧）。
            import hashlib as _hl
            _key = _hl.md5(str(clip_path).encode("utf-8")).hexdigest()[:12]
            out = os.path.join(self.project_dir,
                               f"tail_{_key}.png")
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
        """抽视频【首帧】作关键帧预览图，返回 PNG 路径（失败返回 None）。

        v4.108 M-11：输出名按 clip 路径 hash 固定——已抽过则直接命中缓存返回，
        避免 resume/重渲染/播放预览时对同一长视频反复同步抽帧卡 UI 数秒。
        """
        try:
            # v4.125 P2：内置 hash() 受 PYTHONHASHSEED 影响进程间随机——重启后
            # 缓存永不命中（每次重抽首帧），5 位取模还有碰撞串镜风险。改 md5 稳定命名。
            import hashlib as _hl
            _key = _hl.md5(str(clip_path).encode("utf-8")).hexdigest()[:12]
            out = os.path.join(self.project_dir,
                               f"head_{_key}.png")
            if os.path.exists(out) and os.path.getsize(out) >= 100:
                return out  # 缓存命中
            args = [self.ffmpeg, "-y", "-i", clip_path,
                    "-frames:v", "1", "-q:v", "2", out]
            self._run_ff(args, timeout=30)
            if not os.path.exists(out) or os.path.getsize(out) < 100:
                return None
            return out
        except Exception:
            return None

    # ---------- 第4步：ffmpeg 合成 ----------
    def _run_ff(self, args, timeout=60, cancel_check=None):
        """统一执行 ffmpeg 子进程，返回 (returncode, stdout_text, stderr_text)。

        ⚠️ 绝对不要用 subprocess.run(..., text=True)：
        PyInstaller 冻结环境下 locale 常为 gbk/cp936，而 ffmpeg 输出是 UTF-8。
        一旦出现 gbk 解不了的字节，UnicodeDecodeError 会抛在 subprocess 的后台
        读取线程（_readerthread）里——线程静默死亡、主线程不报错，但 stderr 会
        变成空字符串。这会让 _probe_has_audio 把"探测失败"误判成"无音轨"，
        导致成片被铺成 anullsrc 静音轨（实测 2 kb/s / -91 dB 完全无声）。
        所以这里用 bytes 模式捕获，再显式按 utf-8 解码并容错。

        v4.108 M-06：cancel_check 提供时走 Popen 轮询——每 0.5s 检查一次取消
        标志（如 self.cancelled），置位即 kill 子进程，让长合并能被「停止」中断；
        不传则保持 subprocess.run 原路径（其余调用零影响）。
        """
        if cancel_check is None:
            r = subprocess.run(args, capture_output=True, timeout=timeout,
                               creationflags=_NO_WINDOW)
            out = (r.stdout or b"").decode("utf-8", "replace")
            err = (r.stderr or b"").decode("utf-8", "replace")
            return r.returncode, out, err
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW)
        deadline = time.monotonic() + timeout
        while True:
            try:
                out_b, err_b = proc.communicate(timeout=0.5)
                break  # 子进程正常结束
            except subprocess.TimeoutExpired:
                if cancel_check():
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
                    return -1, "", "ffmpeg 已取消"
                if time.monotonic() > deadline:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        pass
                    return -1, "", f"ffmpeg 超时（>{timeout}s）"
        return (proc.returncode,
                (out_b or b"").decode("utf-8", "replace"),
                (err_b or b"").decode("utf-8", "replace"))

    def _probe_duration(self, path):
        """v4.125 P2：探测片段真实时长（秒）。失败返回 0（调用方回落预设值）。

        ffmpeg -i 的 stderr 里带 "Duration: HH:MM:SS.xx"。用 bytes+utf-8 replace
        解码（_run_ff 已处理 GBK 冻结环境问题）。

        v4.133.1：按 (绝对路径, mtime_ns, 大小) 缓存——时间线面板每次打开/应用
        都要对全片逐个 ffprobe，12 镜 × 两次 = 24 次子进程，肉眼可见地卡。
        素材没换就复用上次结果；重生成会换文件（新 mtime/size）自然失效。
        """
        ff = getattr(self, "ffmpeg", None)
        if not ff or not os.path.exists(ff) or not os.path.isfile(path or ""):
            return 0
        _c = getattr(self, "_dur_cache", None)
        if _c is None:
            _c = self._dur_cache = {}
        _ck = None
        try:
            _st = os.stat(path)
            _ck = (os.path.abspath(path), _st.st_mtime_ns, _st.st_size)
            if _ck in _c:
                return _c[_ck]
        except OSError:
            _ck = None
        try:
            _rc, _out, err = self._run_ff(
                [ff, "-hide_banner", "-i", path], timeout=20)
        except Exception:
            return 0
        import re as _re
        m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err or "")
        if not m:
            return 0
        try:
            d = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                 + float(m.group(3)))
        except Exception:
            return 0
        if _ck:
            _c[_ck] = d
        return d

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

    def _make_transition_clip(self, kind, dur=None):
        """黑/白场转场片段。v4.133：支持逐镜自定义时长，按 (kind, 时长) 分开缓存。

        老路径（全局转场）不传 dur → 走 self.transition_dur，与旧行为完全一致。
        """
        T = float(dur if dur else (self.transition_dur or 0.4))
        cache = getattr(self, "_trans_cache", None)
        if cache is None:
            cache = self._trans_cache = {}
        # v4.133.1：缓存 key 必须带分辨率——低清预览（480 宽）生成的转场
        # 若按 (kind, 时长) 缓存，正式成片会命中它，1080 时间线上糊一条 480 的黑场。
        key = (kind, round(T, 3), int(self.width), int(self.height))
        hit = cache.get(key)
        if hit and os.path.exists(hit):
            return hit
        # 老路径（无 dur）沿用旧文件名，行为不变
        if dur is None and self.trans_clip_path and os.path.exists(self.trans_clip_path):
            cache[key] = self.trans_clip_path
            return self.trans_clip_path
        color = "white" if kind == "white" else "black"
        W, H = self.width, self.height
        out = os.path.join(self.project_dir, "transition.mp4") if dur is None \
            else os.path.join(self.project_dir,
                              f"transition_{kind}_{T:.2f}_{W}x{H}.mp4")
        args = [self.ffmpeg, "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:d={T}:r=24",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", str(T), "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k", out]
        self._run_ff(args, timeout=60)
        if not os.path.exists(out):
            return None
        # v4.133.1：只有全局转场（dur=None）才登记 trans_clip_path。
        # 低清预览生成的 480 转场若登记进去，之后停用时间线再合成就会拿到它。
        if dur is None:
            self.trans_clip_path = out
        cache[key] = out
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

    # ---------- v4.133 可视化时间线（重排 / 裁切 / 逐镜转场） ----------
    # 对标 Flova 的剪辑时间线。小臭不是剪辑软件，只做「成片前最后一道编排」：
    # 顺序 / 入出点 / 逐镜转场，画面本身仍由生成链路决定。
    # 铁律同 v4.132：self.timeline 为 None 时一切照旧，绝不碰旧链路。
    TRANS_KINDS = (("none", "无"), ("black", "黑场"), ("white", "白场"))

    def timeline_default(self):
        """按当前 shots 生成一条「什么都没改」的时间线（原序、不裁、不转场）。"""
        n = len(self.shots or [])
        return {"order": list(range(n)), "trim": {}, "trans": {},
                "audio": {"bgm": "", "vol": 0.30, "duck": True},
                "sub": {"burn": bool(getattr(self, "burn_subtitles", True)),
                        "size": 18, "color": "&H00FFFFFF", "pos": 30}}

    def set_timeline(self, tl):
        """启用/停用时间线。传 None 或非 dict = 停用（回到旧链路）。"""
        self.timeline = tl if isinstance(tl, dict) else None
        return self.timeline

    def _tl_order(self):
        """校验后的播放顺序：剔除越界、重复、素材缺失的镜号。"""
        tl = self.timeline or {}
        raw = tl.get("order") or []
        paths = self.clip_paths or []
        seen, order = set(), []
        for x in raw:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(paths) and i not in seen:
                seen.add(i)
                order.append(i)
        if not order:
            # order 空/全非法 → 退回「有素材的镜按原序」。绝不返回空，否则成片 0 秒。
            order = [i for i in range(len(paths)) if paths[i] and os.path.isfile(paths[i])]
        return order

    def _tl_trim(self, i):
        """第 i 镜的裁切 (入点, 出点)；没裁过或数据非法 → None。"""
        tl = self.timeline or {}
        tr = (tl.get("trim") or {}).get(str(i))
        if not isinstance(tr, (list, tuple)) or len(tr) < 2:
            return None
        try:
            a, b = float(tr[0]), float(tr[1])
        except (TypeError, ValueError):
            return None
        if a < 0 or b <= a:
            return None
        return (a, b)

    def _tl_trans_after(self, i):
        """第 i 镜「后面」的转场 (kind, 时长)；没设或非法 → None。"""
        tl = self.timeline or {}
        v = (tl.get("trans") or {}).get(str(i))
        if not isinstance(v, str) or not v:
            return None
        kind, _, ds = v.partition(":")
        kind = (kind or "none").strip().lower()
        if kind not in ("black", "white"):
            return None
        try:
            d = float(ds)
        except ValueError:
            d = float(self.transition_dur or 0.4)
        if d <= 0:
            return None
        return (kind, min(d, 3.0))

    def render_preview(self, out_path=None, scale=480, burn=False):
        """按当前时间线渲一版低清预览（不覆盖正式成片，也不改设置）。

        只是把 width/height 临时缩小后走同一条 _merge_exec，渲完立刻还原，
        所以预览与最终成片的顺序 / 裁切 / 转场 / BGM 完全一致。
        返回 (路径|None, 错误信息)。
        """
        # ⚠️ 顺序不能反：必须在改 width/height **之前**展开片段。否则转场片段
        # 会按预览分辨率（480）生成并进缓存，正式成片沿用就是一条糊掉的低清黑场。
        segs = self._build_segs(self.clip_paths, self.shots)
        if not segs:
            return None, "没有可用的视频片段"
        ow, oh = self.width, self.height
        out = out_path or os.path.join(self.project_dir or ".", "timeline_preview.mp4")
        try:
            r = scale / max(1, ow)
            self.width = int(scale)
            self.height = max(2, (int(oh * r) // 2) * 2)   # 偶数，libx264 要求
            ok, detail = self._merge_exec(segs, self.shots, out, bool(burn),
                                          use_clip_audio=True)
        except Exception as e:
            return None, str(e)
        finally:
            self.width, self.height = ow, oh
        return (out if ok else None), ("" if ok else (detail or "预览渲染失败"))

    def build_timeline_segs(self):
        """按时间线展开待拼接片段（含逐镜转场）。仅 timeline 启用时调用。"""
        segs = []
        for i in self._tl_order():
            p = self.clip_paths[i]
            if not p or not os.path.isfile(p):
                continue
            full = self._probe_duration(p) or float(self.duration or 5)
            ss, out = 0.0, full
            tr = self._tl_trim(i)
            if tr:
                ss = max(0.0, min(tr[0], max(0.0, full - 0.2)))
                out = min(tr[1], full)
            dur = max(0.2, out - ss)
            segs.append({"path": p, "kind": "clip", "shot_idx": i,
                         "dur": dur, "ss": ss, "src_dur": full})
            trs = self._tl_trans_after(i)
            if trs:
                tpath = self._make_transition_clip(trs[0], trs[1])
                if tpath:
                    segs.append({"path": tpath, "kind": "trans", "shot_idx": None,
                                 "dur": trs[1], "ss": 0.0, "src_dur": trs[1]})
        return segs

    def timeline_info(self):
        """给时间线面板用：按播放顺序返回每镜素材时长 / 裁切 / 转场 / 总时长。"""
        enabled = isinstance(getattr(self, "timeline", None), dict)
        paths = self.clip_paths or []
        order = self._tl_order() if enabled else list(range(len(paths)))
        items = []
        for pos, i in enumerate(order):
            p = paths[i] if i < len(paths) else None
            full = self._probe_duration(p) if (p and os.path.isfile(p)) else 0.0
            if not full:
                full = float(self.duration or 5)
            sh = self.shots[i] if i < len(self.shots or []) else {}
            tr = self._tl_trim(i)
            ts = self._tl_trans_after(i)
            items.append({
                "pos": pos, "idx": i, "path": p, "src_dur": round(full, 2),
                "in": round(tr[0], 2) if tr else 0.0,
                "out": round(tr[1], 2) if tr else round(full, 2),
                "dur": round((tr[1] - tr[0]) if tr else full, 2),
                "trans": f"{ts[0]}:{ts[1]}" if ts else "",
                "scene": (sh.get("scene") or "") if isinstance(sh, dict) else "",
                "desc": ((sh.get("line") or sh.get("zh") or sh.get("desc") or "")
                         if isinstance(sh, dict) else ""),
            })
        trans_total = sum(float((self._tl_trans_after(x["idx"]) or (0, 0))[1])
                          for x in items)
        return {"order": list(order), "items": items,
                "total": round(sum(x["dur"] for x in items) + trans_total, 2)}

    def _build_segs(self, clip_paths, shots):
        """把 clip_paths 展开成待拼接片段（含转场），跳过生成失败的镜。

        v4.133：启用时间线后改走 build_timeline_segs()（重排+裁切+逐镜转场）；
        时间线没启用或构造异常 → 退回原逻辑，保证旧行为字节级不变。
        """
        if isinstance(getattr(self, "timeline", None), dict):
            try:
                tl_segs = self.build_timeline_segs()
            except Exception as e:
                self.log(f"  ⚠️ 时间线构造失败，按原顺序合成（{e}）")
                tl_segs = []
            if tl_segs:
                return tl_segs
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
                         # v4.125 P2：用片段真实时长（模型实际返回 4.6~6.3s 不等），
                         # 此前硬编码 self.duration 导致字幕/静音轨按预设值铺、逐镜累积漂移。
                         "shot_idx": i, "dur": self._probe_duration(clip_paths[i]) or self.duration})
            prev_scene = sc
        return segs

    def _merge_exec(self, segs, shots, out_path, burn_subtitles, use_clip_audio=True,
                    force_duck=None):
        """真正执行一次 ffmpeg 拼接。

        use_clip_audio=True  → 沿用片段自带音轨（正常路径）
        use_clip_audio=False → 全部铺静音轨（仅在沿用音轨失败时兜底）
        force_duck=None      → BGM 闪避按时间线设置；False=强制关（闪避失败降级重试用）

        v4.133：BGM / 字幕样式只在启用时间线后才有；没启用时 aud/sub 都是空字典，
        拼出来的命令与旧版逐字节相同。
        """
        W, H = self.width, self.height
        m = len(segs)
        tl = self.timeline if isinstance(getattr(self, "timeline", None), dict) else {}
        aud = tl.get("audio") or {}
        sub = tl.get("sub") or {}
        bgm = aud.get("bgm") or ""
        if bgm and not os.path.isfile(bgm):
            self.log(f"  ⚠️ BGM 文件不存在，本次合成忽略：{bgm}")
            bgm = ""
        duck = bool(aud.get("duck", True)) if force_duck is None else bool(force_duck)
        self._duck_used = bool(bgm and duck)
        vparts, aparts, seg_str, sub_segs = [], [], [], []
        t = 0.0
        # 记录每段实测音轨情况，供合成后自检判断「成片该不该有声」
        self._seg_audio = []
        for k, s in enumerate(segs):
            # 每段对应一个输入文件（视频即输入 k）；分镜自带音轨优先沿用，
            # 转场/无音轨片段用静音轨补齐，确保 concat 每段音视频齐全。
            ha = self._probe_has_audio(s["path"]) if use_clip_audio else False
            self._seg_audio.append(bool(ha))
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
        out_audio = "[outa]"

        # v4.133 音频轨：BGM 循环铺满 + 可选人声闪避（sidechaincompress）
        if bgm:
            vol = max(0.0, min(2.0, float(aud.get("vol") or 0.3)))
            total = max(0.2, sum(float(s.get("dur") or 0) for s in segs))
            bi = m  # BGM 是追加在 segs 之后的最后一个输入
            filter_complex += (
                f";[{bi}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                f"channel_layouts=stereo,volume={vol:.3f},atrim=0:{total:.3f}[bgm0]")
            if duck:
                # 人声（[outa]）作 sidechain：说话时自动把 BGM 压下去，说完抬回来
                filter_complex += (
                    ";[bgm0][outa]sidechaincompress=threshold=0.05:ratio=8:"
                    "attack=5:release=350:makeup=1[bgm1]")
                filter_complex += (";[outa][bgm1]amix=inputs=2:duration=first:"
                                   "dropout_transition=0[aout]")
            else:
                filter_complex += (";[outa][bgm0]amix=inputs=2:duration=first:"
                                   "dropout_transition=0[aout]")
            out_audio = "[aout]"

        final_map = "[outv]"
        if burn_subtitles and self.with_dialogue and sub_segs:
            srt_path = os.path.join(self.project_dir, "subtitles.srt")
            self._write_srt(srt_path, sub_segs, shots)
            srt_ff = srt_path.replace("\\", "/").replace(":", "\\:")
            sub_size = int(sub.get("size") or 18)
            sub_color = str(sub.get("color") or "&H00FFFFFF")
            sub_pos = int(sub.get("pos") or 30)
            filter_complex += (
                f";[outv]subtitles='{srt_ff}':force_style="
                f"'FontName=Microsoft YaHei,FontSize={sub_size},PrimaryColour={sub_color},"
                "OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,"
                f"MarginV={sub_pos}'[outsub]")
            final_map = "[outsub]"

        args = [self.ffmpeg, "-y"]
        for s in segs:
            # v4.133 裁切：只有时间线 trim 过的片段才带 ss / src_dur；
            # 旧路径这两个键都不存在 → 不加任何参数，命令与旧版逐字节相同。
            ss = float(s.get("ss") or 0)
            src = float(s.get("src_dur") or 0)
            dur = float(s.get("dur") or 0)
            if ss > 0.01:
                args += ["-ss", f"{ss:.3f}"]
            if src > 0.01 and dur > 0.01 and dur < src - 0.01:
                args += ["-t", f"{dur:.3f}"]
            args += ["-i", s["path"]]
        if bgm:
            args += ["-stream_loop", "-1", "-i", bgm]
        args += ["-filter_complex", filter_complex,
                 "-map", final_map, "-map", out_audio,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-preset", "medium", "-crf", "20",
                 "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", out_path]
        # 记录完整命令（调试用）
        mode = "沿用片段音轨" if use_clip_audio else "静音轨兜底"
        self.log(f"🔧 ffmpeg 合成（{mode}）：{m} 路输入，共 {len(args)} 参数")
        try:
            rc, out, err = self._run_ff(args, timeout=600,
                                        cancel_check=lambda: self._is_cancelled())
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

    def _expect_silent(self, segs):
        """判断成片「按设计就该无声」——只有此时无声才不是 BUG。

        两个信号取并集（任一命中即算该镜预期无声）：
          ① 素材被显式标记为天生无声（智谱兜底等，tools.VIDEO_SILENT_MARK）；
          ② 实测该片段没有音轨（_seg_audio）。
        全部分镜都预期无声 → 返回 (True, 无声镜数, 分镜总数)；否则 (False, ...)。
        注意：只要有一镜预期有声，就照常自检——否则「哑弹」（有声素材被合成没声）
        又会从后门溜走。ffprobe 不可用时 _probe_has_audio 乐观返回 True（保守按有声）。
        """
        marked = getattr(self, "silent_shots", set()) or set()
        audio = list(getattr(self, "_seg_audio", []) or [])
        n_clip = n_silent = 0
        for k, s in enumerate(segs or []):
            if s.get("kind") != "clip":
                continue  # 转场片段本就是静音轨，不参与判定
            n_clip += 1
            idx = s.get("shot_idx")
            has_track = audio[k] if k < len(audio) else True
            if (idx in marked) or (not has_track):
                n_silent += 1
        if n_clip == 0:
            return False, 0, 0
        return (n_silent == n_clip), n_silent, n_clip

    def _check_audio_level(self, path, expect_silent=False, n_silent=0, n_clip=0):
        """合成后自检成片音量——「预期有声却无声」才报警。

        2026-09-06 修：原逻辑无条件「峰值 < -60dB 即疑似静音」，而智谱兜底片段
        天生无音轨、合成时铺 anullsrc（约 -91dB），全空镜成片会被 100% 误报成
        「哑弹 BUG」。现改为预期驱动：整片素材本来就没有声音 → 无声是设计，跳过；
        只要有一镜预期有声 → 照旧做峰值检测，漏报的代价远大于误报。
        """
        if expect_silent:
            self.log(f"  🔇 成片自检：{n_silent}/{n_clip} 个素材为静音素材"
                     f"（空镜/智谱兜底，无音轨）→ 成片按设计无声，跳过静音告警")
            return
        try:
            _rc, _o, err = self._run_ff(
                [self.ffmpeg, "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                timeout=300)
            mt = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err)
            if not mt:
                # 拿不到峰值通常意味着成片压根没有音轨——比「静音」更严重。
                # 旧版在这里静默 return，等于漏报；预期有声时必须报出来。
                try:
                    if not self._probe_has_audio(path):
                        self.log("  ⚠️ 成片自检：成片没有音轨！"
                                 "（预期有声，请查 _merge_exec 是否误铺 anullsrc）")
                except Exception:
                    pass
                return
            mx = float(mt.group(1))
            if mx < -60.0:
                extra = (f"（另有 {n_silent}/{n_clip} 镜为静音素材已排除）"
                         if n_silent else "")
                self.log(f"  ⚠️ 成片自检：音轨峰值 {mx} dB，疑似静音！{extra}"
                         f"（预期有声却无声时请查 _probe_has_audio）")
            else:
                extra = (f"，其中 {n_silent}/{n_clip} 镜为静音素材" if n_silent else "")
                self.log(f"  🔊 成片自检：音轨峰值 {mx} dB（正常{extra}）")
        except Exception:
            pass

    def _merge(self, clip_paths, shots, out_path, burn_subtitles=True):
        # v4.133：时间线里的字幕开关覆盖全局设置（没开时间线则完全不碰）
        if isinstance(getattr(self, "timeline", None), dict):
            burn_subtitles = bool((self.timeline.get("sub") or {})
                                  .get("burn", burn_subtitles))
        segs = self._build_segs(clip_paths, shots)
        if not segs:
            return False, "没有可用的视频片段（全部生成失败或文件丢失）"
        # 1) 正常路径：沿用分镜自带音轨
        ok, detail = self._merge_exec(segs, shots, out_path, burn_subtitles,
                                      use_clip_audio=True)
        if ok:
            exp_silent, n_sil, n_clip = self._expect_silent(segs)
            self._check_audio_level(out_path, expect_silent=exp_silent,
                                    n_silent=n_sil, n_clip=n_clip)
            return True, ""
        # v4.108 M-06：被「停止」中断（cancel_check 命中 kill）不算失败，
        # 直接返回取消文案，不再触发静音轨降级重试（避免取消后白跑一次合并）。
        if self._is_cancelled() or "已取消" in (detail or ""):
            return False, "合成已取消"
        # v4.133：sidechaincompress 在部分 ffmpeg 构建上不可用（报 filter 找不到）。
        # 这不是素材问题，关掉闪避重混一次即可；再失败才继续走下面的静音轨兜底。
        if getattr(self, "_duck_used", False) and not self._is_stream_error(detail):
            self.log("  ⚠️ 人声闪避（sidechaincompress）当前 ffmpeg 不支持，"
                     "降级为普通混音重试…")
            ok_d, detail_d = self._merge_exec(segs, shots, out_path, burn_subtitles,
                                              use_clip_audio=True, force_duck=False)
            if ok_d:
                self.log("  ✅ 已按普通混音合成（BGM 不再自动闪避）")
                exp_silent, n_sil, n_clip = self._expect_silent(segs)
                self._check_audio_level(out_path, expect_silent=exp_silent,
                                        n_silent=n_sil, n_clip=n_clip)
                return True, ""
            detail = detail_d
        # 2) 若失败源于「片段音轨不可用」→ 降级为静音轨重试，保证至少能出片
        if self._is_stream_error(detail):
            self.log("  ⚠️ 沿用分镜音轨失败，降级为静音轨重试（成片将无声）…")
            ok2, detail2 = self._merge_exec(segs, shots, out_path, burn_subtitles,
                                            use_clip_audio=False)
            if ok2:
                self.log("  ⚠️ 已按静音轨降级合成：成片无声"
                         "（素材本身无可用音轨，非合成 BUG；空镜/智谱兜底属预期）")
                return True, ""
            return False, detail2
        return False, detail
