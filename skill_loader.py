# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 动态技能加载模块

扫描 skills 目录下所有 .py 文件，解析其顶部的 SKILL_NAME / SKILL_DESCRIPTION /
SKILL_PROMPT 注释声明，封装为 Skill 对象供 Agent 使用。
"""

import os
import re
import logging
import threading

log = logging.getLogger("dsdesktop")

# ---------- 技能扫描缓存（建议4：mtime 签名，避免每次发消息全量重扫） ----------
_scan_cache = {}           # dir_path -> (signature, [Skill])
_scan_lock = threading.Lock()


def _dir_signature(skills_dir):
    """目录内容签名：收集每个技能文件 (st_mtime, st_size)。

    内容/增删不变则签名不变 → 命中缓存跳过解析；编辑 SKILL.md 会因 mtime 变化自动失效。
    """
    sig = []
    try:
        for fname in sorted(os.listdir(skills_dir)):
            full = os.path.join(skills_dir, fname)
            if fname.endswith(".py") and not fname.startswith("__") and os.path.isfile(full):
                st = os.stat(full)
                sig.append(("py", fname, int(st.st_mtime), st.st_size))
            elif os.path.isdir(full):
                md = os.path.join(full, "SKILL.md")
                if os.path.isfile(md):
                    st = os.stat(md)
                    sig.append(("md", fname, int(st.st_mtime), st.st_size))
    except Exception:
        return None
    return tuple(sig)


def invalidate_skill_cache(skills_dir=None):
    """清除技能扫描缓存（技能安装/卸载后调用，强制下次重扫）。

    skills_dir 为 None 时清空全部。
    """
    with _scan_lock:
        if skills_dir is None:
            _scan_cache.clear()
        else:
            _scan_cache.pop(os.path.abspath(skills_dir), None)

# ---------- 技能名归一化 ----------
# 模型调用 use_skill 时常照抄系统提示里「{emoji} {name}」的格式，把 emoji 前缀也带进
# skill_name（如「📊 ppt-generator」），而 SKILL.md 里的 name 不含 emoji → 精确匹配失败。
# 这里统一剥掉 emoji/符号、空白转连字符、转小写，让「📊 ppt-generator」「PPT Generator」
# 都能命中「ppt-generator」。注意：保留中文（CJK 不在下列 emoji/符号区间内）。
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"      # emoji & pictographs
    "\U0001F1E6-\U0001F1FF"        # 区域指示符（国旗）
    "\U00002600-\U000027BF"        # 杂项符号 + 装饰符号（含 ⚡ ✅ ❤ 等）
    "\U00002B00-\U00002BFF"        # 箭头与杂项符号
    "\U00002300-\U000023FF"        # 技术/电信符号
    "\U0000FE00-\U0000FE0F"        # 变异选择符
    "\U0000200D"                   # 零宽连接符 ZWJ
    "\U000020E3]"                  # 键帽组合符
)


def normalize_skill_name(s):
    """把技能名归一：剥 emoji/符号、去首尾空白、空格转连字符、转小写。

    中文技能名（如「代码审查」）原样保留。
    """
    if not s:
        return ""
    s = s.strip()
    s = _EMOJI_RE.sub("", s).strip()
    s = s.replace(" ", "-")
    return s.lower()


# ---------- Skill ----------

class Skill:
    """技能描述对象"""

    def __init__(self, name="", description="", emoji="", prompt="", file_path="", category="", toolbar=False, body=""):
        self.name = name                # 技能名称
        self.description = description  # 简短描述
        self.emoji = emoji              # 表情符号
        self.prompt = prompt            # 技能提示词（全文，含 frontmatter）
        self.file_path = file_path      # 源文件绝对路径
        self.category = category        # 分类（来自 SKILL.md frontmatter，可能为空）
        self.toolbar = toolbar          # 是否上技能条（SKILL.md frontmatter toolbar: true）
        self.body = body                # frontmatter 之后的正文（去元数据）

    def __repr__(self):
        return f"Skill(name={self.name!r}, file={self.file_path!r})"


# ---------- 注释头解析 ----------

_SKILL_HEADER_RE = re.compile(
    r"""^[#']*?\s*SKILL_NAME:\s*(.*?)\s*$|
        ^[#']*?\s*SKILL_DESCRIPTION:\s*(.*?)\s*$|
        ^[#']*?\s*SKILL_EMOJI:\s*(.*?)\s*$|
        ^[#']*?\s*SKILL_PROMPT:\s*$
    """,
    re.MULTILINE | re.VERBOSE,
)


def _read_text(path):
    """鲁棒读取文本文件，自动识别 UTF-8 / UTF-8-BOM / UTF-16(LE/BE) 编码。

    Windows 记事本或工具导出的 SKILL.md 常带 UTF-16 BOM（ff fe），
    用固定 encoding 会解码失败，这里统一探测 BOM 再解码。
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        raise IOError(f"读取失败 {path}: {e}")
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
    elif raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    else:
        try:
            text = raw.decode("utf-8")
        except Exception:
            text = None
        if text is None:
            try:
                text = raw.decode("utf-16")
            except Exception:
                text = raw.decode("latin-1")
    # 剥离解码后残留的 BOM 字符（utf-8-sig 自动去，UTF-16 解码不会）
    if text and text[0] == "\ufeff":
        text = text[1:]
    return text


def _parse_skill_file(file_path):
    """解析单个 .py 技能文件，返回 Skill 或 None。"""
    try:
        text = _read_text(file_path)
    except Exception as e:
        log.warning("读取技能文件失败 %s: %s", file_path, e)
        return None

    name = ""
    description = ""
    emoji = ""
    prompt_lines = []
    in_prompt = False

    for line in text.splitlines():
        # 检测 SKILL_NAME
        m = re.match(r"^[#']*\s*SKILL_NAME:\s*(.*?)\s*$", line)
        if m:
            name = m.group(1).strip()
            continue
        # 检测 SKILL_DESCRIPTION
        m = re.match(r"^[#']*\s*SKILL_DESCRIPTION:\s*(.*?)\s*$", line)
        if m:
            description = m.group(1).strip()
            continue
        # 检测 SKILL_EMOJI（可选）
        m = re.match(r"^[#']*\s*SKILL_EMOJI:\s*(.*?)\s*$", line)
        if m:
            emoji = m.group(1).strip()
            continue
        # 检测 SKILL_PROMPT:（多行模式）
        m = re.match(r"^[#']*\s*SKILL_PROMPT:\s*$", line)
        if m:
            in_prompt = True
            continue
        if in_prompt:
            # 从 # 开头的注释行中提取内容
            stripped = line.strip()
            if stripped.startswith("#"):
                prompt_text = stripped.lstrip("#").strip()
                if prompt_text:
                    prompt_lines.append(prompt_text)
                else:
                    prompt_lines.append("")
            else:
                # 遇到非注释行，结束 prompt 收集
                # 但也可能是空行或代码，安全起见只收集注释行
                # 如果行以注释字符开头，继续收集
                pass

    if not name:
        log.debug("跳过无效技能文件 %s（缺少 SKILL_NAME）", file_path)
        return None

    prompt = "\n".join(prompt_lines).strip()
    return Skill(
        name=name,
        description=description,
        emoji=emoji,
        prompt=prompt,
        file_path=file_path,
    )


# ---------- 公共 API ----------

def _parse_skill_md(md_path, folder_name):
    """解析 技能名/SKILL.md 动态技能，返回 Skill 或 None。

    支持两种格式：
    1) YAML frontmatter：--- name: x / description: y / emoji: z ---
    2) 首行 `# 名称`（最简格式）
    prompt 取 SKILL.md 全文，use_skill 加载时整体注入。
    """
    try:
        text = _read_text(md_path)
    except Exception as e:
        log.warning("读取技能文件失败 %s: %s", md_path, e)
        return None

    if not text.strip():
        return None

    name = ""
    description = ""
    emoji = ""
    category = ""

    # 尝试解析 YAML frontmatter
    text_body = text
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            lines = block.splitlines()
            i = 0
            while i < len(lines):
                m = re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", lines[i].strip())
                if m:
                    key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                    # YAML > 折叠 / | 保留多行：后续缩进行（非 key: value）拼成值
                    if val in (">", "|"):
                        parts, j = [], i + 1
                        while j < len(lines):
                            s = lines[j].strip()
                            if not s or re.match(r"^([A-Za-z_]+)\s*:", s):
                                break
                            parts.append(s)
                            j += 1
                        val = " ".join(parts) if val == ">" else "\n".join(parts)
                        fm[key] = val
                        i = j
                        continue
                    fm[key] = val
                i += 1
            text_body = text[end + 4:].lstrip("\n")

    if fm:
        name = fm.get("name", "")
        description = fm.get("description", "")
        emoji = fm.get("emoji", "")
        category = fm.get("category", "")
        raw_tb = fm.get("toolbar", "")
        toolbar = (isinstance(raw_tb, str) and raw_tb.strip().lower() in ("true", "1", "yes", "是")) or (raw_tb is True)
    else:
        toolbar = False

    # 回退：首行 # 名称
    if not name:
        first = text_body.splitlines()[0].strip() if text_body.splitlines() else ""
        if first.startswith("# "):
            name = first[2:].strip()

    # 再回退：文件夹名
    if not name:
        name = folder_name

    if not name:
        log.debug("跳过无效技能 %s（缺少名称）", md_path)
        return None

    # description 回退：取正文首个非空非标题行
    if not description:
        for line in text_body.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("```"):
                description = s
                break

    return Skill(
        name=name,
        description=description,
        emoji=emoji,
        prompt=text,   # 全文作为专家指令
        file_path=md_path,
        category=category,
        toolbar=toolbar,
        body=text_body,
    )


def scan_skills(skills_dir, use_cache=True):
    """扫描技能目录，返回 Skill 列表。

    同时支持两种形态：
    - 旧式 .py 技能（SKILL_NAME/DESCRIPTION/EMOJI/PROMPT 注释头）
    - 新式 技能名/SKILL.md 动态技能

    Args:
        skills_dir: 技能目录的绝对路径。
        use_cache: True 时按目录 mtime 签名缓存解析结果，内容/增删不变则跳过重扫
                   （建议4：每次发消息都全量扫盘，技能数增长后才显慢，缓存为顺手优化）。

    Returns:
        list[Skill]
    """
    if use_cache:
        with _scan_lock:
            cached = _scan_cache.get(skills_dir)
        if cached is not None and cached[0] is not None:
            sig = _dir_signature(skills_dir)
            if cached[0] == sig:
                log.debug("技能目录命中缓存跳过扫描: %s", skills_dir)
                return list(cached[1])  # 返回新列表，Skill 对象本身只读不复制

    if not os.path.isdir(skills_dir):
        log.warning("技能目录不存在: %s", skills_dir)
        return []

    skills = []
    seen = set()
    for fname in sorted(os.listdir(skills_dir)):
        full = os.path.join(skills_dir, fname)
        # 旧式 .py 技能
        if fname.endswith(".py") and not fname.startswith("__") and os.path.isfile(full):
            sk = _parse_skill_file(full)
            if sk and sk.name not in seen:
                seen.add(sk.name)
                skills.append(sk)
        # 新式 技能名/SKILL.md
        elif os.path.isdir(full):
            md = os.path.join(full, "SKILL.md")
            if os.path.isfile(md):
                try:
                    sk = _parse_skill_md(md, fname)
                except Exception as e:
                    log.warning("解析技能失败 %s: %s", md, e)
                    sk = None
                if sk and sk.name not in seen:
                    seen.add(sk.name)
                    skills.append(sk)

    if use_cache:
        with _scan_lock:
            _scan_cache[skills_dir] = (_dir_signature(skills_dir), skills)
    log.info("从 %s 扫描到 %d 个技能", skills_dir, len(skills))
    return skills


def load_skill_prompt(name, skills_dir):
    """返回指定技能的 prompt 文本。

    Args:
        name: 技能名称（自动归一化：剥 emoji/符号、空白转连字符、大小写不敏感）。
        skills_dir: 技能目录的绝对路径。

    Returns:
        str 或 None
    """
    target = normalize_skill_name(name)
    skills = scan_skills(skills_dir)
    for sk in skills:
        if normalize_skill_name(sk.name) == target:
            return sk.prompt
    return None


def get_available_skills(skills_dir):
    """返回所有可用技能的名字和描述列表。

    Args:
        skills_dir: 技能目录的绝对路径。

    Returns:
        list[dict] — 每个元素包含 name、description、emoji。
    """
    skills = scan_skills(skills_dir)
    return [
        {
            "name": sk.name,
            "description": sk.description,
            "emoji": sk.emoji,
            "category": sk.category,
            "toolbar": getattr(sk, "toolbar", False),
        }
        for sk in skills
    ]
