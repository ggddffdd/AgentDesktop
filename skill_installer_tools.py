# -*- coding: utf-8 -*-
"""小臭玩AI — 技能自装工具（受控：对话触发 + 用户拍板 + 自动安全审计 + 落盘用户目录）

让小臭按用户给的方向自己去搜候选技能、展示给用户拍板，选定后自动拉取
SKILL.md → 安全审计（P0 拒绝 / P1 警告 / P2 通过）→ 规整为统一格式
「技能名/SKILL.md」（frontmatter 必含 category，与用户目录既有技能同构）→
写入用户目录 skills/（避开 _internal 被重建覆盖），并更新 config 的 skills_dir。

搜索源：GitHub 仓库搜索 API（实时、结构化、匿名限流宽松）。
纯标准库实现，无第三方依赖，方便冻结打包。
"""

import os
import re
import json
import urllib.request
import urllib.error
import urllib.parse
import logging

log = logging.getLogger("dsdesktop")

# 用户级技能目录（持久，不被重建覆盖）
def _user_skills_dir():
    return os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "skills")


# ---------- 安全审计 ----------

# P0：明确危险指令（诱导小臭用已有工具干坏事 / 直接恶意）
_DANGER_PROMPT_PATTERNS = [
    r"删除.*(文件|目录|folder|file)",
    r"rm\s+-rf",
    r"格式化",
    r"format\s+(disk|磁盘|c:|d:)",
    r"发送.*(到|至|upload|post).*(外部|external|http|url|网址)",
    r"上传.*(密钥|token|密码|password|私钥|secret)",
    r"窃取",
    r"泄露.*(隐私|privacy|通讯录|密码)",
    r"执行.*(shell|系统命令|system command)",
    r"运行.*(命令|command).*(不要|无需|不必).*(确认|告诉|ask)",
    r"绕过.*(确认|confirm|安全|security|审计|audit)",
    r"关闭.*(杀毒|杀软|antivirus|防火墙|firewall)",
]
# P0：代码块中的真实危险调用（将来若支持执行技能函数）
_DANGER_CODE_PATTERNS = [
    r"os\.system\s*\(",
    r"subprocess\.(Popen|run|call|check_output)",
    r"(^|\W)eval\s*\(",
    r"(^|\W)exec\s*\(",
    r"__import__\s*\(",
    r"requests\.post\s*\(",
    r"urllib\.request\.urlopen\s*\(\s*[\"']https?://",
]
# P1：需警惕但可放行
_WARN_PATTERNS = [
    r"https?://",
    r"requests?\.",
    r"eval\s*\(",
    r"exec\s*\(",
    r"读取.*(文件|目录)",
    r"write_file|run_command|run_python|browser_",
]


def audit_skill(skillmd_text, prompt_text):
    """对技能内容做安全审计，返回 (level, reasons)。

    level: "P0"(拒绝) / "P1"(警告放行) / "P2"(安全)
    reasons: 命中的风险描述列表
    """
    reasons = []
    # 1) prompt 诱导危险
    for pat in _DANGER_PROMPT_PATTERNS:
        if re.search(pat, prompt_text, re.IGNORECASE):
            reasons.append(f"P0 危险指令命中：{pat}")
    # 2) 代码块危险（提取 ```...``` 代码块）
    code_blocks = re.findall(r"```(?:python|bash|sh|cmd)?\s*(.*?)```", skillmd_text, re.DOTALL)
    for cb in code_blocks:
        for pat in _DANGER_CODE_PATTERNS:
            if re.search(pat, cb):
                reasons.append(f"P0 危险代码命中：{pat}")
    if reasons:
        return "P0", reasons
    # 3) 警告级
    warn = []
    for pat in _WARN_PATTERNS:
        if re.search(pat, prompt_text, re.IGNORECASE) or any(re.search(pat, cb, re.IGNORECASE) for cb in code_blocks):
            warn.append(pat)
    if warn:
        return "P1", [f"P1 需留意：{w}" for w in warn]
    return "P2", ["未检出明显风险"]


# ---------- SKILL.md → 小臭 .py 转换 ----------

