# -*- coding: utf-8 -*-
"""v4.129 产物分层落盘：产物/YYYY-MM-DD/<项目>/<类型>/

背景：此前所有产物一律平铺在 PRODUCTS_DIR 下（图片/视频/截图/脚本 四个类型目录
+ 一堆散落的 md/html/png），同一个项目的东西按时间混在一起，跨项目更难找。

本模块只负责「算目录」，不做任何移动/删除——磁盘整理由 ui._on_archive_products
在用户确认后执行。

设计要点：
- **向后兼容**：`products_layout="flat"` 时返回旧路径（PRODUCTS_DIR/<类型>），
  行为与 v4.128 及之前字节级一致；默认 "dated" 走分层。
- **项目名三级兜底**：调用方显式指定 > 当前会话标题（非泛化）> "未分类"。
  上下文由 UI 通过 set_context() 注入（tools.py 是纯函数，拿不到窗口对象）。
- **不抛异常**：任何环节出错都退回旧路径，绝不能因为算目录把生成任务搞挂。
- **零第三方依赖**：只用 os/re/datetime。
"""

import os
import re
from datetime import datetime

# 目录名非法字符（Windows）：\ / : * ? " < > | 及控制字符
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_WS = re.compile(r"\s+")

# 泛化会话标题——这类当不了项目名，直接用会让目录里堆满「新会话」
GENERIC_TITLES = {
    "", "新会话", "新对话", "未命名", "无标题", "对话", "聊天", "测试",
    "untitled", "new chat", "new session", "test",
}

# 类型 → 子目录名（沿用既有目录名，避免旧产物与新产物割裂）
KIND_DIRS = {
    "image": "图片", "images": "图片", "img": "图片", "png": "图片", "jpg": "图片",
    "cover": "封面图", "封面": "封面图",
    "video": "视频", "clip": "视频", "clips": "视频", "mp4": "视频",
    "screenshot": "截图", "shot": "截图", "截图": "截图",
    "code": "脚本", "script": "脚本", "py": "脚本", "脚本": "脚本",
    "md": "文档", "doc": "文档", "docx": "文档", "txt": "文档",
    "markdown": "文档", "report": "文档", "文档": "文档", "file": "文档",
    "html": "网页", "web": "网页",
    "audio": "音频", "mp3": "音频", "wav": "音频", "tts": "音频",
    "pdf": "文档", "xlsx": "表格", "csv": "表格", "表格": "表格",
}

FALLBACK_KIND_DIR = "其他"
UNSORTED = "未分类"

# 已知类型目录名集合（KIND_DIRS 的值）——归档扫描要判断顶层「图片」这类目录
KNOWN_KIND_DIRS = set(KIND_DIRS.values()) | {FALLBACK_KIND_DIR}
# 中文目录名 → 自身（让 kind_dir("图片") 也能返回 "图片" 而不是 "其他"）
_KIND_BY_CN = {v: v for v in KNOWN_KIND_DIRS}
KNOWN_KIND_DIRS_LOWER = {v.lower() for v in KNOWN_KIND_DIRS}

# 运行期上下文（UI 注入，非持久）
_CTX = {"project": "", "session": ""}


def today(date=None):
    """YYYY-MM-DD；date 可传 datetime 或 'YYYY-MM-DD' 字符串。"""
    if isinstance(date, datetime):
        return date.strftime("%Y-%m-%d")
    if isinstance(date, str) and re.match(r"^\d{4}-\d{2}-\d{2}", date.strip()):
        return date.strip()[:10]
    return datetime.now().strftime("%Y-%m-%d")


def slugify(name, maxlen=40):
    """把任意标题压成安全的目录名。空/纯符号 → ''。"""
    s = str(name or "").strip()
    if not s:
        return ""
    # 去掉常见前缀装饰（emoji 保留没问题，但前后空白/引号要清）
    s = s.strip("《》<>()[]「」『』\"'“”‘’ ")
    s = _INVALID_CHARS.sub("", s)
    s = _WS.sub(" ", s).strip(" .-_")
    if not s:
        return ""
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .-_") or s[:maxlen]
    return s


def is_generic_title(title):
    """会话标题是否太泛，不能当项目名。"""
    s = str(title or "").strip().lower()
    if not s:
        return True
    if s in GENERIC_TITLES:
        return True
    # 「新会话 3」这类数字后缀也算泛化
    return bool(re.match(r"^(新会话|新对话|未命名|untitled|new chat)\s*\d*$", s))


