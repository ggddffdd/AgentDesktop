"""硬核实入包：生图修复是否进 dist。
- dist/_internal/tools.py（随附文件）：含 _letterbox_to_size + size 透传
- PYZ 内 tool_defs：image_gen 含 size 参数描述
- PYZ 内 tools：_h_image_gen 含透传、_gen_agnes_image 含 letterbox 调用
"""
import os, sys, types, marshal

EXE = "dist/小臭玩AI/小臭玩AI.exe"
RUNNER_DATA = "dist/小臭玩AI/_internal/tools.py"
ok = True

# 1) 随附文件 tools.py
print("=== 随附文件 dist/_internal/tools.py ===")
if not os.path.isfile(RUNNER_DATA):
    print("FAIL: 找不到", RUNNER_DATA)
    ok = False
else:
    src = open(RUNNER_DATA, "r", encoding="utf-8", errors="ignore").read()
    checks = {
        "_letterbox_to_size 定义": "def _letterbox_to_size",
        "size 透传(_h_image_gen)": "size=size",
        "letterbox 调用(_gen_agnes_image)": "_letterbox_to_size(fpath, tw, th)",
        "默认 1920x1080(源码)": '"image_gen_size": "1920x1080"',
    }
    for k, v in checks.items():
        f = v in src
        ok = ok and f
        print(f"  [{'OK' if f else 'FAIL'}] {k}")

# 2) PYZ 内 tool_defs / tools 字符串常量
print("=== PYZ 内模块 ===")
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
    return raw  # 6.19 直接返回 code object

def walk_consts(co, out):
    if not hasattr(co, "co_consts"):
        return out
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

def find_module(substr):
    for k in zr.toc.keys():
        if k.endswith(substr):
            return k
    return None

# tool_defs: size 参数描述
td_name = find_module("tool_defs")
if td_name:
    code = get_code(td_name)
    blob = "\n".join(walk_consts(code, []))
    f = "1920x1080 横版" in blob and "size" in blob
    ok = ok and f
    print(f"  [{'OK' if f else 'FAIL'}] tool_defs 含 size 参数描述 (命中模块 {td_name})")
else:
    print("  [FAIL] PYZ 内找不到 tool_defs")
    ok = False

# tools: _h_image_gen 透传 + _gen_agnes_image letterbox
tools_name = find_module("tools")
if tools_name:
    code = get_code(tools_name)
    blob = "\n".join(walk_consts(code, []))
    f1 = "size=size" in blob
    f2 = "_letterbox_to_size" in blob
    ok = ok and f1 and f2
    print(f"  [{'OK' if f1 else 'FAIL'}] tools 含 size 透传 (size=size)")
    print(f"  [{'OK' if f2 else 'FAIL'}] tools 含 _letterbox_to_size")
else:
    print("  [FAIL] PYZ 内找不到 tools")
    ok = False

print("\nALL_IMAGE_GEN_OK =", ok)
sys.exit(0 if ok else 1)
