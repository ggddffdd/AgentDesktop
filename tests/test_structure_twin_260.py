# -*- coding: utf-8 -*-
r"""v4.260.0 常驻判据：结构性双份缺陷**不许新增**（MRO 遮蔽 / 判据测影子 / 工具注册实现唯一）。

起因：v4.258.0（P1-6）和 v4.259.0 同族扫描连续撞到**同一类结构性缺陷**：

  ① **MRO 遮蔽**：`AgentWorker(AgentTaskMixin, …, AgentResultMixin, …)` 里
     `_tool_outcome` 在**两个基类都有定义** → MRO 里 Task 在前，Result 那份是
     **死代码**，运行时永远不会执行。
  ② **判据测影子**：`test_toolresult_wiring_226.py` 的桩只继承 `AgentResultMixin`
     → 44 条判据全打在那份死代码上。判据全绿，真 bug 零覆盖。
  ③ **工具注册实现唯一**：`tool_screenshot` 在 `tools.py` 和
     `system_control_tools.py` 各有一份**完整实现**，注册的是后者 → 前者是死代码，
     而且两份**契约漂移**（`mode` vs `region`/`window_title`），死代码成了误导源。

这三条的共同点：**代码看着有、测试看着绿、运行时不是那么回事**。
靠"下次撞见"防不住，所以做成**常驻判据**：存量列进 BASELINE 豁免（附原因），
**新增一个就红**。

两条额外保险（guard probe，防止判据自己变成空转）：
  · 扫描集必须有足够文件数 —— 探针坏了（比如路径过滤写错扫到 0 个文件）要红；
  · **基线项必须被扫到** —— 证明谓词非空（"没命中"是因为真的没有，
    不是因为扫描根本没工作）。这是「改坏必红」里最容易漏的一半。

豁免口径：**委托桩不算独立实现**（`def f(...): from x import f as _i; return _i(...)`）。
两条判据都认这个：函数体去掉 docstring 后 ≤3 条语句且含 `return <调用>`。
所以 v4.260.0 修完的 `tools.tool_screenshot` 与 v4.258.0 归一的两个 mixin
都不算"双份实现"。
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0

SKIP_DIRS = ("tests", "_dev_history", "_archive_", "dist", "backup_",
             "__pycache__", ".git", "_internal", "build")
# 开发脚手架：仓库约定"下划线开头的根脚本"不进交付包
SCAFFOLD_RE = re.compile(r"^(_perturb_|_dev_|_scan_|_probe_|_tmp_|_scratch_)")
NOISE_NAMES = {
    "__init__", "__repr__", "__str__", "__len__", "__iter__", "__enter__",
    "__exit__", "__call__", "__getitem__", "__setitem__", "__eq__", "__hash__",
    "__post_init__", "__await__", "__aenter__", "__aexit__",
    "run", "start", "stop", "close", "emit", "connect", "to_dict", "from_dict",
    "get", "set", "update", "reset", "clear", "main", "setup", "teardown",
}

# ---------- 存量豁免：新增一项就红 ----------
# 每项都带原因。若哪天把缺陷真修掉了，从表里删掉对应项（判据会要求它"不再出现"）。
BASELINE_MRO = {
    ("AgentWorker", "_tool_outcome"):
        "v4.258.0 已归一：两个 mixin 都委托到 tool_contract.tool_outcome，"
        "被遮蔽那份是同真源的桩，无害",
}
BASELINE_SHADOW = {
    ("test_toolresult_wiring_226.py", "_FakeWorker", "_tool_outcome"):
        "v4.258.0 归一后无害（桩仍只继承 AgentResultMixin，但两份已同一真源）",
}
# 工具注册实现唯一：**无豁免**（v4.260.0 P1-7 把最后一处改成委托桩了）


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  → " + str(extra)) if extra else ""))
    return bool(cond)


def iter_source_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if not any(d.startswith(s) for s in SKIP_DIRS)]
        for fn in filenames:
            if fn.endswith(".py") and not SCAFFOLD_RE.match(fn):
                out.append(os.path.join(dirpath, fn))
    return out


def is_delegate_stub(node):
    """委托桩：只有 import + `return <调用>`，没有自己的判定逻辑。"""
    body = [b for b in node.body
            if not (isinstance(b, ast.Expr)
                    and isinstance(getattr(b, "value", None), ast.Constant))]
    if not body or len(body) > 3:
        return False
    return any(isinstance(b, ast.Return) and isinstance(b.value, ast.Call)
               for b in body)


def collect():
    """返回 (文件数, 类基类表, 类方法表, 顶层函数表)。"""
    files = iter_source_files()
    class_bases, class_methods, top_funcs = {}, {}, {}
    n = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except Exception:
            continue
        n += 1
        rel = os.path.relpath(path, ROOT)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top_funcs.setdefault(node.name, []).append(
                    (rel, node.lineno, is_delegate_stub(node)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_bases.setdefault(node.name, [])
            for b in node.bases:
                if isinstance(b, ast.Name):
                    class_bases[node.name].append(b.id)
                elif isinstance(b, ast.Attribute):
                    class_bases[node.name].append(b.attr)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_methods.setdefault((node.name, sub.name), {}).setdefault(
                        rel, is_delegate_stub(sub))
    return n, class_bases, class_methods, top_funcs


def scan_mro(class_bases, class_methods):
    """多继承类里，同名方法在 ≥2 个基类都有定义 → 后面的被遮蔽（死代码）。"""
    m_of_cls = {}
    for (cls, m) in class_methods:
        m_of_cls.setdefault(m, set()).add(cls)
    out = {}
    for cls, bases in class_bases.items():
        if len(bases) < 2:
            continue
        for m, owners in m_of_cls.items():
            if m in NOISE_NAMES or m.startswith("__"):
                continue
            hit = [b for b in bases if b in owners]
            if len(hit) >= 2:
                out[(cls, m)] = (hit[0], hit[1:])
    return out


def scan_shadow_tests(mro):
    """tests/ 里的桩若继承「被遮蔽那份」→ 判据打在影子上。"""
    shadowed = {}
    for (cls, m), (_win, dead) in mro.items():
        for d in dead:
            shadowed.setdefault(d, []).append((cls, m))
    out = set()
    tdir = os.path.join(ROOT, "tests")
    if not os.path.isdir(tdir):
        return out
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(tdir, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            stub_bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            for base in stub_bases:
                for cls, m in shadowed.get(base, []):
                    if m in src:
                        out.add((fn, node.name, m))
    return out


def main():
    nfiles, class_bases, class_methods, top_funcs = collect()

    print("---- G 守卫探针：判据自己不许空转 ----")
    ck(nfiles >= 50, "G1 扫描集规模合理（探针没坏）", "实测只扫到 %d 个文件" % nfiles)
    print("      （扫描 %d 个交付源码文件）" % nfiles)

    mro = scan_mro(class_bases, class_methods)
    ck(("AgentWorker", "_tool_outcome") in mro,
       "G2 ★ MRO 遮蔽扫描**真的在干活**（基线项应被扫到，证明谓词非空）",
       "实际扫到 %d 处" % len(mro))

    print("\n---- M1 MRO 遮蔽：不许新增（被遮蔽那份是死代码）----")
    extra = {k: v for k, v in mro.items() if k not in BASELINE_MRO}
    for k in sorted(extra):
        print("      新增：%s.%s  生效=%s 死代码=%s" % (k[0], k[1], extra[k][0], extra[k][1]))
    ck(not extra, "M1 ★ 无新增 MRO 遮蔽（新增即红）", "新增 %d 处" % len(extra))

    print("\n---- M2 豁免项必须仍是「同真源的桩」（不许偷偷写回内联实现）----")
    for (cls, m), why in sorted(BASELINE_MRO.items()):
        _win, dead = mro.get((cls, m), (None, []))
        impls = class_methods.get((cls, m), {})
        for d in dead:
            sites = {rel: is_stub for (c2, m2), per in class_methods.items()
                     for rel, is_stub in per.items() if c2 == d and m2 == m}
            all_stub = bool(sites) and all(sites.values())
            ck(all_stub,
               "M2 %s.%s 的被遮蔽实现（%s）仍是委托桩" % (cls, m, d),
               "实测 %r" % sites)
            for _i in ():
                pass
        del impls, why

    print("\n---- C1 判据测影子：不许新增 ----")
    shadow = scan_shadow_tests(mro)
    extra_s = {s for s in shadow
               if s not in BASELINE_SHADOW}
    for s in sorted(extra_s):
        print("      新增：tests/%s  %s 测 %s" % s)
    ck(not extra_s, "C1 ★ 无新增判据测影子（新增即红）", "新增 %d 处" % len(extra_s))
    ck(bool(shadow), "C2 判据测影子扫描**真的在干活**（基线项应被扫到）",
       "实际扫到 %d 处" % len(shadow))

    print("\n---- T1 工具注册实现唯一：同名 tool_* 不许有 ≥2 份独立实现 ----")
    hits = {}
    for name, sites in sorted(top_funcs.items()):
        if not name.startswith("tool_"):
            continue
        real = [(rel, ln) for rel, ln, stub in sites if not stub]
        if len({r[0] for r in real}) >= 2:
            hits[name] = real
    for name, real in hits.items():
        print("      双份实现：%s → %s" % (name, real))
    ck(not hits, "T1 ★ 无同名 tool_* 双份独立实现（委托桩不算）",
       "命中 %d 个" % len(hits))

    ts = top_funcs.get("tool_screenshot", [])
    ck(len(ts) == 2 and any(stub for _r, _l, stub in ts)
       and any(not stub for _r, _l, stub in ts),
       "T2 ★ 守卫探针：tool_screenshot 应被识别为「1 份实现 + 1 份委托桩」",
       "实测 %r" % (ts,))

    print("\n" + "=" * 70)
    print("v4.260.0 结构性双份常驻判据：PASS=%d FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
