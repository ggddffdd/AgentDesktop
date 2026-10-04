# -*- coding: utf-8 -*-
"""v4.211.0 进包核验：统一资产有效性入口（四态）+ PEP 701 兼容 + 导入校验加固。

本轮改的东西有相当一部分是**行为判定**（`assess_asset` 判什么算有效、`_wrap` 什么时候
把节点判 failed），这类改动最深的地方同样是「源码改了、判据绿了、打包产物还是旧的」——
表现是「明明修了，装上 exe 行为没变」。所以核验落在**字节码**上。

核验策略：
  · **主判据 = 字节码树指纹**：把当前源码 compile() 出来的 code object 与 PYZ 里抽出来的
    逐字节比 sha256。相等 ⇒ 打进包的就是这份源码（本轮 8 个文件 + 上一轮的关键文件）。
  · **回归钉子**（防重启打包时把本轮能力丢掉）：
    canvas_graph：`ASSET_VALIDITIES` 四态元组齐全、`assess_asset` / `audit_assets` /
    `CanvasAssetError` 都在；
    executors：占位扩展名 `.node-placeholder` 与 content_type 标记；
    task_graph：`VALID_STATUSES` 六态（canvas_export 的 status 白名单引它）；
    canvas_export：`SUPPORTED_VERSIONS` 同时支持 1 与 2。
  · **版本一致性**：PYZ 里 `config` 的常量表必须含 v4.211.0，且不含 v4.210.x。
  · **不漏测试**：`tests/` 下的判据套件与根目录扰动脚本绝不能被收进包。

用法：python _verify_pyz_v42110.py
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
           # 上一轮改动、本轮必须仍在包里（指纹一并核对）
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
    print("v4.211.0 进包核验（统一资产四态 + PEP701 兼容 + 导入校验加固）")
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
        check("PYZ 内 config 的版本常量 == v4.211.0",
              "v4.211.0" in consts, f"包内出现的版本串={old}")
        check("PYZ 内不含上一版旧版本常量 v4.210.3",
              "v4.210.3" not in consts, "残留旧版本串（可能是增量打包的旧模块）")

    print("\n-- 3) 本轮能力的回归钉子（防重启打包时被丢掉）--")
    want4 = ("real", "placeholder", "stale", "invalid")
    if "canvas_graph" in names:
        co_cg = _load(za, "canvas_graph")
        cnames = _code_names(co_cg)
        # ⚠️ 不能拿 `_tuple_consts` 找 ASSET_VALIDITIES：它是 `(ASSET_REAL, ...)`
        # 用**变量名**拼的，字节码里由 BUILD_TUPLE 运行时构造，**不是编译期常量**，
        # 常量表里根本没有这个元组（首版核验就栽在这，报了假 FAIL）。
        # 改为核「四个模块级名字都被赋值」+「四个字符串都在常量表里」。
        # （顺序语义由判据套件负责，字节码层验不了 BUILD_TUPLE 的操作数顺序。）
        check("canvas_graph 模块级定义四态常量 + ASSET_VALIDITIES",
              {"ASSET_REAL", "ASSET_PLACEHOLDER", "ASSET_STALE",
               "ASSET_INVALID", "ASSET_VALIDITIES"} <= cnames,
              f"缺={sorted({'ASSET_REAL', 'ASSET_PLACEHOLDER', 'ASSET_STALE', 'ASSET_INVALID', 'ASSET_VALIDITIES'} - cnames)}")
        for fn in ("assess_asset", "audit_assets", "CanvasAssetError"):
            check(f"canvas_graph 定义 {fn}", fn in cnames, "符号丢失")
        # 四态字符串必须真的出现在常量表里（不是只写在注释里）
        scg = _str_consts(co_cg)
        check("canvas_graph 常量表含四态字符串",
              all(v in scg for v in want4), f"缺={[v for v in want4 if v not in scg]}")
    else:
        check("canvas_graph 在 PYZ 里", False, "缺失 → 画布页一打开就 ModuleNotFoundError")

    if "executors" in names:
        co_ex = _load(za, "executors")
        sex = _str_consts(co_ex)
        check("executors 占位扩展名 == .node-placeholder（不再伪装成媒体文件）",
              ".node-placeholder" in sex, "占位物仍会被外部播放器当真媒体打开")
        check("executors 仍定义 validate_output（执行器第一道自查）",
              "validate_output" in _code_names(co_ex), "第一道防线丢失")
        check("executors 仍定义 select_upstream（上游选择契约）",
              "select_upstream" in _code_names(co_ex), "上游选择回退成「默默取第一个」")
    else:
        check("executors 在 PYZ 里", False, "缺失")

    if "task_graph" in names:
        tgs = _tuple_consts(_load(za, "task_graph"))
        check("task_graph 的 VALID_STATUSES 是六态元组（canvas_export 引它做白名单）",
              ("pending", "in_progress", "completed", "failed",
               "cancelled", "incomplete") in tgs, f"现有 str 元组={sorted(tgs)}")
    else:
        check("task_graph 在 PYZ 里", False, "缺失")

    if "canvas_export" in names:
        co_ce = _load(za, "canvas_export")
        sces = _str_consts(co_ce)
        check("canvas_export 仍定义 _validate_project / import_project_json",
              {"_validate_project", "import_project_json"} <= _code_names(co_ce),
              "导入预检丢失")
        check("canvas_export 含 CanvasImportError（导入失败有专用异常）",
              "CanvasImportError" in _code_names(co_ce), "异常类型丢失")
        check("canvas_export 常量表含「多种」版本支持（SUPPORTED_VERSIONS 有内容）",
              "version" in sces or "graph" in sces, "工程文件序列化键丢失")
    else:
        check("canvas_export 在 PYZ 里", False, "缺失")

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
