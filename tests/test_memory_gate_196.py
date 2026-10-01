# -*- coding: utf-8 -*-
"""v4.196 批⑫ 探针：记忆准入关 —— 判定侧壁垒是否真的拦得住。

批次目标（审查问题 2）：长期记忆会跨会话累积污染，一次错误提炼会被后续
每一次对话当成「用户说过的事实」引用。准入条件是「本轮有没有调过工具」，
等于只问「干没干活」，不问「凭什么这么认为」。

要证明的四件事：
  [A] 来源四分：user 直接放行 / tool 必须绑成功证据且逐字回验 / inference 不入库
  [B] 证据伪造防线：编编号、引用不存在编号、拿失败调用当依据 → 一律 reject
  [C] 冲突不静默覆盖：与旧记忆数字互斥 → pending，旧条目原样保留
  [D] 待确认区真的落盘 + 落过的process happened

跑法：python tests/test_memory_gate_196.py
"""

import os
import sys
import tempfile
import shutil
from datetime import datetime

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


class FakeEvidence(object):
    """最小替身：只实现 memory_gate 会用到的 get / supports / recent。"""

    def __init__(self, rows=None):
        self.rows = {int(r["id"]): dict(r) for r in (rows or [])}

    def get(self, eid):
        try:
            return self.rows.get(int(eid))
        except Exception:
            return None

    def supports(self, eid, needle, start_line=None, end_line=None):
        r = self.rows.get(int(eid))
        if not r:
            return False
        return str(needle) in str(r.get("raw") or "")


EV_ROWS = [
    {"id": 1, "ok": True, "tool": "read_file",
     "raw": "当前版本 v4.195.0\nRelease Notes 共 477 节\n路径 D:/小臭玩AI/deepseek-desktop"},
    {"id": 2, "ok": False, "tool": "web_fetch", "raw": "", "err": "timeout"},
    {"id": 3, "ok": True, "tool": "read_file", "raw": "房贷余额 50000 元"},
]


def part_a_source_quadrant():
    print("\n[A] 来源四分：不同来源走不同通道")
    import memory_gate as mg
    ev = FakeEvidence(EV_ROWS)

    # A1 用户亲口陈述 → 直接放行（用户说自己事，机器无权质疑）
    r = mg.admit({"topic": "姓名", "category": "用户偏好与约定",
                  "source": "user", "confidence": 0.95, "content": "用户自称姓张"})
    check("A1 user 来源直接入库", r["decision"] == mg.ADMIT, r.get("reason"))

    # A2 工具来源 + 证据对得上 → 放行且标记已验证
    r = mg.admit({"source": "tool", "ev": 1, "confidence": 0.9,
                  "content": "当前版本 v4.195.0"}, evidence_mod=ev)
    check("A2 tool 来源绑定成功证据→放行", r["decision"] == mg.ADMIT, r.get("reason"))
    check("A2b 放行时 verified=True", r.get("verified") is True)

    # A3 推断 → 不进长期记忆
    r = mg.admit({"source": "inference", "confidence": 0.9,
                  "content": "用户大概是想做视频"})
    check("A3 推断落待确认区", r["decision"] == mg.PENDING, r.get("reason"))

    # A4 临时任务态 → 可入库但必须带过期
    r = mg.admit({"source": "ephemeral", "expires": "7天",
                  "content": "本轮正在处理值班手册"})
    check("A4 临时态放行", r["decision"] == mg.ADMIT, r.get("reason"))
    check("A4b 带过期日期", bool(r.get("expires_at")), str(r.get("expires_at")))

    # A5 中文别名也能认（模型常写中文）
    r = mg.admit({"source": "用户陈述", "confidence": 0.9, "content": "用户偏好深色模式"})
    check("A5 中文来源别名可识别", r["decision"] == mg.ADMIT, r.get("reason"))

    # A6 未声明来源 → 按最保守（推断）处理，绝不默认放行
    r = mg.admit({"content": "用户喜欢用 python"})
    check("A6 未声明来源→按推断处理", r["decision"] == mg.PENDING, r.get("reason"))

    # A7 低置信度：连 user 来源也不放行（模型自我感觉良好不等于有据）
    r = mg.admit({"source": "user", "confidence": 0.3, "content": "用户可能姓李"})
    check("A7 低置信度挡下", r["decision"] == mg.PENDING, r.get("reason"))


