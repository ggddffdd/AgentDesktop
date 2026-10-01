# -*- coding: utf-8 -*-
"""批⑩ Claim-Evidence 断言-证据绑定 —— 探针（测驱动）

根治的关键转向：

    前八层做的是 **detective**（事后检测 / 猜模型意图）：
        批③猜「这句是不是在引用」、批⑤⑦猜「读完没有」、
        legion 猜「数字附近有没有 URL」。
        猜就有绕过空间——审查列的 5 种绕过本质都是让机器猜不出来。

    批⑩ 做的是 **declarative**（事前声明 / 机器只验证不推断）：
        工具结果先登记成编号证据 [EV#n]（批⑨ 已落地），
        模型引用时必须标注 ⟦EV#n⟧（可精确到第几行 ⟦EV#n:L12-30⟧），
        机器只问一句：**这条被引用的证据原文，到底支不支持这句断言**。

本探针覆盖：
    [A] 强证据 token 抽取（能不能被确定性回验的前提）
    [B] claim 句识别与豁免（防误报：hedging / 元话语 / 诚实否定 / 无 token）
    [C] 支持度判定（direct / unsupported / 幻觉编号 / 失败证据 / 行级精度）
    [D] 端到端 audit_warn
    [E] 让位设计（不启用时不抢批③ 的活，不重复标红）
    [F] 提示词层是否已宣告新协议

运行（MSYS 下必须用系统 Python，才有 PySide6）：
    C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe \
        tests/test_claim_evidence_195.py
"""

import os
import re
import sys
import json
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import evidence                       # noqa: E402
_TMPDIR = tempfile.mkdtemp(prefix="ce_test_")
evidence.set_dir(_TMPDIR)             # 必须在任何 register 之前

from PySide6.QtWidgets import QApplication   # noqa: E402
_app = QApplication.instance() or QApplication([])

import ui as UI                       # noqa: E402
from ui import ChatWindow as CW       # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, extra=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print("  [OK]   %s%s" % (name, ("  — %s" % extra) if extra else ""))
    else:
        _FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  — %s" % extra) if extra else ""))


# ───────────────────────── 测试夹具 ─────────────────────────

DOC_TEXT = "\n".join([
    "# 项目内部纪要",
    "",
    "## 第一节 · 密钥轮换",
    "",
    "- ROTATE-KEY-GAMMA：2026-09-28 轮换，负责人 王工，状态 挂起中。",
    "- 门槛：需安全部门复核。",
    "",
    "## 第二节 · 容量规划",
    "",
    "- 最终拍板：先归档、缓扩容，推迟到 2027Q1。",
    "- 当前配额 2TB，月增 380GB。",
    "",
    "## 第三节 · 值班安排",
    "",
    "- 共 138 节需要巡一遍。",
])

_FILLER = "\n".join("填充行 %d —— 本节为演示内容，与要点无关。" % i
                    for i in range(1, 61))
DOC_TEXT = DOC_TEXT + "\n" + _FILLER + "\n"


def _register_ok(text=DOC_TEXT, tool="read_file",
                 args=None):
    return evidence.register(tool, args or {"path": "D.md"}, text, ok=True)


def _register_fail(text="[RESULT NOT FOUND] 文件不存在：NO.md"):
    return evidence.register("read_file", {"path": "NO.md"}, text,
                             ok=False, err="文件不存在")


class _Sess:
    def __init__(self, m):
        self.messages = m


class _Store:
    def __init__(self, m):
        self._s = _Sess(m)
        self.active = lambda: self._s


class _Chat:
    def __init__(self):
        self.items = []

    def append(self, x):
        self.items.append(x)


def _mk_window(msgs):
    w = CW.__new__(CW)
    w.store = _Store(msgs)
    w.chat_view = _Chat()
    w._save_throttled = lambda: None
    return w


def _msgs(reply, ev_ids=(), ok_flags=None, tool="read_file"):
    """构造一轮会话：user → 若干 tool_log（带 evidence_id）→ assistant。"""
    m = [{"role": "user", "content": "[非图片文件: D.md] 核对一下内容"}]
    for i, eid in enumerate(ev_ids):
        m.append({
            "role": "tool_log", "name": tool,
            "args": json.dumps({"path": "D.md", "offset": i * 8000}),
            "result": "…摘要…",
            "evidence_id": int(eid),
            "evidence_ok": True if ok_flags is None else bool(ok_flags[i]),
        })
    m.append({"role": "assistant", "content": reply})
    return m


def run_audit(reply, ev_ids=(), ok_flags=None):
    msgs = _msgs(reply, ev_ids, ok_flags)
    w = _mk_window(msgs)
    w._audit_claim_evidence()
    return [m for m in msgs if m.get("role") == "audit_warn"], msgs


# ───────────────────────── [A] token 抽取 ─────────────────────────

