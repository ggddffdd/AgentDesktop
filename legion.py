# -*- coding: utf-8 -*-
"""Agent 军团数据层 v4.121

把「编排页」从单一流水线（小说一条龙）泛化成**可自定义团队角色的多项目军团**。

设计要点：
- **角色库 role_library**：可复用角色定义，每个角色含 7 要素
  （使命 / 约束 / 工具白名单 / 模型 / 输出格式 / 质量标准 / 自检），
  范式参考 Apache-2.0 项目 openclaw-multi-agent-team 的结构化角色 prompt。
- **项目 projects**：多项目并存。每个项目 = 若干**波次 wave**，
  wave 内成员并行、wave 间串行依赖，正好映射到已有的 task_graph.TaskGraph。
- **成员自包含**：项目里的成员是角色快照（从角色库导入时复制一份），
  改角色库不会破坏已有项目。

数据存 ~/Documents/小臭玩AI/legion.json（独立文件，不污染 config.json）。
"""
import os
import re
import json
import uuid
import copy
import logging

log = logging.getLogger("legion")

LEGION_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
LEGION_PATH = os.path.join(LEGION_DIR, "legion.json")
# 技能 SKILL.md 默认扫描根目录（不持久化进 legion.json，每次现扫）
DEFAULT_SKILLS_DIR = os.path.join(LEGION_DIR, "skills")

# 项目分类（沿用 workflow_manager_ui 的分类习惯，另补军团专属）
CATEGORIES = ["内容创作", "视频创作", "小说创作", "营销运营", "调研分析", "日常助手", "其他"]

# 角色 7 要素字段名（顺序即表单顺序）
ROLE_FIELDS = [
    ("name", "角色名"),
    ("emoji", "图标"),
    ("mission", "使命"),
    ("constraints", "约束"),
    ("tools", "工具白名单"),
    ("model", "模型"),
    ("output_format", "输出格式"),
    ("quality", "质量标准"),
    ("self_check", "自检"),
]

# 常用工具候选（与 agent_node._TOOL_DESC 对齐，另补实际在用的工具名）
TOOL_CANDIDATES = [
    "web_search", "web_fetch", "write_file", "read_file",
    "run_python", "image_gen", "search_memory", "remember",
]

SCHEMA_VERSION = 1


# ============ 工厂 ============
def new_role(name="新角色", emoji="", mission="", constraints="",
             tools=None, model="", output_format="", quality="", self_check=""):
    """构造一个角色定义（7 要素）。model 留空 = 跟随全局配置。"""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "emoji": emoji or "",
        "mission": mission,
        "constraints": constraints,
        "tools": list(tools or []),
        "model": model or "",
        "output_format": output_format,
        "quality": quality,
        "self_check": self_check,
    }


def new_project(name="新项目", emoji="", description="", category="其他"):
    """构造一个军团项目：含两个空波次，成员由用户在编辑器里添加。"""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "emoji": emoji or "",
        "description": description or "",
        "category": category,
        "waves": [{"members": []}],
        "review_enabled": False,
    }


def new_wave():
    return {"members": []}


# ============ 角色 → system prompt（7 要素 + 可选挂载技能）============
def build_role_prompt(role: dict, skills_dir: str = None) -> str:
    """把角色 7 要素 + 挂载的技能拼成 system prompt。

    只拼非空字段，避免把一堆空标题塞进上下文白烧 token。
    挂载的技能（role.skills[]）按 slug 去 skills_dir 读 SKILL.md 正文，
    拼在末尾 —— 这是「角色身份 + 方法论」组合的关键。
    """
    if not role:
        return ""
    name = role.get("name", "角色")
    parts = [f"你是「{name}」。"]

    def _add(title, key):
        v = (role.get(key) or "").strip()
        if v:
            parts.append(f"\n【{title}】\n{v}")

    _add("使命", "mission")
    _add("约束", "constraints")
    tools = role.get("tools") or []
    if tools:
        parts.append("\n【可用工具】\n只允许调用：" + "、".join(tools) +
                     "。其余工具一律不可用，需要时说明无法完成。")
    else:
        parts.append("\n【可用工具】\n本角色不使用工具，直接输出分析文本。")
    _add("输出格式", "output_format")
    _add("质量标准", "quality")
    _add("自检", "self_check")

    # ---- 挂载的技能 / 方法论（v4.121.3 新增）----
    skill_slugs = [s for s in (role.get("skills") or []) if isinstance(s, str) and s.strip()]
    if skill_slugs:
        skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        parts.append("\n【挂载的技能 / 方法论】\n以下是本角色本次任务需要遵循的方法论/工作流：")
        for slug in skill_slugs:
            sk = _load_skill_prompt(slug, skills_dir)
            if sk:
                emoji = sk.get("emoji", "")
                sname = sk.get("name") or slug
                body = (sk.get("prompt") or "").strip()
                if body:
                    parts.append(f"\n### {emoji} {sname}\n{body}")
            else:
                parts.append(f"\n### ⚠️ {slug}\n（技能文件未找到，跳过）")

    return "\n".join(parts)