def part_b_evidence_forgery():
    print("\n[B] 伪造防线：没有这条证据/这条是失败调用/要素对不上")
    import memory_gate as mg
    ev = FakeEvidence(EV_ROWS)

    # B1 tool 来源但不给编号 → reject（无从回验）
    r = mg.admit({"source": "tool", "content": "版本 v4.195.0"}, evidence_mod=ev)
    check("B1 工具来源无编号→驳回", r["decision"] == mg.REJECT, r.get("reason"))

    # B2 编造编号（本轮不存在）
    r = mg.admit({"source": "tool", "ev": 99, "content": "版本 v4.195.0"},
                 evidence_mod=ev)
    check("B2 编造编号 EV#99→驳回", r["decision"] == mg.REJECT, r.get("reason"))

    # B3 拿失败调用当依据
    r = mg.admit({"source": "tool", "ev": 2, "content": "网页上说 v4.195.0"},
                 evidence_mod=ev)
    check("B3 失败调用不得当依据", r["decision"] == mg.REJECT, r.get("reason"))

    # B4 编号存在、调用成功，但要素对不上（数字是编的）
    r = mg.admit({"source": "tool", "ev": 1, "content": "共 80 节"}, evidence_mod=ev)
    check("B4 要素对不上→驳回", r["decision"] == mg.REJECT, r.get("reason"))

    # B5 编号含前缀写法也能认
    r = mg.admit({"source": "tool", "ev": "EV#1", "content": "当前版本 v4.195.0"},
                 evidence_mod=ev)
    check("B5 'EV#1' 写法可识别", r["decision"] == mg.ADMIT, r.get("reason"))

    # B6 抽不出可核 token → 无从验证，进待确认而非放行
    r = mg.admit({"source": "tool", "ev": 1, "content": "描述性的内容无法核验"},
                 evidence_mod=ev)
    check("B6 无可核 token→待确认", r["decision"] == mg.PENDING, r.get("reason"))

    # B7 证据模块不可用（打包降级场景）→ fail-closed，不静默放行
    class Broke(object):
        @staticmethod
        def get(eid):
            raise RuntimeError("db locked")

        @staticmethod
        def supports(*a, **k):
            raise RuntimeError("db locked")

    r = mg.admit({"source": "tool", "ev": 1, "content": "版本 v4.195.0"},
                 evidence_mod=Broke())
    check("B7 证据库异常→不通关", r["decision"] != mg.ADMIT, r.get("reason"))


def part_c_conflict():
    print("\n[C] 冲突：不许静默覆盖旧记忆")
    import memory_gate as mg

    old = "房贷还剩 5 万没还"
    new = {"source": "user", "confidence": 0.9, "topic": "房贷",
           "content": "房贷已还清，剩 0 万"}
    r = mg.admit(new, old_text=old)
    check("C1 数字冲突→待确认", r["decision"] == mg.PENDING, r.get("reason"))
    check("C1b 原因里点明未覆盖旧条目", "未覆盖旧条目" in (r.get("reason") or ""))

    # 无冲突同主题更新仍可放行（否则每次修订都被挡 = 误报）
    r = mg.admit({"source": "user", "confidence": 0.9, "content": "房贷已还清"},
                 old_text="房贷已还清")
    check("C2 无冲突不误挡", r["decision"] == mg.ADMIT, r.get("reason"))

    # 都抽不出数字 → 无法判冲突，放行（宁放过不清空的前提下不制造假冲突）
    r = mg.admit({"source": "user", "confidence": 0.9, "content": "用户心情不错"},
                 old_text="用户心情一般")
    check("C3 无数字不判冲突", r["decision"] == mg.ADMIT, r.get("reason"))

    check("C4 冲突判定函数本身保守",
          mg.has_conflict("剩 3 万", "剩 8 万") is True
          and mg.has_conflict("剩 3 万", "剩 3 万") is False)