print("=== [A] 强证据 token 抽取（确定性可回验的最小单元） ===")
try:
    cases = [
        ("版本号", "最新版是 v4.195.0，可以用了。", "v4.195.0"),
        ("百分比", "某平台份额为 73%，领先第二。", "73%"),
        ("文件路径", "配置写在 config.py 里。", "config.py"),
        ("标识符", "未完成的是 ROTATE-KEY-GAMMA 这项。", "ROTATE-KEY-GAMMA"),
        ("日期", "轮换日期是 2026-09-28。", "2026-09-28"),
        ("原文引用", "结论是「先归档、缓扩容」。", "先归档、缓扩容"),
    ]
    for name, sent, expect in cases:
        toks = CW._ce_extract_tokens(sent)
        vals = [t[1] for t in toks]
        check("A-「%s」能抽出 '%s'" % (name, expect), expect in vals, str(vals))

    t = CW._ce_extract_tokens("这个嘛，我看看再回复你，稍等一下哈。")
    check("A7 纯元话语无强 token → 不验", t == [], str(t))
except Exception as e:
    check("A 组异常", False, repr(e)[:120])


# ───────────────── [B] claim 句识别与豁免（防误报） ─────────────────

print("=== [B] claim 句识别与豁免 ===")
try:
    check("B1 hedging（推测类）豁免",
          not CW._ce_is_claim("这个数字大概是 73% 左右吧 ⟦EV#1⟧"))
    check("B2 元话语（我要先读）豁免",
          not CW._ce_is_claim("我先把全文读完再回答，避免遗漏"))
    check("B3 诚实否定（如实报告不存在）豁免",
          not CW._ce_is_claim("文件里没有 v9.999.0 这个版本，RESULT NOT FOUND"))
    check("B4 无强 token → 不验",
          not CW._ce_is_claim("这个思路挺好的，可以继续往下做"))
    check("B5 有强 token 的断言 → 需证据",
          CW._ce_is_claim("文件里记载了 v4.195.0"))
    check("B6 原文引用类断言 → 需证据",
          CW._ce_is_claim("结论是「先归档、缓扩容」⟦EV#1⟧"))
except Exception as e:
    check("B 组异常", False, repr(e)[:120])


# ─────────────────── [C] 支持度判定（核心判据） ───────────────────

print("=== [C] 支持度判定 ===")
try:
    EID_OK = _register_ok()
    EID_FAIL = _register_fail()
    evmap = CW._ce_collect_evidence_map([
        {"role": "tool_log", "name": "read_file", "evidence_id": EID_OK,
         "evidence_ok": True},
        {"role": "tool_log", "name": "read_file", "evidence_id": EID_FAIL,
         "evidence_ok": False},
    ])

    lv, info = CW._ce_verify_sentence(
        "文件里记载 v4.195.0 ⟦EV#%d⟧" % EID_OK, [(EID_OK, None, None)], evmap)
    # 注意：这句的 token 是 v4.195.0，而文档里写的是通用内容 → 应 unsupported
    check("C1 断言事实不在被引证据里 → unsupported",
          lv == "unsupported", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "未完成的是 ROTATE-KEY-GAMMA ⟦EV#%d⟧" % EID_OK,
        [(EID_OK, None, None)], evmap)
    check("C2 断言事实在被引证据里 → direct",
          lv == "direct", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "结论是「先归档、缓扩容」⟦EV#%d⟧" % EID_OK,
        [(EID_OK, None, None)], evmap)
    check("C3 原文引用（引号内容）能对上 → direct",
          lv == "direct", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "据记载是 v4.195.0 ⟦EV#9999⟧", [(9999, None, None)], evmap)
    check("C4 引用不存在的证据编号（幻觉编号）→ hallucinated",
          lv == "hallucinated", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "文件里写明了 v4.195.0 ⟦EV#%d⟧" % EID_FAIL,
        [(EID_FAIL, None, None)], evmap)
    check("C5 引用失败调用作为支撑 → failed_evidence",
          lv == "failed_evidence", "%s %s" % (lv, info))

    # 行级精度：票据 TOKEN 在 DOC_TEXT 里存在（第 5 行左右），
    # 但指定 L40-70 区间（填充行）里没有 → 只按行区间回验应判 unsupported。
    lv, info = CW._ce_verify_sentence(
        "未完成的是 ROTATE-KEY-GAMMA ⟦EV#%d:L40-70⟧" % EID_OK,
        [(EID_OK, 40, 70)], evmap)
    check("C6 行级引用：指定区间内不支持 → unsupported（精度即漏检率）",
          lv == "unsupported", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "未完成的是 ROTATE-KEY-GAMMA ⟦EV#%d:L1-10⟧" % EID_OK,
        [(EID_OK, 1, 10)], evmap)
    check("C7 行级引用：指定区间内支持 → direct",
          lv == "direct", "%s %s" % (lv, info))

    lv, info = CW._ce_verify_sentence(
        "文档里有 v9.999.0，也有 ROTATE-KEY-GAMMA ⟦EV#%d⟧" % EID_OK,
        [(EID_OK, None, None)], evmap)
    check("C8 一句里部分要素查不到 → partial（同样不可采信，不许放行）",
          lv == "partial", "%s %s" % (lv, info))
except Exception as e:
    check("C 组异常", False, repr(e)[:120])


# ───────────────────── [D] 端到端 audit_warn ─────────────────────

