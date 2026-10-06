"""v4.197.0 可读性一期守卫：弱色对比度达标 + 微标签不小于 12px + 两处字号 token 同源。

背景（本套件就是这三件事的机器守卫，防止后人改回去）：

  1. **弱文色对比度**：旧值 `#9AA0A6` 在白底只有 **2.6:1**（WCAG AA 正文需 4.5:1），
     而它承载了状态条全部文字、placeholder、角标、时间戳 —— 全项目最大的一处
     可读性欠账。改成 `#6B7280`（4.8:1）后仍明显浅于正文 #202124（15.9:1），
     层级不丢。**注意这里算的是真实对比度，不是比对 hex 字符串** ——
     哪天有人换个同样不达标的灰（比如 #A0A0A0），字符串比对会放行，这里不会。

  2. **微标签字号**：11px 在 100%/125% 缩放下笔画糊。全项目 41 处 11px 已归一到
     12px（ui.py 27 + 旁支面板 14）。这里钉住「不得再有 <12px 的字号」。

  3. **两处字号 token 必须同源**：`ui.py THEME['font_micro']` 与 `theme_qss.py F['micro']`
     是手写镜像的两份。本次改值时**两处都得改**，只改一处就会出现「同一 token
     两套值」——且不会报错，只是某些面板悄悄小 1px。守卫它俩必须相等。

用 AST 静态解析，不 import ui（避免拉起 Qt 依赖），可跑在 CI / offscreen。
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} got={got!s:<8} exp={exp}  {extra}")


# ---- WCAG 相对亮度 / 对比度（真算，不查表）----
def _srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_color):
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_to_lin(r) + 0.7152 * _srgb_to_lin(g) + 0.0722 * _srgb_to_lin(b)


def contrast(fg, bg):
    """WCAG 2.1 对比度；返回 (较亮/较暗) 比值。"""
    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _dict_literal(path, name):
    """用 AST 取出模块顶层 `name = {...}` 的字面量字典（不执行模块）。"""
    src = open(os.path.join(ROOT, path), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in tree.body:
        targets = getattr(node, 'targets', [])
        if isinstance(node, ast.Assign) and len(targets) == 1 \
                and isinstance(targets[0], ast.Name) and targets[0].id == name \
                and isinstance(node.value, ast.Dict):
            out = {}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    out[k.value] = v.value
            return out
    raise AssertionError(f"{path} 里找不到顶层字面量字典 {name}")


def _strip_py_comment(line):
    """剥掉 Python 行注释（跟踪引号状态，避免把 QSS 里的 #rrggbb 当注释起点）。

    之所以要剥：改色时会在注释里**留痕写旧值**（"旧值 #9AA0A6 只有 2.6:1"），
    那是刻意保留的历史说明，不是违规实现。判据只该看代码。
    """
    out, quote = [], None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == '\\':
                if i + 1 < len(line):
                    out.append(line[i + 1]); i += 2
                else:
                    i += 1
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            out.append(ch)
        elif ch == '#':
            break                      # 引号外的 # → 行注释起点
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


BG_WHITE = "#FFFFFF"
AA_NORMAL = 4.5   # WCAG AA 正文

print("=== 1) 弱文色对比度（真算 WCAG，不是比对 hex 字符串）===")
THEME = _dict_literal('theme_tokens.py', 'THEME')  # v4.216.0 迁移
for key in ('faint', 'placeholder', 'weak'):
    val = THEME.get(key, '')
    ok_hex = bool(re.fullmatch(r'#[0-9A-Fa-f]{6}', val or ''))
    ratio = contrast(val, BG_WHITE) if ok_hex else 0.0
    check(f"THEME['{key}'] 白底对比度 >= {AA_NORMAL}", round(ratio, 2) >= AA_NORMAL,
          extra=f"{val} = {ratio:.2f}:1")

# 正文/次级/强调色也必须达标（防连锁回退）
for key in ('text', 'dim', 'accent'):
    val = THEME.get(key, '')
    ratio = contrast(val, BG_WHITE)
    check(f"THEME['{key}'] 白底对比度 >= {AA_NORMAL}", round(ratio, 2) >= AA_NORMAL,
          extra=f"{val} = {ratio:.2f}:1")

# 层级仍在：弱色必须明显浅于正文（否则"全都变黑"也是一种回退）
check("层级保留：faint 亮度 > text 亮度",
      _lum(THEME['faint']) > _lum(THEME['text']),
      extra=f"faint={_lum(THEME['faint']):.3f} text={_lum(THEME['text']):.3f}")

print("\n=== 2) 微标签字号不得小于 12px（token 值）===")
micro_px = int(str(THEME['font_micro']).replace('px', ''))
check("THEME['font_micro'] >= 12", micro_px >= 12, extra=f"{micro_px}px")
second_px = int(str(THEME['font_second']).replace('px', ''))
check("font_micro 与 font_second 已合并同档", micro_px == second_px,
      extra=f"micro={micro_px} second={second_px}")

print("\n=== 3) 两处字号 token 必须同源（手写镜像，最容易只改一处）===")
F = _dict_literal('theme_qss.py', 'F')
check("ui.THEME['font_micro'] == theme_qss.F['micro']",
      THEME['font_micro'] == F['micro'],
      extra=f"ui={THEME['font_micro']} theme_qss={F['micro']}")
check("ui.THEME['font_body'] == theme_qss.F['body']",
      THEME['font_body'] == F['body'],
      extra=f"ui={THEME['font_body']} theme_qss={F['body']}")

print("\n=== 4) 源码不得再出现 <12px 的字号（防回潮）===")
# v4.216.0：ui.py 拆出的四个 UI 模块一并纳入字号巡逻（样式随代码搬家，
# 巡逻范围必须跟着走，否则新文件成了盲区）
SCAN_FILES = ['ui.py', 'theme_tokens.py', 'ui_widgets.py', 'ui_workers.py',
              'ui_msg.py', 'ui_audit_mixin.py', 'automation_panel.py', 'director_panel.py',
              'legion_chat.py', 'legion_status_widget.py', 'chat_web.py',
              'director_web.py', 'skill_market_ui.py', 'skill_manager_ui.py',
              'tool_manager_ui.py', 'onboarding.py', 'legion_ui.py']
# 10/11px 都算违规；CSS 里 "font-size: 11px"（带空格）也算
BAD = re.compile(r'font-size:\s*(?:1[01]|[1-9])px')
bad_hits = []
for fn in SCAN_FILES:
    p = os.path.join(ROOT, fn)
    if not os.path.exists(p):
        continue
    for i, line in enumerate(open(p, encoding='utf-8'), 1):
        if BAD.search(_strip_py_comment(line)):
            bad_hits.append(f"{fn}:{i}")
check("无 <12px 字号残留", not bad_hits, extra="; ".join(bad_hits[:5]))

print("\n=== 5) 弱色不得再硬写旧值 #9AA0A6（防回潮，排除备份目录）===")
old_hits = []
for fn in SCAN_FILES + ['main.py', 'agent.py']:
    p = os.path.join(ROOT, fn)
    if not os.path.exists(p):
        continue
    for i, line in enumerate(open(p, encoding='utf-8'), 1):
        if '#9AA0A6' in _strip_py_comment(line).upper():
            old_hits.append(f"{fn}:{i}")
check("无 #9AA0A6 残留", not old_hits, extra="; ".join(old_hits[:5]))

print(f"\n汇总：PASS={_p} FAIL={_f}")
sys.exit(1 if _f else 0)
