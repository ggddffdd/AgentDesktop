# -*- coding: utf-8 -*-
"""G4 第二步判据：系统关键进程黑名单 → 硬 deny（三个入口）。

来源：SYSTEM_CONTROL_INVENTORY_REPORT.md 的 G4。第一步（`/IM` 影响面计数）已随
v4.211.6 落地；本套件守第二步 —— 把「一句 process_kill("lsass.exe") 就能让机器
蓝屏」这条路彻底堵死：

  入口 1  system_control_tools.tool_process_kill   （结构化工具，主入口）
  入口 2  software_control_tools.tool_app_kill     （同样走 taskkill /IM）
  入口 3  tools._dangerous_command_check           （run_command 的文本入口）

四条口径（每条都能被扰动单独翻红）：
  · 命中名单 → 直接拒绝，且**不 spawn 任何子进程**（不是「弹确认」，是连确认都没有）；
  · 归一化：带不带 .exe、大小写、前后空白都算命中；
  · PID 路径也要过（先只读反查镜像名）—— 否则 process_list 拿到 lsass 的 PID 再杀
    就绕过去了；反查失败 / 不是关键进程 → 放行（不误伤普通 PID 终止）；
  · 单源：判定与文案只在 system_control_tools 一份，swc 复用（把名单换成空集能让
    两个入口的行为同时改变 = 证明没抄第二份）。

零副作用：sct / swc 的 subprocess 全部换成记录型假实现。任何一条判据都不会真的
杀进程，也不会真的调 tasklist。

扰动用环境变量指向变异副本：SCT_PATH / SWC_PATH / TOOLS_PATH。
用法：python tests/test_critical_proc_deny.py
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


def _load_override(env_key, modname):
    """按环境变量把某模块换成变异副本（扰动用）；未设则什么都不做。"""
    p = os.environ.get(env_key)
    if not p:
        return
    spec = importlib.util.spec_from_file_location(modname, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)


_load_override("SCT_PATH", "system_control_tools")
_load_override("SWC_PATH", "software_control_tools")
_load_override("TOOLS_PATH", "tools")

import system_control_tools as sct      # noqa: E402
import software_control_tools as swc    # noqa: E402
import tools as T                       # noqa: E402

APP_DIR = ROOT

EXPECT = ("lsass.exe", "csrss.exe", "winlogon.exe", "wininit.exe", "smss.exe",
          "services.exe", "svchost.exe", "explorer.exe", "dwm.exe")


# ===========================================================================
# 零副作用隔离层
# ===========================================================================
class _Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class FakeSP:
    """记录型假 subprocess：run/Popen 都不真起进程。"""

    def __init__(self):
        self.runs = []
        self.run_result = _Completed()

    def run(self, cmd, **kw):
        self.runs.append((list(cmd), kw))
        return self.run_result

    def Popen(self, argv, **kw):
        self.runs.append((list(argv), kw))
        return type("_P", (), {"pid": 1})()


SP = FakeSP()
sct.subprocess = SP
swc.subprocess = SP


def _kill(args):
    SP.runs.clear()
    SP.run_result = _Completed(returncode=0)
    out, _assets, _x = sct.tool_process_kill(None, APP_DIR, args)
    return out


def _app_kill(args):
    SP.runs.clear()
    SP.run_result = _Completed(returncode=0)
    out, _assets, _x = swc.tool_app_kill(None, APP_DIR, args)
    return out


def _src(env_key, default):
    p = os.environ.get(env_key) or os.path.join(ROOT, default)
    return open(p, encoding="utf-8").read()


def _strip_comments(src):
    """剥掉整行注释（静态断言不能被注释骗 —— 本仓已踩过两次）。"""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


# ===========================================================================
# A 组 名单与归一化判定
# ===========================================================================
def part_a():
    print("== A 组 名单与归一化 ==")
    names = set(sct.CRITICAL_PROCESS_NAMES)
    check("★ 名单覆盖 9 个系统关键进程（少一个就是缺口）", set(EXPECT) <= names,
          f"缺={sorted(set(EXPECT) - names)}")
    check("★ 名单每项都是小写 .exe（归一化后可直接与 _critical_process_hit 的返回比）",
          all(n == n.lower() and n.endswith(".exe") for n in names),
          f"异常={[n for n in sorted(names) if not (n == n.lower() and n.endswith('.exe'))]}")
    check("★ 名单是 frozenset（防误改，且可做等值比较）",
          isinstance(sct.CRITICAL_PROCESS_NAMES, frozenset),
          f"type={type(sct.CRITICAL_PROCESS_NAMES).__name__}")

    check("★ 命中：'lsass.exe'（标准写法）",
          sct._critical_process_hit("lsass.exe") == "lsass.exe")
    check("★ 命中：'LSASS.EXE'（大小写不敏感）",
          sct._critical_process_hit("LSASS.EXE") == "lsass.exe")
    check("★ 命中：'lsass'（无扩展名 → 补 .exe 后命中）",
          sct._critical_process_hit("lsass") == "lsass.exe")
    check("★ 命中：' lsass.exe '（前后空白）",
          sct._critical_process_hit(" lsass.exe ") == "lsass.exe")
    check("★ 命中：'Svchost'（混写 + 无扩展名）",
          sct._critical_process_hit("Svchost") == "svchost.exe")

    check("★ 不误伤：'notepad.exe'", sct._critical_process_hit("notepad.exe") is None)
    check("★ 不误伤：'lsassx.exe'（前缀相同不算命中）",
          sct._critical_process_hit("lsassx.exe") is None)
    check("★ 不误伤：'tasklist.exe'（只读工具本身）",
          sct._critical_process_hit("tasklist.exe") is None)
    check("★ 不误伤：空串 / None / 纯数字 PID 都不命中且不抛",
          sct._critical_process_hit("") is None
          and sct._critical_process_hit(None) is None
          and sct._critical_process_hit("716") is None)


# ===========================================================================
# B 组 入口 1：tool_process_kill
# ===========================================================================
def part_b():
    print("== B 组 tool_process_kill ==")
    out = _kill({"name": "lsass.exe"})
    check("★ process_kill('lsass.exe') 直接拒绝",
          "已拒绝" in (out or "") and "lsass.exe" in (out or ""), f"out={out!r}")
    check("★ …且零 spawn（是硬拒绝，不是「弹确认」）", SP.runs == [], f"runs={SP.runs}")
    check("★ 拒绝文案点明后果（系统不稳/重启/蓝屏）",
          any(k in (out or "") for k in ("强制重启", "蓝屏", "系统不稳")), f"out={out!r}")

    _kill({"name": "lsass.exe", "force": True})
    check("★ force=True 不是逃生口（照样拒绝且零 spawn）",
          SP.runs == [], f"runs={SP.runs}")

    out = _kill({"name": "LSASS"})
    check("★ 'LSASS'（大写、无扩展名）也拒绝", "已拒绝" in (out or ""), f"out={out!r}")

    missed = []
    for n in EXPECT:
        o = _kill({"name": n})
        if "已拒绝" not in (o or "") or SP.runs:
            missed.append(n)
    check("★ 9 个名单成员逐个实测：全部拒绝且零 spawn", not missed, f"漏={missed}")

    # ---- PID 路径：反查镜像名 ----
    SP.runs.clear()
    SP.run_result = _Completed(stdout='"lsass.exe","716","Services","0","1,234 K"',
                               returncode=0)
    out, _a, _x = sct.tool_process_kill(None, APP_DIR, {"name": "716"})
    check("★ PID 路径反查镜像名：'716' 实为 lsass.exe → 拒绝",
          "已拒绝" in (out or ""), f"out={out!r}")
    check("★ …且没走到 taskkill（只调了 tasklist 做反查）",
          bool(SP.runs) and all(c[0] == "tasklist" for c, _ in SP.runs),
          f"runs={[c for c, _ in SP.runs]}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout='"notepad.exe","1234","Console","1","1 K"',
                               returncode=0)
    out, _a, _x = sct.tool_process_kill(None, APP_DIR, {"name": "1234"})
    check("★ 普通 PID（notepad.exe）不被误伤，照常终止",
          "已终止" in (out or ""), f"out={out!r}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout="信息: 没有运行的任务匹配指定标准。", returncode=1)
    out, _a, _x = sct.tool_process_kill(None, APP_DIR, {"name": "999999"})
    check("★ 反查不到（PID 不存在）→ 不误判为关键进程，照常走终止流程",
          "已拒绝" not in (out or ""), f"out={out!r}")

    # ---- 普通进程名照旧 ----
    out = _kill({"name": "notepad.exe"})
    check("★ 普通进程名 'notepad.exe' 不被名单误伤（走原 /IM 路径）",
          "已终止" in (out or ""), f"out={out!r}")


# ===========================================================================
# C 组 入口 2：tool_app_kill（复用同一份判定）
# ===========================================================================
def part_c():
    print("== C 组 tool_app_kill ==")
    out = _app_kill({"target": "lsass.exe"})
    check("★ app_kill('lsass.exe') 直接拒绝", "已拒绝" in (out or ""), f"out={out!r}")
    check("★ …且零 spawn", SP.runs == [], f"runs={SP.runs}")

    out = _app_kill({"target": "explorer"})
    check("★ app_kill('explorer')（无扩展名，原逻辑会补 .exe）→ 拒绝",
          "已拒绝" in (out or ""), f"out={out!r}")

    SP.runs.clear()
    SP.run_result = _Completed(stdout='"lsass.exe","716","Services","0","1 K"',
                               returncode=0)
    out, _a, _x = swc.tool_app_kill(None, APP_DIR, {"target": "716"})
    check("★ app_kill 的 PID 路径也过名单", "已拒绝" in (out or ""), f"out={out!r}")

    out = _app_kill({"target": "notepad.exe"})
    check("★ app_kill 普通目标不被误伤", "已拒绝" not in (out or ""), f"out={out!r}")


# ===========================================================================
# D 组 入口 3：命令层文本入口
# ===========================================================================
def part_d():
    print("== D 组 命令层 _dangerous_command_check ==")
    denied = T._dangerous_command_check
    bad = [n for n in EXPECT if not denied("taskkill /IM " + n + " /F")]
    check("★ 命令层：taskkill /IM <9 个关键进程> 全部被拦", not bad, f"漏={bad}")
    check("★ 命令层：参数顺序无关（/F 在前也拦）",
          bool(denied("taskkill /F /IM svchost.exe")))
    check("★ 命令层：Stop-Process -Name lsass 也被拦（PowerShell 同一条路）",
          bool(denied("Stop-Process -Name lsass -Force")))
    check("★ 命令层：Stop-Process -Id 716 不拦（精确形式，逃生口）",
          denied("Stop-Process -Id 716 -Force") is None)
    check("★ 命令层不误伤：taskkill /IM notepad.exe /F",
          denied("taskkill /IM notepad.exe /F") is None)
    check("★ 命令层不误伤：taskkill /PID 716 /F（PID 精确形式是逃生口）",
          denied("taskkill /PID 716 /F") is None)
    check("★ 命令层不误伤：tasklist /FI \"IMAGENAME eq lsass.exe\"（只读查询）",
          denied('tasklist /FI "IMAGENAME eq lsass.exe"') is None)
    check("★ 命令层不误伤：日常命令",
          all(denied(c) is None
              for c in ("dir", "git status", "rm -rf build", "python -m pytest")),
          "有日常命令被误拦")

    # 双事实源：命令层是**正则字面量**，没法 import sct 的名单，所以必须有一条判据
    # 把两处的名字集合钉死 —— 否则「改了 sct 名单忘了改正则」会静默漂移。
    _pats = [(p, lb) for p, lb in T._DANGEROUS_CMD_PATTERNS if "系统关键进程" in lb]
    check("★ 命令层恰好 2 条关键进程模式（taskkill / Stop-Process）", len(_pats) == 2,
          f"n={len(_pats)}")
    _names = set()
    for _p, _lb in _pats:
        _g = re.search(r"\(\?:([\w|]+)\)", _p)
        if _g:
            _names |= set(_g.group(1).split("|"))
    _expect = set(n[:-4] for n in sct.CRITICAL_PROCESS_NAMES)
    check("★ 命令层的名字集合与 sct 的 CRITICAL_PROCESS_NAMES 完全一致（防两处漂移）",
          _names == _expect, f"cmd={sorted(_names)} sct={sorted(_expect)}")


# ===========================================================================
# E 组 单源 + 源码契约
# ===========================================================================
def part_e():
    print("== E 组 单源与源码契约 ==")
    saved = sct.CRITICAL_PROCESS_NAMES
    try:
        sct.CRITICAL_PROCESS_NAMES = frozenset()
        o1 = _kill({"name": "lsass.exe"})
        o2 = _app_kill({"target": "lsass.exe"})
    finally:
        sct.CRITICAL_PROCESS_NAMES = saved
    check("★ 单源：清空 sct 的名单 → 两个入口同时不再拒绝（swc 读的就是这一份）",
          "已拒绝" not in (o1 or "") and "已拒绝" not in (o2 or ""),
          f"o1={o1!r} o2={o2!r}")
    check("★ swc 模块级没有第二份名单（防双事实源漂移）",
          not hasattr(swc, "CRITICAL_PROCESS_NAMES"))
    check("★ 名单恢复后行为复原（上面的 monkeypatch 没有污染后续判据）",
          sct._critical_process_hit("lsass.exe") == "lsass.exe")

    code = _strip_comments(_src("SCT_PATH", "system_control_tools.py"))
    # 顺序断言必须限定在 tool_process_kill 的**函数体**内，且用 AST 取行号 ——
    # 文本版（整个文件 find "subprocess.run(cmd"）被文件里更早的 process_list 段
    # 骗了一次：那个 22422 的命中根本不是本函数的 spawn（本仓第二次栽在
    # 「文本断言命中同名前缀」上）。
    import ast as _ast
    _tree = _ast.parse(_src("SCT_PATH", "system_control_tools.py"))
    _fn = None
    for _n in _tree.body:
        if isinstance(_n, _ast.FunctionDef) and _n.name == "tool_process_kill":
            _fn = _n
    _deny_ln = _spawn_ln = None
    if _fn is not None:
        for _node in _ast.walk(_fn):
            if (isinstance(_node, _ast.Assign)
                    and any(isinstance(_t, _ast.Name) and _t.id == "_deny"
                            for _t in _node.targets)):
                _deny_ln = _node.lineno
            if (isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Attribute)
                    and _node.func.attr == "run"
                    and isinstance(_node.func.value, _ast.Name)
                    and _node.func.value.id == "subprocess"):
                _spawn_ln = (_node.lineno if _spawn_ln is None
                             else min(_spawn_ln, _node.lineno))
    check("★ 源码契约：tool_process_kill 内先做 deny 判定、后 spawn（顺序不许被挪）",
          _fn is not None and _deny_ln is not None and _spawn_ln is not None
          and _deny_ln < _spawn_ln,
          f"fn={_fn is not None} deny@{_deny_ln} spawn@{_spawn_ln}")
    check("★ 源码契约：判定与文案收在一个 helper 里（不是散在函数里现写）",
          code.count("def _critical_process_deny(name):") == 1
          and code.count("_critical_process_deny(") == 2,
          f"def={code.count('def _critical_process_deny(name):')} "
          f"call={code.count('_critical_process_deny(')}")

    swc_code = _strip_comments(_src("SWC_PATH", "software_control_tools.py"))
    check("★ 源码契约：swc 从 sct 取判定（自带 import，不抄第二份）",
          "from system_control_tools import _critical_process_deny" in swc_code)

    t_code = _strip_comments(_src("TOOLS_PATH", "tools.py"))
    check("★ 源码契约：命令层两条模式都在（taskkill + Stop-Process）",
          "终止系统关键进程 (taskkill)" in t_code
          and "终止系统关键进程 (Stop-Process)" in t_code)


def _run_part(fn):
    """逐组兜异常：一组炸掉不影响其它组，且异常变成一条 FAIL（不是静默跳过）。"""
    try:
        fn()
    except Exception as e:                                    # noqa: BLE001
        check(f"★ {fn.__name__} 整组未抛异常", False,
              f"{type(e).__name__}: {e}")


def main():
    for fn in (part_a, part_b, part_c, part_d, part_e):
        _run_part(fn)
    print(f"\n==== 关键进程黑名单结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