print("=== [D] 端到端（store 里出不出警示卡） ===")
try:
    EID = _register_ok()

    # 一句里两个事实：一个能在证据里核实、一个是编的 → 必须出卡
    w1, _ = run_audit(
        "文件里记载了 v9.999.0 ⟦EV#%d⟧，另外 ROTATE-KEY-GAMMA 也在 ⟦EV#%d⟧。"
        % (EID, EID), ev_ids=[EID])
    check("D1 一句里一个对一个错 → 出 1 张警示卡（不许因partial放行）",
          len(w1) == 1,
          "%d 张：%s" % (len(w1), (w1[0]["content"][:100] if w1 else "无")))
    if w1:
        c = w1[0]["content"]
        check("D1b 警示里点出具体断言片段", "v9.999.0" in c, c[:120])
        check("D1c 警示里说明它引用了哪条证据", ("EV#%d" % EID) in c, c[:120])
        check("D1d partial 场景文案说明「只有部分要素查到」",
              "部分要素" in c, c[:160])

    w2, _ = run_audit(
        "未完成的是 ROTATE-KEY-GAMMA ⟦EV#%d⟧；日期 2026-09-28 ⟦EV#%d⟧。"
        % (EID, EID), ev_ids=[EID])
    check("D2 断言全部能在证据里核实 → 不出卡", len(w2) == 0,
          "%d 张：%s" % (len(w2), (w2[0]["content"][:100] if w2 else "无")))

    w3, _ = run_audit("话说 TOOL 报错 v9.999.0 ⟦EV#4242⟧。", ev_ids=[EID])
    check("D3 幻觉证据编号 → 出卡并标明编号不存在", len(w3) == 1,
          "%d 张：%s" % (len(w3), (w3[0]["content"][:110] if w3 else "无")))

    EIDF = _register_fail()
    w4, _ = run_audit("失败调用里写明了 v4.195.0 ⟦EV#%d⟧。" % EIDF,
                      ev_ids=[EIDF], ok_flags=[False])
    check("D4 引用失败调用 → 出卡", len(w4) == 1,
          "%d 张：%s" % (len(w4), (w4[0]["content"][:110] if w4 else "无")))

    w5, _ = run_audit("好的，我来看一下这个问题。", ev_ids=[EID])
    check("D5 无事实断言的普通回复 → 不出卡", len(w5) == 0,
          "%d 张" % len(w5))
except Exception as e:
    check("D 组异常", False, repr(e)[:120])


# ─────────────────── [E] 让位设计（不抢别人的活） ───────────────────

print("=== [E] 让位与渐进生效 ===")
try:
    EID = _register_ok()

    # E1：本轮没有任何 evidence_id（旧会话 / 批⑨ 未启用）→ 本层不介入
    msgs = [{"role": "user", "content": "核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": "D.md"}), "result": "…"},
            {"role": "assistant", "content": "文件里记载了 v9.999.0"}]
    w = _mk_window(msgs)
    w._audit_claim_evidence()
    warns = [m for m in msgs if m.get("role") == "audit_warn"]
    check("E1 无 evidence_id → 本层不启用（让位批③）", len(warns) == 0,
          "%d 张" % len(warns))

    # E2：回复完全没用 ⟦EV#n⟧ 协议 → 不做裸断言扫描（批③接管，避免双标）
    w2, _ = run_audit("文件里记载了 v9.999.0，还有 config.py 也提到。",
                      ev_ids=[EID])
    check("E2 回复未使用 EV 协议 → 不扫裸断言（渐进，不批量标红）",
          len(w2) == 0, "%d 张" % len(w2))

    # E3：同一回复里一部分标了、一部分没标 → 不一致，值得单独提示
    w3, _ = run_audit(
        "未完成的是 ROTATE-KEY-GAMMA ⟦EV#%d⟧。此外 config.py 里也有 v9.999.0。"
        % EID, ev_ids=[EID])
    check("E3 同一回复里标注不一致 → 提示裸断言", len(w3) >= 1,
          "%d 张：%s" % (len(w3), (w3[0]["content"][:100] if w3 else "无")))
except Exception as e:
    check("E 组异常", False, repr(e)[:120])


# ─────────────────── [F] 提示词是否宣告协议 ───────────────────

print("=== [F] 提示词宣告（预防优于检测） ===")
try:
    import config
    sysp = config.AGENT_SYS_APPEND
    check("F1 提示词宣告了 ⟦EV#n⟧ 引用协议", "EV#" in sysp and "⟦" in sysp)
    check("F2 提示词明确「无证据不得编 / 必须如实说没有」",
          ("没有就说没有" in sysp) or ("禁止编" in sysp)
          or ("不得编造" in sysp),
          "缺失" if not (("没有就说没有" in sysp) or ("禁止编" in sysp)
                         or ("不得编造" in sysp)) else "已含")
except Exception as e:
    check("F 组异常", False, repr(e)[:120])


print("=" * 66)
print("批⑩ 探针结果：PASS=%d FAIL=%d" % (_PASS, _FAIL))
print("=" * 66)
sys.exit(1 if _FAIL else 0)
