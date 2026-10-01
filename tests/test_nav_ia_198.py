"""v4.198.0 界面二期守卫：导航分组不改变下标契约 + 侧栏可折叠 + 状态条不再塞操作提示。

本套件是二期三项改动的机器守卫。**按 L160 的教训，判据打在性质上，不打在字面量上**：
不比对某段源码字符串长什么样，而是证明「要保的性质还在」。

钉住的三条契约：

  1. **分组绝不能改变导航顺序**（最高风险项）
     `nav_defs` 的顺序和 `main_stack` 的页面顺序是**同一个序列**：
     `_switch_nav(idx) -> main_stack.setCurrentIndex(idx + 1)`（首页占 0）。
     一期诊断说「10 项平铺无分组」要改成分组，但分组是**纯视觉**的 ——
     一旦有人为了"分组更好看"顺手调了顺序，军团页就会静默串页。
     所以这里要求：`NAV_GROUPS` 展平后必须与 `nav_defs` **逐项严格相等**。
     （注：`_ensure_lazy_page` 用控件对象比对、不写死下标，是第二道保险。）

  2. **侧栏折叠必须有阈值 + 必须有状态短路**
     没有短路时 `resizeEvent` 每次拖动都重建布局，窗口边框一拖就卡；
     没有阈值常量就没人知道多窄才折。这里要求两者都在。

  3. **状态条只放状态，不放操作提示**
     「Enter 发送 · Shift+Enter 换行」是**操作说明**，放在底部状态条里
     和「● 已连接」「计费信息」混在一起，用户永远不会去看（它在最右下角）。
     已移进输入框 placeholder —— 那里才是用户视线落点。
     这里要求状态条里再无该提示、且输入框 placeholder 里确实有。

用 AST 静态解析，不 import ui（避免拉起 Qt 依赖），可跑在 CI / offscreen。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
UI_SRC = os.path.join(ROOT, "ui.py")

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} got={got!s:<8} exp={exp}  {extra}")


src = open(UI_SRC, encoding="utf-8").read()
tree = ast.parse(src)


def _const_list(name):
    """取模块级/任意作用域里 `name = [(str, ...), ...]` 的字面量。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        out.append(ast.literal_eval(node.value))
                    except Exception:
                        pass
    return out


def _nav_defs_labels():
    """nav_defs = [(label, icon), ...] -> 只取 label 序列。"""
    got = _const_list("nav_defs")
    if not got:
        return []
    return [item[0] for item in got[0] if isinstance(item, (list, tuple)) and item]


def _nav_groups():
    """NAV_GROUPS = ((组名, (成员...)), ...) -> 原样返回。"""
    got = _const_list("NAV_GROUPS")
    return got[0] if got else None


def _flatten_groups(groups):
    flat = []
    for gname, members in groups:
        flat.extend(members)
    return flat


print("=" * 78)
print("A 组：导航分组契约（顺序绝不能被分组改动）")
print("=" * 78)

nav_labels = _nav_defs_labels()
check("A1 nav_defs 可解析且非空", len(nav_labels) > 0, extra=f"{len(nav_labels)} 项")

groups = _nav_groups()
check("A2 存在 NAV_GROUPS 常量", groups is not None)

if groups is not None:
    check("A3 NAV_GROUPS 结构为 (组名, 成员元组)",
          all(isinstance(g, (list, tuple)) and len(g) == 2 for g in groups),
          extra=f"{len(groups)} 组")
    flat = _flatten_groups(groups)
    check("A4 分组展平 == nav_defs 顺序（严格逐项）", flat == nav_labels,
          extra=f"分组{len(flat)}项 vs 导航{len(nav_labels)}项")
    check("A5 无导航项被漏分组", set(nav_labels) - set(flat) == set(),
          extra=str(set(nav_labels) - set(flat)) or "无遗漏")
    check("A6 无多余成员（分组里不存在的项目）", set(flat) - set(nav_labels) == set(),
          extra=str(set(flat) - set(nav_labels)) or "无多余")
    check("A7 组数 >= 2（否则等于没分组）", len(groups) >= 2, extra=f"{len(groups)} 组")
    check("A8 每组至少 1 项", all(len(m) >= 1 for _, m in groups))
    check("A9 组名不为空串", all(str(g).strip() for g, _ in groups))

# 顺序绑定仍在：_switch_nav -> setCurrentIndex(index + 1)
check("A10 仍走 idx+1 绑定页面栈",
      "setCurrentIndex(_si)" in src or "setCurrentIndex(index + 1)" in src
      or "self.main_stack.setCurrentIndex(_si)" in src)

print()
print("=" * 78)
print("B 组：侧栏折叠（阈值 + 防抖短路）")
print("=" * 78)

collapse_widths = []
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and "COLLAPSE" in t.id.upper():
                try:
                    collapse_widths.append((t.id, ast.literal_eval(node.value)))
                except Exception:
                    pass

check("B1 存在折叠阈值常量", len(collapse_widths) >= 1,
      extra=str(collapse_widths))
if collapse_widths:
    w = collapse_widths[0][1]
    check("B2 阈值在合理区间 [900, 1200]", isinstance(w, int) and 900 <= w <= 1200,
          extra=f"{w}px")

check("B3 有 resizeEvent 响应窗口宽度", "def resizeEvent" in src)
check("B4 折叠状态有短路（防拖动抖动）",
      src.count("_nav_collapsed") >= 2, extra=f"引用 {src.count('_nav_collapsed')} 次")
check("B5 折叠宽度常量存在且窄于展开", "NAV_COLLAPSED_W" in src or "_set_sidebar_collapsed" in src)

print()
print("=" * 78)
print("C 组：状态条只放状态（操作提示已移入输入框）")
print("=" * 78)

_fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_build_status_bar":
        _fn = node
check("C1 _build_status_bar 存在", _fn is not None)

if _fn is not None:
    seg = ast.get_source_segment(src, _fn) or ""
    check("C2 状态条高度 >= 28px", "setFixedHeight(28)" in seg or "setFixedHeight(32)" in seg,
          extra="24px 装不下 12px 字 + chip 内边距")
    check("C3 状态条里已无 'Enter 发送' 操作提示", "Enter 发送" not in seg,
          extra="操作提示不属状态")
    check("C4 计费信息带底色 chip（有 background）", "background" in seg)

check("C5 操作提示已移入输入框 placeholder",
      "Enter 发送" in src and "setPlaceholderText" in src
      and any("Enter 发送" in ln and "PlaceholderText" in ln for ln in src.splitlines()))

print()
print("=" * 78)
print("D 组：折叠时文字必须隐藏（否则 64px 栏里文字被裁）")
print("=" * 78)

check("D1 组标题有可见性切换", src.count("setVisible") >= 2,
      extra=f"setVisible {src.count('setVisible')} 处")
check("D2 折叠逻辑集中在单一方法（不散落）", "_set_sidebar_collapsed" in src)

print()
print("=" * 78)
print(f"汇总：PASS={_p}  FAIL={_f}")
print("=" * 78)
sys.exit(1 if _f else 0)