def _parse_skillmd(text):
    """解析 SKILL.md：取 frontmatter 的 name/description/emoji + 正文。"""
    name, description, emoji = "", "", "🧩"
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        fm, body = m.group(1), m.group(2)
        for line in fm.splitlines():
            km = re.match(r"^\s*(name|description|emoji)\s*[:=]\s*(.*?)\s*$", line, re.IGNORECASE)
            if km:
                k, v = km.group(1).lower(), km.group(2).strip().strip('"').strip("'")
                if k == "name":
                    name = v
                elif k == "description":
                    description = v
                elif k == "emoji":
                    emoji = v
    # 若 frontmatter 没 name，尝试从一级标题取
    if not name:
        hm = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if hm:
            name = hm.group(1).strip()
    return name, description, emoji, body.strip()


def _safe_filename(name):
    """把技能名转成安全文件名（保留中文/字母数字，截断）。"""
    s = re.sub(r"[^\w一-鿿\- ]", "_", name).strip().replace(" ", "_")
    s = re.sub(r"_+", "_", s)
    return s[:40] or "skill"


def _normalize_skillmd_for_install(raw_text, default_category="通用技能"):
    """把拉到的 SKILL.md 规整为统一安装格式。

    统一格式 = 技能名/SKILL.md（与用户目录既有 27 个技能同构），frontmatter 必含
    name / description / emoji / category。来源缺字段时给默认值，缺 category 时给
    默认分类，保证小臭市场里每个技能都有分类、可筛选。

    返回 (text, name, category)。
    """
    name, description, emoji, body = _parse_skillmd(raw_text)
    # 单独再取原始 frontmatter 里的 category（_parse_skillmd 不返回 category）
    category = default_category
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw_text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            km = re.match(r"^\s*category\s*[:=]\s*(.*?)\s*$", line, re.IGNORECASE)
            if km:
                category = (km.group(1).strip().strip('"').strip("'") or default_category)
                break
    name = name or "未命名技能"
    emoji = emoji or "🧩"
    description = description or "（无描述）"
    text_body = body.strip()
    front = (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"emoji: {emoji}\n"
        f"category: {category}\n"
        f"---\n\n"
    )
    return front + text_body + "\n", name, category


def convert_skillmd_to_xiaochou(skillmd_text):
    """把 SKILL.md 文本转成小臭 .py 技能文件内容（带 SKILL_* 头注释）。"""
    name, description, emoji, body = _parse_skillmd(skillmd_text)
    lines = [
        "# -*- coding: utf-8 -*-",
        f"# SKILL_NAME: {name}",
        f"# SKILL_DESCRIPTION: {description}",
        f"# SKILL_EMOJI: {emoji}",
        "# SKILL_PROMPT:",
    ]
    for bl in body.splitlines():
        lines.append(f"# {bl}" if bl.strip() else "#")
    return name, description, emoji, "\n".join(lines) + "\n"


# ---------- GitHub 拉取 ----------

_GH_API = "https://api.github.com"
_UA = {"User-Agent": "xiaochou-skill-installer"}


def _http_get(url, timeout=25, retries=2):
    """带重试的 HTTP GET，失败抛最后一个异常。"""
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


# 国内可达的 raw 镜像（官方优先，失败后回退镜像，解决 raw.githubusercontent.com 被墙/超时）
def _mirror_urls(raw_url):
    """生成 raw_url 的多个镜像候选（官方 + 国内可达镜像）。"""
    m = re.match(r"https?://raw\.githubusercontent\.com/(.+)", raw_url)
    if not m:
        return [raw_url]
    path = m.group(1)  # owner/repo/branch/...
    owner, repo, rest = path.split("/", 2)
    bparts = rest.split("/", 1)
    branch = bparts[0]
    fpath = bparts[1] if len(bparts) > 1 else ""
    jsd = (f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{fpath}"
           if fpath else f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}")
    return [
        raw_url,
        f"https://ghproxy.net/https://raw.githubusercontent.com/{path}",
        f"https://raw.gitmirror.com/{path}",
        jsd,
    ]


def _tree_candidates(owner, repo, branch, sub):
    """生成某分支下的 SKILL.md 候选 raw URL（顶层 + 常见子目录 + 大小写）。"""
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    sub = (sub or "").strip("/")
    if sub:
        roots = [sub]
    else:
        roots = ["", "skills", "skill", "Skills", "src", "plugins"]
    files = ["SKILL.md", "skill.md"]
    out = []
    for r in roots:
        for fn in files:
            p = f"{r}/{fn}" if r else fn
            out.append(f"{base}/{p}")
    return out


