# -*- coding: utf-8 -*-
"""test_tokens_200 —— 三期①间距 token + 三期②标签族收口 的守卫

    python tests/test_tokens_200.py

判据设计原则（沿用 L160/L161 的教训）
----------------------------------
判据打在**性质**上，不打在源码字面量上；每条都要能被「注入一个真实会犯的错」
打红（`_perturb_200.py` 负责验证这一点，全绿不等于有效）。

四条主防线：
A 间距 token 的派生关系（不是第二份手写字典）
B 标签族改写前/后的 QSS **属性集合等价**（视觉零变化的可执行定义）
C 收口干净度（残留裸写只能等于已豁免的 backlog 数，新增即红）
D label_* 必须联动字号 token（证明它们不是写死的字符串副本）
"""
import ast
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAIL = []


TOTAL = 0


def check(name, ok, extra=""):
    global TOTAL
    TOTAL += 1
    tag = "OK  " if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  — {extra}" if extra and not ok else ""))
    if not ok:
        FAIL.append(name)


# ---------------------------------------------------------------- 环境
try:
    import theme_qss as tq
    from ui import THEME
except Exception as e:  # pragma: no cover
    print(f"环境不可用：{e}")
    sys.exit(1)

import _qss_scan_lib as Q  # noqa: E402

print("\n=== A 间距 token：派生而非镜像 ===")
EXPECT_S = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24}
src_th = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
src_tq = open(os.path.join(ROOT, "theme_qss.py"), encoding="utf-8").read()

check("A1 THEME 有六档 space_*", all("space_" + k in THEME for k in EXPECT_S))
check("A2 取值 == 设计稿 §3.3", {k: THEME.get("space_" + k) for k in EXPECT_S} == EXPECT_S,
      f"实际 {({k: THEME.get('space_' + k) for k in EXPECT_S})}")
check("A3 派生关系成立（S 来自 THEME，不是第二份手写）",
      all(tq.S[k] == THEME["space_" + k] for k in EXPECT_S))
check("A4 S 是 int / gap 是带 px 的 str",
      all(isinstance(tq.S[k], int) and isinstance(tq.gap(k), str)
          and tq.gap(k) == f"{tq.S[k]}px" for k in EXPECT_S))
# 若 theme_qss.py 里又写死一份数字，说明退化成了 F 那种双源镜像
check("A5 theme_qss 没有第二份间距字面量",
      not any(f'"{k}": {v}' in src_tq or f"'{k}': {v}" in src_tq
              for k, v in EXPECT_S.items()),
      "theme_qss.py 里出现了写死的间距数字")
check("A6 gap() 落到实处能拼出 QSS 片段",
      all(tq.gap(k).endswith("px") and tq.gap(k)[:-2].isdigit() for k in EXPECT_S))

print("\n=== B 标签族：改前/改后属性集合等价（视觉零变化）===")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "labels_200_before.json")
check("B0 改前冻结夹具存在",
      os.path.isfile(FIXTURE), f"缺失 {FIXTURE}")


def snap_of(src_dir):
    env = Q.build_env(src_dir, tq=tq)
    out = {}
    for fn in Q.SCAN_TARGETS:
        path = os.path.join(src_dir, fn)
        if not os.path.isfile(path):
            continue
        # 补本文件的本地样式 helper（v4.201.0：`_chk_style()` 这类不在 theme_qss
        # 里的函数若解析不出来，整条 setStyleSheet 会从快照里消失，B1 直接假红）。
        # 必须每文件单独补 —— 不同文件的同名 helper 实现不同，共用 env 会串。
        fenv = dict(env)
        fenv.update(Q.local_qss_funcs(src_dir, fn, fenv))
        calls, _ = Q.parse_calls(src_dir, fn, fenv)
        bag = []
        for c in calls:
            for sel, props in c["rules"]:
                if props:
                    bag.append((sel, json.dumps(props, sort_keys=True)))
        out[fn] = bag
    return out


