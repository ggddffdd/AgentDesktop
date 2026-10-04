# -*- coding: utf-8 -*-
"""v4.211.2 进包核验：系统控制可中断 + 软件控制不再按类型兜底选中。

本轮改的两件事都是**行为判定**（点了停止会不会真停、找不到控件时是报错还是乱点），
这类改动最深的地方同样是「源码改了、判据绿了、打包产物还是旧的」——
表现是「明明修了，装上 exe 行为没变」。所以核验落在**字节码**上。

核验策略：
  · **主判据 = 字节码树指纹**：把当前源码 compile() 出来的 code object 与 PYZ 里抽出来的
    逐字节比 sha256。相等 ⇒ 打进包的就是这份源码（本轮 2 个新模块 + 上一轮的关键文件）。
  · **回归钉子**（防重启打包时把本轮能力丢掉）：
    system_control_tools：`_aborted` 存在，且 **14 个 `tool_*` 的形参表里都有 should_stop**
    —— 这一条直接钉住「可中断」这件事本身（参数丢了，信号就永远传不进来）；
    software_control_tools：`_list_candidates` 存在，且代码里确实**调用 `descendants`**
    （即"枚举同类控件"这条路径真的在，而不是注释里写了一句话）；
    上一轮的画布钉子（四态 / 收帧入口 / 递归反查）一并复验，防止本轮改控制层时顺带塌掉。
  · **版本一致性**：PYZ 里 `config` 的常量表必须含 v4.211.2，且不含 v4.211.1。
  · **不漏测试**：`tests/` 下的判据套件与根目录扰动脚本绝不能被收进包。

用法：python _verify_pyz_v42112.py
"""
import hashlib
import marshal
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"

# 本轮改动过的源码（指纹必须与包内一致）
MODULES = ["config", "canvas_graph", "canvas_export", "executors",
           "canvas_panel", "task_graph", "digital_twin_panel",
           "director_panel", "ui",
           # 本轮改动（系统控制补可中断 / 软件控制控件查找改造）
           "system_control_tools", "software_control_tools",
           # 本轮新增判据所依赖的上一轮基础设施（仍在包里即核对指纹）
           "agnes_bridge", "risk",
           # 上上轮改动、本轮必须仍在包里（指纹一并核对）
           "theme_qss", "ui_motion", "toast"]

# ⚠️ 入口脚本 main 不在 PYZ 里 —— PyInstaller 把它单独编译进 PKG(CArchive)
# 的 `main` 条目（marshal 后的 code object），故单独走 CArchive 取。

_n_pass = _n_fail = 0


def check(name, ok, detail=""):
    global _n_pass, _n_fail
    if ok:
        _n_pass += 1
        print(f"  [PASS] {name}")
    else:
        _n_fail += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _prim(x):
    if isinstance(x, (str, int, float, bool, bytes, type(None))):
        return x
    if isinstance(x, tuple):
        return tuple(_prim(v) for v in x)
    if isinstance(x, frozenset):
        # ⚠️ frozenset 的 repr 顺序不稳定（受 PYTHONHASHSEED 影响）→ 必须规范化排序，
        # 否则同一份源码会算出两个指纹，出现"假不一致"。
        return ("<frozenset>", tuple(sorted((repr(_prim(v)) for v in x))))
    if isinstance(x, types.CodeType):
        return ("<code>", x.co_name)
    return f"<{type(x).__name__}>"


def _fingerprint(co):
    """对 code object 树算规范化 sha256。刻意不含 co_filename / co_firstlineno
    （PyInstaller 编译时文件名与源码路径不同，但那是无关差异）。"""
    acc = hashlib.sha256()

    def walk(c, depth=0):
        acc.update((c.co_name + "\x00").encode("utf-8"))
        acc.update(c.co_code)
        for seq in (c.co_names, c.co_varnames, c.co_freevars, c.co_cellvars):
            acc.update(("\x01".join(map(str, seq)) + "\x02").encode("utf-8"))
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                walk(k, depth + 1)
            else:
                acc.update(repr(_prim(k)).encode("utf-8"))
            acc.update(b"\x03")
        acc.update(b"\x04")

    walk(co)
    return acc.hexdigest()


def _code_iter(c):
    yield c
    for k in c.co_consts:
        if isinstance(k, types.CodeType):
            yield from _code_iter(k)


def _str_consts(co):
    out = set()
    for c in _code_iter(co):
        for x in c.co_consts:
            if isinstance(x, str):
                out.add(x)
            elif isinstance(x, (tuple, frozenset)):
                out.update(y for y in x if isinstance(y, str))
    return out


def _tuple_consts(co):
    """所有 tuple 形态的常量（含内层全 str 的）—— ASSET_VALIDITIES / VALID_STATUSES
    这类「元组即枚举」的声明只有这样才能精确钉住。"""
    out = set()
    for c in _code_iter(co):
        for x in c.co_consts:
            if isinstance(x, tuple) and all(isinstance(y, str) for y in x):
                out.add(x)
    return out