def _resolve_candidate_raw_urls(url):
    """把用户给的链接解析成一组按优先级排序的 raw SKILL.md 候选 URL。

    支持：raw 直链 / blob 链接 / tree 链接（含子目录）/ 仓库页（自动探测分支与子目录）。
    不依赖 GitHub API 拿默认分支，避免匿名限流；直接试 main/master + 常见子目录。
    返回 list[str]（空=无法识别）。
    """
    url = (url or "").strip()
    # 0) 用户直接给的镜像直链（原样使用）
    if re.match(r"https?://(ghproxy\.net|mirror\.ghproxy\.com|raw\.gitmirror\.com|cdn\.jsdelivr\.net|raw\.github\.com)/", url):
        return [url]
    # 1) 已是 raw 直链
    m = re.match(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(.+)", url)
    if m:
        return [url]
    # 2) blob 链接 -> raw
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", url)
    if m:
        owner, repo, branch, path = m.groups()
        return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"]
    # 3) tree 链接（含子目录）
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)(?:/(.+))?$", url)
    if m:
        owner, repo, branch, sub = m.groups()
        return _tree_candidates(owner, repo, branch, sub or "")
    # 4) 仓库页（无子路径）
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)(?:/.*)?$", url)
    if not m:
        return []
    owner, repo = m.groups()
    cands = []
    for branch in ("main", "master"):
        cands += _tree_candidates(owner, repo, branch, "")
    return cands


def _fetch_skillmd(candidate_raw_urls):
    """依次尝试候选 raw URL 及其镜像，返回 (text, used_url) 或 (None, reason)。"""
    for ru in candidate_raw_urls:
        for mu in _mirror_urls(ru):
            try:
                txt = _http_get(mu, timeout=25)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break  # 此候选确定不存在，跳下一个候选
                continue
            except Exception:
                continue
            if not txt or txt.lstrip().upper().startswith("<!DOCTYPE") or txt.lstrip().startswith("<html"):
                continue
            return txt, mu
    return None, "所有候选地址（含国内镜像）均拉取失败"


def skill_search(query):
    """按方向搜索候选技能仓库，返回格式化文本。"""
    q = (query or "").strip()
    if not q:
        return "失败：skill_search 需要搜索方向关键词，例如「短视频脚本」「PDF处理」"
    q_enc = urllib.parse.quote(f"{q} skill")
    api = f"{_GH_API}/search/repositories?q={q_enc}&sort=stars&order=desc&per_page=10"
    try:
        data = json.loads(_http_get(api))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return ("在线搜索受限（GitHub API 限流），请稍候再试，或直接在对话里发给我具体的 "
                    "SKILL.md / 仓库链接（也可发 cdn.jsdelivr.net 或 ghproxy 的直链），我用 skill_install 直接装。")
        return f"搜索失败：GitHub API 返回 {e.code}"
    except Exception as e:
        return f"搜索失败：{e}"
    items = data.get("items", [])
    if not items:
        return f"未找到与「{q}」相关的技能仓库。换个关键词，或给我具体链接。"
    out = [f"🔍 找到 {len(items)} 个候选技能仓库（按 stars 排序）：\n"]
    for i, it in enumerate(items, 1):
        name = it.get("full_name", "")
        desc = (it.get("description") or "无描述")
        stars = it.get("stargazers_count", 0)
        html = it.get("html_url", "")
        out.append(f"{i}. {name} ⭐{stars}\n   {desc}\n   {html}")
    out.append("\n请告诉我装第几个（或发仓库链接），我来拉取并自动审计安装。")
    return "\n".join(out)


# ---------- 安装 ----------

def tool_skill_search(cfg, app_dir, args):
    return (skill_search(args.get("query", "")), [], None)