# ============ 默认角色库 ============
def default_role_library():
    """开箱即用的角色库（大哥可直接用，也可改）。

    工具名与 agent_node._TOOL_DESC / config.get_all_tools 对齐。
    """
    return [
        new_role(
            name="研究员", emoji="🔍",
            mission="搜索互联网获取信息，整理成结构化中文摘要",
            constraints="只采信有明确来源的信息，标注来源；找不到就明说找不到，不许编",
            tools=["web_search", "web_fetch"],
            output_format="分点列出，每条含【结论】【来源】【可信度】",
            quality="至少 3 个独立来源，覆盖正反两面观点",
            self_check="逐条检查是否有无来源的断言，有就删或补来源",
        ),
        new_role(
            name="分析师", emoji="📊",
            mission="基于上游材料提炼关键洞察、判断与建议",
            constraints="不做新的事实检索，只基于已有材料推理；区分「事实」与「推断」",
            tools=[],
            output_format="洞察 3-5 条 + 每条的判断依据 + 最终建议",
            quality="每条洞察必须能追溯到上游材料，禁止凭空发挥",
            self_check="检查是否存在没有依据的推断，标出来",
        ),
        new_role(
            name="写手", emoji="✍️",
            mission="把上游结论写成可直接使用的成稿",
            constraints="语气自然、去 AI 味；不新增未经上游确认的事实",
            tools=["write_file", "read_file"],
            output_format="完整成稿，结构清晰，可直接发布",
            quality="开头有钩子、中间有干货、结尾有收束",
            self_check="通读检查是否有 AI 腔和空话，有就改掉",
        ),
        new_role(
            name="配图师", emoji="🎨",
            mission="把文字内容转成可直接生图的提示词",
            constraints="只输出提示词，不输出解释；中文提示词，风格统一",
            tools=["image_gen"],
            output_format="按条编号，每条一句完整提示词（含风格+主体+构图+光线）",
            quality="提示词要具体到能直接出图，避免抽象形容词堆砌",
            self_check="检查每条提示词是否含风格与主体，缺一补上",
        ),
        new_role(
            name="审校", emoji="🔎",
            mission="独立审查上游成稿，挑事实错误、逻辑漏洞与合规风险",
            constraints="只评判不改写；问题按严重度分级；参与执行者不得自审",
            tools=[],
            output_format="问题清单（严重度 / 位置 / 问题描述 / 修改建议）+ 总评 PASS 或 FAIL",
            quality="必须给出至少 1 条具体可执行的修改建议，不能只说「不够好」",
            self_check="检查每条问题是否指出了具体位置和改法",
        ),
        new_role(
            name="策划", emoji="🧭",
            mission="把模糊需求拆成可执行方案：目标、路径、分工、验收标准",
            constraints="方案要能落地，拒绝空泛方法论；明确标出前置依赖",
            tools=[],
            output_format="目标 / 拆解步骤 / 每步产出物 / 验收标准",
            quality="每步都要有可交付的产出物，没有产出物的步骤删掉",
            self_check="检查是否每步都能判断「做完了没有」",
        ),
        # ---- 电商自动运营军团专用（大哥 09-05 新增）----
        new_role(
            name="选品官", emoji="🛒",
            mission="从市场趋势、需求缺口、利润空间三维度，选出有潜力且可落地的商品",
            constraints="只基于已有调研材料或常识判断，不凭空编造数据；区分「趋势」与「跟风」",
            tools=[],
            output_format="候选商品 3-5 个，每个含【市场趋势】【目标人群】【利润预估】【风险点】",
            quality="每个候选必须给出至少 1 条可辩护的支撑理由，禁止「感觉不错」",
            self_check="逐条检查是否都有依据，无依据的候选删掉",
        ),
        new_role(
            name="竞品分析师", emoji="⚔️",
            mission="拆解竞品/对标账号的商品、内容、价格与打法，找出可借鉴处与差异化机会",
            constraints="只做事实拆解与中立对比，不贬低对手；信息来源需标注",
            tools=["web_search", "web_fetch"],
            output_format="竞品画像（每个：定位/核心卖点/价格/内容风格/可借鉴处/差异化机会）",
            quality="每个竞品至少指出 1 点可借鉴 + 1 点差异化机会",
            self_check="检查是否有主观拉踩描述，一律改成立中陈述",
        ),
        new_role(
            name="带货文案", emoji="✍️",
            mission="把商品卖点写成能打动目标人群、可直接发布的带货文案或口播稿",
            constraints="不夸大功效、不虚假承诺、符合广告合规；语气口语化、去 AI 味",
            tools=["write_file", "read_file"],
            output_format="标题钩子 + 正文（痛点-卖点-信任-行动）+ 适用人群 / 慎用人群",
            quality="前 3 秒有钩子，每个卖点有依据，结尾有明确行动指令",
            self_check="通读检查是否有夸大或违禁词，有就改写",
        ),
        new_role(
            name="主图策划", emoji="🖼️",
            mission="把商品卖点转成能直接生图/出素材的视觉方案与生图提示词",
            constraints="只输出视觉方案与提示词，不输出成图解释；风格全篇统一",
            tools=["image_gen"],
            output_format="每张素材 1 条完整提示词（风格+主体+构图+光线+卖点可视化）",
            quality="提示词具体到能直接出图，卖点要可视化而非抽象形容词堆砌",
            self_check="检查每条是否含风格+主体+卖点，缺一补上",
        ),
        new_role(
            name="投放运营", emoji="📈",
            mission="基于商品与人群，制定流量投放/起量策略与数据复盘框架",
            constraints="不承诺具体 ROI 数字；区分策略与执行；标注关键前提假设",
            tools=["web_search", "read_file"],
            output_format="人群分层 / 渠道匹配 / 预算分配 / 关键指标与复盘模板",
            quality="每个渠道给出适用场景与理由；复盘要有清晰 KPI，可落地",
            self_check="检查是否承诺了不切实际的数字，是就改为区间或条件",
        ),
        new_role(
            name="转化话术师", emoji="💬",
            mission="设计售前/私域/客服转化的沟通话术与常见异议应答",
            constraints="不催单不骚扰、不承诺售后之外的事项；语气真诚不油腻",
            tools=[],
            output_format="开场话术 / 常见异议应答（问-答）/ 促单边界话术",
            quality="每个异议给出话术+适用场景+要避免踩的坑",
            self_check="检查是否有过度承诺或骚扰式话术，有就删除",
        ),
    ]


