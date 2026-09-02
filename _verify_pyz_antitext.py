import types
from PyInstaller.archive.readers import ZlibArchiveReader

EXE = "dist/小臭玩AI/小臭玩AI.exe"
data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
zr = ZlibArchiveReader(EXE, start_offset=off)

code = None
for k in zr.toc.keys():
    if k == "tools":
        code = zr.extract(k)
        break
assert code is not None, "tools module not found in PYZ"

found = set()
def walk(co):
    found.add(co.co_name)
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk(c)
        elif isinstance(c, str):
            found.add("STR:" + c)
walk(code)

ok1 = "_size_to_tier_ratio" in found
ok2 = "_gen_agnes_image" in found
ok3 = any("强制约束" in s for s in found if s.startswith("STR:"))
print("has _size_to_tier_ratio:", ok1)
print("has _gen_agnes_image:", ok2)
print("has 强制约束 anti-text marker:", ok3)
print("ALL_OK:", ok1 and ok2 and ok3)
