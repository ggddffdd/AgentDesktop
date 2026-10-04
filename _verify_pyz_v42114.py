# -*- coding: utf-8 -*-
"""v4.211.4 进包核验：主对话链决策审计落盘（盘点 G1 / 宪法第二章第 ③ 条）。

本轮改的是**授权层本体**（`permissions.decide()` 从纯内存决策变成"每条 return
路径都落一笔审计"）+ 抽出一个公共审计件（`tool_audit`，军团与主对话共用）。
这一类的典型失效是"源码改了、判据绿了、装上 exe 还是旧行为" —— 审计少写一笔，
界面上完全看不出任何区别。所以核验落在**字节码**上：当前源码 compile() 出来的
code object 必须与 PYZ 里抽出来的逐字节一致，再补几根钉子钉住本轮能力本身。

核验策略：
  · **主判据 = 字节码树指纹**：把当前源码 compile() 出来的 code object 与 PYZ 里抽出来
    的逐字节比 sha256。相等 ⇒ 打进包的就是这份源码（本轮新增 1 个模块 + 3 个改动文件
    + 上一轮的关键文件）。
  · **回归钉子（本轮）**：
    permissions：`_audited` / `_audit_decision` / `default_audit_dir` 三个符号必须在，
    顶层必须 import 了 `tool_audit`；`PermissionEngine.__init__` 必须留 `audit_dir` 形参；
    **`decide` 的函数体不许引用 `tool_audit`** —— 审计必须留在函数体之外，否则
    `tests/test_permission_gates.py` G 组的 AST 抽取契约会被破坏。
    tool_audit：五个公开函数 + 两个账本文件名 + `args_preview`（只剩不可逆哈希就等于
    查不出"干了啥"）+ `origin`（两账本可区分通道）+ `AUDIT_LOCK` + 脱敏标记。
    legion_permissions：必须复用公共件（`tool_audit` 在 co_names 里），且保留
    `_digest` / `_redact` 名字（既有判据直接调它们）。
  · **回归钉子（前几轮）**：system_control 的 `_require` 与 8 处调用、`csv.reader`、
    可中断形参、软件控制候选枚举、画布四态与统一入口 —— 一并复验，防本轮改授权层
    时顺带塌掉。
  · **版本一致性**：PYZ 里 `config` 的常量表必须含 v4.211.4，且不含 v4.211.3。
  · **不漏测试**：`tests/` 下的判据套件与根目录扰动脚本绝不能被收进包。

用法：python _verify_pyz_v42114.py
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
           # 上一轮改动（系统控制补可中断 / 软件控制控件查找改造）—— 本轮必须仍在包里
           "system_control_tools", "software_control_tools",
           # 本轮改动（主对话决策审计落盘 + 抽公共审计件 + 军团改为复用）
           "permissions", "legion_permissions", "tool_audit",
           # 仍在包里即核对指纹
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
    print("v4.211.4 进包核验（主对话链决策审计落盘 + 公共审计件）")
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
        check("PYZ 内 config 的版本常量 == v4.211.4",
              "v4.211.4" in consts, f"包内出现的版本串={old}")
        check("PYZ 内不含上一版旧版本常量 v4.211.3",
              "v4.211.3" not in consts, "残留旧版本串（可能是增量打包的旧模块）")

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

        # ---- v4.211.3 新钉子 ----
        check("system_control_tools 定义 _require（必填参数校验入口）",
              "_require" in scn,
              "缺参校验丢失 → 回到 KeyError + 英文『工具执行异常：x』")
        _guard8 = ("mouse_move", "keyboard_type", "keyboard_press", "clipboard_write",
                   "window_focus", "window_get_info", "process_kill", "process_start")
        no_guard = sorted(n for n in _guard8
                          if n in tools_co and "_require" not in _code_names(tools_co[n]))
        check("★ 8 个缺必填参数的工具函数体里都真的调用了 _require（声明≠真的校验）",
              not no_guard, f"退回 args[...] 直取的={no_guard}")
        check("★ 解析 tasklist 走 csv.reader（不是按逗号裸切）",
              "csv" in scn and "StringIO" in scn,
              "退回裸切 → 内存列又被千分位逗号切碎、1.2G 的进程显示 0 MB")
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

    print("\n-- 3c) v4.211.4 决策审计钉子（主对话每笔非只读决策必须留痕）--")
    if "permissions" in names:
        co_pe = _load(za, "permissions")
        pen = _code_names(co_pe)
        check("permissions 定义 _audited（把落盘挂在 decide 入口上的装饰器）",
              "_audited" in pen, "装饰器丢失 → 决策又只在内存里")
        check("permissions 定义 _audit_decision（组记录 + 落盘入口）",
              "_audit_decision" in pen, "落盘入口丢失 → 装饰器成空壳")
        check("permissions 定义 default_audit_dir（接线用的目录来源，单一事实源）",
              "default_audit_dir" in pen, "缺失 → ui.py 接线处会 NameError")
        check("permissions 顶层 import 了 tool_audit（公共审计件）",
              "tool_audit" in co_pe.co_names, "公共件没被引用 → 落盘走不通")
        dec_co = _func_codes(co_pe, "decide").get("decide")
        check("permissions 里存在名为 decide 的函数（AST 抽取契约）",
              dec_co is not None, "改名会连带搞坏 test_permission_gates.py 的 G 组")
        if dec_co is not None:
            body = _code_names(dec_co)
            check("★ decide 函数体不引用 tool_audit（审计必须留在函数体之外："
                  "函数体是被 AST 抽出来在受限命名空间里 exec 的）",
                  "tool_audit" not in body, "函数体里出现 tool_audit → 受限 exec 会 NameError")
            check("★ decide 函数体不含 open()（写盘不许进函数体）",
                  "open" not in dec_co.co_names, "写盘动作跑进函数体了")
        init_co = _func_codes(co_pe, "__init__").get("__init__")
        check("★ PermissionEngine.__init__ 有 audit_dir 形参（可接线）",
              init_co is not None and "audit_dir" in init_co.co_varnames,
              "形参丢失 → ui.py 传 audit_dir 会 TypeError")
    else:
        check("permissions 在 PYZ 里", False, "缺失 → 权限引擎消失，整个启动崩")

    if "tool_audit" in names:
        co_ta = _load(za, "tool_audit")
        tan = _code_names(co_ta)
        _want_ta = {"digest", "redact", "build_record", "write", "default_dir"}
        check("tool_audit 定义 digest / redact / build_record / write / default_dir",
              _want_ta <= tan, f"缺={sorted(_want_ta - tan)}")
        tas = _str_consts(co_ta)
        check("★ tool_audit 常量表含两个账本文件名（主对话 + 军团）",
              {"tool_audit.jsonl", "legion_tool_audit.jsonl"} <= tas,
              f"缺={sorted({'tool_audit.jsonl', 'legion_tool_audit.jsonl'} - tas)}")
        check("★ tool_audit 记录含 args_preview（只剩不可逆哈希就等于查不出「干了啥」）",
              "args_preview" in tas, "可读预览字段丢失")
        check("tool_audit 记录含 args_digest / origin / by",
              {"args_digest", "origin", "by"} <= tas,
              f"缺={sorted({'args_digest', 'origin', 'by'} - tas)}")
        check("tool_audit 定义 AUDIT_LOCK（两通道共用一把锁）", "AUDIT_LOCK" in tan)
        check("tool_audit 常量表含脱敏标记 ***", "***" in tas, "敏感值会原样落盘")
    else:
        check("tool_audit 在 PYZ 里", False,
              "缺失 → permissions 顶层 import 失败，整个启动崩")

    if "legion_permissions" in names:
        co_lp = _load(za, "legion_permissions")
        lpn = _code_names(co_lp)
        check("★ legion_permissions 复用公共件（tool_audit 在其 co_names 里）",
              "tool_audit" in co_lp.co_names, "军团退回自己一份实现 → 两账本迟早漂移")
        check("legion_permissions 仍保留 _digest / _redact 名字"
              "（tests/test_legion_permissions.py 直接调它们）",
              {"_digest", "_redact"} <= lpn, f"缺={sorted({'_digest', '_redact'} - lpn)}")
        check("legion_permissions 账本记录带 origin=legion（可区分通道）",
              "origin" in _str_consts(co_lp), "origin 未传入 → 会被默认记成 main")
    else:
        check("legion_permissions 在 PYZ 里", False, "缺失 → 军团权限闸门消失")

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
