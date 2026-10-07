# -*- coding: utf-8 -*-
"""v4.219.0 进包核验：exec_tool 最终权限闸门（审查报告 P1 #4）真进包。

本轮改动：tools.py 新增 `_permission_gate` 作为不可绕过的最终闸门（无合法
权限上下文时按风险 fail-closed，READ 放行、其余拒绝）；agent.py 3 处
exec_tool 调用 + 并发段透传 perm_ctx；agent_node.py 军团直调补兜底决策；
test_software_control_2b.py 的 app_kill 直调带 Decision 授权。

核验重点：① 版本号 v4.219.0 且不含 v4.218.0；② tools 字节码指纹一致
（闸门逻辑真进包，不是改了个寂寞）；③ 包内 tools 含 `_permission_gate`
与 fail-closed 拒绝串；④ 复用关键历史钉子（系统控制 15 tool_* / 画布反向 /
测试不随包）防重打包回归。
"""
import hashlib
import marshal
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"

MODULES = ["tools", "agent", "agent_node", "risk", "permissions",
           "system_control_tools", "software_control_tools", "config"]

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
        return ("<frozenset>", tuple(sorted((repr(_prim(v)) for v in x))))
    if isinstance(x, types.CodeType):
        return ("<code>", x.co_name)
    return f"<{type(x).__name__}>"


def _fingerprint(co):
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


def _code_names(co):
    out = set()
    for c in _code_iter(co):
        out |= set(c.co_names)
        out.add(c.co_name)
    return out


def _func_codes(co, prefix):
    return {c.co_name: c for c in _code_iter(co) if c.co_name.startswith(prefix)}


def _load(za, name):
    got = za.extract(name)
    return got if isinstance(got, types.CodeType) else marshal.loads(got)


def _load_entry_script(exe: Path, name: str = "main"):
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
    print("v4.219.0 进包核验（exec_tool 最终权限闸门真进包 + 关键历史钉子复验）")
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
        co_src = compile(src.decode("utf-8-sig"), f"{mod}.py", "exec")
        co_pkg = _load(za, mod)
        same = _fingerprint(co_src) == _fingerprint(co_pkg)
        check(f"{mod} 字节码指纹一致（包内 == 当前源码）", same,
              f"src={_fingerprint(co_src)[:12]} pkg={_fingerprint(co_pkg)[:12]}")

    print("\n-- 2) 版本一致性 --")
    if "config" in names:
        consts = _str_consts(_load(za, "config"))
        check("PYZ 内 config 版本常量 == v4.219.0",
              "v4.219.0" in consts, f"包内版本串={sorted(s for s in consts if s.startswith('v4.21'))}")
        check("PYZ 内不含上一版旧版本常量 v4.218.0",
              "v4.218.0" not in consts, "残留旧版本串（可能是增量打包旧模块）")
    else:
        check("config 在 PYZ 里", False, "缺失 → 启动崩")

    print("\n-- 3) 本轮钉子：exec_tool 最终权限闸门真进包 --")
    if "tools" in names:
        co_tools = _load(za, "tools")
        tnames = _code_names(co_tools)
        check("★ 包内 tools 定义 _permission_gate（最终闸门函数编进去了）",
              "_permission_gate" in tnames, "_permission_gate 不在包内 co_names → 闸门逻辑没编进去")
        tt = _deep_str_consts(co_tools)
        check("★ 包内含 fail-closed 拒绝串（无 ctx 即拒绝执行）",
              any("exec_tool 需要权限上下文" in s for s in tt),
              "拒绝串不在包内常量表 → 文本入口没编进去")
        # `RiskClass.READ` 在字节码里是 LOAD_GLOBAL(RiskClass) + LOAD_ATTR(READ)：
        # RiskClass 进 co_names，READ 是属性名（不在字符串常量表）。故只验 RiskClass 引用。
        check("★ 包内 tools 引用 RiskClass（fail-closed 分支按风险分类）",
              "RiskClass" in _code_names(co_tools),
              "RiskClass 引用缺失 → 分类判定逻辑没编进去")
        # exec_tool 必须接受 perm_ctx 形参（否则 agent 透传进不去）
        exec_co = _func_codes(co_tools, "exec_tool").get("exec_tool")
        if exec_co is not None:
            check("★ exec_tool 形参表含 perm_ctx（调用方透传链路打通）",
                  "perm_ctx" in exec_co.co_varnames, "缺 perm_ctx 形参 → agent 的透传被静默丢弃")
        else:
            check("★ 包内 tools 存在 exec_tool", False, "入口函数没了")
    else:
        check("tools 在 PYZ 里", False, "缺失 → 全流程崩")

    print("\n-- 3b) 关键历史钉子：系统控制 15 tool_* 仍随包 --")
    if "system_control_tools" in names:
        co_sc = _load(za, "system_control_tools")
        scn = _code_names(co_sc)
        tools_co = _func_codes(co_sc, "tool_")
        n_tools = len(tools_co)
        _want15 = {"screenshot", "mouse_move", "mouse_click", "mouse_scroll",
                   "keyboard_type", "keyboard_press", "clipboard_read",
                   "clipboard_write", "window_list", "window_focus",
                   "window_get_info", "process_list", "process_kill",
                   "process_start", "clean_recycle_bin"}
        lack = sorted(n for n, c in tools_co.items()
                      if not {"progress", "stop_event", "should_stop"} <= set(c.co_varnames))
        check(f"system_control 共 15 个 tool_*（实际 {n_tools}）", n_tools == 15,
              f"漏={sorted(_want15 - set(tools_co))}")
        check("★ 全部 15 个 tool_* 形参表都含 should_stop", not lack, f"缺形参的={lack}")
        sct_consts = _str_consts(co_sc)
        check("★ clean_recycle_bin 工具 + 路由表登记都在包内",
              "tool_clean_recycle_bin" in scn and "clean_recycle_bin" in sct_consts,
              "清空回收站真实工具没编进去")
        check("★ _resolve_save_path 含越界回落默认目录护栏（v4.218 截图路径围栏）",
              any("越界被拒" in s for s in sct_consts), "save_path 越界不拦")
        check("★ 关键进程黑名单 9 名仍在包内常量表",
              {"lsass.exe", "csrss.exe", "winlogon.exe", "wininit.exe", "smss.exe",
               "services.exe", "svchost.exe", "explorer.exe", "dwm.exe"} <= sct_consts,
              "关键进程名单不全 → 系统保护缺口")
    else:
        check("system_control_tools 在 PYZ 里", False, "缺失 → 系统控制工具全不可用")

    print("\n-- 3c) 关键历史钉子：画布模块不得随包 + 测试不随包 --")
    for _cm in ("canvas_graph", "canvas_panel", "canvas_export", "executors"):
        check(f"★ 包内不含已移除画布模块 {_cm}", _cm not in names, "死代码进包")
    leaked = sorted(n for n in names
                    if n.startswith("test_") or "perturb" in n or "pep701" in n)
    check("PYZ 里没有 tests/ 判据套件与根目录扰动脚本",
          not leaked, f"泄漏的模块={leaked}")

    co_main = _load_entry_script(EXE, "main")
    check("main 从 CArchive 取到（入口脚本不在 PYZ 是正常结构）", co_main is not None)

    print(f"  PYZ 模块数：{len(names)}")
    print("\n" + "=" * 62)
    print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
