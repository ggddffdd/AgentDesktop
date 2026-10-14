# -*- coding: utf-8 -*-
"""v4.267.0 结构治理第一刀：路由外移 + 根目录分组。

守的是「搬了、而且接线还在」，不是新行为：
  R  复杂度 / 工具意图 / 拒绝调工具的实现在 ui_routing，ChatWindow 只转发
  S  ui.py 行数仍低于红线，词表没有被抄回类方法
  L  扰动脚本在 _perturb/，HANDOVER / RELEASE 过程文档进 docs/，
     cleanup_products 不与 tools.py 抢 tools/ 这个名字
  B  薄包装的行为与外移前一致（寒暄不升舱、工具句仍命中、讨论句不强制）

改坏任何一条都应红。配套扰动：_perturb/_perturb_struct_267.py。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def _read(rel):
    path = os.path.join(ROOT, rel)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return f.read()


def _func_src(rel, name):
    src = _read(rel)
    tree = ast.parse(src)
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == name:
            return ast.get_source_segment(src, fn) or ""
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == name:
                return ast.get_source_segment(src, fn) or ""
    return ""


ROUTING_FUNCS = (
    "_is_complex", "_is_complex_v1",
    "_complexity_reason", "_complexity_reason_v1",
    "_needs_tool_intent", "_user_refuses_tools",
    "_looks_like_learning_question", "_looks_like_praise",
)

# ChatWindow 方法名 → 它必须调用的 ui_routing 入口
DELEGATES = {
    "_is_complex": "ui_routing._is_complex(",
    "_is_complex_v1": "ui_routing._is_complex_v1(",
    "_complexity_reason": "ui_routing._complexity_reason(",
    "_complexity_reason_v1": "ui_routing._complexity_reason_v1(",
    "_needs_tool_intent": "ui_routing._needs_tool_intent(",
    "_user_refuses_tools": "ui_routing._user_refuses_tools(",
    "_looks_like_learning_question": "ui_routing._looks_like_learning_question(",
    "_looks_like_praise": "ui_routing._looks_like_praise(",
}


def part_r_routing():
    print("\n-- R) 路由纯逻辑在 ui_routing，ChatWindow 薄包装 --")
    routing_path = os.path.join(ROOT, "ui_routing.py")
    check("R1 ui_routing.py 存在", os.path.isfile(routing_path))
    if not os.path.isfile(routing_path):
        for name in ROUTING_FUNCS:
            check("R2 模块级函数 " + name, False, "文件不存在")
        return
    src = _read("ui_routing.py")
    try:
        ast.parse(src)
        check("R1b ui_routing.py 可解析", True)
    except SyntaxError as e:
        check("R1b ui_routing.py 可解析", False, repr(e))
    for name in ROUTING_FUNCS:
        body = _func_src("ui_routing.py", name)
        # 必须是模块级：类方法外移后，源码段不以缩进 def 开头
        check("R2 模块级 " + name,
              body.startswith("def " + name + "("),
              (body.splitlines() or ["<缺失>"])[0][:80])
    nt = _func_src("ui_routing.py", "_needs_tool_intent")
    check("R3 词表在 ui_routing._needs_tool_intent（不是留在类里的空壳）",
          "KEYWORDS" in nt and '"写文件"' in nt and "is_non_action_message" in nt)
    cx = _func_src("ui_routing.py", "_is_complex")
    check("R4 复杂度走 route_judge v2，并保留 route_complex_v1 回退",
          "route_judge.is_complex_v2(" in cx and 'cfg.get("route_complex_v1")' in cx)
    for name, needle in DELEGATES.items():
        body = _func_src("ui.py", name)
        check("R5 ChatWindow.%s 转发" % name, needle in body, (body or "<缺失>")[:120])
        # 文档里提到 route_judge 这个名字不算把实现抄回来。
        # 真正的实现是 import 和 is_complex_v2 调用，那两样必须留在 ui_routing。
        check("R6 ChatWindow.%s 不再内嵌词表/回退实现" % name,
              "KEYWORDS" not in body and "import route_judge" not in body
              and "is_complex_v2(" not in body,
              "薄包装里不该再出现实现")


def part_s_size():
    print("\n-- S) ui.py 体积红线 --")
    n = len(_read("ui.py").splitlines())
    check("S1 ui.py < 10000 行", n < 10000, "当前 %d" % n)
    spec = _read("小臭玩AI.spec")
    check("S2 spec hiddenimports 登记 ui_routing", "'ui_routing'" in spec)


def part_l_layout():
    print("\n-- L) 根目录分组 --")
    import run_all as R
    found = R.discover_perturb()
    check("L1 discover_perturb 找到扰动脚本", len(found) >= 90,
          "只找到 %d" % len(found))
    bad_parent = [p.name for p in found if p.parent.name != "_perturb"]
    check("L2 扰动脚本都在 _perturb/ 下", not bad_parent, str(bad_parent[:5]))
    root_left = sorted(
        n for n in os.listdir(ROOT)
        if n.startswith("_perturb_") and n.endswith(".py"))
    check("L3 仓库根目录不再放 _perturb_*.py", not root_left, str(root_left[:5]))
    guard = os.path.join(ROOT, "_perturb", "_perturb_guard.py")
    check("L4 护栏在 _perturb/_perturb_guard.py", os.path.isfile(guard))
    check("L5 run_all.PERTURB_GUARD 指向新路径",
          os.path.normpath(str(R.PERTURB_GUARD)) == os.path.normpath(guard),
          str(R.PERTURB_GUARD))
    gi = _read(".gitignore")
    check("L6 .gitignore 反选 _perturb/ 目录（父目录被 _* 忽略时必须先解开）",
          "!_perturb/" in gi)
    hand = os.path.join(ROOT, "docs", "handover")
    rels = os.path.join(ROOT, "docs", "release")
    for name in ("HANDOVER_20261011_v4254.md",
                 "HANDOVER_20261011_v4255.md",
                 "HANDOVER_20261011_v4256.md"):
        check("L7 %s 在 docs/handover" % name,
              os.path.isfile(os.path.join(hand, name))
              and not os.path.isfile(os.path.join(ROOT, name)))
    for name in ("RELEASE_NOTES_v4.107.md", "RELEASE_NOTES_v4.108.md"):
        check("L8 %s 在 docs/release" % name,
              os.path.isfile(os.path.join(rels, name))
              and not os.path.isfile(os.path.join(ROOT, name)))
    check("L9 cleanup_products 在 scripts/（不与 tools.py 抢名）",
          os.path.isfile(os.path.join(ROOT, "scripts", "cleanup_products.py"))
          and not os.path.isfile(os.path.join(ROOT, "tools", "cleanup_products.py")))
    pc = _read("precommit_check.py")
    # 不能只搜「_perturb」：护栏文件名 `_perturb_guard` 自己就含这段前缀，
    # 搬家前这条会恒绿。必须看到把目录放进 sys.path 的那一行。
    check("L10 precommit 把 _perturb 目录放进 sys.path",
          'os.path.join(HERE, "_perturb")' in pc)


def part_b_behavior():
    print("\n-- B) 薄包装行为与外移前一致 --")
    try:
        import ui
    except Exception as e:
        check("B0 能导入 ui", False, repr(e))
        return
    W = ui.ChatWindow.__new__(ui.ChatWindow)
    W.cfg = {}
    routing = {"complex_hint": ["代码", "分析", "报告", "设计"], "length_threshold": 1500}
    sys_msg = {"role": "system", "content": "你可以写代码、做分析、出报告、做设计。" * 30}
    simple = [sys_msg, {"role": "user", "content": "你好"}]
    complex_ = [sys_msg, {"role": "user", "content": "帮我把路由层重构一下并出个分析报告"}]
    check("B1 简单寒暄不判复杂", W._is_complex(simple, routing) is False)
    check("B2 真实复杂需求仍判复杂", W._is_complex(complex_, routing) is True)
    check("B3 v1 仍会把带关键词的 system 判成复杂",
          W._is_complex_v1(simple, routing) is True)
    check("B4 工具句仍命中",
          W._needs_tool_intent([{"role": "user", "content": "帮我删掉昨天的临时文件"}]) is True)
    check("B5 讨论句不强制工具",
          W._needs_tool_intent([{"role": "user", "content": "为什么会自动搜索？"}]) is False)
    refused = [
        {"role": "user", "content": "不要用工具"},
        {"role": "user", "content": "写文件", "_internal": True},
    ]
    check("B6 拒绝调工具不被 _internal 顶掉",
          W._user_refuses_tools(refused) is True)


def main():
    print("v4.267.0 结构治理判据")
    part_r_routing()
    part_s_size()
    part_l_layout()
    part_b_behavior()
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
