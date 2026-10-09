# -*- coding: utf-8 -*-
"""QSS 静态求值 + 属性集合解析引擎（三期收口的公用底座）

为什么不用「字符串相等」做收口判据
----------------------------------
QSS 里 `color:X;font-size:Y;` 和 `font-size:Y;color:X;` **渲染完全相同**——
属性是无序集合。实际代码里这两种顺序都大量存在（实测 dim+12px 就有 29 处
`font-size` 在前、9 处 `color` 在前），所以收口时既要允许重排，又要能抓到
真正的走样（值变了 / 属性丢了 / selector 变了）。本模块把 QSS 解析成
`(selector -> {prop: value})` 的字典，比对落到**属性集合级**。

另外一层：源码里的 QSS 全是分片 f-string，字面量层面看不出完整串，所以必须
**静态求值**（把 THEME/F/S 和 theme_qss 的函数调用真算出来）而不是正则抓取。
"""
import ast
import collections
import os

# 可静态求值的 theme_qss 导出符号（收口后源码会改成调这些）
QSS_FUNCS = {
    "label_micro", "label_second", "label_body", "label_title",
    "label_title_xl", "gap", "fs",
    "btn_primary", "btn_secondary", "btn_outline", "btn_danger",
    "btn_small", "btn_dialog", "edit_style", "input_style",
    "combo_style", "chk_style", "scroll_transparent",
}

SCAN_TARGETS = (
    "ui.py", "ui_widgets.py", "ui_workers.py", "ui_msg.py", "ui_audit_mixin.py",
    "director_panel.py", "digital_twin_panel.py",
    "automation_panel.py", "director_chat.py",
)
# v4.216.0：ui.py 拆分后 THEME 真源在 theme_tokens.py；扫描目标同步扩容，
# 搬到新模块的 setStyleSheet 调用继续被 B1 快照管住（否则新文件成盲区）。


def load_literal_dict(path, name):
    """从源码顶层 `name = {...}` 抠出字典真值；拿不到返回 {}。"""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return {}
    return {}


def build_env(base_dir, tq=None):
    """构造静态求值环境：THEME / F / S / W + theme_qss 函数。

    tq 可传入已导入的 theme_qss 模块（探针里能 import 就用真模块，
    这样函数调用走真实实现，不靠二次实现猜语义）。
    """
    # v4.216.0：THEME 唯一真源迁至 theme_tokens.py；老基线快照时代在 ui.py，
    # 兼容两者（找不到 theme_tokens.py 时回退 ui.py，适配 %TEMP% 扰动副本等场景）
    _theme_path = os.path.join(base_dir, "theme_tokens.py")
    if not os.path.isfile(_theme_path):
        _theme_path = os.path.join(base_dir, "ui.py")
    theme = load_literal_dict(_theme_path, "THEME")
    env = {
        "THEME": theme,
        "F": load_literal_dict(os.path.join(base_dir, "theme_qss.py"), "F"),
        "W": load_literal_dict(os.path.join(base_dir, "theme_qss.py"), "W"),
        "S": {k[6:]: v for k, v in theme.items() if k.startswith("space_")},
    }
    if tq is not None:
        for name in QSS_FUNCS:
            fn = getattr(tq, name, None)
            if callable(fn):
                env[name] = fn
    return env