def set_context(project=None, session_title=None):
    """UI 在会话切换 / 军团启动时注入当前项目上下文。"""
    try:
        if project is not None:
            _CTX["project"] = str(project or "")
        if session_title is not None:
            _CTX["session"] = str(session_title or "")
    except Exception:
        pass


def get_context():
    return dict(_CTX)


def current_project(explicit=None):
    """项目名三级兜底：显式 > 上下文项目 > 会话标题 > 未分类。

    返回已 slugify 的安全目录名；拿不到有效名时返回 UNSORTED（"未分类"）。
    """
    # (候选值, 是否要求「非泛化标题」)——只有会话标题这一档要求非泛化，
    # 显式传入与军团项目名是用户/系统的明确意图，哪怕叫「测试」也照用。
    cands = ((explicit, False), (_CTX.get("project"), False), (_CTX.get("session"), True))
    for cand, need_specific in cands:
        s = slugify(cand)
        if not s:
            continue
        if need_specific and is_generic_title(cand):
            continue
        return s
    return UNSORTED


def kind_dir(kind=None):
    """类型 → 子目录名；未知类型（含 None）→ '其他'。"""
    k = str(kind or "").strip().lower()
    if not k:
        return FALLBACK_KIND_DIR
    if k in KIND_DIRS:
        return KIND_DIRS[k]
    # 中文目录名本身（"图片"/"视频"）直接认——归档扫描时顶层已是中文目录名
    if k in KNOWN_KIND_DIRS_LOWER:
        return KIND_DIRS.get(k) or _KIND_BY_CN.get(k) or FALLBACK_KIND_DIR
    # 按扩展名再试一次（".PNG" → "png"）
    ext = k.lstrip(".").lower()
    if ext in KIND_DIRS:
        return KIND_DIRS[ext]
    return FALLBACK_KIND_DIR


def is_kind_dir(name):
    """顶层目录名是不是类型目录（图片/视频/截图/…）——归档扫描用。"""
    n = str(name or "").strip()
    return n in KNOWN_KIND_DIRS


def kind_dir_cn(name):
    """已知类型目录名原样返回（大小写/全半角已归一），否则返回 ''。"""
    n = str(name or "").strip()
    return n if n in KNOWN_KIND_DIRS else ""


def product_dir(base, kind=None, project=None, date=None, layout="dated", make=True):
    """算出产物落盘目录（必要时创建）。

    - layout="flat"  → `<base>/<类型>`（旧行为，向后兼容）
    - layout="dated" → `<base>/YYYY-MM-DD/<项目>/<类型>`

    任何异常都退回 `<base>/<类型>`，绝不抛。
    """
    base = str(base or "").strip() or "."
    kd = kind_dir(kind)
    try:
        if str(layout or "dated").lower() != "dated":
            p = os.path.join(base, kd)
        else:
            pj = slugify(project) or current_project()
            p = os.path.join(base, today(date), pj, kd)
        if make:
            os.makedirs(p, exist_ok=True)
        return p
    except Exception:
        try:
            p = os.path.join(base, kd)
            if make:
                os.makedirs(p, exist_ok=True)
            return p
        except Exception:
            return base


def rel_to_products(abs_path, base=None):
    """把绝对路径转成『相对产物目录』的展示路径（用于交付物卡片副标题）。

    例：`.../产物/2026-09-09/小红书带货/图片/a.png` → `2026-09-09/小红书带货/图片/a.png`
    不在产物目录下的返回 ''。
    """
    try:
        b = os.path.abspath(str(base or ""))
        a = os.path.abspath(str(abs_path or ""))
        if not b or not a:
            return ""
        rel = os.path.relpath(a, b)
        if rel.startswith(".."):
            return ""
        return rel.replace("\\", "/")
    except Exception:
        return ""


def is_dated_layout_enabled(cfg=None):
    """配置开关：products_layout，默认 'dated'（分层）。'flat' = 旧平铺。"""
    try:
        v = str(((cfg or {}).get("products_layout") or "dated")).strip().lower()
    except Exception:
        v = "dated"
    return v != "flat"


def parse_existing_groups(base):
    """扫描产物目录顶层，返回 [(日期, [子项名...]), ...]，按日期倒序。

    供「归档旧产物」预览使用；只做只读扫描，不移动任何东西。
    """
    out = []
    try:
        for n in sorted(os.listdir(base)):
            p = os.path.join(base, n)
            if os.path.isdir(p) and re.match(r"^\d{4}-\d{2}-\d{2}$", n):
                try:
                    kids = sorted(os.listdir(p))
                except Exception:
                    kids = []
                out.append((n, kids))
    except Exception:
        return []
    out.sort(key=lambda x: x[0], reverse=True)
    return out
