"""v4.199.0 界面二期补完守卫：首页卡片契约 + 顶栏搜索框作用域 + 最近对话摘要。

补的是二期原本 4 项里的 ③④（前三项已在 v4.198.0 装车）：
  ③ 顶栏搜索框改伸缩 + placeholder 修正
  ④ 首页场景卡替换 + 最近对话加摘要

**按 L160 的教训，判据打在性质上，不打在字面量上**：不比对某段源码长什么样，
而是证明「要保证的性质还在」。每条 CHECK 下面都写了它防的是哪一种失效。

核心性质：

  A. **卡片数与 prompts 必须严格等长**（Bug 模式：静默失真）
    卡是按下标 `prompts[idx]` 取文案的，多一个少一个都不报错 ——
      - prompts 少一条 → 最后一张卡点了**什么也不发生**；
      - card_data 多一条 → 最后一张卡**永远取不到文案**。
     都是「看起来没坏」的坏。所以要证明两者同长。

  B. **卡片配色必须真实存在**（Bug 模式：静默降级）
    渲染写的是 `THEME.get(f"card_{key}_icon", THEME["accent"])` ——
    拼错一个颜色名不会崩溃，只会悄悄 fallback 成默认蓝，
    肉眼看上去是「设计师改了配色」，其实是 bug。这里强制每个色号能在 THEME 里查到。

  C. **图标名必须在 _NAV_ICONS 里**（同上）
    `_nav_icon_pixmap` 查不到就返回空 QPixmap —— 不报错，直接是一片空白。

  D. **搜索框不许写死宽度**（这次要消除的东西）
    原 `setFixedSize(480,36)` 在窄窗口里会把 Logo/头像/系统按钮挤出去。
    要证明的是「它现在真的能伸缩」：有最小宽、有最大宽、且 最小 < 最大，
    同时被 addWidget 带上了 stretch（否则永远停在 sizeHint，宽屏缩成一小条）。

  E. **placeholder 不许承诺它做不到的作用域**（Bug 模式：用户以为坏了）
    这条搜索框实际只搜当前对话（见 `_search_in_chat`）。旧文案写「搜索对话、文件、工具…」，
    于是搜文件搜工具的期待换来一个空结果，用户的结论是「搜索坏了」而不是「我理解错了」。
    这里要求 placeholder 里不再出现「文件」「工具」，且必须写明「当前对话」。

  F. **最近对话行必须装得下两行**（Bug 模式：提内容不抬容器）
    加摘要后一行从 40px 涨到需要 ~56px。沿用一期的教训：
    「12px 字装 12px 容器」会被裁；这里若不抬高度，摘要行直接被切掉，
    **比没有摘要更糟**（用户会以为列表渲染出问题）。

用 AST 静态解析，不 import ui（避免拉起 Qt 依赖），可跑在 CI / offscreen。
"""
import ast
import os
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
UI_SRC = os.path.join(ROOT, "ui.py")

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<50} got={got!s:<8} exp={exp}  {extra}")


src = open(UI_SRC, encoding="utf-8").read()
tree = ast.parse(src)


def _module_assign(name):
    """取模块级 `name = <literal>` 的值（THEME / _NAV_ICONS 用）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return None
    return None


def _assign_list(name):
    """取任意作用域里 `name = [..]` 的字面量列表（含方法内的局部赋值）。"""
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


def _calls(attr, receiver=None):
    """收集所有 `recv.attr(args)` 的字面量参数。

    receiver 支持点号路径（"self.search_box"）——
    顶栏里全是 `self.search_box.setMinimumWidth(...)`，接收者是 ast.Attribute
    不是 ast.Name，只认 ast.Name 会静默取不到值 → **守卫自己变成瞎子**。
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != attr:
            continue
        if receiver is not None:
            try:
                if ast.unparse(node.func.value) != receiver:
                    continue
            except Exception:
                continue
        try:
            out.append([ast.literal_eval(a) for a in node.args])
        except Exception:
            pass
    return out


def _strip_comments(text):
    """删掉行注释后再判断文本内容。

    必须做这一步：解释这次改动的注释里难免写到「文件」「工具」这些词，
    而判据关心的是**用户实际看到的那串文字**，不是注释的措辞。
    不剥离的话，改一句注释就能让守卫变色，那它就守不住任何东西。
    （与 tests/test_readability_197.py 同一处理思路。）
    """
    import io as _io
    import tokenize as _tok
    text = textwrap.dedent(text)
    lines = text.splitlines(keepends=True)
    try:
        for tok in _tok.generate_tokens(_io.StringIO(text).readline):
            if tok.type == _tok.COMMENT:
                srow, scol = tok.start
                erow, ecol = tok.end
                if erow == srow:
                    ln = lines[srow - 1]
                    lines[srow - 1] = ln[:scol] + ' ' * (ecol - scol) + ln[ecol:]
    except Exception:
        return text
    return ''.join(lines)


