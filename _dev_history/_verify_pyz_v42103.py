# -*- coding: utf-8 -*-
"""v4.210.3 进包核验：触控命中区修复（纯字面量改动）+ 上一轮三件套未回退。

本轮改的是 **9 处尺寸字面量**（`setFixedSize(32, 32)` 之类）。
这类改动最阴的地方是：源码改了、判据绿了、但**打包产物还是旧的那份** ——
表现是「明明修了，装上 exe 还是点不中」。所以核验必须落在**字节码**上，
而不是「包里有没有 32 这个数」（32 到处都是，证明不了任何事）。

核验策略：
  · **主判据 = 字节码树指纹**：把当前源码 compile() 出来的 code object 与
    PYZ 里抽出来的逐字节比 sha256。相等 ⇒ 打进包的就是这份源码。
    这条同时钉住了「9 处 32px 真进了包」与「上一轮三件套没被这轮覆盖掉」。
  · **回归钉子**（防上一轮的东西在重启打包时被丢掉）：
    `ui_motion` 仍在 PYZ 且含 SPI_GETCLIENTAREAANIMATION=4162；
    `theme_qss` 仍有 `_focus_ring` 且常量表含 `border:2px solid ` 模板；
    `main`（在 CArchive 里，不在 PYZ）仍调用 `apply_native_font`。
  · **版本一致性**：PYZ 里 `config` 的常量表必须含 v4.210.3。
  · **不漏测试**：`tests/` 下的判据套件绝不能被收进包。

用法：python _verify_pyz_v42103.py
"""
import hashlib
import marshal
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"

# 本轮改动过的源码（指纹必须与包内一致）
MODULES = ["config", "ui", "toast", "director_panel",
           # 上一轮改动、本轮必须仍在包里（指纹一并核对）
           "theme_qss", "ui_motion", "skill_market_ui", "canvas_panel"]

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


def _int_consts(co):
    out = set()
    for c in _code_iter(co):
        for x in c.co_consts:
            if isinstance(x, int) and not isinstance(x, bool):
                out.add(x)
    return out


def _code_names(co):
    out = set()
    for c in _code_iter(co):
        out |= set(c.co_names)
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
    print("v4.210.3 进包核验（命中区修复 + 字节码指纹 + 上一轮回归钉子）")
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
        co_cfg = _load(za, "config")
        consts = _str_consts(co_cfg)
        src_ver = [s for s in consts if s.startswith("v4.210.")]
        check("PYZ 内 config 的版本常量 == v4.210.3",
              "v4.210.3" in consts, f"包内出现的版本串={sorted(src_ver)}")
        check("PYZ 内不含旧版本常量 v4.210.2",
              "v4.210.2" not in consts, "残留旧版本串（可能是增量打包的旧模块）")

    print("\n-- 3) 上一轮三件套的回归钉子（防重启打包时被丢掉）--")
    if "ui_motion" in names:
        co_m = _load(za, "ui_motion")
        check("ui_motion 常量表含 SPI_GETCLIENTAREAANIMATION 取值 0x1042(4162)",
              4162 in _int_consts(co_m), "取值不在常量表里 → 动效开关读不到系统偏好")
    else:
        check("ui_motion 在 PYZ 里", False, "缺失 → toast 一启动就 ModuleNotFoundError")

    if "theme_qss" in names:
        co_tq = _load(za, "theme_qss")
        check("theme_qss 仍定义 _focus_ring（键盘焦点环）",
              "_focus_ring" in _code_names(co_tq), "焦点环函数丢失")
        check("theme_qss 含 `border:2px solid ` 模板（环真会被画出来）",
              any(s.startswith("border:2px solid ") for s in _str_consts(co_tq)),
              "焦点环模板串不在常量表里")
        check("theme_qss 含字体栈串（字体收口未回退）",
              any("Microsoft YaHei" in s for s in _str_consts(co_tq)),
              "FONT_STACK/FONT_FAMILY 不回退")
    else:
        check("theme_qss 在 PYZ 里", False, "缺失")

    co_main = _load_entry_script(EXE, "main")
    check("main 从 CArchive 取到（入口脚本不在 PYZ 是正常结构）", co_main is not None)
    if co_main is not None:
        check("main 仍调用 apply_native_font（字体收口真落地，不是只定义）",
              "apply_native_font" in _code_names(co_main), "收口调用被丢")

    print("\n-- 4) 不漏测试 --")
    leaked = sorted(n for n in names if "hitarea" in n or "ui_a11y" in n)
    check("PYZ 里没有 tests/ 的判据套件（测试不得随包发布）", not leaked,
          f"泄漏的测试模块={leaked}")
    print(f"  PYZ 模块数：{len(names)}")

    print("\n" + "=" * 62)
    print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