if os.path.isfile(FIXTURE):
    before_raw = json.load(open(FIXTURE, encoding="utf-8"))["bag"]
    # before 快照是按 {file: [[(sel, props_json)]]} 存的（snap 脚本 double-wrap 了）
    before = {fn: [tuple(x) for x in items] for fn, items in before_raw.items()}
    after_raw = snap_of(ROOT)
    after = {fn: v for fn, v in after_raw.items()}

    # v4.206.0（空状态组件化）**有意**把 8 条 QSS 从调用点搬进了组件工厂
    # （empty_state.py + theme_qss.empty_*）。这些不是走样，是搬家 ——
    # 逐条登记在下面，判据时先扣除，剩下的必须完全相等。
    # ⚠️ 为什么不直接重拍基线：重拍会把「收口零变化」这个历史锚点一起丢掉，
    #    以后再动样式就没人拦了。登记制保住了锚点，也留下了可审计的差异清单。
    # 生成方式：python _diff_206.py（差异必须全部是 before-only，无新增）
    KNOWN_MIGRATIONS_206 = {
        "ui.py": collections.Counter({
            # 会话空态（v4.112 那段内联 QSS）→ empty_state / theme_qss.empty_*
            ("", '{"QPushButton": "hover"}'): 1,
            ("QPushButton:hover", '{"background": "#1765CC"}'): 1,
            ("", '{"background": "transparent"}'): 1,
            ("", '{"color": "#6B7280", "font-size": "12px"}'): 1,
            ("", '{"background": "rgba(26,115,232,0.08)", "border-radius": "22px"}'): 1,
            ("", '{"color": "#5F6368", "font-size": "13px", "font-weight": "600",'
                 ' "padding-top": "4px"}'): 1,
            ("QPushButton", '{"background": "#1A73E8", "border": "none",'
                            ' "border-radius": "16px", "color": "#FFFFFF",'
                            ' "font-size": "12px", "font-weight": "600",'
                            ' "padding": "0 16px"}'): 1,
        }),
        "automation_panel.py": collections.Counter({
            # 自动化空态那一行居中灰字 → empty_state（有意变化：补了徽章+按钮）
            ("", '{"color": "#5F6368", "font-size": "13px", "padding": "24px"}'): 1,
        }),
    }

    # v4.207.0：会话管理搜索空态从内联 QLabel 迁到 empty_state(compact=True)。
    # 旧写法 `color:#6B7280; font-size:13px; padding:12px 0`（13px 灰字）
    # → compact 形态（12px/dim，无背景）—— 属**有意的视觉变化**（窄栏需要更轻），
    # 所以按登记制扣除，而不是重拍基线（重拍会丢掉 v4.200 的历史锚点）。
    KNOWN_MIGRATIONS_207 = {
        "ui.py": collections.Counter({
            ("", '{"color": "#6B7280", "font-size": "13px", "padding": "12px 0"}'): 1,
        }),
    }

    # 与上面两条**不同性质**的一类登记：不是「旧样式迁走了」，而是
    # **同一个已知属性多了一个实例** —— 新加了一个页面外壳。
    #
    # 背景（2026-10-03）：节点画布集成步 dfd1fd8 把「画布」作为第 3 页挂进主窗口。
    # 本文件里**每一页**都按既有约定写同一句
    # `X_page.setStyleSheet(f"background:{THEME['bg']};")`
    # （welcome / chat / orchestrate / legion / image / video 全如此），
    # 画布页只是照办。B1 用**多重集**比较，同一 (sel, props) 由 6 份变 7 份也会红。
    # 但这不是样式词条变更 —— 词条早就在，配色没动过一丝；多的是「一个页面实例」。
    # 若为此去删掉画布页那一句，反而会让它成为唯一不遵守约定的页面（更糟）。
    # 所以按既有登记制显式登记，并配 B1b 同款反向判据（登记条目必须真被消耗），
    # 既保住 v4.200 历史锚点，也留下可审计的清单。
    # ⚠️ 注意边界：**全新 (sel, props) 词条仍然一律红、没有登记机制** —— 那才是样式走样。
    #    这里只吸收「已存在词条的 +1 实例」，且写死条数（多来一份照样红）。
    KNOWN_ADDITIONS = {
        "ui.py": collections.Counter({
            ("", '{"background": "#F7F8FC"}'): 1,   # 画布页外壳（第 3 页），同页 0/1/2/4/5…
        }),
    }

    files = sorted(set(before) | set(after))
    for fn in files:
        cb = collections.Counter(before.get(fn, []))
        ca = collections.Counter(after.get(fn, []))
        # 两批登记合并：v4.206 的 8 条 + v4.207 的 1 条
        known = (KNOWN_MIGRATIONS_206.get(fn, collections.Counter())
                 + KNOWN_MIGRATIONS_207.get(fn, collections.Counter()))
        known_add = KNOWN_ADDITIONS.get(fn, collections.Counter())
        # 扣掉已登记的有意迁移后，剩下的差异必须为零
        rest_before = cb - ca - known
        # 扣掉已登记的「同词条 +1 实例」后，剩下的**新增**一律红
        rest_after = ca - cb - known_add
        check(f"B1[{fn}] 属性多重集等价（已扣除登记的 {sum(known.values())} 条迁移"
              f" + {sum(known_add.values())} 条新增实例）",
              (not rest_before) and (not rest_after),
              f"未登记改前差异 {len(rest_before)} / 改后差异 {len(rest_after)}"
              + (f" | 例 {list(rest_before)[:2]}" if rest_before else ""))
        # 反向判据：登记的条目必须真的被消耗 —— 清单里塞一条不存在的差异蒙混过关
        unused = known - (cb - ca)
        check(f"B1b[{fn}] 登记的迁移条目都被真正消耗（不许塞空条目）",
              not unused, f"未被消耗 {len(unused)} 条：{list(unused)[:1]}")
        # 反向判据（新增实例同理）：登记了却没多出来 → 说明这行登记已经过期/写错，
        # 会悄悄放宽后续的判断（拉高阈值后真实走样就溜过去了）。
        unused_add = known_add - (ca - cb)
        check(f"B1c[{fn}] 登记的新增实例都被真正消耗（不许塞空条目）",
              not unused_add, f"未被消耗 {len(unused_add)} 条：{list(unused_add)[:1]}")
    check("B2 快照非空（防止负负得正的空比对）",
          sum(len(v) for v in before.values()) > 400,
          f"before 仅 {sum(len(v) for v in before.values())} 条")