# ============ 默认项目（示例，多项目并存）============
def _member(role):
    """成员快照：从角色定义复制一份进项目（自包含，改库不破项目）。"""
    return copy.deepcopy(role)


def default_projects():
    lib = default_role_library()
    by_name = {r["name"]: r for r in lib}

    research = new_project(
        name="通用调研军团", emoji="🔬",
        description="多角度并行检索 → 交叉分析 → 成稿。适合任何调研类需求",
        category="调研分析")
    research["waves"] = [
        {"members": [_member(by_name["研究员"]), _member(by_name["策划"])]},
        {"members": [_member(by_name["分析师"])]},
        {"members": [_member(by_name["写手"])]},
    ]

    wechat = new_project(
        name="公众号养生文", emoji="📝",
        description="选题 → 写稿 → 配图 → 审校（示例：内容生产线）",
        category="内容创作")
    wechat["waves"] = [
        {"members": [_member(by_name["策划"]), _member(by_name["研究员"])]},
        {"members": [_member(by_name["写手"])]},
        {"members": [_member(by_name["配图师"]), _member(by_name["审校"])]},
    ]

    return [research, wechat]


def default_legion():
    return {
        "version": SCHEMA_VERSION,
        "role_library": default_role_library(),
        "projects": default_projects(),
    }


# 预置角色名清单：加载时若角色库缺这些名字，自动从默认库补齐（保留用户对已有同名的定制）
PRESET_ROLE_NAMES = [
    "研究员", "分析师", "写手", "配图师", "审校", "策划",
    "选品官", "竞品分析师", "带货文案", "主图策划", "投放运营", "转化话术师",
]