def tool_skill_install(cfg, app_dir, args):
    """拉取 SKILL.md → 审计 → 转换 → 写用户目录 skills/ → 更新 config skills_dir。"""
    url = (args.get("url") or "").strip()
    if not url:
        return ("失败：skill_install 需要来源 url（GitHub 仓库页或 raw SKILL.md 链接）", [], None)

    cands = _resolve_candidate_raw_urls(url)
    if not cands:
        return ("失败：无法识别的 GitHub 链接（支持：仓库页 / tree 链接 / blob 链接 / raw SKILL.md 直链）", [], None)

    # 1) 拉取（多候选 + 多镜像自动回退 + 重试）
    skillmd, used = _fetch_skillmd(cands)
    if not skillmd:
        return (
            f"失败：{used}。\n"
            f"💡 秘诀：直接给我「raw.githubusercontent.com 的 SKILL.md 直链」（不要只给仓库页），\n"
            f"或确认该仓库里 SKILL.md 的真实路径（很可能在 skills/ 之类子目录下）。",
            [], None,
        )
    # 校验确实是 SKILL.md
    if "SKILL_NAME" not in skillmd and "name:" not in skillmd and "name =" not in skillmd and "frontmatter" not in skillmd.lower():
        return (f"失败：拉到的内容不像 SKILL.md（缺少技能名称字段）。请确认链接指向原始 SKILL.md 文件。", [], None)

    # 2) 规整为统一安装格式：技能名/SKILL.md（与用户目录既有技能同格式，必带 category）
    norm_md, name, category = _normalize_skillmd_for_install(skillmd)
    if not name:
        return ("失败：无法从 SKILL.md 解析出技能名称（缺少 frontmatter 的 name 或标题）。", [], None)

    # 3) 审计（基于原始 SKILL.md 全文：代码块危险 + prompt 危险指令）
    level, reasons = audit_skill(skillmd, skillmd)
    audit_summary = "；".join(reasons)
    if level == "P0":
        return (
            f"⛔ 安全审计拒绝安装「{name}」（来源 {url[:80]}）\n"
            f"风险：{audit_summary}\n\n该技能含危险指令/代码，已阻止安装。",
            [], None,
        )

    # 4) 写盘：用户目录 技能名/SKILL.md（唯一完整来源，统一格式，重打包不丢）
    skills_dir = _user_skills_dir()
    os.makedirs(skills_dir, exist_ok=True)
    folder = os.path.join(skills_dir, _safe_filename(name))
    os.makedirs(folder, exist_ok=True)
    fpath = os.path.join(folder, "SKILL.md")
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(norm_md)
    except Exception as e:
        return (f"失败：写入技能文件出错：{e}", [], None)

    # 5) 更新 config skills_dir（让下次启动加载用户目录技能）
    try:
        cfg_path = os.path.join(app_dir, "config.json")
        data = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if data.get("skills_dir") != skills_dir:
            data["skills_dir"] = skills_dir
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("更新 config skills_dir 失败: %s", e)

    warn_note = ""
    if level == "P1":
        warn_note = f"\n⚠️ 审计提示（已放行）：{audit_summary}"
    return (
        f"✅ 已安装技能「{name}」\n"
        f"分类：{category}\n"
        f"来源：{url}\n"
        f"文件：{fpath}\n"
        f"安全等级：{level}{warn_note}\n\n"
        f"重启小臭后，它会出现在【技能市场 / 可用技能】清单，用 use_skill 传入「{name}」即可加载。",
        [], None,
    )


def _split_prompt(py_content):
    """从转换后的 .py 取出 SKILL_PROMPT 正文（供审计）。"""
    name, desc, emoji, prompt = "", "", "🧩", ""
    in_prompt = False
    plines = []
    for line in py_content.splitlines():
        m = re.match(r"^#\s*SKILL_NAME:\s*(.*?)\s*$", line)
        if m:
            name = m.group(1); continue
        m = re.match(r"^#\s*SKILL_DESCRIPTION:\s*(.*?)\s*$", line)
        if m:
            desc = m.group(1); continue
        m = re.match(r"^#\s*SKILL_EMOJI:\s*(.*?)\s*$", line)
        if m:
            emoji = m.group(1); continue
        m = re.match(r"^#\s*SKILL_PROMPT:\s*$", line)
        if m:
            in_prompt = True; continue
        if in_prompt:
            if line.startswith("#"):
                plines.append(line.lstrip("#").strip())
            else:
                plines.append(line)
    return (name, desc, emoji, "\n".join(plines).strip())


# ---------- 声明式 schema ----------

SKILL_INSTALLER_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "skill_search",
            "description": "按方向实时搜索外部技能仓库（GitHub），返回候选清单（名称、描述、stars、来源链接）。用于在【可用技能】不足时为用户发现新技能。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索方向关键词，如「短视频脚本」「PDF处理」「数据分析」"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_install",
            "description": "安装一个外部技能：拉取 GitHub 上的 SKILL.md，自动做安全审计（P0 危险拒绝），通过后转成小臭技能格式写入用户目录并生效。安装前会弹确认框。链接支持：仓库页 / tree 或 blob 链接 / raw SKILL.md 直链；会自动探测子目录与分支，国内网络不可达时自动走镜像。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "技能来源：GitHub 仓库页链接，或 raw SKILL.md 直链"},
                },
                "required": ["url"],
            },
        },
    },
]

SKILL_INSTALLER_TOOL_TABLE = {
    "skill_search": tool_skill_search,
    "skill_install": tool_skill_install,
}
