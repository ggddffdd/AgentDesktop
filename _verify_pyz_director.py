# -*- coding: utf-8 -*-
"""硬核实入包：确认导演台预览区网页化三处改动已进冻结 exe。

判据：
1. director_web 模块在 PYZ 内，且 code 含关键符号
   (QWebEngineView / register_localres_scheme / attach_localres_handler / render_cards)
2. director_panel 的 _on_web_action 函数存在，且 co_names 含真实业务分发
   (_play_clip / _modify_clip / _regenerate_clip / _view_prompt)
3. director_panel 旧 Qt 容器已全部清除（director_clips_grid / director_keyframes_grid /
   director_characters_layout / director_merge_preview / director_clip_cards 不在 co_names）
4. main 入口在 CArchive 内，字节含 register_localres_scheme（QApplication 前注册 scheme）

注意：main 不进 PYZ（它是入口脚本，存于 CArchive/PKG），故用 CArchiveReader 提取字节判定。
PYZ/PKG 均为 zlib 压缩块，直接用 grep 搜 exe 字节会假阴性（搜不到明文），必须用归档读取器。
"""
import os
import sys
import types
import marshal

EXE = "dist/小臭玩AI/小臭玩AI.exe"
ok = True

from PyInstaller.archive.readers import ZlibArchiveReader, CArchiveReader

data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
zr = ZlibArchiveReader(EXE, start_offset=off)


def get_code(name):
    raw = zr.extract(name)
    return raw if isinstance(raw, types.CodeType) else None


def walk_codes(co, out):
    out.append(co)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk_codes(c, out)


# ---- 1. director_web 模块存在 + 关键符号 ----
dw = get_code("director_web")
if dw is None:
    print("  [FAIL] PYZ 内找不到 director_web 模块")
    ok = False
else:
    dcodes = []
    walk_codes(dw, dcodes)
    dn = set()
    for c in dcodes:
        dn |= set(c.co_names)
    for sym in ("QWebEngineView", "register_localres_scheme",
                "attach_localres_handler", "render_cards"):
        has = sym in dn
        ok = ok and has
        print(f"  [{'OK' if has else 'FAIL'}] director_web 含 {sym}")

# ---- 2 & 3. director_panel 接线 + 旧容器清除 ----
dp = get_code("director_panel")
if dp is None:
    print("  [FAIL] PYZ 内找不到 director_panel")
    ok = False
else:
    pcodes = []
    walk_codes(dp, pcodes)
    owa = [c for c in pcodes if c.co_name == "_on_web_action"]
    if not owa:
        print("  [FAIL] director_panel 找不到 _on_web_action 函数")
        ok = False
    else:
        ns = set(owa[0].co_names)
        for x in ("_play_clip", "_modify_clip", "_regenerate_clip", "_view_prompt"):
            has = x in ns
            ok = ok and has
            print(f"  [{'OK' if has else 'FAIL'}] _on_web_action 调 {x}")
    pnames = set()
    for c in pcodes:
        pnames |= set(c.co_names)
    for old in ("director_clips_grid", "director_keyframes_grid",
                "director_characters_layout", "director_merge_preview",
                "director_clip_cards"):
        present = old in pnames
        ok = ok and (not present)
        print(f"  [{'OK' if not present else 'FAIL'}] 旧容器 {old} 已清除")

# ---- 4. main 入口 scheme 注册（CArchive 字节明文判定） ----
ca = CArchiveReader(EXE)
mkey = [k for k in ca.toc if k in ("main", "main.py")]
if not mkey:
    print("  [FAIL] CArchive 内找不到 main 入口")
    ok = False
else:
    mraw = ca.extract(mkey[0])
    has = b"register_localres_scheme" in mraw
    ok = ok and has
    print(f"  [{'OK' if has else 'FAIL'}] main 入口字节含 register_localres_scheme"
          f"（QApplication 前注册 scheme）")

print("\nALL_DIRECTOR_WEB_OK =", ok)
sys.exit(0 if ok else 1)
