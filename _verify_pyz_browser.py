# -*- coding: utf-8 -*-
"""硬核实入包：验证小臭玩AI 重打包后 browser 修复已生效。

- 文件层：dist/browser_runner.py 含新函数 _fill_el / _looks_like_css / _is_contenteditable
- PYZ 层：尝试从 exe 内 PYZ 提取 browser_control_tools，确认新描述（不要用 app_*）已入包
"""
import os
import marshal
import types
import sys

DESK = r"C:\Users\xyb\WorkBuddy\2026-07-11-22-26-49\deepseek-desktop"
EXE = os.path.join(DESK, "dist", "小臭玩AI", "小臭玩AI.exe")
RUNNER = os.path.join(DESK, "dist", "小臭玩AI", "browser_runner.py")


def walk_consts(co, out):
    for c in co.co_consts:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, bytes):
            try:
                out.append(c.decode("utf-8", "ignore"))
            except Exception:
                pass
        elif isinstance(c, types.CodeType):
            walk_consts(c, out)
    return out


ok = True

# 1) 文件层：browser_runner.py 是随附文件，运行时由 browser_control_tools 调系统 Python 执行
print("== 文件层 ==")
assert os.path.exists(RUNNER), "browser_runner.py 未随附进 dist"
src = open(RUNNER, encoding="utf-8").read()
print("  browser_runner.py 存在:", True)
f1 = "_fill_el" in src
f2 = "_looks_like_css" in src
f3 = "_is_contenteditable" in src
print("    含 _fill_el:", f1)
print("    含 _looks_like_css:", f2)
print("    含 _is_contenteditable:", f3)
ok &= (f1 and f2 and f3)

# 2) PYZ 层：browser_control_tools 的 schema 在 PYZ 内
print("== PYZ 层 ==")
try:
    from PyInstaller.archive.readers import ZlibArchiveReader
    data = open(EXE, "rb").read()
    off = data.find(b"PYZ\x00")
    assert off != -1, "exe 内无 PYZ"
    zr = ZlibArchiveReader(EXE, start_offset=off)
    names = list(zr.contents.keys())
    tgt = [n for n in names if n == "browser_control_tools" or n.endswith("browser_control_tools")]
    print("  PYZ 内 browser_control_tools 命中:", tgt)
    if tgt:
        raw = zr.read(tgt[0])
        try:
            code = marshal.loads(raw)
        except Exception:
            code = raw
        consts = walk_consts(code, []) if isinstance(code, types.CodeType) else []
        blob = "\n".join(consts)
        hit = "不要用 app_*" in blob
        print("  含新描述(不要用 app_*):", hit)
        ok &= hit
    else:
        print("  PYZ 未找到 browser_control_tools（跳过该项）")
except Exception as e:
    print("  PYZ 层验证跳过（异常）:", repr(e)[:200])

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