print("\n=== C 收口干净度：不许回到裸写 ===")
# 重跑一次勘查，剩下的「可自动收口却还是裸写」的条目应当正好等于豁免数
# 12px + text 色这 4 处是非标组合（theme_qss 无对应默认函数），本期显式留白；
# 因为不在上表的语义集合里，count_raw_labels 不会统计它们，故豁免数为 0。
EXEMPT = 0


def _uses_theme_qss(node):
    """参数的 AST 里有没有调用 theme_qss 的标签函数（= 已收口）。

    必须看**源码形态**而不是求值结果：收口后的 `label_second()` 求值出来仍是
    `color:dim;font-size:12px`，用结果判「是否裸写」会把已收口的 83 处全算成残留。
    """
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in Q.QSS_FUNCS):
            return True
    return False


def count_raw_labels(src_dir):
    """仍在裸写（没走 theme_qss）的标准标签族数目。"""
    env = Q.build_env(src_dir, tq=tq)
    color2key = {v: k for k, v in env["THEME"].items()
                 if k in ("faint", "dim", "text")}
    table = {("12px", None, "faint"), ("12px", None, "dim"),
             ("13px", None, "text"), ("15px", "600", "text"),
             ("20px", "700", "text")}
    n = 0
    for fn in Q.SCAN_TARGETS:
        path = os.path.join(src_dir, fn)
        if not os.path.isfile(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setStyleSheet" and node.args):
                continue
            val = Q.eval_expr(node.args[0], env)
            if not isinstance(val, str):
                continue
            rules = list(Q.iter_rules(val))
            if len(rules) != 1:
                continue
            sel, props = rules[0]
            p = dict(props)
            if p.get("background") == "transparent":
                p.pop("background")
            if not set(p) <= {"color", "font-size", "font-weight"}:
                continue
            if "color" not in p or "font-size" not in p:
                continue
            combo = (p["font-size"], p.get("font-weight"), color2key.get(p["color"]))
            if combo in table and not _uses_theme_qss(node.args[0]):
                n += 1
    return n


left = count_raw_labels(ROOT)
check(f"C1 残留裸写 == 豁免数 {EXEMPT}", left == EXEMPT, f"实测残留 {left}")
calls_after = sum(1 for fn in Q.SCAN_TARGETS
                  if os.path.isfile(os.path.join(ROOT, fn))
                  for line in open(os.path.join(ROOT, fn), encoding="utf-8")
                  if "label_second()" in line or "label_body()" in line
                  or "label_title()" in line or "label_title_xl()" in line
                  or "label_micro()" in line)
check("C2 收口调用点不少于 80 处", calls_after >= 80, f"实测 {calls_after}")
# backlog 也要有锁：非标组合只准是那已知 4 处，不许悄悄长大
_backlog = 0
_env = Q.build_env(ROOT, tq=tq)
for _fn in Q.SCAN_TARGETS:
    _p_ = os.path.join(ROOT, _fn)
    if not os.path.isfile(_p_):
        continue
    _t = ast.parse(open(_p_, encoding="utf-8").read())
    for _n in ast.walk(_t):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "setStyleSheet" and _n.args):
            continue
        _v = Q.eval_expr(_n.args[0], _env)
        if not isinstance(_v, str):
            continue
        _r = list(Q.iter_rules(_v))
        if len(_r) != 1:
            continue
        _props = _r[0][1]
        # 属性必须只是「字号+色值」组合（可外挂 background:transparent）；
        # 带 border/padding 的是「卡片式标签」，属控件族，不算这批 backlog；
        # 12px+600+text 是另一类（加粗小标签），同样不在内。
        if (_r[0][0] == ""
                and set(_props) <= {"font-size", "color", "background"}
                and _props.get("background", "transparent") == "transparent"
                and _props.get("font-size") == "12px"
                and _props.get("color") == THEME["text"]
                and _props.get("font-weight") is None
                and not _uses_theme_qss(_n.args[0])):
            _backlog += 1