def part_d_pending_area():
    print("\n[D] 待确认区：拦下来的必须留痕，不能无声消失")
    import memory_store as ms
    import memory_gate as mg

    tmp = tempfile.mkdtemp(prefix="memgate_")
    ms._configure(tmp)
    try:
        check("D1 初始待确认区为空", ms.pending_count() == 0)
        ok = ms.append_pending({"fact": "用户大概想做视频", "source": "inference",
                                "category": "用户偏好与约定", "topic": "视频",
                                "confidence": 0.8,
                                "reason": "来源为模型推断，不进长期记忆"})
        check("D2 写入待确认区成功", ok is True)
        check("D3 条目数=1", ms.pending_count() == 1, str(ms.pending_count()))
        txt = ms.load_pending()
        check("D4 记录了拦截原因", "拦截原因" in txt, txt[:120])
        check("D5 记录了来源", "inference" in txt)
        # 幂等：同内容不重复堆
        ms.append_pending({"fact": "用户大概想做视频", "reason": "同上"})
        check("D6 同内容不重复堆叠", ms.pending_count() == 1, str(ms.pending_count()))
        # 待确认区不得污染正式记忆
        check("D7 正式记忆仍为空", (ms.load_memory() or "").strip() == "",
              (ms.load_memory() or "")[:80])
        check("D8 清空待确认区", ms.clear_pending() is True and ms.pending_count() == 0)
        # summary 文案
        s = mg.summarize([{"decision": "admit"}, {"decision": "reject"}])
        check("D9 汇总文案含入库/驳回数", "1 条入库" in s and "1 条无据驳回" in s, s)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def part_e_parse_fields():
    print("\n[E] 字段透传：解析丢了字段 = 整条降级为推断")
    import agent as AG

    raw = ('[{"topic":"版本","category":"重要决策","content":"当前版本 v4.195.0",'
           '"source":"tool","ev":1,"confidence":0.9},'
           '{"topic":"偏好","category":"用户偏好与约定","content":"用户喜欢蓝",'
           '"source":"user","confidence":0.95}]')
    facts = AG.AgentWorker._parse_remember_facts(raw)
    check("E1 解析出 2 条", len(facts) == 2, str(len(facts)))
    check("E2 source 透传", facts[0].get("source") == "tool", str(facts[0]))
    check("E3 ev 透传", facts[0].get("ev") == 1, str(facts[0].get("ev")))
    check("E4 confidence 透传", facts[1].get("confidence") == 0.95)
    # 老格式（纯字符串）仍能解析，只是没 source → 走保守通道
    old = AG.AgentWorker._parse_remember_facts("用户工作区在 D 盘")
    check("E5 旧格式字符串仍可解析", len(old) >= 1 and old[0].get("content"))
    check("E6 旧格式无 source（走保守通道）", old[0].get("source") is None)


def part_f_prompt_declares_source():
    print("\n[F] 预防侧：提取提示词必须要求自报来源")
    import agent as AG

    p = AG.AUTO_REMEMBER_PROMPT
    for anchor in ('"source"', "inference", "tool", "ephemeral", "confidence"):
        check("F 提示词含 %s" % anchor, anchor in p)
    check("F 提示词禁止把推断伪装成陈述",
          ("不许把推断伪装" in p) or ("禁止" in p and "推断" in p))


def main():
    print("=" * 70)
    print("v4.196 批⑫ 记忆准入关 — 探针")
    print("=" * 70)
    part_a_source_quadrant()
    part_b_evidence_forgery()
    part_c_conflict()
    part_d_pending_area()
    part_e_parse_fields()
    part_f_prompt_declares_source()
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
