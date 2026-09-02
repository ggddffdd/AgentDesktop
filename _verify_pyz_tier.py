# -*- coding: utf-8 -*-
"""PYZ 硬核验证：确认 _size_to_tier_ratio 已编译进冻结 exe，且参数(w,h)与档位字符串(2K/16:9)在字节码中。"""
import types
from PyInstaller.archive.readers import ZlibArchiveReader

EXE = "dist/小臭玩AI/小臭玩AI.exe"
data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
zr = ZlibArchiveReader(EXE, start_offset=off)


def find_module(exact):
    for k in zr.toc.keys():
        if k == exact:
            return k
    return None


mod = find_module("tools")
raw = zr.extract(mod)
assert isinstance(raw, types.CodeType), type(raw)

found = []
def walk(co):
    found.append(co)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk(c)
walk(raw)

fn = [c for c in found if c.co_name == "_size_to_tier_ratio"]
print("has _size_to_tier_ratio:", bool(fn))
if not fn:
    print("TIER_IN_PKG = False")
    raise SystemExit(1)

f = fn[0]
print("co_varnames:", f.co_varnames[:6])
print("has w,h:", "w" in f.co_varnames and "h" in f.co_varnames)

def collect_strings(co):
    """收集字符串常量。

    ⚠️ `return "2K", "16:9"` 会被编译期 fold 成一个 tuple const，
    只扫 co_consts 的 str 会漏掉档位/比例字符串 —— 必须展开 tuple。
    """
    out = []
    stack = [co]
    while stack:
        c = stack.pop()
        for k in c.co_consts:
            if isinstance(k, str):
                out.append(k)
            elif isinstance(k, tuple):
                out.extend(x for x in k if isinstance(x, str))
            elif isinstance(k, types.CodeType):
                stack.append(k)
    return out


consts = collect_strings(f)
print("has '2K':", "2K" in consts)
print("has '16:9':", "16:9" in consts)

ok = ("w" in f.co_varnames and "h" in f.co_varnames and "2K" in consts and "16:9" in consts)
print("TIER_IN_PKG =", ok)
raise SystemExit(0 if ok else 1)
