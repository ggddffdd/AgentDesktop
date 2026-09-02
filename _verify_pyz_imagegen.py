"""硬核实入包：生图修复是否进 dist。
- 随附文件 tools.py（若落盘则查源码，frozen module 不落盘属正常）
- PYZ 内 tool_defs：image_gen 含 size 参数描述
- PYZ 内 tools：_h_image_gen 含透传、_gen_agnes_image 含 letterbox 调用

⚠️ PYZ 找模块必须精确匹配：toc 里 endswith("tools") 会命中
browser_control_tools / director_agent_tools / system_control_tools 等 30+ 个模块，
dict 顺序不定 → 会拿错模块来断言，伪 FAIL。
"""
import os, sys, types, marshal

EXE = "dist/小臭玩AI/小臭玩AI.exe"
RUNNER_DATA = "dist/小臭玩AI/_internal/tools.py"
ok = True

# 1) 随附文件 tools.py（PyInstaller 6.x 下 tools 是 PYZ frozen module，通常不落盘）
print("=== 随附文件 dist/_internal/tools.py ===")
if not os.path.isfile(RUNNER_DATA):
    print("  [SKIP] 未落盘（tools 走 PYZ frozen，属正常），以 PYZ 段判定为准")
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
    """收集字符串常量 + 关键字参数名元组 + 名字（co_names）。

    ⚠️ 只扫 co_consts 的 str 会漏掉两类关键证据：
      - 函数名/属性名只在 co_names（如 _letterbox_to_size）
      - f(size=size) 的 kw 名是个 tuple const（如 ('size',)），不在 str 里
    """
    if not hasattr(co, "co_consts"):
        return out
    out.extend(getattr(co, "co_names", ()))
    for c in co.co_consts:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, bytes):
            try:
                out.append(c.decode("utf-8", "ignore"))
            except Exception:
                pass
        elif isinstance(c, tuple):
            out.extend(x for x in c if isinstance(x, str))
        elif isinstance(c, types.CodeType):
            walk_consts(c, out)
    return out


def walk_codes(co):
    stack = [co]
    while stack:
        c = stack.pop()
        yield c
        stack.extend(x for x in c.co_consts if isinstance(x, types.CodeType))

def find_module(name):
    """精确匹配模块名（含 pkg.sub），绝不用 endswith —— 见文件头警告。"""
    if name in zr.toc:
        return name
    for k in zr.toc.keys():
        if k.split(".")[-1] == name:
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

# tools: _h_image_gen 透传 size + 生图函数体调 _letterbox_to_size
tools_name = find_module("tools")
if tools_name:
    code = get_code(tools_name)
    codes = list(walk_codes(code))

    # f1: size 透传链 = tool_image_gen 取 size 并调用 _gen_agnes_image
    #     ⚠️ 不是 kw 调用（源码是位置参数 _gen_agnes_image(cfg, app_dir, prompt, size, ...)）
    #     ⚠️ size 是形参 → 走 LOAD_FAST，只在 co_varnames，不在 co_names
    tg = [c for c in codes if c.co_name == "tool_image_gen"]
    f1 = bool(tg) and all(
        "size" in c.co_varnames and "_gen_agnes_image" in c.co_names for c in tg
    )
    ok = ok and f1
    print(f"  [{'OK' if f1 else 'FAIL'}] tool_image_gen 取 size 并调用 _gen_agnes_image (命中 {len(tg)})")

    # f2: 某函数体真的调用了 _letterbox_to_size（在 co_names 里 = 调用点，而非仅模块出现）
    callers = [c.co_name for c in codes if "_letterbox_to_size" in c.co_names]
    f2 = bool(callers)
    ok = ok and f2
    print(f"  [{'OK' if f2 else 'FAIL'}] _letterbox_to_size 调用点: {callers[:3]}")

    # f3: 定义还在
    f3 = any(c.co_name == "_letterbox_to_size" for c in codes)
    ok = ok and f3
    print(f"  [{'OK' if f3 else 'FAIL'}] _letterbox_to_size 定义存在")
else:
    print("  [FAIL] PYZ 内找不到 tools")
    ok = False

print("\nALL_IMAGE_GEN_OK =", ok)
sys.exit(0 if ok else 1)
