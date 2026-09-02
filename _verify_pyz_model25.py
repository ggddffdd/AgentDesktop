# -*- coding: utf-8 -*-
"""PYZ 硬核：确认新默认模型名 agnes-image-2.5-flash 已编译进 exe，
且旧值 agnes-image-2.1-flash 仍作为兼容分支存在（tools.py:1196 旧值兜底）。
只读 dist 产物，不查源码，防「源码改了没重打包」假阳性。
"""
import os, types
from PyInstaller.archive.readers import ZlibArchiveReader

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "小臭玩AI", "小臭玩AI.exe")

data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
if off < 0:
    print("NO_PYZ_FOUND")
    raise SystemExit(1)
zr = ZlibArchiveReader(EXE, start_offset=off)

consts = []

def walk(co):
    for c in co.co_consts:
        if isinstance(c, str):
            consts.append(c)
        elif isinstance(c, types.CodeType):
            walk(c)

for key in zr.toc.keys():
    try:
        raw = zr.extract(key)
    except Exception:
        continue
    if isinstance(raw, types.CodeType):
        walk(raw)

blob = "\n".join(consts)
m25 = "agnes-image-2.5-flash" in blob
m21 = "agnes-image-2.1-flash" in blob
print("PYZ modules scanned, const strings:", len(consts))
print("has agnes-image-2.5-flash:", m25)
print("has agnes-image-2.1-flash (compat):", m21)
print("MODEL25_IN_PKG =", m25)
raise SystemExit(0 if m25 else 2)
