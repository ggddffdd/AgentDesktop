# -*- coding: utf-8 -*-
"""v4.215.0 进包核验：自动化独立会话 + 独立工具权限。

本轮 = 删：源码 7 个（canvas_graph / canvas_panel / canvas_export / executors /
agnes_bridge / image_local_edit / demo_canvas_cli）+ 判据 14 + 扰动 14 + 文档 20；
UI 摘掉「画布」导航项与页外壳；spec 去掉 6 个画布 hiddenimports。
公共底座 task_graph / cancel_token / asset_store **保留**（军团 / 导演台 /
数字分身共用）。

核验重点：① **反向** —— 6 个画布模块绝不能出现在 PYZ，ui 里也不能再有
_build_canvas_page / CanvasPanel 引用（防「源码删了但 spec/入口没清」死代码进包）；
② 版本号 v4.215.0 且不含 v4.214.0；③ 前几轮钉子（系统控制 tool_*、决策审计、
UI 颜色等值化、关键进程黑名单、不漏测试不漏扰动）一并复验。
"""
import hashlib
import marshal
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"

# 本轮改动过的源码（指纹必须与包内一致）
MODULES = ["config", "task_graph", "digital_twin_panel",
           "director_panel", "ui",
           # 本轮改动（G4 第二步：关键进程黑名单 → 硬拒绝；三个入口）
           "tools",
           # 上一轮改动（⑨：颜色字面量 → 等值 THEME[key]，零视觉变化）
           "skill_manager_ui", "skill_market_ui", "tool_manager_ui",
           # 上上轮改动（系统控制补可中断 / 软件控制控件查找改造）—— 本轮必须仍在包里
           "system_control_tools", "software_control_tools",
           # 上轮改动（主对话决策审计落盘 + 抽公共审计件 + 军团改为复用）
           "permissions", "legion_permissions", "tool_audit",
           # 兜底字典的宿主：护栏让它静默，但它本身是产品代码，必须仍在包里
           "legion_status_widget", "legion_ui",
           # 仍在包里即核对指纹
           "risk",
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


def _deep_str_consts(co):
    """递归收集**任意嵌套层级**里的 str 常量。

    为什么不用 `_str_consts`：它只下探一层（`elif isinstance(x, (tuple, frozenset))`
    只取直接元素），而 `_DANGEROUS_CMD_PATTERNS = ((re, label), ...)` 是
    **tuple 套 tuple** → 标签字符串一个都扫不到 → 假 FAIL。同类坑本仓已踩过一次
    （L240：`(NAME_A, NAME_B)` 枚举元组扫不到）。这里不动公共的 `_str_consts`，
    免得把既有「包含/不包含」类断言的严格度一起改掉。
    """
    out = set()

    def _walk(o):
        if isinstance(o, str):
            out.add(o)
        elif isinstance(o, (tuple, list, frozenset, set)):
            for y in o:
                _walk(y)

    for c in _code_iter(co):
        for x in c.co_consts:
            _walk(x)
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
    print("v4.215.0 进包核验（自动化独立会话 + 前几轮钉子复验）")
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
        old = sorted(s for s in consts if s.startswith("v4.210.") or s.startswith("v4.211.")
                     or s.startswith("v4.212."))
        check("PYZ 内 config 的版本常量 == v4.215.0",
              "v4.215.0" in consts, f"包内出现的版本串={old}")
        check("PYZ 内不含上一版旧版本常量 v4.214.0",
              "v4.214.0" not in consts, "残留旧版本串（可能是增量打包的旧模块）")

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

    print("\n-- 3d) 本轮钉子：UI 颜色等值化真进包 / 构建期脚本不得进包 --")
    for _m in ("skill_manager_ui", "skill_market_ui", "tool_manager_ui"):
        if _m not in names:
            check(f"{_m} 在 PYZ 里", False, "缺失 → 对应管理面板打不开")
            continue
        _src = (ROOT / f"{_m}.py").read_bytes()
        _co_src = compile(_src.decode("utf-8-sig"), f"{_m}.py", "exec")
        check(f"★ {_m} 字节码指纹一致（⑨ 的颜色改动真进包了）",
              _fingerprint(_co_src) == _fingerprint(_load(za, _m)),
              "包内还是旧字面量版本 → 改了个寂寞")
    check("★ ui_hex_guard 不得进包（构建期脚本不进分发物）",
          "ui_hex_guard" not in names,
          "构建期扫描逻辑混进分发物")
    if "legion_status_widget" in names:
        _src = (ROOT / "legion_status_widget.py").read_bytes()
        _co_src = compile(_src.decode("utf-8-sig"), "legion_status_widget.py", "exec")
        check("★ legion_status_widget 指纹一致（护栏让它静默，但它本身是产品代码）",
              _fingerprint(_co_src) == _fingerprint(_load(za, "legion_status_widget")))

    print("\n-- 3e) 本轮钉子：关键进程黑名单真进包（三个入口）--")
    _CRIT_NAMES = ("lsass.exe", "csrss.exe", "winlogon.exe", "wininit.exe", "smss.exe",
                   "services.exe", "svchost.exe", "explorer.exe", "dwm.exe")
    if "system_control_tools" not in names:
        check("system_control_tools 在 PYZ 里", False, "缺失 → 系统控制工具全不可用")
    else:
        _src = (ROOT / "system_control_tools.py").read_bytes()
        _co_src = compile(_src.decode("utf-8-sig"), "system_control_tools.py", "exec")
        _co_in = _load(za, "system_control_tools")
        check("★ system_control_tools 字节码指纹一致（本轮黑名单真进包了）",
              _fingerprint(_co_src) == _fingerprint(_co_in),
              "包内还是旧版 → 改了个寂寞")
        _names_in = _code_names(_co_in)
        check("★ 包内 system_control_tools 含 _critical_process_deny（判定入口编进去了）",
              "_critical_process_deny" in _names_in,
              "_critical_process_deny 不在包内 co_names → 判定逻辑没编进去")
        check("★ 包内仍含 _count_processes（上一轮的钉子不能掉）",
              "_count_processes" in _names_in, "上一轮的计数入口没了")
        # frozenset({...}) 可能被折叠成 frozenset 常量，也可能走 BUILD_SET ——
        # `_str_consts` 两种形态都扫（它已处理 tuple/frozenset 元素）。
        _consts_in = _str_consts(_co_in)
        _missing = [n for n in _CRIT_NAMES if n not in _consts_in]
        check("★ 包内常量表含全部 9 个关键进程名（名单不是空壳）",
              not _missing, f"缺={_missing}")

    if "software_control_tools" in names:
        _src = (ROOT / "software_control_tools.py").read_bytes()
        _co_src = compile(_src.decode("utf-8-sig"), "software_control_tools.py", "exec")
        check("★ software_control_tools 指纹一致（第二个入口的复用真进包了）",
              _fingerprint(_co_src) == _fingerprint(_load(za, "software_control_tools")))

    if "tools" in names:
        _co_in = _load(za, "tools")
        _src = (ROOT / "tools.py").read_bytes()
        _co_src = compile(_src.decode("utf-8-sig"), "tools.py", "exec")
        check("★ tools 字节码指纹一致（命令层两条拦截模式真进包了）",
              _fingerprint(_co_src) == _fingerprint(_co_in))
        _tags = _deep_str_consts(_co_in)
        check("★ 包内 tools 含两条关键进程拦截标签（taskkill / Stop-Process）",
              "终止系统关键进程 (taskkill)" in _tags
              and "终止系统关键进程 (Stop-Process)" in _tags,
              "拦截标签不在包内常量表 → 文本入口没编进去")

        print("\n-- 3x) v4.214.0 钉子：证据库入库脱敏 --")
    if "evidence" in names:
        _evn = _code_names(_load(za, "evidence"))
        check("★ evidence 定义 _mask_secrets（入库脱敏）",
              "_mask_secrets" in _evn, "证据脱敏能力丢失")
        _ev_consts = _str_consts(_load(za, "evidence"))
        check("★ evidence 含 *** 打码标记常量",
              "***" in _ev_consts, "打码标记常量不在包内")
        check("★ evidence 含 Cookie 头脱敏规则（cookie 字样）",
              any("cookie" in c.lower() for c in _ev_consts), "Cookie 脱敏规则不在包内")

    print("\n-- 3y) v4.213.0 钉子：先执行后标记 + 记忆元数据 --")
    if "memory_store" in names:
        _msn = _code_names(_load(za, "memory_store"))
        check("★ memory_store 定义 _meta_line（元数据行拼接）",
              "_meta_line" in _msn, "记忆元数据落库能力丢失")
        check("★ memory_store 定义 entry_is_expired（过期过滤）",
              "entry_is_expired" in _msn, "过期事实过滤能力丢失")
        _ms_consts = _str_consts(_load(za, "memory_store"))
        check("★ memory_store 含 [元数据] 前缀常量（真正落盘的行格式）",
              "[元数据] " in _ms_consts, "元数据行格式常量不在包内")
    if "ui" in names:
        _uin2 = _code_names(_load(za, "ui"))
        check("★ ui 保留 _fire_automation_run（先执行后标记主体）",
              "_fire_automation_run" in _uin2, "自动化执行入口丢失")
        print("\n-- 3w) v4.215.0 钉子：自动化独立会话 + 独立工具权限 --")
    if "automation" in names:
        _atn = _code_names(_load(za, "automation"))
        check("★ automation 定义 filter_tools_safe（受限工具集）",
              "filter_tools_safe" in _atn, "自动化工具过滤能力丢失")
        _at_consts = _str_consts(_load(za, "automation"))
        check("★ automation 含 full_tools 字段常量（任务级逃生口）",
              "full_tools" in _at_consts, "full_tools 任务字段丢失")
        check("★ automation 含 auto_ 会话前缀常量（专属会话 sid）",
              "auto_" in _at_consts, "专属会话前缀丢失")
    if "ui" in names:
        _uin3 = _code_names(_load(za, "ui"))
        check("★ ui 定义 _automation_session（get-or-create 专属会话）",
              "_automation_session" in _uin3, "独立会话入口丢失")
        check("★ ui 含 _auto_task_active（受限工具标记）",
              "_auto_task_active" in _uin3, "自动化权限标记丢失")
    if "automation_panel" in names:
        _apn = _code_names(_load(za, "automation_panel"))
        check("★ automation_panel 含 full_tools_chk（UI 逃生口复选框）",
              "full_tools_chk" in _apn, "执行类工具复选框丢失")

    print("\n-- 3z) v4.212.0 反向钉子：画布模块**不得**出现在包里 --")
    _CANVAS_MODS = ("canvas_graph", "canvas_panel", "canvas_export",
                    "executors", "agnes_bridge", "image_local_edit")
    for _cm in _CANVAS_MODS:
        check(f"★ 包内不含已移除的画布模块 {_cm}",
              _cm not in names, "仍在包里 → spec/源码没清干净（死代码进包）")
    if "ui" in names:
        _uin = _code_names(_load(za, "ui"))
        check("★ ui 不再引用 _build_canvas_page（入口已摘）",
              "_build_canvas_page" not in _uin, "入口残留 → 切页 ModuleNotFoundError")
        check("★ ui 不再引用 CanvasPanel",
              "CanvasPanel" not in _uin, "懒导入残留 → 同上")
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