# v4.202.0：12px+text 非标 4 处已收口 → 走 label_second("text")，残留应为 0。
# 锁的**性质没变**（不许回到裸写、不许悄悄长大）：以前是「恒为 4」，现在是「恒为 0」；
# 一旦有人再写回裸写，或新增一处非标，这里立刻 > 0。
check("C3 12px+text 非标已收口（残留 == 0）", _backlog == 0, f"实测残留 {_backlog}")

print("\n=== F 控件族收口（v4.201.0：透明滚动区 13 处）===")


def _env_for(src_dir, fn):
    e = Q.build_env(src_dir, tq=tq)
    e.update(Q.local_qss_funcs(src_dir, fn, e))
    return e


def _scan_scroll(src_dir):
    """返回 (裸写残留数, 走了 scroll_transparent 的处数)。

    判「是否裸写」看**源码形态**不看求值结果（与 C 同理）：收口后求值出来仍是
    `QScrollArea{border:none;background:transparent;}`，用结果判会把已收口的
    13 处全算成残留。
    """
    want = frozenset({"border": "none", "background": "transparent"}.items())
    raw = collected = 0
    for fn in Q.SCAN_TARGETS:
        p = os.path.join(src_dir, fn)
        if not os.path.isfile(p):
            continue
        tree = ast.parse(open(p, encoding="utf-8").read())
        env = _env_for(src_dir, fn)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setStyleSheet" and node.args):
                continue
            val = Q.eval_expr(node.args[0], env)
            if not isinstance(val, str):
                continue
            for sel, props in Q.iter_rules(val):
                if sel != "QScrollArea" or frozenset(props.items()) != want:
                    continue
                if _uses_theme_qss(node.args[0]):
                    collected += 1
                else:
                    raw += 1
    return raw, collected


_raw_scroll, _n_scroll = _scan_scroll(ROOT)
check("F1 透明滚动区裸写残留 == 0", _raw_scroll == 0, f"实测残留 {_raw_scroll}")
check("F2 scroll_transparent 调用点 == 13", _n_scroll == 13, f"实测 {_n_scroll}")
_sr = dict(Q.iter_rules(tq.scroll_transparent())).get("QScrollArea", {})
check("F3 scroll_transparent 输出属性集合正确",
      frozenset(_sr.items())
      == frozenset({"border": "none", "background": "transparent"}.items()),
      f"实际 {_sr}")
# 锁住本轮补的基建：本地 helper 必须能静态求值，否则快照会静默丢规则
_lf = Q.local_qss_funcs(ROOT, "digital_twin_panel.py", Q.build_env(ROOT, tq=tq))
check("F4 本地 helper `_chk_style` 可被静态求值（判据不再失明）",
      "_chk_style" in _lf and "QCheckBox" in _lf["_chk_style"]())

print("\n=== D label_* 必须联动字号 token（不是写死副本）===")


def props_of(q):
    return dict((sel, props) for sel, props in Q.iter_rules(q))


pm = props_of(tq.label_micro())
ps = props_of(tq.label_second())
pb = props_of(tq.label_body())
pt = props_of(tq.label_title())
px = props_of(tq.label_title_xl())
for name, d, want_key in (("label_micro", pm, "micro"), ("label_second", ps, "second"),
                          ("label_body", pb, "body"), ("label_title", pt, "title"),
                          ("label_title_xl", px, "title_xl")):
    body = d.get("", {})
    check(f"D[{name}] 字号取自 F['{want_key}']", body.get("font-size") == tq.F[want_key])
check("D[label_*] 色值取自 THEME token",
      ps.get("", {}).get("color") == THEME["dim"]
      and pb.get("", {}).get("color") == THEME["text"])