def _code_names(co):
    out = set()
    for c in _code_iter(co):
        out |= set(c.co_names)
        out.add(c.co_name)
    return out


def _func_codes(co, prefix):
    """{函数名: code object}，只收名字以 prefix 开头的（含嵌套层）。"""
    return {c.co_name: c for c in _code_iter(co) if c.co_name.startswith(prefix)}


def _load(za, name):
    got = za.extract(name)
    return got if isinstance(got, types.CodeType) else marshal.loads(got)


def _load_entry_script(exe: Path, name: str = "main"):
    """入口脚本从 PKG(CArchive) 取（不在 PYZ 里）。返回 code object 或 None。"""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        r = CArchiveReader(str(exe))
        if name not in r.toc:
            return None
        raw = r.extract(name)
        co = marshal.loads(raw)
        return co if isinstance(co, types.CodeType) else None
    except Exception:
        return None


def main():
    print("v4.211.2 进包核验（系统控制可中断 + 控件查找不再按类型兜底）")
    print("-" * 62)
    if not EXE.is_file():
        print(f"未找到产物：{EXE}")
        return 1

    data = EXE.read_bytes()
    off = data.find(b"PYZ\x00")
    check("exe 内含 PYZ 段", off != -1, f"offset={off}")
    if off == -1:
        print("\n找不到 PYZ 段，后续核验无意义，提前退出。")
        print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
        return 1
    print(f"  产物：{EXE.name}  {len(data)/1048576:.1f} MB")

    sys.path.insert(0, str(ROOT))
    # ⚠️ PYZ 是**嵌在 CArchive 里**的，不在文件开头 —— 直接 ZlibArchiveReader(exe)
    # 会抛 "PYZ magic pattern mismatch!"。必须先扫出 `PYZ\x00` 的偏移再传进去。
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    za = ZlibArchiveReader(str(EXE), start_offset=off)
    names = set(za.toc)
    check("PYZ 模块表可读（模块数 > 50）", len(names) > 50, f"{len(names)} 个模块")

    print("\n-- 1) 本轮改动的模块：字节码指纹（源码 compile() == 包内 code object）--")
    for mod in MODULES:
        if mod not in names:
            check(f"{mod} 在 PYZ 模块表里", False, "模块缺失 → 运行必 ImportError")
            continue
        src_file = ROOT / f"{mod}.py"
        if not src_file.is_file():
            check(f"{mod} 源码存在", False, str(src_file))
            continue
        src = src_file.read_bytes()
        # 源码按 utf-8-sig 解码后 compile（config.py 带 BOM）
        co_src = compile(src.decode("utf-8-sig"), f"{mod}.py", "exec")
        co_pkg = _load(za, mod)
        same = _fingerprint(co_src) == _fingerprint(co_pkg)
        check(f"{mod} 字节码指纹一致（包内 == 当前源码）", same,
              f"src={_fingerprint(co_src)[:12]} pkg={_fingerprint(co_pkg)[:12]}")

    print("\n-- 2) 版本一致性 --")
    if "config" in names:
        consts = _str_consts(_load(za, "config"))
        old = sorted(s for s in consts if s.startswith("v4.210.") or s.startswith("v4.211."))
        check("PYZ 内 config 的版本常量 == v4.211.2",
              "v4.211.2" in consts, f"包内出现的版本串={old}")
        check("PYZ 内不含上一版旧版本常量 v4.211.1",
              "v4.211.1" not in consts, "残留旧版本串（可能是增量打包的旧模块）")

    print("\n-- 3) 本轮能力的回归钉子（防重启打包时被丢掉）--")
    if "system_control_tools" in names:
        co_sc = _load(za, "system_control_tools")
        scn = _code_names(co_sc)
        check("system_control_tools 定义 _aborted（停止判定入口）",
              "_aborted" in scn, "可中断的判定函数丢失")
        tools_co = _func_codes(co_sc, "tool_")
        n_tools = len(tools_co)
        _want14 = {"screenshot", "mouse_move", "mouse_click", "mouse_scroll",
                   "keyboard_type", "keyboard_press", "clipboard_read",
                   "clipboard_write", "window_list", "window_focus",
                   "window_get_info", "process_list", "process_kill",
                   "process_start"}
        lack = sorted(n for n, c in tools_co.items()
                      if not {"progress", "stop_event", "should_stop"} <= set(c.co_varnames))
        check(f"system_control 共 14 个 tool_*（实际 {n_tools}）", n_tools == 14,
              f"漏={sorted(_want14 - set(tools_co))}")
        check("★ 全部 14 个 tool_* 形参表都含 should_stop（漏一个信号就传不进来）",
              not lack, f"缺形参的={lack}")
    else:
        check("system_control_tools 在 PYZ 里", False,
              "缺失 → 键鼠/进程工具集体消失")

    if "software_control_tools" in names:
        co_sw = _load(za, "software_control_tools")
        swn = _code_names(co_sw)
        check("software_control_tools 定义 _list_candidates（只枚举、不选中）",
              "_list_candidates" in swn, "候选枚举入口丢失 → 找不到控件时又会乱选一个")
        check("★ 代码里确实走 descendants 枚举同类控件",
              "descendants" in swn,
              "只剩 child_window 按类型挑第一个 → 误点静默会回来")
        check("software_control_tools 仍定义 _find_control / _connect_window",
              {"_find_control", "_connect_window"} <= swn, "控件定位链断裂")
        swt = _func_codes(co_sw, "tool_app_")
        check(f"software_control 的 tool_app_* 数量未变（实际 {len(swt)}）", len(swt) == 10,
              f"{sorted(swt)}")
    else:
        check("software_control_tools 在 PYZ 里", False,
              "缺失 → 软件控制工具集体消失")

    print("\n-- 3b) 上一轮画布钉子复验（本轮改控制层，别把画布顺带塌掉）--")
    want4 = ("real", "placeholder", "stale", "invalid")
    if "canvas_graph" in names:
        co_cg = _load(za, "canvas_graph")
        cnames = _code_names(co_cg)
        # ⚠️ 不能拿 `_tuple_consts` 找 ASSET_VALIDITIES：它是 `(ASSET_REAL, ...)`
        # 用**变量名**拼的，字节码里由 BUILD_TUPLE 运行时构造，**不是编译期常量**，
        # 常量表里根本没有这个元组（首版核验就栽在这，报了假 FAIL）。
        check("canvas_graph 模块级定义四态常量 + ASSET_VALIDITIES",
              {"ASSET_REAL", "ASSET_PLACEHOLDER", "ASSET_STALE",
               "ASSET_INVALID", "ASSET_VALIDITIES"} <= cnames, "四态声明丢失")
        for fn in ("assess_asset", "is_usable_asset", "audit_assets",
                   "CanvasAssetError"):
            check(f"canvas_graph 定义 {fn}", fn in cnames, "符号丢失")
        scg = _str_consts(co_cg)
        check("canvas_graph 常量表含四态字符串",
              all(v in scg for v in want4), f"缺={[v for v in want4 if v not in scg]}")
    else:
        check("canvas_graph 在 PYZ 里", False, "缺失 → 画布页一打开就 ModuleNotFoundError")

    if "executors" in names:
        co_ex = _load(za, "executors")
        sex = _str_consts(co_ex)
        check("executors 占位扩展名 == .node-placeholder",
              ".node-placeholder" in sex, "占位物仍会被当真媒体")
        check("executors 定义 _ref_by_path（递归上游按路径反查 ref）",
              "_ref_by_path" in _code_names(co_ex), "促销帧四态过滤会变死代码")
        check("executors 常量表含 skipped_frames",
              "skipped_frames" in sex, "跳过计数报不出来")
    else:
        check("executors 在 PYZ 里", False, "缺失")

    if "agnes_bridge" in names:
        check("agnes_bridge 定义 collect_promo_frames",
              "collect_promo_frames" in _code_names(_load(za, "agnes_bridge")),
              "收帧退回「有路径就要」")
    else:
        check("agnes_bridge 在 PYZ 里", False, "缺失")

    if "task_graph" in names:
        tgs = _tuple_consts(_load(za, "task_graph"))
        check("task_graph 的 VALID_STATUSES 是六态元组",
              ("pending", "in_progress", "completed", "failed",
               "cancelled", "incomplete") in tgs, f"现有 str 元组={sorted(tgs)}")
    else:
        check("task_graph 在 PYZ 里", False, "缺失")

    if "risk" in names:
        co_rk = _load(za, "risk")
        rn = _code_names(co_rk)
        check("risk 仍定义 _policy / classify / tier_of（授权层唯一入口）",
              {"_policy", "classify", "tier_of"} <= rn, "授权层入口丢失")
        check("risk 仍定义 validate_policy（策略表自检）",
              "validate_policy" in rn, "自检丢失")
        check("risk 仍定义 command_danger_level（参数级高危探测）",
              "command_danger_level" in rn, "高危探测丢失")
    else:
        check("risk 在 PYZ 里", False, "缺失 → 权限引擎 import 失败，整个启动崩")

    co_main = _load_entry_script(EXE, "main")
    check("main 从 CArchive 取到（入口脚本不在 PYZ 是正常结构）", co_main is not None)

    print("\n-- 4) 不漏测试 / 不漏扰动脚本 --")
    leaked = sorted(n for n in names
                    if n.startswith("test_") or "perturb" in n or "pep701" in n)
    check("PYZ 里没有 tests/ 判据套件与根目录扰动脚本（测试不得随包发布）",
          not leaked, f"泄漏的模块={leaked}")
    print(f"  PYZ 模块数：{len(names)}")

    print("\n" + "=" * 62)
    print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
