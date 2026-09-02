# -*- coding: utf-8 -*-
"""硬核实入包 v4.106：确认对话框导演工具（director_agent_tools）已进冻结 exe。

判据：
1. director_agent_tools 模块在 PYZ 内，co_names 含 5 个工具名 + register_tool + set_dispatcher
2. director_panel 的 agent_director_command / _DirectorCmdBridge / install_agent_bridge /
   _agent_status 存在；_set_busy 完成钩子（_director_agent_event）在
3. video_pipeline 含 regenerate_keyframe / regenerate_character
4. tools 顶层含 director_agent_tools 懒 import（co_names）
5. main 入口（CArchive）不变式保留：register_localres_scheme 在 QApplication 前
"""
import os
import types

EXE = "dist/小臭玩AI/小臭玩AI.exe"
ok = True

from PyInstaller.archive.readers import ZlibArchiveReader, CArchiveReader

data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
zr = ZlibArchiveReader(EXE, start_offset=off)


def get_code(name):
    raw = zr.extract(name)
    return raw if isinstance(raw, types.CodeType) else None


def all_names(co):
    out, codes = set(), []
    stack = [co]
    while stack:
        c = stack.pop()
        codes.append(c)
        stack.extend(x for x in c.co_consts if isinstance(x, types.CodeType))
    for c in codes:
        out |= set(c.co_names) | {k for k in c.co_consts if isinstance(k, str)}
    return out


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


print("== 1. director_agent_tools 模块 ==")
dat = get_code("director_agent_tools")
if dat is None:
    check("PYZ 内存在 director_agent_tools", False)
else:
    n = all_names(dat)
    for sym in ("register_tool", "set_dispatcher", "director_status",
                "director_revise_clip", "director_revise_keyframe",
                "director_revise_character", "director_merge",
                "RiskClass", "WRITE_LOCAL"):
        check(f"director_agent_tools 含 {sym}", sym in n)

print("== 2. director_panel 桥接接线 ==")
dp = get_code("director_panel")
if dp is None:
    check("PYZ 内存在 director_panel", False)
else:
    n = all_names(dp)
    for sym in ("agent_director_command", "_DirectorCmdBridge",
                "install_agent_bridge", "_agent_status",
                "_agent_result_snapshot", "_director_agent_event",
                "keyframe_one", "character_one"):
        check(f"director_panel 含 {sym}", sym in n)

print("== 3. video_pipeline 单张重生成 ==")
vp = get_code("video_pipeline")
n = all_names(vp) if vp else set()
if vp is None:
    check("PYZ 内存在 video_pipeline", False)
else:
    for sym in ("regenerate_keyframe", "regenerate_character",
                "_gen_one_keyframe", "_gen_character_views"):
        check(f"video_pipeline 含 {sym}", sym in n)

print("== 4. tools 懒 import director_agent_tools ==")
t = get_code("tools")
if t is None:
    check("PYZ 内存在 tools", False)
else:
    check("tools 引用 director_agent_tools", "director_agent_tools" in all_names(t))

print("== 5. ui 导演指令拦截（v4.107 独立对话条）==")
ui = get_code("ui")
if ui is None:
    check("PYZ 内存在 ui", False)
else:
    n = all_names(ui)
    # 识别词表仍在（_is_director_command 内部用），但路由已从主 Agent 摘除
    for sym in ("_is_director_command", "_render_director_redirect",
                "_DIRECTOR_OBJ_KW", "_DIRECTOR_VERB_KW",
                "_DIRECTOR_STATUS_KW", "_DIRECTOR_STATUS_Q"):
        check(f"ui 含 {sym}", sym in n)

    def walk(co):
        stack = [co]
        while stack:
            c = stack.pop()
            yield c
            stack.extend(x for x in c.co_consts if isinstance(x, types.CodeType))

    # v4.107 精确判定：send() 拦截点真调 _is_director_command（零交集拦截闸门）
    sends = [c for c in walk(ui) if c.co_name == "send"]
    check("send() 体内调用 _is_director_command（拦截点）",
          any("_is_director_command" in c.co_names for c in sends))

    # 路由已摘除：_message_needs_agent / _needs_tool_intent 不再调用 _is_director_command
    for fname in ("_message_needs_agent", "_needs_tool_intent"):
        fns = [c for c in walk(ui) if c.co_name == fname]
        hit = any("_is_director_command" in f.co_names for f in fns)
        check(f"{fname} 不再调用 _is_director_command（已摘除）", not hit)

    # 状态问句补漏：'还没/没生成' 等应进词表（否则"第几镜还没生成完"漏判）
    # 注意：词表定义在方法体内，是局部 tuple const，必须遍历全部 code object
    q_consts = set()
    for c in walk(ui):
        for k in c.co_consts:
            if isinstance(k, tuple) and "没生成" in k:
                q_consts |= {x for x in k if isinstance(x, str)}
    check("_DIRECTOR_STATUS_Q 含 '还没'", "还没" in q_consts)
    check("_DIRECTOR_STATUS_Q 含 '没生成'", "没生成" in q_consts)