def _func(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _top_level_func(name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


THEME = _module_assign("THEME") or {}
NAV_ICONS = _module_assign("_NAV_ICONS") or {}

print("=" * 78)
print("A 组：首页能力卡（卡片与文案同序等长 + 配色/图标真实存在）")
print("=" * 78)

cards = (_assign_list("card_data") or [None])[0]
prompts = (_assign_list("prompts") or [None])[0]

check("A1 card_data 可解析且非空", bool(cards), extra=f"{len(cards) if cards else 0} 张")
check("A2 prompts 可解析且非空", bool(prompts), extra=f"{len(prompts) if prompts else 0} 条")

if cards and prompts:
    check("A3 卡片数 == prompts 数（防点了没反应）", len(cards) == len(prompts),
          extra=f"卡 {len(cards)} 张 vs 文案 {len(prompts)} 条")
    check("A4 每张卡 4 个字段（色/图标/标题/描述）",
          all(isinstance(c, (list, tuple)) and len(c) == 4 for c in cards))
    titles = [c[2] for c in cards]
    descs = [c[3] for c in cards]
    check("A5 标题均非空", all(str(t).strip() for t in titles), extra="/".join(titles))
    check("A6 描述均非空", all(str(d).strip() for d in descs))
    check("A7 标题不重复（重复说明分类没想清楚）", len(set(titles)) == len(titles))

    # Bug 模式：静默降级 —— 拼错色名不报错，只 fallback
    miss_color = [c[0] for c in cards
                  if f"card_{c[0]}_bg" not in THEME or f"card_{c[0]}_icon" not in THEME]
    check("A8 每张卡的配色在 THEME 里真实存在", miss_color == [],
          extra=f"缺失 {miss_color}" if miss_color else "全部命中")

    # Bug 模式：空图 —— 查不到 icon 名返回空 QPixmap，不报错
    miss_icon = [c[1] for c in cards if c[1] not in NAV_ICONS]
    check("A9 每张卡的图标在 _NAV_ICONS 里存在", miss_icon == [],
          extra=f"缺失 {miss_icon}" if miss_icon else "全部命中")

    # Bug 模式：旧模板文案回潮 —— 说「AI 能干什么」而不是「这个产品能干什么」
    stale = {"写文章", "写代码", "做设计", "聊天问答", "翻译"}
    check("A10 无通用助手模板标题回潮", set(titles) & stale == set(),
          extra=str(set(titles) & stale) or "已换成真实能力")
    check("A11 卡数 >= 3（信息量足够）", len(cards) >= 3, extra=f"{len(cards)} 张")

# 布局容量：多卡并排不得把整行撑爆
_min = [a[0] for a in _calls("setMinimumSize", "card") if a and isinstance(a[0], int)]
_sp = [a[0] for a in _calls("setSpacing", "cards_row") if a and isinstance(a[0], int)]
if _min and cards:
    n = len(cards)
    total = _min[0] * n + (_sp[0] if _sp else 16) * (n - 1)
    check("A12 多卡最小总宽不撑爆常规窗口(<=820)", total <= 820,
          extra=f"{_min[0]}×{n} + {(_sp[0] if _sp else 16)}×{n-1} = {total}px")
    check("A13 单卡最小宽 <= 200（原 200 起 4 张会挤）", _min[0] <= 200, extra=f"{_min[0]}px")

print()
print("=" * 78)
print("B 组：顶栏搜索框（可伸缩 + 作用域说真话）")
print("=" * 78)

# Bug 模式：写死宽度 —— 窄窗口时搜索框把 Logo/头像/系统按钮挤出去
_fxW = [a for a in _calls("setFixedSize", "self.search_box")]
check("B1 search_box 不再写死宽高", _fxW == [], extra=f"仍存在 {_fxW}" if _fxW else "已改 Min/Max")

_minw = [a[0] for a in _calls("setMinimumWidth", "self.search_box") if a]
_maxw = [a[0] for a in _calls("setMaximumWidth", "self.search_box") if a]
check("B2 有最小宽度", bool(_minw), extra=str(_minw))
check("B3 有最大宽度", bool(_maxw), extra=str(_maxw))
if _minw and _maxw:
    # 「能伸缩」的性质：只有 min < max 才真的能动，写 min==max 等于换了个写法固定住
    check("B4 最小 < 最大（真的会伸缩）", _minw[0] < _maxw[0],
          extra=f"{_minw[0]} ~ {_maxw[0]}")
    check("B5 最小宽 <= 240（窄窗仍可用）", _minw[0] <= 240, extra=f"{_minw[0]}px")
    check("B6 最大宽 <= 480（不占死顶栏）", _maxw[0] <= 480, extra=f"{_maxw[0]}px")
check("B7 高度仍固定（顶栏不能跟着抖）",
      bool([a for a in _calls("setFixedHeight", "self.search_box")]))

# stretch：不带 factor 的话 lunch 只会停在 sizeHint，宽屏缩成一小条
_welcome_tb = "self.search_box" in src
_addwidgets = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "addWidget":
        for arg in node.args:
            if isinstance(arg, ast.Attribute) and arg.attr == "search_box":
                _addwidgets.append(len(node.args))
check("B8 search_box 带 stretch 因子加入布局",
      bool(_addwidgets) and max(_addwidgets) >= 2,
      extra=f"addWidget 参数个数 {_addwidgets}")

_tb_fn = _func("_build_title_bar")
if _tb_fn is not None:
    seg = _strip_comments(ast.get_source_segment(src, _tb_fn) or "")
    check("B9 placeholder 不再承诺「文件」", "文件" not in seg,
          extra="搜索不到文件就别承诺能搜文件")
    check("B10 placeholder 不再承诺「工具」", "工具" not in seg)
    check("B11 placeholder 写明作用域「当前对话」", "当前对话" in seg)
    check("B12 有 tooltip 补全快捷键说明", "setToolTip" in seg)

check("B13 对话内搜索功能仍在（没被改掉）", _func("_search_in_chat") is not None)

print()
print("=" * 78)
print("C 组：最近对话摘要（行容器装得下两行）")
print("=" * 78)

check("C1 _session_preview 是模块级函数（可复用可测）",
      _top_level_func("_session_preview") is not None)

_refresh = _func("_refresh_recent_on_welcome")
check("C2 _refresh_recent_on_welcome 存在", _refresh is not None)

if _refresh is not None:
    seg = ast.get_source_segment(src, _refresh) or ""
    check("C3 行容器调用了 _session_preview", "_session_preview" in seg)
    check("C4 摘要控件真的加进了布局（不是算了没用）", "preview_lbl" in seg
          and "addWidget(preview_lbl)" in seg.replace(" ", ""))
    check("C5 标题与摘要走纵向容器",
          "QVBoxLayout()" in seg and "txt_col" in seg)
    _h = [a[0] for a in _calls("setFixedHeight", "row") if a and isinstance(a[0], int)]
    check("C6 行高 >= 48（13px 标题 + 12px 摘要 + 行距）", bool(_h) and max(_h) >= 48,
          extra=f"{max(_h) if _h else '未设置'}px")
    check("C7 摘要缺失时有兜底文案", "或 \"暂无内容\"" in seg or '"暂无内容"' in seg)

_pv = _top_level_func("_session_preview")
if _pv is not None:
    seg = ast.get_source_segment(src, _pv) or ""
    # Bug 模式：只翻最后一条消息 —— 工具卡片类消息没有正文，取到空串就整项空白
    check("C8 空/无正文消息会被跳过而非产出空摘要", "continue" in seg)
    check("C9 换行被压平（否则预览会顶出第二行）", '" ".join' in seg or "' '.join" in seg)
    check("C10 摘要有长度上限（不撑破列表宽度）", "maxlen" in seg)

print()
print("=" * 78)
print("D 组：新增紫色不凭空造色")
print("=" * 78)

if "card_purple_icon" in THEME:
    check("D1 card_purple_icon == 既有 accent2（不引入新色）",
          str(THEME["card_purple_icon"]).lower() == str(THEME.get("accent2", "")).lower(),
          extra=f"{THEME['card_purple_icon']} vs {THEME.get('accent2')}")
    check("D2 card_purple_bg 用 rgba（与其余三套同写法）",
          str(THEME["card_purple_bg"]).startswith("rgba("), extra=str(THEME["card_purple_bg"]))
else:
    check("D1 card_purple_icon 存在", False, extra="THEME 里没有该键")

print()
print("=" * 78)
print(f"汇总：PASS={_p}  FAIL={_f}")
print("=" * 78)
sys.exit(1 if _f else 0)
