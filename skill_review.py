# -*- coding: utf-8 -*-
"""技能审核队列（v4.84 热修15·B，借鉴 Prime-Agent 自进化「创造模式」）。

设计目标：
- 模型调用 create_skill 时**不直接生效**——先落「待审核」目录 skills_pending/，
  该目录不被 skill_loader 自动加载（skill_loader 只扫 get_skill_scan_dirs() 返回的目录），
  因此对正在运行的 APP 零副作用。
- 用户从「技能审核」入口查看、点「通过」才把技能移到正式 skills/ 并热重载；点「拒绝」即删除。
- 这是「软自进化」：知识/技能层可自我生长，但受人工闸门约束，杜绝冻结 EXE 做硬自改的安全反模式。

纯标准库，无 Qt 依赖，可离线单测。
"""

import os
import shutil
import datetime


# ---------- 路径 ----------
def get_pending_dir(cfg):
    """待审核目录：默认 ~/Documents/小臭玩AI/skills_pending。"""
    if isinstance(cfg, dict) and cfg.get("skills_pending_dir"):
        return cfg["skills_pending_dir"]
    return os.path.join(
        os.path.expanduser("~"), "Documents", "小臭玩AI", "skills_pending")


def get_active_dir(cfg):
    """正式技能目录：用户目录 ~/Documents/小臭玩AI/skills（与 skill_loader 第一来源一致）。

    cfg 可带 "skills_dir" 覆盖（离线单测用），否则取 config.get_skill_scan_dirs()[0]。
    """
    if isinstance(cfg, dict) and cfg.get("skills_dir"):
        return cfg["skills_dir"]
    try:
        import config
        dirs = config.get_skill_scan_dirs()
        if dirs:
            return dirs[0]
    except Exception:
        pass
    return os.path.join(
        os.path.expanduser("~"), "Documents", "小臭玩AI", "skills")


# ---------- 提交（模型侧调用）----------
def submit_skill(cfg, name, description, prompt, emoji="⚡", category="自动生成"):
    """把模型提炼出的技能写入待审核目录（不进正式 skills/）。

    返回人类可读结果字符串。
    """
    name = (name or "").strip()
    description = (description or "").strip()
    prompt = (prompt or "").strip()
    emoji = (emoji or "⚡").strip() or "⚡"
    category = (category or "自动生成").strip() or "自动生成"

    if not name or not prompt:
        return "技能提交失败：缺少 name 或 prompt。"

    # 目录名清洗：去掉路径分隔符，避免越界
    safe_name = name.replace("\\", "_").replace("/", "_").strip()
    if not safe_name:
        return "技能提交失败：name 非法。"

    pending_dir = get_pending_dir(cfg)
    skill_dir = os.path.join(pending_dir, safe_name)
    try:
        os.makedirs(skill_dir, exist_ok=True)
    except Exception as e:
        return f"技能提交失败：创建目录错误 {e}"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"""# {name}
- 分类：{category}
- 创建时间：{ts}（自动生成·待审核）
- 图标：{emoji}

## 描述
{description}

## 执行流程
{prompt}
"""
    try:
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(content)
        return (f"技能「{name}」已提交到审核队列（待审核目录 {skill_dir}）。"
                f"需你在「技能审核」中点击通过，才会正式生效。")
    except Exception as e:
        return f"技能提交失败：{e}"


# ---------- 列举 ----------
def list_pending(cfg):
    """返回待审核技能列表，元素为 dict：
    {name, category, emoji, created, description, path, size}。
    """
    pending_dir = get_pending_dir(cfg)
    out = []
    if not os.path.isdir(pending_dir):
        return out
    for entry in sorted(os.listdir(pending_dir)):
        skill_dir = os.path.join(pending_dir, entry)
        md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(md):
            continue
        meta = _parse_skill_md(md)
        # 目录名(已清洗)作为唯一 id，approve/reject 据此定位；
        # 原始展示名存 display_name（header 可能含路径分隔符，不能当路径用）。
        meta["name"] = entry
        meta["display_name"] = meta.get("name") or entry
        meta["path"] = skill_dir
        try:
            meta["size"] = os.path.getsize(md)
        except Exception:
            meta["size"] = 0
        out.append(meta)
    return out


def count_pending(cfg):
    """待审核数量。"""
    try:
        return len(list_pending(cfg))
    except Exception:
        return 0


# ---------- 审核动作（用户侧调用）----------
def approve_skill(cfg, name):
    """通过审核：把待审核技能移到正式 skills/ 并重载由 UI 负责。

    返回人类可读结果字符串。
    """
    pending_dir = get_pending_dir(cfg)
    src = os.path.join(pending_dir, name)
    if not os.path.isdir(src):
        return f"通过失败：找不到待审核技能「{name}」。"

    active_dir = get_active_dir(cfg)
    dst = os.path.join(active_dir, name)
    try:
        os.makedirs(active_dir, exist_ok=True)
        if os.path.exists(dst):
            # 同名则替换（用户明确通过新版本）
            shutil.rmtree(dst)
        shutil.move(src, dst)
        return f"已通过「{name}」并移入正式技能目录：{dst}。"
    except Exception as e:
        return f"通过失败：{e}"


def reject_skill(cfg, name):
    """拒绝审核：直接删除待审核技能。"""
    pending_dir = get_pending_dir(cfg)
    src = os.path.join(pending_dir, name)
    if not os.path.isdir(src):
        return f"拒绝失败：找不到待审核技能「{name}」。"
    try:
        shutil.rmtree(src)
        return f"已拒绝并删除待审核技能「{name}」。"
    except Exception as e:
        return f"拒绝失败：{e}"


# ---------- 内部 ----------
def _parse_skill_md(md_path):
    """从 SKILL.md 提取 name/category/emoji/created/description 摘要。"""
    meta = {
        "name": "",
        "category": "自动生成",
        "emoji": "⚡",
        "created": "",
        "description": "",
    }
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return meta

    desc_lines = []
    in_desc = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("# "):
            meta["name"] = s[2:].strip()
        elif s.startswith("- 分类："):
            meta["category"] = s[len("- 分类："):].strip()
        elif s.startswith("- 创建时间："):
            meta["created"] = s[len("- 创建时间："):].strip()
        elif s.startswith("- 图标："):
            meta["emoji"] = s[len("- 图标："):].strip() or "⚡"
        elif s == "## 描述":
            in_desc = True
        elif s.startswith("## "):
            in_desc = False
        elif in_desc and s:
            desc_lines.append(s)
    meta["description"] = " ".join(desc_lines)[:200]
    return meta


if __name__ == "__main__":
    # 简单自测
    import tempfile
    tmp = tempfile.mkdtemp()
    fake_cfg = {"skills_pending_dir": os.path.join(tmp, "pending"),
                "skills_dir": os.path.join(tmp, "skills")}
    print(submit_skill(fake_cfg, "测试技能", "用于自测", "第一步\n第二步", "🧪", "自测"))
    print("count:", count_pending(fake_cfg))
    for s in list_pending(fake_cfg):
        print(s["name"], s["category"], s["emoji"], s["created"], s["description"][:20])
    print(approve_skill(fake_cfg, "测试技能"))
    print("count after approve:", count_pending(fake_cfg))
