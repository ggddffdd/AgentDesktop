# -*- coding: utf-8 -*-
"""BUG 审核 P2 四项补完验收（v4.210.0，DESIGN.md §12.8 / §13.5）

四项：
  P2-1 回执残留 → 分类登记表（在 test_receipt_copy_209.py 的 _REGISTRY 里）
  P2-2 欢迎页文案说谎 → 按"全集是否为空"分叉
  P2-3 军团删光最后一波 → 补空态
  P2-4 动态文案进状态栏 → 先压缩

判据分五组（独立运行：python tests/test_p2_210.py）：
  A P2-2 欢迎页      B P2-3 军团 0 波     C P2-4 动态压缩（行为级）
  D P2-1 登记表      E 文档与版本

写法约定（前几波踩出来的，别退回去）：
  L190 注释里引用旧值会被"无裸字面量"类判据扫到 —— 本文件注释不写十六进制色值。
  L201 关键词在场 ≠ 结构在场 —— 凡是"某个调用里有某参数"都走 `_call_block()`
       取到**那一个调用块**再判，不在整文件里搜关键词。
  L196 不写死当前版本号 —— 版本号一律跟随 config 解析。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

FAIL = []
CHECKED = 0


def check(name, ok, extra=""):
    global CHECKED
    CHECKED += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


def _hook(t, v, tb):
    print(f"\n[!] 未捕获异常：{t.__name__}: {v}")
    print(f"PASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
    sys.exit(1)


sys.excepthook = _hook


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def strip_comments_keep_lines(src):
    """剥注释但保留行号（丢行会让行号漂移，见本波踩过的 616 行偏移）。"""
    return "\n".join(
        "" if ln.strip().startswith("#") else re.sub(r"#.*$", "", ln)
        for ln in src.replace("\r\n", "\n").split("\n"))


def _call_block(src, title):
    """取出包含 `title` 的那个 empty_state(...) **调用块**（到括号配平为止）。

    为什么不整文件搜关键词：L201 —— 「还没有对话」这类串在文件别处也有，
    抹掉某个调用的参数后整文件搜照样绿。必须定位到**那一个调用**再判。
    """
    i = src.find(title)
    if i < 0:
        return ""
    j = src.rfind("empty_state(", 0, i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j + len("empty_state") , len(src)):
        if src[k] == "(":
            depth += 1
        elif src[k] == ")":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    return src[j:]


_ui = read("ui.py")
_lg = read("legion_ui.py")
_dp = read("director_panel.py")
_ui_s = strip_comments_keep_lines(_ui)
_lg_s = strip_comments_keep_lines(_lg)
_dp_s = strip_comments_keep_lines(_dp)
_design = read("DESIGN.md")

print("=== v4.210.0 BUG 审核 P2 补完验收（DESIGN §12.8 / §13.5）===")

# ------------------------------------------------- A P2-2 欢迎页文案说谎（ui.py）
print("\n--- A P2-2 欢迎页：有会话却被说成「还没有对话」 ---")
# A1：前置条件 —— recent 确实过滤了当前会话（没有这条前提，分叉就无从谈起）
check("A1 recent 的过滤条件里排除了当前激活会话（分叉的前提）",
      "s.sid != self.store.active_sid" in _ui_s)
# A2：分叉存在
check("A2 有按「全集是否为空」分叉的分支", "_has_any_session" in _ui_s)

_blk_filtered = _call_block(_ui_s, "这里是最近对话")
_blk_true_empty = _call_block(_ui_s, "还没有对话")
check("A3 「这里是最近对话」这个空态存在（过滤空分支）", bool(_blk_filtered))
check("A4 过滤空分支**不说**「还没有对话」（那句留给真空分支）",
      _blk_filtered and "还没有对话" not in _blk_filtered,
      _blk_filtered[:80])
check("A5 真空分支仍说「还没有对话」并给「新建对话」按钮",
      _blk_true_empty and "还没有对话" in _blk_true_empty
      and "action=" in _blk_true_empty, _blk_true_empty[:80])
check("A6 过滤空分支**不给** action 按钮（他已在会话里，再给一个是噪音）",
      _blk_filtered and "action=" not in _blk_filtered, _blk_filtered[:80])
check("A7 过滤空分支的指引指向真实存在的「查看全部」",
      _blk_filtered and "查看全部" in _blk_filtered, _blk_filtered[:80])
# A8：指引必须**真的可点** —— 光有文字不算（207 C9 同款）。
# 判据别写成 `clicked.connect`：那个是 QPushButton 的接法。这里「查看全部」是
# **QLabel**，QLabel 没有 clicked 信号，靠的是**覆写 mousePressEvent**。
# 第一版判据就是按 clicked.connect 写的，误判成"装饰文字"—— 记下来别再犯。
_lines = _ui_s.split("\n")
_va_i = next((i for i, l in enumerate(_lines)
              if "查看全部" in l and "QLabel" in l), None)
_va_seg = "\n".join(_lines[_va_i:_va_i + 8]) if _va_i is not None else ""
check("A8 「查看全部」确有点击处理（QLabel 走 mousePressEvent 覆写）",
      _va_i is not None and "mousePressEvent" in _va_seg,
      _va_seg[:80] or "没找到该 QLabel")
# A8c：光"有 mousePressEvent"不够 —— 覆写成 `lambda e: None` 也有这一行。
# 必须确认它真的**调到会话管理器**。
check("A8c 该覆写真实调用了 _open_session_manager（不是空实现）",
      "_open_session_manager" in _va_seg, _va_seg[:80])
check("A8b 该 QLabel 设了手型光标（可点是有视觉暗示的，不是隐藏交互）",
      "PointingHandCursor" in _va_seg, _va_seg[:80])
# A9：两个分支互斥（都有 return），否则会同时挂两个空态
_seg = _ui_s[_ui_s.find("_has_any_session"):]
_seg = _seg[:_seg.find("def ", 200)] if "def " in _seg[200:] else _seg
check("A9 过滤空分支以 return 收尾（两分支互斥，不会挂两个空态）",
      bool(re.search(r"_has_any_session[\s\S]{0,600}?return", _ui_s)))
# A10：守"分叉方向"。写成 `if not _has_any_session:` 的话，两句文案会**对调**，
# 有会话的人反而看到"还没有对话" —— 比不分叉还糟，而且 A2 查 "_has_any_session
# 在不在"是抓不到的。必须锁住"哪个文案在 if 分支里"。
check("A10 「这里是最近对话」确实在 `if _has_any_session:` 分支里（方向锁）",
      bool(re.search(r"if _has_any_session:[\s\S]{0,400}?这里是最近对话", _ui_s)))

# ------------------------------------------------- B P2-3 军团 0 波（legion_ui.py）
print("\n--- B P2-3 军团删光最后一波后整片空白 ---")
check("B1 有 0 波分支", "if not waves:" in _lg_s)
_blk_wave = _call_block(_lg_s, "还没有波次")
check("B2 0 波分支里挂了空态「还没有波次」", bool(_blk_wave))
check("B3 该空态指引顶部「+ 添加波次」",
      _blk_wave and "添加波次" in _blk_wave, _blk_wave[:80])
check("B4 该空态**不给** action 按钮（真按钮一直在面板顶部，不重复入口）",
      _blk_wave and "action=" not in _blk_wave, _blk_wave[:80])
# B5：位置 —— 必须在 for 循环之后、addStretch 之前
_i_waves = _lg_s.find("if not waves:")
_i_stretch = _lg_s.find("self.waves_lay.addStretch(1)", _i_waves)
check("B5 空态挂在循环之后、addStretch 之前（顺序错了会被 stretch 顶走）",
      _i_waves > 0 and _i_stretch > _i_waves
      and "empty_state(" in _lg_s[_i_waves:_i_stretch])
# B6：指引的按钮真实存在且可点
check("B6 「添加波次」按钮在 legion_ui 里真实存在并绑定了槽",
      bool(re.search(r"添加波次[\s\S]{0,300}?clicked\.connect", _lg_s))
      or bool(re.search(r"clicked\.connect[\s\S]{0,300}?添加波次", _lg_s)))

# ------------------------------------------------- C P2-4 动态文案压缩（行为级）
print("\n--- C P2-4 动态文案进状态栏前先压缩 ---")
try:
    import director_panel as _dpmod
except Exception as _e:                      # 缺 PySide6 时降级，不静默通过
    _dpmod = None
    print(f"  [warn] director_panel 导入失败（{_e}）—— C 组改为源码级判定")

if _dpmod is not None:
    _sd = getattr(_dpmod, "_status_dyn", None)
    check("C1 _status_dyn 存在", _sd is not None)
    _long = ("C:/Users/xyb/Documents/小臭玩AI/output/2026-10-03/"
             "final_export_with_a_very_long_name.mp4")
    _r_long = _sd(_long) if _sd else ""
    check("C2 长文本被压到上限内（<=36）", _sd and len(_r_long) <= 36,
          f"len={len(_r_long)}")
    check("C3 长文本结尾有省略号（截断是可感知的，不是硬切）",
          _sd and _r_long.endswith("…"), _r_long[-10:])
    check("C4 短文本原样返回（不无谓截断）",
          _sd and _sd("[Errno 2] No such file") == "[Errno 2] No such file")
    check("C5 多行堆栈被压成一行", _sd and "\n" not in _sd("a\nb\nc" * 60))
    check("C6 传 None 不崩（状态栏不会因异常而整体挂掉）",
          _sd and _sd(None) == "None", repr(_sd(None)) if _sd else "?")

check("C7 _STATUS_MAX 是 36 且注释里写了推导（不许拍脑袋改数）",
      "_STATUS_MAX = 36" in _dp_s
      and bool(re.search(r"_STATUS_MAX[\s\S]{0,80}?36", _dp))
      and "460" in _dp)
# C8：五处调用点都过了 _status_dyn（行级，不是整文件搜关键词）。
# 关键串必须**带足前缀**："导出失败" 是 "工程文件导出失败" 的**子串** ——
# 第一版判据就栽在这：把 `f"导出失败：{...}"` 那处退回裸 f-string，判据照样绿，
# 因为同文件里还有一处「工程文件导出失败」顶着。子串碰撞是这类判据的头号坑。
_call_lines = [l for l in _dp_s.split("\n") if "_status_dyn(" in l]
_hit = {'f"预填输入框失败：': False, '"合成失败：" +': False,
        'f"出错：': False, 'f"工程文件导出失败：': False, 'f"导出失败：': False}
for _l in _call_lines:
    for _k in _hit:
        if _k in _l:
            _hit[_k] = True
check("C8 五处动态文案都过 _status_dyn（关键串带前缀，防子串碰撞）",
      all(_hit.values()), [k for k, v in _hit.items() if not v])
# C9：只压状态栏那一份 —— 日志/错误标记仍记原文
check("C9 日志仍记原文（压缩只作用于状态栏那一份）",
      "_log(app," in _dp_s and "_director_agent_error" in _dp_s)
# C10：_set_status 本身**没有**被改成自动压缩（写死文案不该被动到）
_set_body = _dp_s[_dp_s.find("def _set_status("):]
_set_body = _set_body[:_set_body.find("\ndef ", 1)] if "\ndef " in _set_body[1:] else _set_body
check("C10 _set_status 本身没被改成自动压缩（写死文案不受影响）",
      "_status_dyn" not in _set_body)

# ------------------------------------------------- D P2-1 分类登记表
print("\n--- D P2-1 回执：白名单 → 分类登记表 ---")
_r209 = read("tests/test_receipt_copy_209.py")
check("D1 回执套件里有 _REGISTRY（分类登记表已就位）", "_REGISTRY = [" in _r209)
_entries = re.findall(r'^\s*\("([^"]+\.py)", "([^"]+)", "(改|留)",', _r209, re.M)
# 本波定稿 10 条（改 5 + 留 5）。用**下限**而不是等值：容许后续新增登记，
# 但**删条目必须红** —— 删一条就等于那一处又变成"没人处置"。
check("D2 登记表条目 >= 10（删一条就红：那处又变成没人处置）",
      len(_entries) >= 10, f"实际 {len(_entries)}")
_srcs = {"ui.py": _ui_s, "legion_ui.py": _lg_s, "director_panel.py": _dp_s,
         "workflow_manager_ui.py": strip_comments_keep_lines(
             read("workflow_manager_ui.py"))}
# D3：跨套件复核"改"的那几条真的没了（(?<!还) 是关键：还没有X 天然含 没有X）
_bad = [f"{f}::{k}" for f, k, d in _entries
        if d == "改" and re.search(r"(?<!还)" + re.escape(k), _srcs.get(f, ""))]
check("D3 标「改」的旧文案确实没了（后视断言，防『还没有X』子串误判）",
      not _bad, _bad)
# D4：至少有一条"留"登记在**续行**上 —— 证明扫描器必须吃跨行调用，
#     否则后人把 _iter_calls 改成单行扫描，D3a/D3b 会静默失效
_keep_lines = [(f, k) for f, k, d in _entries if d == "留"]
_on_continuation = []
for _f, _k in _keep_lines:
    _src = _srcs.get(_f)
    if not _src:
        continue
    for _l in _src.split("\n"):
        if _k in _l and not any(c in _l for c in
                                ("_set_status(", "QMessageBox.information(",
                                 "status_label.setText(", "chat_panel.say(")):
            _on_continuation.append(f"{_f}::{_k}")
            break
check("D4 至少一条「留」登记在**续行**上（逼着扫描器必须处理跨行调用）",
      bool(_on_continuation), "没有续行样本 —— 跨行扫描能力没被守护住")

# ------------------------------------------------- E 文档与版本
print("\n--- E 文档与版本 ---")
check("E1 DESIGN 有 §12.8（P2-2 / P2-3 的规格与两条共通原则）",
      bool(re.search(r"^### 12\.8 .*P2-2 / P2-3", _design, re.M)))
check("E2 DESIGN 有 §13.5（动态压缩 + 「还没有X」判定规则）",
      bool(re.search(r"^### 13\.5", _design, re.M)))
# E3：判定规则表 —— 行级判定（L201：整节搜"待办"会被别处的"待办"骗过）
_rule_rows = [ln for ln in _design.split("\n")
              if ln.startswith("|") and "还没有X" in ln and "待办" in ln]
check("E3 §13.5 有「还没有X = 待办语义」这一行（行级，不是整节关键词）",
      bool(_rule_rows))
_e_err = [ln for ln in _design.split("\n")
          if "拒绝执行" in ln and "err" in ln]
check("E4 §13.5 写了 err=True 的边界（拒绝执行 ≠ 状态说明，防后人误改）",
      bool(_e_err))
check("E5 CHANGELOG 有 v4.210.0 条目", "## v4.210.0" in read("CHANGELOG.md"))
_cf = read("config.py")
_m = re.search(r'APP_VERSION = "v(\d+)\.(\d+)\.(\d+)"', _cf)
_rd = read("README.md")
_i = _rd.find("当前版本")
_r = re.search(r"v\d+\.\d+\.\d+", _rd[_i:_i + 60]) if _i >= 0 else None
check("E6 config 能解析出版本号", _m is not None)
check("E7 README 与 config 版本一致（跟随式，不写死）",
      _m and _r and _m.group(0).split('"')[1] == _r.group(0),
      f"config={_m.group(0) if _m else '?'} README={_r.group(0) if _r else '?'}")
check("E8 版本不低于 v4.209.2（防回退）",
      _m and tuple(int(x) for x in _m.group(1, 2, 3)) >= (4, 209, 2),
      _m.group(0) if _m else "?")

print(f"\nPASS={CHECKED - len(FAIL)} FAIL={len(FAIL)}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("=== P2_210_OK ===")