def eval_expr(node, env):
    """尽力求值 QSS 片段；无法静态确定时返回 None（不猜、不兜底成空串）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                # conversion 的「无转换」是 **-1 而不是 None**（AST 设计如此），
                # 判成 is not None 会把所有 f-string 误判为不可解。
                if v.conversion not in (None, -1) or v.format_spec is not None:
                    return None
                r = eval_expr(v.value, env)
                if r is None:
                    return None
                out.append(str(r))
            else:
                return None
        return "".join(out)
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Subscript):
        base = eval_expr(node.value, env)
        key = eval_expr(node.slice, env)
        try:
            return base[key]
        except Exception:
            return None
    if isinstance(node, ast.Attribute):
        base = eval_expr(node.value, env)
        return getattr(base, node.attr, None) if base is not None else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = env.get(node.func.id)
        if not callable(fn):
            return None
        args = [eval_expr(a, env) for a in node.args]
        if any(a is None for a in args):
            return None
        # L176：kwargs 必须支持。收口后调用形态必然是 label_x(color_key=..., weight=...)，
        # 若判不可解，整条 setStyleSheet 会从快照里消失 —— 收得越多盲区越大。
        kw = {}
        for k in node.keywords:
            if k.arg is None:          # **kwargs 展开：不猜
                return None
            v = eval_expr(k.value, env)
            if v is None:
                return None
            kw[k.arg] = v
        try:
            return fn(*args, **kw)
        except Exception:
            return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a = eval_expr(node.left, env)
        b = eval_expr(node.right, env)
        return None if a is None or b is None else a + b
    return None


# CSS2 命名色 -> 十六进制（Qt 与浏览器同规范）。只收录会用到的；
# `transparent` 不是颜色名，不做归一。
NAMED_COLORS = {
    "white": "#FFFFFF", "black": "#000000", "red": "#FF0000",
    "green": "#008000", "blue": "#0000FF", "yellow": "#FFFF00",
    "orange": "#FFA500", "gray": "#808080", "grey": "#808080",
    "silver": "#C0C0C0", "purple": "#800080",
}


def iter_rules(qss):
    """把一段 QSS 拆成 (selector, {prop: value}) 序列。

    Qt 的 widget 级 QSS 允许**裸声明**（`w.setStyleSheet("color:red;font-size:12px;")`，
    没有 selector），而且裸声明必然排在所有带 selector 的规则之前。标签族几乎全是
    这种形态 —— 只认 `{...}` 会把它们整批漏掉。
    """
    def _decls(body):
        props = {}
        for decl in body.split(";"):
            if ":" not in decl:
                continue
            p, v = decl.split(":", 1)
            v = " ".join(v.split())
            # 命名色归一化：`color:white` 与 `color:#FFFFFF` 在 CSS/Qt 里是同一个值。
            # 不归一的话，把裸写收口成 token 调用会假报「走样」。
            if v.lower() in NAMED_COLORS:
                v = NAMED_COLORS[v.lower()]
            props[p.strip()] = v
        return props

    pos = 0
    n = len(qss)
    while pos < n:
        j = qss.find("{", pos)
        if j < 0:
            # 整段剩余都是裸声明（或尾部垃圾）
            tail = qss[pos:]
            props = _decls(tail)
            if props:
                yield "", props
            return
        if j > pos:
            # '{' 之前的裸声明段
            props = _decls(qss[pos:j])
            if props:
                yield "", props
        depth, k = 1, j + 1
        while k < n and depth:
            if qss[k] == "{":
                depth += 1
            elif qss[k] == "}":
                depth -= 1
            k += 1
        sel = qss[pos:j].strip()
        # 相邻两条规则之间没有分隔符时（"...}QLabel{...}"），sel 会带上一条的
        # 残留；裸声明已在上面单独产出，这里只取最后一个 '}' 之后的真 selector。
        sel = sel.rsplit("}", 1)[-1].strip()
        props = _decls(qss[j + 1:k - 1])
        if sel or props:
            yield sel, props
        pos = k


def local_qss_funcs(base_dir, fn, env):
    """把该文件的**模块级零参样式helper**求成字符串，补进 env。

    为什么需要：收口会把重复的内联 QSS 改成本地函数调用（如 digital_twin_panel.py
    的 `_chk_style()`）。这类函数不在 theme_qss 里，`eval_expr` 查不到就返回 None，
    整条 setStyleSheet 被计入「不可求值」而**从快照里消失** —— 看起来像 5 条规则
    凭空没了（v4.201.0 首轮比对就报了这次假红）。后果是：以后凡用本地 helper 收口，
    判据都会失明，收口越大盲区越大。

    只认最安全的形态：模块级、`def f():` 零参、body 是**单条 return** 且能静态求值。
    带参数 / 多语句 / 有分支的一律不猜（猜错比认不出来更危险）。
    """
    try:
        src = open(os.path.join(base_dir, fn), encoding="utf-8").read()
    except OSError:
        return {}
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.args.args or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
            continue
        body = node.body
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue
        val = body[0].value
        if val is None:
            continue
        s = eval_expr(val, env)
        if isinstance(s, str) and s:
            out[node.name] = (lambda _s=s: _s)
    return out


def parse_calls(base_dir, fn, env):
    """扫描一个文件里所有 `*.setStyleSheet(...)`，产出可静态求值的结果。"""
    src = open(os.path.join(base_dir, fn), encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    stats = collections.Counter()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setStyleSheet"):
            continue
        stats["calls"] += 1
        if not node.args:
            continue
        val = eval_expr(node.args[0], env)
        if val is None or not isinstance(val, str):
            stats["unresolved"] += 1
            continue
        stats["resolved"] += 1
        rules = list(iter_rules(val))
        is_full_random = all(sel for sel, _ in rules)
        out.append({
            "line": node.lineno,
            "col": node.col_offset,
            "raw": val,
            "rules": rules,
            "full": is_full_random,
        })
    return out, stats
