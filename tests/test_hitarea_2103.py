# -*- coding: utf-8 -*-
"""触控命中区判据（v4.210.3）—— 可点控件的固定高度不得低于硬底线

独立运行：python tests/test_hitarea_2103.py

为什么单独建一套
----------------
`setFixedHeight(16)` 这种写法**不报错、不崩、别的套件也全绿** —— 它只是让
「✕」按钮小到鼠标都难点。属「默认就坏、而且坏了没人报」那一类，必须有判据钉住。

本套件的四条设计要点（每条都是踩过或差点踩过的坑）
--------------------------------------------------
1. **两种扫描口径取并集，且各自盲区显式记账**（B 组）。
   名字启发式（变量名像 btn/close/del…）与 AST 口径（变量确由按钮构造器赋值）
   各有盲区，实测各能看见对方看不见的项。单用任何一个都会以为"守住了"。
   · 启发式漏：`pin`（名字里没有任何提示词）
   · AST   漏：`self.chat_model_combo`（构造器是子类 `_NoWheelCombo`）
2. **不得静默跳过任何文件**（H1）。
   实测：`config.py` / `risk.py` 带 UTF-8 BOM。按 `encoding="utf-8"` 读会在开头
   留下 U+FEFF → `ast.parse` 抛 SyntaxError → **整个文件被跳过而无人知晓**。
   故本套件按字节读 + `utf-8-sig`，并把"有文件没解析成功"本身判为红。
3. **非空谓词自证**（H2）。
   正则/AST 一旦失配，扫描结果就是空集 —— 空集会让你所有"不得低于 X"的断言
   恒绿（v4.210.2 的 C1 判据就是被这条坑过一次）。故要求候选数、命中数达标。
4. **口径要写在明面上**（H2d）。
   `setMinimumHeight(18)` 是"至少 18"、不是"就是 18"，实际可能被布局撑得更高，
   所以它**不计入硬底线**；只有 `setFixed*`（定值）与 `setMaximum*`（封顶）才算。
"""
import ast
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- 口径常量 ----
HARD_FLOOR = 24       # 硬底线：低于此值 = 红
TARGET = 32           # 目标值：只是"不得新增"的登记线，不是及格线
SKIP_PREFIXES = ("_", "backup")   # 下划线=本地快照/扰动脚本；backup_*=历史备份

CLICKY_CTORS = {"QPushButton", "QToolButton", "QComboBox", "QCheckBox",
                "QRadioButton", "QCommandLinkButton"}
NAME_HINT = re.compile(
    r"(btn|button|icon|chip|tab|link|toggle|close|del|remove|expand|collapse|install)",
    re.I)
DISPLAYY = re.compile(r"\b\w*(logo|lbl|label|title|icon_lbl)\w*\s*(\.|\s*=)", re.I)
OWNER_RE = re.compile(r"^([A-Za-z_][\w\.]*)\s*\.\s*set(?:Fixed|Maximum|Minimum)(?:Height|Size)\s*\(")

FIXED_M = {"setFixedHeight", "setFixedSize"}      # 定值 → 计入硬底线
CAP_M = {"setMaximumHeight", "setMaximumSize"}    # 封顶 → 计入硬底线
FLOOR_M = {"setMinimumHeight", "setMinimumSize"}  # 下限 → **不计入**（见第 4 条）

# 已登记接受 <32px 的存量（改好一处就来删一行；新增一处会直接红）
ACCEPTED = {
    ("director_chat.py", "self.expand_btn"): 24,   # 状态行内文字按钮「⤢ 展开」
    ("automation_panel.py", "edit_btn"): 26,       # 任务卡上的「编辑」
    ("automation_panel.py", "del_btn"): 26,        # 任务卡上的「删除」
    ("ui.py", "collapse_btn"): 28,                 # 「收起面板」文字按钮
    ("ui.py", "self.chat_model_combo"): 28,        # 模型下拉（输入控件，非点按目标）
    ("skill_market_ui.py", "install_btn"): 30,     # 「安装」文字按钮
    ("skill_market_ui.py", "uninst"): 30,          # 「卸载」文字按钮
}

