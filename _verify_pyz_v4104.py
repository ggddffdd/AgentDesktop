# -*- coding: utf-8 -*-
"""v4.104 硬核实入包验证：直接从 dist exe 内嵌 PYZ 里 walk 字节码常量，
确认「重复气泡修复」与「Agent 步数/提示整改」确实打进了最终 exe。
（不查源码，只查产物，避免"源码改了但没重打包"的假阳性）"""
import os, sys, types, marshal

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "dist", "小臭玩AI", "小臭玩AI.exe")
if not os.path.exists(EXE):
    print("[FAIL] exe 不存在:", EXE)
    sys.exit(1)
print("exe:", EXE, os.path.getsize(EXE), "bytes")

from PyInstaller.archive.readers import ZlibArchiveReader

with open(EXE, "rb") as f:
    blob = f.read()
off = blob.find(b"PYZ\x00")
print("PYZ offset:", off)
assert off > 0, "未找到内嵌 PYZ"

# PyInstaller 6.19+：PYZ 嵌在 EXE 里，用 start_offset 读
try:
    reader = ZlibArchiveReader(EXE, start_offset=off)
except TypeError:
    reader = ZlibArchiveReader(EXE)
print("PYZ 条目数:", len(reader.toc))


def walk_names(co, sink_names, sink_consts):
    sink_names.update(co.co_names)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk_names(c, sink_names, sink_consts)
        else:
            # 收集全部非 code 常量（str / int / float / tuple 等），
            # 只收 str 会漏掉 MAX_AGENT_STEPS 这类数字常量
            try:
                hash(c)
                sink_consts.add(c)
            except TypeError:
                pass


def module_mods(modname):
    """取出某模块在 PYZ 里的字节码，返回 (co_names, co_consts)"""
    names, consts = set(), set()
    found = 0
    for key in reader.toc:
        if key != modname and not key.startswith(modname + "."):
            continue
        try:
            data = reader.extract(key)
        except Exception:
            continue
        # PyInstaller 新版 extract() 已直接返回 code object；旧版返回 marshal bytes
        co = data if isinstance(data, types.CodeType) else marshal.loads(data)
        found += 1
        walk_names(co, names, consts)
    return found, names, consts


CHECKS = []


def chk(label, cond, extra=""):
    CHECKS.append((label, bool(cond), extra))
    print(("[OK ] " if cond else "[FAIL] ") + label + ((" | " + extra) if extra else ""))


# ---------- 1) ui 模块：重复气泡修复 ----------
n, ui_names, ui_consts = module_mods("ui")
print(f"\n== ui 模块（PYZ 内 {n} 项）==")
chk("ui: 增量路径调用 end_stream() 清残留流式气泡", "end_stream" in ui_names)
chk("ui: 全量重建走 render_all()", "render_all" in ui_names)
# ---------- 1b) chat_web 模块：WebEngine 页面骨架 ----------
n1b, cw_names, cw_consts = module_mods("chat_web")
print(f"== chat_web 模块（PYZ 内 {n1b} 项）==")
chk("chat_web: 流式气泡标识 stream-bubble 已入包",
    any("stream-bubble" == c for c in cw_consts)
    or any("stream-bubble" in c for c in cw_consts if isinstance(c, str)))
chk("ui: WebEngine 聊天视图 chat_web 已入包", "chat_web" in ui_names or "ChatWebView" in ui_names)
chk("ui: chat_view 已切换为 ChatWebView", "ChatWebView" in ui_names)

# ---------- 2) config 模块：Agent 行为整改 ----------
n2, cfg_names, cfg_consts = module_mods("config")
print(f"\n== config 模块（PYZ 内 {n2} 项）==")
style_txt = [c for c in cfg_consts
             if isinstance(c, str) and ("执行风格铁律" in c or "最少步骤" in c)]
chk("config: AGENT_SYS_APPEND 含执行风格/最少步骤铁律提示",
    bool(style_txt), repr([c[:36] for c in style_txt[:2]]))
# v4.104.1：步数从 12 放宽回 20（总 24 → 60），且改为 config.json 可覆盖
chk("config: MAX_AGENT_STEPS=20 已放宽（v4.104 曾收到 12）", 20 in cfg_consts)
chk("config: AGENT_RESUME_ROUNDS 常量存在", "AGENT_RESUME_ROUNDS" in cfg_names)
chk("config: AGENT_RESUME_STEPS 常量存在", "AGENT_RESUME_STEPS" in cfg_names)
chk("config: MAX_AGENT_STEPS 常量存在", "MAX_AGENT_STEPS" in cfg_names)
chk("config: 新增 get_agent_step_budget（步数可配置）",
    "get_agent_step_budget" in cfg_names)
chk("config: 步数配置键 agent_max_steps 存在", "agent_max_steps" in cfg_consts)
# v4.104.1：token 预算 200K → 400K，付费档 150K → 0（跟随总预算，消除 min() 提前熔断）
# 09-02：总预算再上调到 500K（config.py:483），断言随源码走
chk("config: AGENT_TOKEN_BUDGET=500000", 500000 in cfg_consts)

print("\n===== 汇总 =====")
bad = [l for l, okc, _ in CHECKS if not okc]
for l, okc, _ in CHECKS:
    print(("  PASS  " if okc else "  FAIL  ") + l)
print("RESULT:", "ALL_PASS" if not bad else f"HAS_FAIL ({len(bad)})")
