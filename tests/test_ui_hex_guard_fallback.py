# -*- coding: utf-8 -*-
"""UI 裸 hex 护栏 · 「THEME 兜底副本」豁免 判据

背景
----
护栏把「THEME 内颜色却写死」判为 SOFT 警告。但有一类写死是**必须**的：
`legion_status_widget.py` 的

    try:
        from ui import THEME
        return THEME
    except Exception:
        return {"bg": "#F7F8FC", "text": "#202124", ...}   # ← 这份

—— except 分支是「THEME 取不到时顶上」的备份（注释写明"仅离线/测试场景命中"）。
把它改成 `THEME[key]` 就成了自引用，正是它要兜的场景失效。护栏原先把这 12 处报成
SOFT「建议改 THEME[key]」，是**结构性误报**。

修复 = 护栏新增 `_theme_fallback_spans()`（AST）：`try` 体里有 `return THEME`、
某 `except` 分支里 `return {…字面量…}` → 该字面量占用的行**豁免**，并把豁免数
打进结果行（可见、不隐形放宽）。

本套件钉三层
------------
A `_theme_fallback_spans` 行为：认兜底形、不认近邻形（无 try / 返回非 Dict / 语法错）。
B 真仓生效：兜底那些 hex 确实不在 hits 里，且 `legion_status_widget.py` 有豁免。
C 豁免**不过宽**：同一份合成源码里，兜底 dict 被豁免而**普通裸 hex 照报**。

用法：python tests/test_ui_hex_guard_fallback.py
"""
import ast
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 可选：从扰动拷贝路径加载 ui_hex_guard（UIHG_PATH），做法同其它判据套件的路径覆盖
_uihg = os.environ.get("UIHG_PATH")
if _uihg and os.path.exists(_uihg):
    _spec = importlib.util.spec_from_file_location("ui_hex_guard", _uihg)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["ui_hex_guard"] = _mod
    _spec.loader.exec_module(_mod)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import ui_hex_guard as guard  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")


# 兜底形：8 行 `return {`，9/10 行是带 hex 的成员，11 行收尾
SRC_FALLBACK = (
    "from ui import THEME\n"
    "\n"
    "\n"
    "def _theme():\n"
    "    try:\n"
    "        return THEME\n"
    "    except Exception:\n"
    "        return {\n"
    '            "bg": "#F7F8FC",\n'
    '            "text": "#202124",\n'
    "        }\n"
)


def _first_line_of(src, needle):
    for i, l in enumerate(src.splitlines(), 1):
        if needle in l:
            return i
    return -1


# ---------------- A 识别器行为 ----------------
def group_A():
    spans = guard._theme_fallback_spans(SRC_FALLBACK)
    l_bg = _first_line_of(SRC_FALLBACK, "#F7F8FC")
    l_tx = _first_line_of(SRC_FALLBACK, "#202124")
    check("A1 兜底形 → 命中 dict 成员行",
          l_bg in spans and l_tx in spans, "spans=%s bg@%d tx@%d" % (sorted(spans), l_bg, l_tx))
    check("A2 兜底形 → 不含 try 里的 return THEME 行",
          _first_line_of(SRC_FALLBACK, "return THEME") not in spans)

    s2 = SRC_FALLBACK.replace("        return {\n", "        return None\n") \
                    .replace('            "bg": "#F7F8FC",\n', "") \
                    .replace('            "text": "#202124",\n', "") \
                    .replace("        }\n", "")
    check("A3 except 里 return None → 不豁免", guard._theme_fallback_spans(s2) == set(),
          str(sorted(guard._theme_fallback_spans(s2))))

    s3 = 'def f():\n    return {\n        "a": "#ABCDEF",\n    }\n'
    check("A4 没有 try/return THEME → 不豁免", guard._theme_fallback_spans(s3) == set())

    s4 = "def f(:\n    this is not python\n"
    check("A5 语法错误 → 静默返回空集（不抛）", guard._theme_fallback_spans(s4) == set())

    s5 = ("def f():\n    try:\n        return THEME\n"
          "    except Exception:\n        return OTHER_DICT\n")
    check("A6 except 里 return 变量（非 Dict 字面量）→ 不豁免",
          guard._theme_fallback_spans(s5) == set())


# ---------------- B 真仓生效 ----------------
def group_B():
    ok = guard.ui_hex_guard(str(ROOT))
    check("B1 真仓护栏通过（无 HARD 违规）", ok is True, str(ok))

    _approved, _files, hits, fallback_hits = guard._scan(str(ROOT))
    check("B2 真仓有兜底豁免命中（机制真在跑）", fallback_hits > 0, "fallback_hits=%d" % fallback_hits)

    lw = ROOT / "legion_status_widget.py"
    wsrc = lw.read_text(encoding="utf-8", errors="replace")
    spans = guard._theme_fallback_spans(wsrc)
    l_bg = _first_line_of(wsrc, '"bg": "#F7F8FC"')
    check("B3 legion_status_widget 的兜底行被识别",
          l_bg > 0 and l_bg in spans, "bg@%d spans=%s" % (l_bg, sorted(spans)[:8]))

    leaked = [h for h in hits if h[0].replace("\\", "/").endswith("legion_status_widget.py")]
    check("B4 该文件的裸 hex 一处都没进 hits（全被豁免）", not leaked, str(leaked[:5]))

    # 真仓 SOFT 应为 0（若不为 0，说明除兜底外还有「THEME 内写死」残留）
    hard_new, soft_new = [], []
    approved = guard._collect_approved_palette(str(ROOT))
    for rel, ln, h in hits:
        (soft_new if h in approved else hard_new).append((rel, ln, h))
    check("B5 真仓 HARD 违规为 0", not hard_new, str(hard_new[:5]))
    check("B6 真仓 SOFT 为 0（兜底豁免后无残留）", not soft_new, str(soft_new[:5]))


# ---------------- C 豁免不过宽 ----------------
def group_C():
    d = tempfile.mkdtemp(prefix="hexfb_")
    (Path(d) / "ui.py").write_text(
        'THEME = {\n    "bg": "#F7F8FC",\n    "text": "#202124",\n}\n', encoding="utf-8")
    (Path(d) / "zz_probe_widget.py").write_text(
        "from ui import THEME\n"
        "\n"
        "def _theme():\n"
        "    try:\n"
        "        return THEME\n"
        "    except Exception:\n"
        '        return {"bg": "#F7F8FC", "text": "#202124"}\n'
        "\n"
        'PLAIN1 = "#ABCDEF"\n'
        'PLAIN2 = {"c": "#123456"}\n', encoding="utf-8")

    _a, _f, hits, fb = guard._scan(d)
    hexes = {h[2] for h in hits}
    check("C1 兜底 dict 里的 hex 被豁免", "#f7f8fc" not in hexes and "#202124" not in hexes,
          str(sorted(hexes)))
    check("C2 同文件的普通裸 hex 照报（豁免不过宽）",
          "#abcdef" in hexes and "#123456" in hexes, str(sorted(hexes)))
    check("C3 豁免命中数被单独计数（可见）", fb == 2, "fb=%d" % fb)


def _run_part(fn):
    try:
        fn()
    except Exception as ex:
        global FAIL
        FAIL += 1
        print("  [FAIL] 组 %s 抛异常未跑完：%s: %s" % (fn.__name__, type(ex).__name__, ex))
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("== A 识别器行为 ==")
    _run_part(group_A)
    print("== B 真仓生效 ==")
    _run_part(group_B)
    print("== C 豁免不过宽 ==")
    _run_part(group_C)
    print()
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)
