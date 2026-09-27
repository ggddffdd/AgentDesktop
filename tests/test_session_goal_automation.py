# -*- coding: utf-8 -*-
"""会话目标注入 + 内部标注不外泄 回归测试（v4.168.3）。

钉住两件事：

一、**陈旧「本会话目标」被当成任务**（v4.168.1 只做了止血，v4.168.3 修根因）
    自动化任务与用户对话**共用同一个会话**（`ui._fire_automation_run`
    把任务追加成一条 user 消息），而会话的 goal 是**建会话时第一句用户原话**、
    之后永不变。实测那个会话积了 18 条自动化任务消息，goal 却仍是
    「帮我写一段人生感悟口播稿…」—— 于是每一轮（自动化轮、用户说「继续」轮）
    都把这条陈旧线索塞进 system prompt，模型甚至把它当任务又做了一遍。
    → 会话被自动化任务借用过时，**不再注入 goal**，改注入中性说明。

二、**模型在正文里讨论内部注入**（纯噪音）
    实测回复原文：「这条注入又把【本会话目标】字段当成任务推给我了——按你定的规矩，
    注入字段只当背景…」。判断是对的（它确实拒绝了错误任务），但把
    「我在处理提示词注入」讲给用户听毫无意义。→ 加 `_META_SILENCE_RULE`。

纯标准库；用 AST 抽方法（注意：必须挂到**类**上，挂实例上不会绑定 self）。
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0
_UI_SRC = os.path.join(ROOT, "ui.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


_SRC = open(_UI_SRC, encoding="utf-8-sig").read()
_TREE = ast.parse(_SRC)
_CLS = next(n for n in ast.walk(_TREE)
            if isinstance(n, ast.ClassDef) and n.name == "ChatWindow")

_METHODS = ("_session_carries_automation", "_prompt_section_session_goal")


def _attr_const(name):
    """取类里 `name = "字面量"` 的值。"""
    for it in _CLS.body:
        if isinstance(it, ast.Assign):
            for t in it.targets:
                if isinstance(t, ast.Name) and t.id == name \
                        and isinstance(it.value, ast.Constant):
                    return it.value.value
    return None


def _make(**overrides):
    """造一个「类」并把方法与类属性挂上去，再实例化（L112 教训）。"""
    ns = {"re": re, "ast": ast}
    for it in _CLS.body:
        if isinstance(it, ast.FunctionDef) and it.name in _METHODS:
            exec(ast.get_source_segment(_SRC, it), ns)
    cls = type("FakeWin", (), {})
    for k in ("_AUTO_TASK_PREFIX", "_META_SILENCE_RULE"):
        v = _attr_const(k)
        if v is not None:
            setattr(cls, k, v)
    for m in _METHODS:
        if m in ns:
            setattr(cls, m, overrides.get(m, ns[m]))
    return cls()


class Sess:
    def __init__(self, messages=None, goal=""):
        self.messages = messages
        self.goal = goal


AUTO_MSG = "【自动化任务】抓取今天最新的 GitHub trending，整理成要点简报"
PLAIN_MSGS = [{"role": "user", "content": "帮我写个爬虫"},
              {"role": "assistant", "content": "好的"}]


# ---------------------------------------------------------------------------
def part_a_carries():
    print("\n-- A) 判据：会话是否被自动化任务借用过 --")
    w = _make()
    check("A1 含【自动化任务】user 消息 → True",
          w._session_carries_automation(Sess([{"role": "user", "content": AUTO_MSG}])) is True)
    check("A2 普通会话 → False",
          w._session_carries_automation(Sess(PLAIN_MSGS)) is False)
    check("A3 空消息 → False", w._session_carries_automation(Sess([])) is False)
    check("A4 messages=None → 不崩且 False",
          w._session_carries_automation(Sess(None)) is False)
    check("A5 含非 dict 元素 → 不崩",
          w._session_carries_automation(Sess(["垃圾", 1, None])) is False)
    check("A6 content=None → 不崩",
          w._session_carries_automation(Sess([{"role": "user", "content": None}])) is False)
    check("A7 content 为 list（多模态）→ 不崩",
          w._session_carries_automation(
              Sess([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])) is False)
    check("A8 前缀在句中（非开头）→ 不算（要求 startswith）",
          w._session_carries_automation(
              Sess([{"role": "user", "content": "我说【自动化任务】挺好用"}])) is False)
    check("A9 assistant 带前缀 → 不算（只认 user）",
          w._session_carries_automation(
              Sess([{"role": "assistant", "content": AUTO_MSG}])) is False)
    check("A10 前缀前有空白/换行 → 仍算（lstrip）",
          w._session_carries_automation(
              Sess([{"role": "user", "content": "  \n " + AUTO_MSG}])) is True)
    check("A11 混在长会话中间任一位置都算",
          w._session_carries_automation(
              Sess(PLAIN_MSGS + [{"role": "user", "content": AUTO_MSG}]
                   + PLAIN_MSGS)) is True)
    check("A12 sess=None → 不崩",
          w._session_carries_automation(None) is False)
    check("A13 前缀常量值正确", w._AUTO_TASK_PREFIX == "【自动化任务】",
          repr(w._AUTO_TASK_PREFIX))


def part_b_injection_branches():
    print("\n-- B) 注入分支：自动化会话不注入陈旧 goal --")
    w = _make()
    auto_sess = Sess([{"role": "user", "content": AUTO_MSG}],
                     goal="帮我写一段人生感悟口播稿，开头就猛钩子")
    seg = w._prompt_section_session_goal(auto_sess)
    check("B1 走中性说明分支（【本会话说明】）", "【本会话说明】" in seg, seg[:80])
    check("B2 ★ 不再出现【本会话最初目标】", "【本会话最初目标" not in seg)
    check("B3 ★ 段里完全不含 goal 内容",
          "口播稿" not in seg and "人生感悟" not in seg, repr(seg[:120]))
    check("B4 保留「只看最后一条用户消息」护栏", "只看最后一条" in seg)
    check("B5 保留「不要主动去做」护栏", "不要主动去做" in seg)

    plain = Sess(PLAIN_MSGS, goal="帮我写个爬虫")
    seg2 = w._prompt_section_session_goal(plain)
    check("B6 普通会话仍注入【本会话最初目标】", "【本会话最初目标" in seg2)
    check("B7 带上 goal 内容", "帮我写个爬虫" in seg2)
    check("B8 标注为「仅供参考的背景」", "仅供参考的背景" in seg2)
    check("B9 标注「不是本轮指令」", "不是本轮指令" in seg2)
    check("B10 普通会话不用【本会话说明】", "【本会话说明】" not in seg2)

    check("B11 goal 为空 → 返回空串", w._prompt_section_session_goal(Sess([], "")) == "")
    check("B12 sess=None → 返回空串", w._prompt_section_session_goal(None) == "")
    check("B13 goal 为 None → 返回空串",
          w._prompt_section_session_goal(Sess(PLAIN_MSGS, None)) == "")
    check("B14 自动化会话 + goal 为空 → 空串（不白注入说明）",
          w._prompt_section_session_goal(Sess([{"role": "user", "content": AUTO_MSG}], "")) == "")


def part_c_source_contract():
    print("\n-- C) 源码契约：接线正确、前缀同源 --")
    check("C1 _fire_automation_run 用常量拼消息（不再硬编码字面量）",
          'f"{self._AUTO_TASK_PREFIX}{msg}"' in _SRC)
    check("C2 _build_system_prompt 改调 _prompt_section_session_goal",
          "base += self._prompt_section_session_goal(sess)" in _SRC)
    check("C3 旧的 sess.goal 直拼已消失",
          'base += ("\\n\\n【本会话最初目标' not in _SRC)
    check("C4 _META_SILENCE_RULE 已定义", "_META_SILENCE_RULE = (" in _SRC)
    check("C5 _META_SILENCE_RULE 已拼进 system prompt",
          'base += "\\n\\n" + self._META_SILENCE_RULE' in _SRC)
    # 注入顺序：权限规则 → 元信息规则 → goal 段
    i_perm = _SRC.index('base += "\\n\\n" + self._PERMISSION_RULES')
    i_meta = _SRC.index('base += "\\n\\n" + self._META_SILENCE_RULE')
    i_goal = _SRC.index("base += self._prompt_section_session_goal(sess)")
    check("C6 三段顺序合理（权限 → 元信息 → 目标）", i_perm < i_meta < i_goal,
          f"{i_perm} < {i_meta} < {i_goal}")
    check("C7 注入被 try/except 包住（不因会话异常炸掉整个 prompt）",
          "base += self._prompt_section_session_goal(sess)" in _SRC
          and "except Exception:" in _SRC[i_goal:i_goal + 200])


def part_d_meta_rule_content():
    print("\n-- D) 元信息不外泄规则的内容 --")
    rule = _attr_const("_META_SILENCE_RULE") or ""
    check("D1 规则取到内容", len(rule) > 100, f"{len(rule)} 字符")
    check("D2 点名列出了要静默的标注类型",
          all(k in rule for k in ("本会话最初目标", "本会话说明", "自动化任务", "系统强制指令")),
          rule[:100])
    check("D3 明确禁止元认知说明", "元认知说明" in rule)
    check("D4 禁止「我识别到某个注入」式表述", "我识别到某个注入" in rule)
    check("D5 与「如实汇报」划清界限", "不冲突" in rule and "照实说" in rule)
    check("D6 要求直接干活/直接回答", "直接干活" in rule)


def part_e_negative_verification():
    print("\n-- E) 负面验证：拆掉修复，回归必须变红 --")
    method_src = ast.get_source_segment(
        _SRC, next(n for n in _CLS.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_prompt_section_session_goal"))
    auto_sess = Sess([{"role": "user", "content": AUTO_MSG}], goal="帮我写一段口播稿")

    # E1 把「被借用」判定关掉（＝恢复 v4.168.1 行为：注入陈旧 goal）
    p1 = method_src.replace(
        "        if self._session_carries_automation(sess):",
        "        if False and self._session_carries_automation(sess):")
    check("E1 锚点命中", p1 != method_src)
    if p1 != method_src:
        w1 = _make()
        ns = {"re": re}
        ns["_session_carries_automation"] = w1._session_carries_automation
        exec(p1, ns)
        import types as _t
        w1._prompt_section_session_goal = _t.MethodType(ns["_prompt_section_session_goal"], w1)
        seg_bad = w1._prompt_section_session_goal(auto_sess)
        check("E1 拆掉判定 → 陈旧 goal 果然又被注入（证明修复必需）",
              "【本会话最初目标" in seg_bad and "口播稿" in seg_bad, seg_bad[:100])

    # E2 把判定改成恒真 → 所有会话的 goal 都被吞掉（一刀切，误伤普通会话）
    p2 = method_src.replace(
        "        if self._session_carries_automation(sess):", "        if True:")
    check("E2 锚点命中", p2 != method_src)
    if p2 != method_src:
        w2 = _make()
        import types as _t2
        ns2 = {"re": re, "_session_carries_automation": w2._session_carries_automation}
        exec(p2, ns2)
        w2._prompt_section_session_goal = _t2.MethodType(ns2["_prompt_section_session_goal"], w2)
        seg_over = w2._prompt_section_session_goal(Sess(PLAIN_MSGS, goal="帮我写个爬虫"))
        check("E2 判据恒真 → 普通会话的 goal 也被吞掉（说明不能一刀切）",
              "【本会话最初目标" not in seg_over, seg_over[:100])

    # E3 前缀若与 _fire_automation_run 不一致 → 判据静默失配（功能悄悄失效）
    check("E3 前缀字面量只应出现在常量定义那一行（其余必须走常量）",
          _SRC.count('"【自动化任务】"') == 1,
          f"出现 {_SRC.count(chr(34) + '【自动化任务】' + chr(34))} 次")


def part_f_optional_real_session():
    """本机有真实 sessions.json 才跑（可选，不因缺失而失败）。"""
    print("\n-- F) 真实会话（本机有才跑） --")
    import json
    p = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "sessions.json")
    if not os.path.isfile(p):
        print("  [SKIP] 未找到 sessions.json")
        return
    try:
        d = json.load(open(p, encoding="utf-8"))
        cur = next((s for s in d["sessions"] if s.get("sid") == d.get("active")), None)
    except Exception as e:
        print(f"  [SKIP] 解析失败：{e}")
        return
    if not cur:
        print("  [SKIP] 没有 active 会话")
        return
    sess = Sess(cur.get("messages") or [], cur.get("goal") or "")
    n_auto = sum(1 for m in sess.messages
                 if isinstance(m, dict) and m.get("role") == "user"
                 and isinstance(m.get("content"), str)
                 and m["content"].lstrip().startswith("【自动化任务】"))
    print(f"  真实会话：{len(sess.messages)} 条消息 / {n_auto} 条自动化任务 / "
          f"goal={sess.goal[:24]!r}")
    w = _make()
    if n_auto == 0:
        print("  [SKIP] 该会话无自动化任务，走不到本分支")
        return
    check(f"F1 判定为被借用（{n_auto} 条自动化消息）",
          w._session_carries_automation(sess) is True)
    seg = w._prompt_section_session_goal(sess)
    check("F2 注入的是中性说明", "【本会话说明】" in seg)
    check("F3 ★ 陈旧 goal 内容未泄露进 prompt",
          sess.goal not in seg if sess.goal else True, repr(seg[:120]))


def main():
    part_a_carries()
    part_b_injection_branches()
    part_c_source_contract()
    part_d_meta_rule_content()
    part_e_negative_verification()
    part_f_optional_real_session()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
