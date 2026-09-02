# -*- coding: utf-8 -*-
"""调试导出诊断包（v4.69）

把运行环境 / 配置摘要(脱敏) / 技能清单 / 长期记忆统计 / 运行日志尾部 / 最近报错
整合进一个自包含文本文件，用户一键导出后直接发给开发者，免截图拼凑。

入口：ui.py 设置页「导出诊断包」按钮、托盘菜单「📦 诊断包」。
"""

import os
import sys
import json
import platform
from datetime import datetime

from PySide6.QtWidgets import QFileDialog, QMessageBox

# 这些配置键的值需要脱敏（只报"已设置(N位)"，不泄露真实密钥）
_SECRET_KEYWORDS = ("key", "pass", "token", "secret", "pwd", "password", "authorization")


def _mask(value):
    """把敏感值脱敏成『已设置(N位)』；空值报『未设置』。"""
    if value is None:
        return "未设置"
    s = str(value).strip()
    if s == "":
        return "未设置"
    return f"已设置({len(s)}位)"


def _is_secret(key):
    k = key.lower()
    return any(w in k for w in _SECRET_KEYWORDS)


def _collect_env():
    lines = []
    lines.append(f"运行环境      : {platform.system()} {platform.release()} "
                 f"({platform.version()})")
    lines.append(f"Python        : {platform.python_version()}")
    lines.append(f"可执行        : {sys.executable}")
    lines.append(f"是否打包      : {'是(frozen)' if getattr(sys, 'frozen', False) else '否(源码)'}")
    try:
        import config as _cfg
        lines.append(f"APP_DIR       : {_cfg.APP_DIR}")
        lines.append(f"版本          : {getattr(_cfg, 'APP_VERSION', '未知')}")
    except Exception as e:
        lines.append(f"APP_DIR       : 读取失败 {e}")
    return lines


def _collect_config():
    lines = []
    try:
        import config as _cfg
        cfg = _cfg.load_config()
    except Exception as e:
        return [f"（配置读取失败：{e}）"]
    # 决定技能启用规则的辅助信息
    enabled = cfg.get("enabled_skills", None)
    lines.append(f"enabled_skills: {'空=全部启用' if not enabled else f'{len(enabled)} 个白名单'}")
    for key, val in cfg.items():
        if key in ("system_prompt", "user_prompt", "agent_sys_append"):
            # 过长提示词只报长度
            s = str(val)
            lines.append(f"{key:16}: （已省略，{len(s)} 字符）")
            continue
        if _is_secret(key):
            lines.append(f"{key:16}: {_mask(val)}")
        else:
            # 普通值：长度截断，避免把大段内容塞进诊断包
            s = str(val)
            if len(s) > 200:
                s = s[:200] + " …(截断)"
            lines.append(f"{key:16}: {s}")
    return lines


def _collect_skills():
    lines = []
    try:
        from skill_loader import get_available_skills
        import config as _cfg
        skills = get_available_skills()
        cfg = _cfg.load_config()
        enabled = cfg.get("enabled_skills", None)
        default_all = not enabled
        lines.append(f"共 {len(skills)} 个技能：")
        for i, sk in enumerate(skills, 1):
            name = sk.get("name", "?")
            cat = sk.get("category") or "未分类"
            desc = (sk.get("description") or "").strip().replace("\n", " ")
            if len(desc) > 60:
                desc = desc[:60] + "…"
            is_on = default_all or (name in enabled)
            mark = "✅" if is_on else "⚪"
            lines.append(f"  {i:2}. [{mark}] {name}（{cat}）— {desc}")
    except Exception as e:
        lines.append(f"（技能清单读取失败：{e}）")
    return lines


def _collect_memory():
    lines = []
    try:
        from memory_store import memory_stats
        n, sz = memory_stats()
        lines.append(f"长期记忆: {n} 条 · {sz} 字节")
    except Exception as e:
        lines.append(f"（记忆统计读取失败：{e}）")
    return lines


def _collect_log(errors_only=False, tail=400):
    """读取 debug.log：返回 (recent_errors, tail_text)。"""
    lines = []
    try:
        import config as _cfg
        log_path = os.path.join(_cfg.APP_DIR, "debug.log")
        if not os.path.exists(log_path):
            return ["（debug.log 不存在）"], ""
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        # 最近报错段：抓取含 ERROR / Traceback / Exception / Error: /   File " 的行
        err_patterns = (" ERROR ", "Traceback", "Exception", "Error:", '  File "', "Critical")
        err_lines = [ln.rstrip("\n") for ln in all_lines
                     if any(p in ln for p in err_patterns)]
        # 去重相邻重复，限制条数
        seen = set()
        dedup = []
        for ln in err_lines:
            if ln not in seen:
                seen.add(ln)
                dedup.append(ln)
        err_tail = dedup[-80:]
        if errors_only:
            return err_tail, ""
        return err_tail, "".join(all_lines[-tail:])
    except Exception as e:
        return [f"（日志读取失败：{e}）"], ""


def build_report():
    """生成完整诊断报告文本。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = []
    parts.append("=" * 60)
    parts.append("        小臭玩AI · 调试诊断包")
    parts.append("=" * 60)
    try:
        import config as _cfg
        parts.append(f"生成时间 : {now}")
        parts.append(f"版本     : {getattr(_cfg, 'APP_VERSION', '未知')}")
    except Exception:
        parts.append(f"生成时间 : {now}")

    parts.append("")
    parts.append("【一、运行环境】")
    parts.extend(_collect_env())

    parts.append("")
    parts.append("【二、配置摘要（密钥已脱敏）】")
    parts.extend(_collect_config())

    parts.append("")
    parts.append("【三、技能清单】")
    parts.extend(_collect_skills())

    parts.append("")
    parts.append("【四、长期记忆】")
    parts.extend(_collect_memory())

    parts.append("")
    parts.append("【五、最近报错（debug.log 中 ERROR / Traceback 段）】")
    errs, _ = _collect_log(errors_only=True)
    if errs and errs != ["（debug.log 不存在）"]:
        parts.extend(errs)
    else:
        parts.append("（无报错记录，或日志不可用）")

    parts.append("")
    parts.append("【六、运行日志尾部（最近 400 行）】")
    _, tail = _collect_log(errors_only=False)
    if tail:
        parts.append(tail.rstrip("\n"))
    else:
        parts.append("（无日志内容）")

    parts.append("")
    parts.append("=" * 60)
    parts.append("诊断包结束 · 把本文件发给开发者即可，无需截图")
    parts.append("=" * 60)
    return "\n".join(parts)


def export_diagnostic_package(parent=None):
    """弹出保存对话框，把诊断报告写入用户选定文件。"""
    try:
        report = build_report()
    except Exception as e:
        QMessageBox.critical(parent, "诊断包导出失败",
                             f"生成报告时出错：{e}")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"小臭诊断包_{stamp}.txt"
    # 默认落在用户文档/桌面，方便查找
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    default_dir = docs if os.path.isdir(docs) else os.path.expanduser("~")
    path, _ = QFileDialog.getSaveFileName(
        parent, "导出诊断包", os.path.join(default_dir, default_name),
        "文本文件 (*.txt);;所有文件 (*.*)")
    if not path:
        return  # 用户取消
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        QMessageBox.information(parent, "诊断包已导出",
                                f"已保存到：\n{path}\n\n把此文件发给开发者即可。")
    except Exception as e:
        QMessageBox.critical(parent, "诊断包导出失败", f"写入文件失败：{e}")
