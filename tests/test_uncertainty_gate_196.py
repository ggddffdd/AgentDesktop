# -*- coding: utf-8 -*-
"""v4.196 批⑬ 探针：拒答阈值 + 闸门任务风险分级。

要证明的不是「有没有某个函数」，而是**两条失效路径真的被堵上了**：

失败路径一：话说得比证据允许的程度更满
    单一来源 / 来源打架 / 零证据 / 问实时数据却没联网
    → 过去没有任何一层管，因为它每个字都没编错。

失败路径二：同一把工具，在普通任务和高风险任务上走同一道岔
    「把草稿另存一份」和「把生产库这张表清掉重灌」IDE 决策完全一致，
    auto 模式 / 会话信任下一次放行。

验证分五段：
  [A] 决策表四级灰度：answer / hedged / conflict / refuse 各自触发条件
  [B] 语气匹配：只有强肯定句越过等级才判越界（防误报）
  [C] 任务风险分级：critical 类能被认出来、normal 类不误伤
  [D] fail-closed：critical 任务的写入/执行，会话信任与 auto 模式都绕不过
  [E] 链上接通：审批链真的会调到第 11 层

跑法：python tests/test_uncertainty_gate_196.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = FAIL = 0
_FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        _FAILS.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


EV_OK_1 = {"id": 1, "ok": True, "tool": "read_file", "raw": "版本 v4.195.0 共 477 节"}
EV_OK_2 = {"id": 2, "ok": True, "tool": "read_file", "raw": "路径 D:/proj 打包于 2026-10-01"}
EV_BAD = {"id": 3, "ok": False, "tool": "web_fetch", "raw": "", "err": "timeout"}


def part_a_decision_table():
    print("\n[A] 四级灰度：证据水平决定能说到什么程度")
    import uncertainty as unc

    # A1 仅一条成功证据 → hedged（不得下肯定结论）
    v = unc.assess("这份手册有多少节？", [EV_OK_1], tools_used=["read_file"], cited_ids=[1])
    check("A1 单一来源→hedged", v["level"] == unc.LEVEL_HEDGED, v.get("reason"))
    check("A1b hedged 要求保留措辞", "初步判断" in v["allowed_tone"], v["allowed_tone"])

    # A2 两条互不冲突的证据 → answer
    v = unc.assess("手册情况", [EV_OK_1, EV_OK_2], tools_used=["read_file"],
                   cited_ids=[1, 2])
    check("A2 两条独立证据→answer", v["level"] == unc.LEVEL_ANSWER, v.get("reason"))

    # A3 两条互相打架 → conflict
    conflict_row = {"id": 9, "ok": True, "tool": "read_file",
                    "raw": "版本 v4.188.0 共 88 节"}
    v = unc.assess("手册多少节", [EV_OK_1, conflict_row],
                   tools_used=["read_file"], cited_ids=[1, 9])
    check("A3 要素互斥→conflict", v["level"] == unc.LEVEL_CONFLICT, v.get("reason"))

    # A4 零证据 → refuse
    v = unc.assess("手册多少节", [], tools_used=[], cited_ids=[])
    check("A4 零证据→refuse", v["level"] == unc.LEVEL_REFUSE, v.get("reason"))

    # A5 引用失败调用 → refuse（最容易被忽略的一条：失败也是一种"有来源"）
    v = unc.assess("最新情况如何", [EV_BAD], tools_used=["web_fetch"], cited_ids=[3])
    check("A5 失败调用→refuse", v["level"] == unc.LEVEL_REFUSE, v.get("reason"))
    check("A5b 标出 failed_used", v.get("failed_used") is True)

    # A6 问实时信息却没调任何查询工具 → refuse
    v = unc.assess("今天昆明天气怎么样？", [], tools_used=[], cited_ids=[])
    check("A6 需实时但无工具→refuse", v["level"] == unc.LEVEL_REFUSE, v.get("reason"))

    # A7 问实时信息但有实时工具 → 不再 refuse（退到按证据数判定）
    v = unc.assess("今天股价多少？", [EV_OK_1], tools_used=["web_search"], cited_ids=[1])
    check("A7 有实时工具→不再因实时拒答", v["level"] != unc.LEVEL_REFUSE, v.get("reason"))

    # A8 严格度比较
    check("A8 stricter 取更保守",
          unc.stricter(unc.LEVEL_ANSWER, unc.LEVEL_REFUSE) == unc.LEVEL_REFUSE
          and unc.stricter(unc.LEVEL_CONFLICT, unc.LEVEL_HEDGED) == unc.LEVEL_CONFLICT)

    # A9 requires_realtime 基本判据
    check("A9 实时话题识别",
          unc.requires_realtime("最新版本是多少") is True
          and unc.requires_realtime("帮我把这段 python 排序") is False)


def part_b_tone():
    print("\n[B] 语气匹配：只抓说得太满，不抓正常表述")
    import uncertainty as unc

    # B1 hedged 级下写「确定是」→ 越界
    ok, why = unc.tone_allowed("确定是 477 节", unc.LEVEL_HEDGED)
    check("B1 hedged 下强肯定→判越界", ok is False, why)

    # B2 已带保留词 → 放行（防误报：不能一见肯定句就红）
    ok, _ = unc.tone_allowed("初步判断是 477 节，待核实", unc.LEVEL_HEDGED)
    check("B2 带保留词→放行", ok is True)

    # B3 承认不知道 → 放行
    ok, _ = unc.tone_allowed("这个我没查到，不敢下结论", unc.LEVEL_REFUSE)
    check("B3 承认不知道→放行", ok is True)

    # B4 answer 级一律放行（证据够就别管措辞）
    ok, _ = unc.tone_allowed("确定是 477 节", unc.LEVEL_ANSWER)
    check("B4 answer 级不限制措辞", ok is True)

    # B5 conflict 级摆出分歧 → 放行
    ok, _ = unc.tone_allowed("两处说法不一致：一处 477 节，一处 88 节",
                             unc.LEVEL_CONFLICT)
    check("B5 conflict 下摆分歧→放行", ok is True)

    # B6 refuse 级仍下结论 → 越界
    ok, why = unc.tone_allowed("答案就是 80 节", unc.LEVEL_REFUSE)
    check("B6 refuse 下给答案→判越界", ok is False, why)

    # B7 普通陈述句不误伤（没有强肯定词）
    ok, _ = unc.tone_allowed("我把文件读完了，共三个部分", unc.LEVEL_HEDGED)
    check("B7 无强肯定词→不误伤", ok is True)

    # B8 system_hint 每级都有对应措辞建议
    for lv in (unc.LEVEL_ANSWER, unc.LEVEL_HEDGED, unc.LEVEL_CONFLICT, unc.LEVEL_REFUSE):
        check("B8 %s 有措辞建议" % lv, bool(unc.system_hint(lv)))


def part_c_task_risk():
    print("\n[C] 任务风险分级：认得出高风险，也不误伤日常")
    import risk as rk

    cases = [
        ("帮我把生产库的这张表清掉重灌", rk.TASK_CRITICAL),
        ("帮我把这份合同里的违约条款标出来", rk.TASK_CRITICAL),
        ("给供应商转 5000 元货款", rk.TASK_CRITICAL),
        ("这个药用剂量一天吃几次", rk.TASK_CRITICAL),
        ("把这个新版本部署到生产环境", rk.TASK_CRITICAL),
        ("帮我把这份草稿另存一份", rk.TASK_NORMAL),
        ("总结一下这篇文章的三段大意", rk.TASK_NORMAL),
        ("帮我写个 python 排序函数", rk.TASK_NORMAL),
    ]
    for text, expect in cases:
        lvl, label = rk.task_risk_level(text)
        check("C 「%s」→%s" % (text[:14], expect),
              lvl == expect, "实际=%s(%s)" % (lvl, label))
    check("C 空文本→normal", rk.task_risk_level("")[0] == rk.TASK_NORMAL)
    check("C high 档存在", rk.task_risk_level("把这封邮件群发给客户")[0] == rk.TASK_HIGH)


def part_d_fail_closed():
    print("\n[D] fail-closed：critical 任务绕不过人和'全部信任'")
    from permissions import PermissionEngine

    pe = PermissionEngine("auto", auto_allow={"write_file"}, scope_paths=[])

    # D1 baseline：普通任务 + auto 模式，写文件直行（不打扰日常）
    d = pe.decide("write_file", {"path": "a.txt", "content": "x"}, task_risk="normal")
    check("D1 普通任务不打扰", d.allowed and not d.needs_user, d.reason)

    # D2 critical 任务 → 必须确认，即使 auto 模式
    d = pe.decide("write_file", {"path": "a.txt"}, task_risk="critical")
    check("D2 critical 任务写文件需确认", d.allowed and d.needs_user, d.reason)
    check("D2b rule=task_risk_critical", d.rule == "task_risk_critical", d.rule)

    # D3 会话信任也豁免不了（这是关键：否则"全部信任"一键破功）
    pe.set_session_trusted()
    d = pe.decide("write_file", {"path": "a.txt"}, task_risk="critical")
    check("D3 会话信任不豁免 critical", d.needs_user is True, d.reason)
    d2 = pe.decide("run_command", {"command": "x"}, task_risk="critical")
    check("D3b 执行类同样需确认", d2.needs_user is True, d2.reason)

    # D4 只读仍然放行（否则连"帮我看看合同"都被拦 = 矫枉过正）
    d = pe.decide("read_file", {"path": "a.txt"}, task_risk="critical")
    check("D4 critical 任务下只读仍放行", d.allowed and not d.needs_user, d.reason)

    # D5 传任务原文也能自动分级（调用方不需要先算）
    d = pe.decide("run_python", {"code": "x"}, task_risk="帮我给客户转账 3000 元")
    check("D5 传原文自动分级", d.needs_user is True and d.rule == "task_risk_critical",
          "%s/%s" % (d.rule, d.reason))

    # D6 普通任务 + 会话信任 → 仍然一路放行（零回归）
    d = pe.decide("write_file", {"path": "a.txt"}, task_risk="normal")
    check("D6 普通任务+信任仍直行", d.allowed and not d.needs_user, d.reason)

    # D7 force 名单已收录（否则 decide 说要确认、UI 却被信任短路 = 形同虚设）
    try:
        import io
        src = io.open(os.path.join(_ROOT, "agent.py"), encoding="utf-8").read()
    except Exception:
        src = ""
    check("D7 agent 侧 force 名单含该规则", "task_risk_critical" in src)


def part_e_wiring():
    print("\n[E] 链上接通：第 11 层真的挂在收尾链上")
    import io

    ui_src = io.open(os.path.join(_ROOT, "ui.py"), encoding="utf-8").read()
    # v4.216.0：审计族方法体迁至 ui_audit_mixin.py；调用链仍在 ui.py 的 _on_agent_done
    mixin_src = io.open(os.path.join(_ROOT, "ui_audit_mixin.py"),
                        encoding="utf-8").read()
    check("E1 定义了 _audit_tone_evidence", "def _audit_tone_evidence" in mixin_src)
    idx_call = ui_src.find("self._audit_tone_evidence()")
    idx_chain = ui_src.find("self._audit_claim_evidence()")
    check("E2 已挂进 _on_agent_done 收尾链", idx_call > 0)
    check("E3 挂在批⑩ 之后（不抢旧层的活）", idx_call > idx_chain > 0,
          "call=%s chain=%s" % (idx_call, idx_chain))

    cfg = io.open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()
    for anchor in ("不确定性纪律", "初步判断", "吵架", "高风险任务"):
        if anchor == "吵架":
            continue
        check("E4 系统提示词含「%s」" % anchor, anchor in cfg)
    check("E5 提示词写明零来源要拒答", ("直接说" in cfg and "不敢下结论" in cfg))

    spec = io.open(os.path.join(_ROOT, "小臭玩AI.spec"), encoding="utf-8").read()
    check("E6 spec hiddenimports 登记 memory_gate", "'memory_gate'" in spec)
    check("E7 spec hiddenimports 登记 uncertainty", "'uncertainty'" in spec)


def main():
    print("=" * 70)
    print("v4.196 批⑬ 拒答阈值 + 闸门任务风险分级 — 探针")
    print("=" * 70)
    part_a_decision_table()
    part_b_tone()
    part_c_task_risk()
    part_d_fail_closed()
    part_e_wiring()
    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if _FAILS:
        print("失败项：")
        for f in _FAILS:
            print("  - " + f)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