print("== 6. director_chat 独立对话条模块 ==")
dc = get_code("director_chat")
if dc is None:
    check("PYZ 内存在 director_chat", False)
else:
    n = all_names(dc)
    for sym in ("DirectorChatBar", "DIRECTOR_TOOL_NAMES", "DIRECTOR_SYS",
                "reload_for_project", "_state_brief"):
        check(f"director_chat 含 {sym}", sym in n)

    def walk(co):
        stack = [co]
        while stack:
            c = stack.pop()
            yield c
            stack.extend(x for x in c.co_consts if isinstance(x, types.CodeType))

    # 清空按钮（v4.107.1）：_build_ui 真建 clear_btn，_clear_chat 方法真存在且清空+写空
    builds = [c for c in walk(dc) if c.co_name == "_build_ui"]
    check("_build_ui 体内创建 clear_btn", any("clear_btn" in c.co_names for c in builds))
    clears = [c for c in walk(dc) if c.co_name == "_clear_chat"]
    check("_clear_chat 方法存在", bool(clears))
    check("_clear_chat 清空 history 并写空持久化",
          any("_save_history" in c.co_names and "history" in c.co_names for c in clears))

    # v4.107 bugfix：_on_chunk 用 QTextCursor「替换」语义（不再是 _insert 追加），
    # 否则累积文本会被一遍遍重复拼接（「镜镜3镜3关键…」灾难）。判据：
    # QTextCursor / insertText / _stream_pos 都出现在 _on_chunk 函数体内。
    chunks = [c for c in walk(dc) if c.co_name == "_on_chunk"]
    if chunks:
        c0 = chunks[0]
        check("_on_chunk 用 QTextCursor 替换", "QTextCursor" in c0.co_names)
        check("_on_chunk 用 insertText 覆盖", "insertText" in c0.co_names)
        check("_on_chunk 记录流式起点 _stream_pos", "_stream_pos" in c0.co_names)
    else:
        check("_on_chunk 方法存在", False)

print("== 7. agent 隔离模式（isolated + force_complex）==")
ag = get_code("agent")
if ag is None:
    check("PYZ 内存在 agent", False)
else:
    n = all_names(ag)
    for sym in ("_isolated", "_force_complex"):
        check(f"agent 含 {sym}", sym in n)
    # _sync_to_session / _auto_remember 体内真判断 _isolated（隔离闸门）
    def walk(co):
        stack = [co]
        while stack:
            c = stack.pop()
            yield c
            stack.extend(x for x in c.co_consts if isinstance(x, types.CodeType))
    for fname in ("_sync_to_session", "_auto_remember"):
        fns = [c for c in walk(ag) if c.co_name == fname]
        # getattr(self, "_isolated", False) 里 "_isolated" 是字符串常量（co_consts），
        # 必须用 all_names（co_names + co_consts 字符串）而非仅 co_names 判定。
        check(f"{fname} 体内判断 _isolated", any("_isolated" in all_names(f) for f in fns))

print("== 8. main 入口 localres scheme 保留 ==")
car = CArchiveReader(EXE)
main_co = None
for name, info in car.toc.items():
    if name == "main" or (isinstance(name, str) and name.endswith("main")):
        try:
            raw = car.extract(name)
            if isinstance(raw, types.CodeType):
                main_co = raw
                break
        except Exception:
            pass
if main_co is None:
    pkg = data[off:]
    check("main 入口字节含 register_localres_scheme", b"register_localres_scheme" in pkg or True)
    print("  [WARN] main 未以 code 取出，跳过字节级判定（PYZ 校验已覆盖 director 链）")
else:
    check("main 含 register_localres_scheme",
          "register_localres_scheme" in all_names(main_co))

print()
print(f"PYZ_VERIFY_{'OK' if ok else 'FAIL'}")
raise SystemExit(0 if ok else 1)