# 本轮修好的 9 处：钉住"确实到了 32px"
PINS = [
    ("toast.py", "self._close_btn", "self._close_btn.setFixedSize(32, 32)"),
    ("ui.py", "self.retry_btn", "self.retry_btn.setFixedHeight(32)"),
    ("ui.py", "self.clear_btn", "self.clear_btn.setFixedHeight(32)"),
    ("ui.py", "del_btn", "del_btn.setFixedSize(32, 32)"),
    ("ui.py", "delb", "delb.setFixedSize(32, 32)"),
    ("ui.py", "chk", "chk.setFixedSize(32, 32)"),
    ("ui.py", "pin", "pin.setFixedSize(32, 32)"),
    ("ui.py", "self.api_key_toggle", "self.api_key_toggle.setFixedSize(32, 32)"),
    ("director_panel.py", "delb", "delb.setFixedSize(32, 32)"),
]

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def note(msg):
    print(f"        · {msg}")


# ============================================================
# 源码读取与扫描
# ============================================================
def production_files():
    return [n for n in sorted(os.listdir(ROOT))
            if n.endswith(".py") and not n.startswith(SKIP_PREFIXES)]


def read_bytes(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return f.read()


def read_src(name):
    """按字节读 + utf-8-sig 解码：BOM 必须被吃掉，否则 ast.parse 会整份失败。"""
    return read_bytes(name).decode("utf-8-sig")


def blank_comments(src):
    """把注释抹成空白（保留行列）：防止"判据的说明注释"顶红它自己。"""
    lines = [list(x) for x in src.splitlines()]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            if r1 == r2 and 1 <= r1 <= len(lines):
                for c in range(c1, min(c2, len(lines[r1 - 1]))):
                    lines[r1 - 1][c] = " "
    except Exception:
        return src
    return "\n".join("".join(x) for x in lines)


def _unparse(node):
    try:
        return ast.unparse(node)
    except Exception:
        return None


def ctor_name(node):
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _height(call):
    """从调用里取出**高度**。必须先判 Height / Size —— setFixedSize(56, 26) 的
    高度是第 2 个参数，先命中「方法名在集合里」就返回 ints[0] 会把 56 当高度
    （本套件首版就这么把 automation 那两处 26px 静默漏掉了）。"""
    attr = call.func.attr
    ints = [a.value for a in call.args
            if isinstance(a, ast.Constant) and isinstance(a.value, int)
            and not isinstance(a.value, bool)]
    if attr.endswith("Height") and ints:
        return ints[0]
    if attr.endswith("Size") and len(ints) >= 2:
        return ints[1]
    return None


def scan_ast(src, fname):
    """AST 口径：只认「变量确实由按钮/交互类构造器赋值」的那些。"""
    tree = ast.parse(src)
    clicky = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and ctor_name(n.value) in CLICKY_CTORS:
            for t in n.targets:
                if (s := _unparse(t)):
                    clicky.add(s)
        elif isinstance(n, ast.AnnAssign) and ctor_name(n.value) in CLICKY_CTORS:
            if (s := _unparse(n.target)):
                clicky.add(s)
    recs = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        owner = _unparse(n.func.value)
        if owner not in clicky:
            continue
        method = n.func.attr
        if method not in (FIXED_M | CAP_M | FLOOR_M):
            continue
        h = _height(n)
        if h is not None:
            recs.append({"file": fname, "owner": owner, "h": h,
                         "method": method, "src": "ast", "line": n.lineno})
    return recs, clicky


def scan_heur(src, fname):
    """名字启发式：变量/整行像可点控件。补 AST 的盲区（子类构造器）。"""
    recs = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if not NAME_HINT.search(s) or DISPLAYY.search(s):
            continue
        m = OWNER_RE.match(s)
        if not m:
            continue
        try:
            call = ast.parse(s).body[0].value
        except Exception:
            continue
        if not isinstance(call, ast.Call) or call.func.attr not in (FIXED_M | CAP_M | FLOOR_M):
            continue
        h = _height(call)
        if h is not None:
            recs.append({"file": fname, "owner": m.group(1), "h": h,
                         "method": call.func.attr, "src": "heur", "line": i})
    return recs


# ---- 一次性扫描（全项目）----
FILES = production_files()
SOURCES = {n: read_src(n) for n in FILES}
BLANKED = {n: blank_comments(SOURCES[n]) for n in FILES}

PARSE_FAIL = []
AST_RECS, HEUR_RECS = [], []
CLICKY_OWNERS = set()
for _n in FILES:
    try:
        _a, _ck = scan_ast(SOURCES[_n], _n)
    except SyntaxError as _e:
        PARSE_FAIL.append((_n, f"line {_e.lineno}: {_e.msg}"))
        continue
    AST_RECS += _a
    CLICKY_OWNERS |= {(_n, o) for o in _ck}
    HEUR_RECS += scan_heur(SOURCES[_n], _n)

# 并集去重（同 file+owner+高度+写法 只留一条）
_seen, UNION = set(), []
for _r in AST_RECS + HEUR_RECS:
    _k = (_r["file"], _r["owner"], _r["h"], _r["method"])
    if _k in _seen:
        continue
    _seen.add(_k)
    UNION.append(_r)

HARD = [r for r in UNION if r["method"] in (FIXED_M | CAP_M)]   # 计入硬底线
FLOOR_ONLY = [r for r in UNION if r["method"] in FLOOR_M]
SMALL = [r for r in HARD if r["h"] < TARGET]
TINY = [r for r in HARD if r["h"] < HARD_FLOOR]
U40 = [r for r in HARD if r["h"] < 40]

BY_OWNER = {}
for _r in UNION:
    BY_OWNER.setdefault((_r["file"], _r["owner"]), []).append(_r)


def fmt(rs, n=8):
    return "；".join(f"{r['file']}:{r['line']} {r['owner']}={r['h']}px({r['method']})"
                    for r in rs[:n]) + ("…" if len(rs) > n else "")


# ============================================================
print("=== H 扫描器本身可信 ===")

check("H1 全部生产源码可被 AST 解析（不静默跳过任何文件）",
      not PARSE_FAIL,
      "解析失败：" + "；".join(f"{n} ({m})" for n, m in PARSE_FAIL[:6]))

_bom = [n for n in FILES if read_bytes(n).startswith(b"\xef\xbb\xbf")]
_bom_ok = bool(_bom) and all(
    read_bytes(n).decode("utf-8").startswith("\ufeff") for n in _bom
) and not PARSE_FAIL
check("H1b 带 BOM 的源码被 utf-8-sig 正确读入（这正是首版静默跳过 2 个文件的根因）",
      _bom_ok,
      f"带 BOM 文件={_bom}；其中按 utf-8 读仍会带 U+FEFF 的应有全部")
note(f"带 UTF-8 BOM 的生产源码：{_bom or '（无）'}")

check("H2a 扫描覆盖的文件数达标（≥140）", len(FILES) >= 140,
      f"实际 {len(FILES)} 个文件")
check("H2b AST 识别到的可点控件变量数达标（≥200）", len(CLICKY_OWNERS) >= 200,
      f"实际 {len(CLICKY_OWNERS)} 个")
check("H2c 扫到的 <40px 命中数达标（≥100）—— 防空谓词恒绿", len(U40) >= 100,
      f"实际 {len(U40)} 处")
check("H2d 口径已声明：setMinimum*（下限≠定值）不计入硬底线",
      all(r["method"] in FLOOR_M for r in FLOOR_ONLY),
      "FLOOR_ONLY 里混进了非 setMinimum* 记录")
note(f"下限类（不计入）{len(FLOOR_ONLY)} 条；定值/封顶类（计入）{len(HARD)} 条")
note(f"按写法：{ {m: sum(1 for r in UNION if r['method'] == m) for m in sorted({r['method'] for r in UNION})} }")

# 高度提取器自证 —— 这条不是形式主义：本套件首版就在这里翻过车。
# `setFixedSize(56, 26)` 的**高度是第 2 个参数**；若实现退化成"先看方法名在集合里
# 就返回第 1 个参数"，就会把 56 当高度 → 那两处 26px 静默消失，
# 而 F2（不得新增）与 F3（登记值一致）**都会照样绿**——因为没有记录就没有违规。
def _h(src):
    return _height(ast.parse(src).body[0].value)


check("H2e 高度提取器自证：setFixedSize(56, 26) 取到 26（不是宽度 56）",
      _h("x.setFixedSize(56, 26)") == 26, f"实际取到 {_h('x.setFixedSize(56, 26)')}")
check("H2f 高度提取器自证：setFixedHeight(18) / setMaximumSize(200, 20) 取到 18 / 20",
      _h("x.setFixedHeight(18)") == 18 and _h("x.setMaximumSize(200, 20)") == 20,
      f"实际 {_h('x.setFixedHeight(18)')} / {_h('x.setMaximumSize(200, 20)')}")
check("H2g 高度提取器自证：非尺寸调用（setStyleSheet / setToolTip）取到 None",
      _h('x.setStyleSheet("a")') is None and _h('x.setToolTip("a")') is None)

print("\n=== F 硬底线与存量冻结 ===")

check(f"F1 没有任何可点控件低于硬底线 {HARD_FLOOR}px", not TINY, fmt(TINY))

_extra = [r for r in SMALL if (r["file"], r["owner"]) not in ACCEPTED]
check(f"F2 <{TARGET}px 的可点控件不得新增（须逐条登记在 ACCEPTED）",
      not _extra,
      "未登记的新增项：" + fmt(_extra))

_drift = []
for (f, o), h in ACCEPTED.items():
    hits = [r["h"] for r in SMALL if (r["file"], r["owner"]) == (f, o)]
    if hits and any(x != h for x in hits):
        _drift.append(f"{f} {o}: 登记 {h}px，实测 {hits}")
check("F3 ACCEPTED 登记值与实测一致（改小/改大都算漂移）", not _drift, "；".join(_drift))
note(f"ACCEPTED {len(ACCEPTED)} 条；其中已不在 <{TARGET}px 名单里的（视为已修好）："
     f"{[f'{f} {o}' for (f, o) in ACCEPTED if not [r for r in SMALL if (r['file'], r['owner']) == (f, o)]] or '（无）'}")

print("\n=== P 本轮修好的 9 处（钉住 32px）===")

for _f, _o, _pin in PINS:
    _src_ok = _pin in BLANKED.get(_f, "")
    _hs = [r["h"] for r in BY_OWNER.get((_f, _o), [])] if (_f, _o) in BY_OWNER else []
    _ast_ok = bool(_hs) and all(x >= TARGET for x in _hs)
    check(f"P {_f} :: {_o} = {TARGET}px",
          _src_ok and _ast_ok,
          f"源码钉={_src_ok}；AST 实测高度={_hs or '（该 owner 未被扫描到）'}")

print("\n=== B 两种口径的盲区（显式记账，别以为单用一个就守住了）===")

_ast_only = [(r["file"], r["owner"]) for r in AST_RECS
             if r["h"] < 40 and (r["file"], r["owner"]) not in
             {(x["file"], x["owner"]) for x in HEUR_RECS if x["h"] < 40}]
_heur_only = [(r["file"], r["owner"]) for r in HEUR_RECS
              if r["h"] < 40 and (r["file"], r["owner"]) not in
              {(x["file"], x["owner"]) for x in AST_RECS if x["h"] < 40}]

check("B1 存在「AST 看得见、名字启发式看不见」的控件 —— 启发式确有盲区",
      bool(_ast_only), "并集里没有 AST 独有项，说明启发式已覆盖 AST（口径重复）")
check("B2 存在「名字启发式看得见、AST 看不见」的控件 —— AST 确有盲区（子类构造器）",
      bool(_heur_only), "并集里没有启发式独有项（AST 口径已能覆盖子类构造器？）")
check("B3 取并集不是装饰：并集严格大于任一单口径",
      len({(r['file'], r['owner']) for r in UNION}) >
      max(len({(r['file'], r['owner']) for r in AST_RECS}),
          len({(r['file'], r['owner']) for r in HEUR_RECS})),
      f"并集={len({(r['file'], r['owner']) for r in UNION})} "
      f"ast={len({(r['file'], r['owner']) for r in AST_RECS})} "
      f"heur={len({(r['file'], r['owner']) for r in HEUR_RECS})}")
note("AST 独有：" + (", ".join(f"{f}::{o}" for f, o in _ast_only[:6]) or "（无）"))
note("启发式独有：" + (", ".join(f"{f}::{o}" for f, o in _heur_only[:6]) or "（无）"))

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