check("D[title/title_xl] 带 600/700 字重",
      pt.get("", {}).get("font-weight") == "600"
      and px.get("", {}).get("font-weight") == "700")
# 改 token 必须能带动输出（写死副本就带不动）
_old = tq.F["second"]
tq.F["second"] = "99px"
_follow = props_of(tq.label_second()).get("", {}).get("font-size") == "99px"
tq.F["second"] = _old
check("D2 改 F['second'] 能带动 label_second 输出（证明是真引用）", _follow)

print("\n=== E UI 源码合规 ===")
# 裸 hex 由 ui_hex_guard.py 专管，这里不重复管辖（L161：护栏范围重叠必出假红）
guarded = len([l for l in src_tq.splitlines() if "THEME[" in l])
check("E1 theme_qss 全部走 THEME token（无写死色值）", guarded > 30, f"仅 {guarded} 行引用 THEME")
tree_tq = ast.parse(src_tq)
_has_space_literal = any(
    isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
    and not isinstance(n.value, bool) and n.value in EXPECT_S.values()
    and any(isinstance(p, ast.Dict) for p in [getattr(n, "_parent", None)])
    for n in ast.walk(tree_tq))
check("E2 theme_qss 未把间距数字写进自己的字典", not _has_space_literal)

# run_all 靠输出里的 `PASS=<n>` / `FAIL=<n>` 判定结果（tests/run_all.py L15 的约定）；
# 缺了这两行会被判 [EMPT] 并计入失败套件 —— 探针内部全绿也会被算成失败。
print("\n=== G 加粗变体收口（v4.203.0：31 处 / 4 文件）===")

_BY_SIZE = ("12px", "13px", "15px", "20px")   # 有对应 label_* token 的字号
_bold_left = 0      # 有 token 却仍裸写 → 必须 0
_no_token = 0       # 无 token 的孤例（16px/28px）→ 锁住不许长大


def _count_weight(bag, w):
    return sum(1 for fn in bag for sel, pj in bag[fn]
               if json.loads(pj).get("font-weight") == w)


for _fn in Q.SCAN_TARGETS:
    _p = os.path.join(ROOT, _fn)
    if not os.path.isfile(_p):
        continue
    _e = _env_for(ROOT, _fn)
    for _n in ast.walk(ast.parse(open(_p, encoding="utf-8").read())):
        if not (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "setStyleSheet" and _n.args):
            continue
        _v = Q.eval_expr(_n.args[0], _e)
        if not isinstance(_v, str):
            continue
        _r = list(Q.iter_rules(_v))
        if len(_r) != 1 or _r[0][0] != "" or "font-weight" not in _r[0][1]:
            continue
        if _uses_theme_qss(_n.args[0]):
            continue                      # 已收口
        if _r[0][1].get("font-size") in _BY_SIZE:
            _bold_left += 1
        else:
            _no_token += 1

check("G1 有字号 token 的加粗变体已收口（残留 == 0）", _bold_left == 0,
      f"实测残留 {_bold_left}")
check("G2 无字号 token 的孤例恒为 4（16px x3 + 28px x1，不许长大）",
      _no_token == 4, f"实测 {_no_token}")

check("G3 label_second 默认不带 font-weight（102 处旧调用零影响）",
      "font-weight" not in tq.label_second())
check("G4 weight 取自 W 字典（不是写死数字）",
      "W[weight]" in open(os.path.join(ROOT, "theme_qss.py"), encoding="utf-8").read())

_bag_now = snap_of(ROOT)
# 写「快照里还有 500」是弱判据 —— 别处恰好有 500 就永远绿（扰动 ⑬ 实测确实没红）。
# 改成直接问 token：走 medium 出来的必须还是 500。
check("G5 500 字重保留（未归一到 600 —— 归一属视觉变更，须明确批准）",
      "font-weight:500;" in tq.label_second(weight="medium"),
      f"label_second(weight='medium') 输出 {tq.label_second(weight='medium')!r}")
check("G6 600/700 字重仍在（收口没有把字重抹平）",
      _count_weight(_bag_now, "600") >= 1 and _count_weight(_bag_now, "700") >= 1)

print(f"\n=== 汇总：{len(FAIL)} 条失败 ===")
for f in FAIL:
    print("   ✗", f)
# 总数改成实测计数：写死 24 时每加一条判据都要人肉改，忘了就会出现
# 「PASS 数比实际少」的假象（v4.201.0 加 F 段时就会踩到）。
print(f"PASS={TOTAL - len(FAIL)} FAIL={len(FAIL)}")
sys.exit(1 if FAIL else 0)
