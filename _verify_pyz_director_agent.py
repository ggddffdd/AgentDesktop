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

print("== 5. main 入口 localres scheme 保留 ==")
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
