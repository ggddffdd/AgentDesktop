# -*- coding: utf-8 -*-
"""硬核实入包：确认 progress 参数修复已进冻结 exe 的 PYZ。
决定性判据：_gen_agnes_image 与 tool_image_gen 的 code object 的
co_varnames 含 'progress'（即 progress 已成为真实参数），
则 line 1199 的 `if progress:` 永不会 NameError。
"""
import os
import sys
import types
import marshal

EXE = "dist/小臭玩AI/小臭玩AI.exe"
ok = True

from PyInstaller.archive.readers import ZlibArchiveReader
data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
zr = ZlibArchiveReader(EXE, start_offset=off)


def get_code(name):
    raw = zr.extract(name)
    if isinstance(raw, bytes):
        try:
            return marshal.loads(raw)
        except Exception:
            return None
    return raw


def find_module(substr):
    # 精确匹配模块名，避免 browser_control_tools 等被 endswith 误命中
    if substr in zr.toc:
        return substr
    for k in zr.toc.keys():
        if k == substr:
            return k
    return None


def walk_codes(co, out):
    out.append(co)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk_codes(c, out)


tools_name = find_module("tools")
assert tools_name, "PYZ 内找不到 tools"
mod_code = get_code(tools_name)
all_codes = []
walk_codes(mod_code, all_codes)

targets = ["_gen_agnes_image", "tool_image_gen", "_h_image_gen"]
found = {}
for co in all_codes:
    if co.co_name in targets:
        found[co.co_name] = co

for name in targets:
    co = found.get(name)
    if co is None:
        print(f"  [FAIL] PYZ 内找不到函数 {name}")
        ok = False
        continue
    has = "progress" in co.co_varnames
    ok = ok and has
    print(f"  [{'OK' if has else 'FAIL'}] {name} 的 co_varnames 含 'progress' "
          f"(varnames={list(co.co_varnames)})")

# 额外确认：随附源码同样已修（双保险）
RUNNER = "dist/小臭玩AI/_internal/tools.py"
if os.path.isfile(RUNNER):
    src = open(RUNNER, "r", encoding="utf-8", errors="ignore").read()
    f1 = "def _gen_agnes_image(cfg, app_dir, prompt, size=None, progress=None):" in src
    f2 = "return _gen_agnes_image(cfg, app_dir, prompt, size, progress=progress)" in src
    ok = ok and f1 and f2
    print(f"  [{'OK' if f1 else 'FAIL'}] 随附源码 _gen_agnes_image 签名含 progress")
    print(f"  [{'OK' if f2 else 'FAIL'}] 随附源码 调用点透传 progress=progress")

print("\nALL_PROGRESS_OK =", ok)
sys.exit(0 if ok else 1)