# ============ 持久化 ============
def load_legion():
    """读取军团数据；文件不存在/损坏则回落默认（不抛异常拖垮主程序）。"""
    try:
        if not os.path.exists(LEGION_PATH):
            data = default_legion()
            save_legion(data)
            return data
        with open(LEGION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("legion.json 顶层不是对象")
        data.setdefault("version", SCHEMA_VERSION)
        data.setdefault("role_library", default_role_library())
        data.setdefault("projects", [])
        # 预置角色缺失自动补齐：旧数据升级能拿到新增预置角色（如电商标），
        # 已存在的同名角色保留用户定制，绝不覆盖。
        _fill_preset_roles(data)
        # 结构自愈：项目/波次字段缺失补齐，避免旧数据炸 UI
        for p in data["projects"]:
            if not isinstance(p, dict):
                continue
            p.setdefault("id", str(uuid.uuid4()))
            p.setdefault("name", "未命名项目")
            p.setdefault("emoji", "")
            p.setdefault("description", "")
            p.setdefault("category", "其他")
            p.setdefault("review_enabled", False)
            waves = p.get("waves")
            if not isinstance(waves, list) or not waves:
                p["waves"] = [new_wave()]
            for w in p["waves"]:
                if isinstance(w, dict):
                    w.setdefault("members", [])
                    # 成员自愈：补 skills 字段（v4.121.3 新增，按需挂载技能）
                    for m in w.get("members", []):
                        if isinstance(m, dict):
                            m.setdefault("skills", [])
        return data
    except Exception as e:
        log.warning("读取军团数据失败，回落到默认: %s", e)
        return default_legion()


def _fill_preset_roles(data):
    """按 PRESET_ROLE_NAMES 补齐缺失的预置角色。仅补缺，不动已有同名。"""
    try:
        lib = data.get("role_library") or []
        if not isinstance(lib, list):
            lib = []
        existing = {r.get("name") for r in lib if isinstance(r, dict)}
        defaults = {r["name"]: r for r in default_role_library()}
        for name in PRESET_ROLE_NAMES:
            if name not in existing and name in defaults:
                lib.append(copy.deepcopy(defaults[name]))
        if lib:
            data["role_library"] = lib
    except Exception as e:
        log.warning("补齐预置角色失败: %s", e)


def save_legion(data):
    try:
        os.makedirs(LEGION_DIR, exist_ok=True)
        with open(LEGION_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.warning("保存军团数据失败: %s", e)
        return False


# ============ 查询辅助 ============
def find_project(data, project_id):
    for p in (data or {}).get("projects", []):
        if p.get("id") == project_id:
            return p
    return None


def find_role(data, role_id):
    for r in (data or {}).get("role_library", []):
        if r.get("id") == role_id:
            return r
    return None


def wave_members(project):
    """返回项目的波次成员二维列表 [[role, ...], ...]（过滤空波次）。"""
    out = []
    for w in (project or {}).get("waves", []):
        members = [m for m in (w.get("members") or []) if isinstance(m, dict)]
        if members:
            out.append(members)
    return out


def project_summary(project):
    """一行摘要：共 N 个成员 / M 个波次。"""
    waves = wave_members(project)
    n_members = sum(len(w) for w in waves)
    return f"{len(waves)} 个波次 · {n_members} 位成员"


# ============ 技能扫描（运行时，不持久化）============
# SKILL.md frontmatter 是 4 行 key: value 块，简单 regex 拆即可，无需引入 yaml 库。
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([a-zA-Z_]+)\s*:\s*(.*)$")


def _parse_skill_md(path: str):
    """读一份 SKILL.md，返回 {name, emoji, description, category, prompt}；失败返 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    m = _FM_RE.match(text)
    if not m:
        # 没有 frontmatter 也允许：name=目录名，prompt=全文
        return {"name": "", "emoji": "", "description": "", "category": "", "prompt": text.strip()}
    fm, body = m.group(1), m.group(2)
    out = {"name": "", "emoji": "", "description": "", "category": "", "prompt": body.strip()}
    for line in fm.splitlines():
        km = _KV_RE.match(line.strip())
        if not km:
            continue
        key = km.group(1).lower()
        val = km.group(2).strip().strip('"').strip("'")
        if key in out:
            out[key] = val
    # 兜底：emoji 从正文首行标题里抓（"# xxx emoji xxx" 这种）
    if not out["emoji"]:
        hh = re.match(r"^#\s+(.+)", body.strip())
        if hh:
            for ch in hh.group(1):
                if ord(ch) > 127 and ord(ch) > 0x1F300:  # 命中第一个非 ASCII 字符（粗略）
                    out["emoji"] = ch
                    break
    return out


def scan_available_skills(skills_dir: str = None):
    """扫描 skills 目录，返回 [{slug, name, emoji, description, category, prompt, path}]。

    路径不存在或为空 → 返回 []。每次现扫，不缓存，方便新建/修改 SKILL.md 后立即可用。
    """
    skills_dir = skills_dir or DEFAULT_SKILLS_DIR
    out = []
    if not os.path.isdir(skills_dir):
        return out
    try:
        for slug in sorted(os.listdir(skills_dir)):
            md_path = os.path.join(skills_dir, slug, "SKILL.md")
            if not os.path.isfile(md_path):
                continue
            info = _parse_skill_md(md_path)
            if not info:
                continue
            info["slug"] = slug
            info["path"] = md_path
            out.append(info)
    except Exception as e:
        log.warning("扫描技能目录失败: %s", e)
    return out


def _load_skill_prompt(slug: str, skills_dir: str = None):
    """按 slug 单文件读取，返回 dict（与 _parse_skill_md 一致）；失败返 None。"""
    skills_dir = skills_dir or DEFAULT_SKILLS_DIR
    md_path = os.path.join(skills_dir, slug, "SKILL.md")
    info = _parse_skill_md(md_path)
    if info:
        info["slug"] = slug
    return info
