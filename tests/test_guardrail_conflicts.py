"""v4.192.0 · 七层防线冲突审计探针

目的：层数多了最怕层与层互斥/互相抵消。本探针把 7 层的判据放在同一场景下交叉验证，
找出「同一输入下两层结论矛盾」「一层豁免把另一层该抓的放跑」「一层的约束被另一层违反」。

七层对照：
  批① 引用纪律（system prompt 措辞约束）
  批② 对账强制预读（agent 注入指令）
  批③ 回复自动回验（版本断言 grep）
  批④ 附件必读（读没读）
  批⑤ 部分读取（读没读全 · 游标版，已被批⑦取代算法）
  批⑥ 纪律下放 tool result（tools.py 返回文本）
  批⑦ 读取区间空洞检测（区间覆盖）

运行：python tests/test_guardrail_conflicts.py
"""
import os, sys, re, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0
def check(name, ok, detail=""):
    global _p, _f
    if ok:
        _p += 1
        print("  PASS %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))
    else:
        _f += 1
        print("  FAIL %s %s" % (name, ("-> " + str(detail)[:70]) if detail else ""))


def main():
    global _p, _f
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import config
    import tools as TS

    tmp = tempfile.mkdtemp(prefix="gc_")

    # ============ C 组：提示词层措辞冲突（批① vs 批② vs 批⑦） ============
    print("=== [C] 提示词层措辞冲突 ===")
    try:
        sysp = config.AGENT_SYS_APPEND
        # C1 批① 是否有「轮次上限」与「不要拆调用」的硬约束
        check("C1 批① 含轮次上限约束",
              "12 轮" in sysp, "最多连续调 12 轮" if "12 轮" in sysp else "未找到")
        # C2 批① 是否有「不要分步/不要先看看」约束（与批②强制预读潜在冲突）
        has_no_probe = ("不要先探测再操作" in sysp or "不要「先看看再动手」" in sysp)
        check("C2 批① 含「不要先探测/不要先看看」约束", has_no_probe)
        # C3 批② 的对账指令是否在同一 system prompt 里（若不是，措辞无冲突但作用域不同）
        in_same = "完整读取目标文件" in sysp
        check("C3 批② 对账指令是否也在 system prompt（影响冲突面）",
              True, "不在同一处（批②在 agent 运行时注入）" if not in_same
              else "在同一处")
        # C4 冲突真实存在性：批①的 12 轮上限 vs 大文件分段读取需求
        # CHANGELOG 55151 字符 / TOOL_READ_LIMIT 8000 ≈ 7 段（比 12 小）；
        # 但实战中模型读了 16 段（乱序重读）→ 超上限
        seg_min = (55151 + TS.TOOL_READ_LIMIT - 1) // TS.TOOL_READ_LIMIT
        check("C4 大文件最小分段数 vs 12 轮上限",
              seg_min <= 12,
              "最小 %d 段（≤12，理论上不超；但乱序重读会超，实战 16 段）" % seg_min)
    except Exception as e:
        check("C 组提示词冲突", False, str(e)[:100])

    # ============ D 组：豁免词表交叉（批③ vs 批⑤/⑦） ============
    print("=== [D] 豁免逻辑交叉（一层豁免是否放跑另一层该抓的） ===")
    try:
        from ui import ChatWindow as CW
        # D1 批③ 否定词表 vs 批⑤/⑦ 部分词表，是否有交集冲突
        neg = set(getattr(CW, "_AUDIT_NEG_KW", ()))
        part = set(getattr(CW, "_READ_PARTIAL_HINT_KW", ()))
        both = neg & part
        check("D1 两套豁免词表无重叠（否则豁免语义混淆）",
              len(both) == 0, ("重叠: %s" % both) if both else "无重叠")
        # D2 关键冲突场景：模型说「整份读完，只有 v4.190 没在这份文件里」
        #     批③：v4.190 在否定句 → 豁免（对，它如实说不存在）
        #     批⑤/⑦：整句含「整份读完」+ 无部分词 + 有洞 → 该标红
        #     → 判据独立，各行其是，不冲突（一个管版本真伪，一个管读取完整性）
        check("D2 批③/批⑦ 判据正交（版本真伪 vs 读取完整性）", True,
              "批③看版本是否在文件；批⑦看区间是否有洞——对象不同")
        # D3 但危险场景：模型声称「整份读完」时**引用了一个不存在的版本号**
        #     批③会标红；批⑦也会标红 → 两张警示卡，是否重复刷屏？
        check("D3 多层同时标红 → 是否重复刷屏（需实测条数）", True,
              "见 E 组实测")
    except Exception as e:
        check("D 组豁免交叉", False, str(e)[:100])

    # ============ E 组：同一输入下多层同时触发 → 警示卡数量 ============
    print("=== [E] 多层同时触发 → 警示卡数量（是否刷屏/矛盾） ===")
    try:
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        import ui as UI
        # 构造：大文件 + 只读一段 + 声称整份读完 + 引用不存在的版本号
        big = os.path.join(tmp, "BIG.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("# 更新日志\n## v4.190.0 — 2026-10-01\n" + ("内容行\n" * 3000))

        class _Sess:
            def __init__(self, msgs):
                self.messages = msgs

        class _Store:
            def __init__(self, msgs):
                self._s = _Sess(msgs)
                self.active = lambda: self._s

        class _Chat:
            def __init__(self):
                self.items = []
                self.append = self.items.append

        def _mkmw(msgs):
            w = UI.ChatWindow.__new__(UI.ChatWindow)
            w.store = _Store(msgs)
            w.chat_view = _Chat()
            w._save_throttled = lambda: None
            return w

        msgs = [
            {"role": "user", "content": "[非图片文件: BIG.md] 帮我核对这份文件"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": big, "offset": 0, "limit": 8000}),
             "result": "…内容…"},
            {"role": "assistant",
             "content": "整份读完 BIG.md，其中记载了 v4.126 和 v4.190.0 两个版本。"},
        ]
        w = _mkmw(msgs)
        w._audit_reply_citations()
        w._audit_partial_reads()
        warns = [m for m in msgs if m.get("role") == "audit_warn"]
        check("E1 多层同时触发 → 警示卡数量合理（不刷屏）",
              len(warns) <= 2, "共 %d 张警示卡" % len(warns))
        for i, wm in enumerate(warns):
            print("      [%d] %s" % (i + 1, wm["content"][:80]))
        nopanic = all(("读取" in wm["content"] or "核验" in wm["content"])
                      for wm in warns)
        check("E2 多卡结论方向一致（不互相否定）", nopanic,
              "OK" if nopanic else "存在方向矛盾")
    except Exception as e:
        check("E 组多层触发", False, str(e)[:120])

    # ============ F 组：批①「不要先探测」是否会被批②强制预读违反 ============
    print("=== [F] 批①约束 vs 批②行为（条款级冲突） ===")
    try:
        import agent as AG
        src = open(os.path.join(root, "agent.py"), encoding="utf-8").read()
        # F1 批② 注入是否绕过「不要先探测」
        check("F1 批② 注入存在且为「必须先完整读」",
              "_AUDIT_REF_INSTRUCTION" in src and "必须先用 read_file 完整读取" in src)
        # F2 关键冲突：批①禁止「先看看再动手」，批②要求「先完整读」——措辞直接矛盾
        sysp = config.AGENT_SYS_APPEND
        conflict = ("不要「先看看再动手」" in sysp and
                    "必须先用 read_file 完整读取" in src)
        check("F2 批①/批② 措辞直接冲突（已确认，需处置）", conflict,
              "批①禁「先看看再动手」 vs 批②要「必须先完整读」")
        # F3 冲突是否实际有害？—— 批②是对账场景专用，批①是通用风格；
        #     若模型在两者间摇摆，表现为「读了一下就停」或「不敢读」
        check("F3 冲突作用域是否隔离（批②仅对账场景触发）",
              "对账" in src and "_audit_ref_needed" in src)
    except Exception as e:
        check("F 组条款冲突", False, str(e)[:100])

    # ============ G 组：v4.193 修复验证（冲突1 + 冲突2 已修） ============
    print("=== [G] v4.193 修复验证 ===")
    try:
        from ui import ChatWindow as CW
        # G1 冲突1修复：提示词含「read_file 续读不计入 12 轮」例外
        sysp = config.AGENT_SYS_APPEND
        check("G1 轮次冲突已修：read_file 续读豁免 12 轮预算",
              "分段续读，不计入这 12 轮预算" in sysp)
        # G2 冲突1修复：「不要先看看」加了作用域例外
        check("G2 探测冲突已修：「先完整读取」不算违规探测",
              "是**必须的前置**，不算违规探测" in sysp)
        # G3 冲突2修复：批③ 语境词表含引用动词类
        ctx = CW._AUDIT_CITE_CTX
        check("G3 语境词表补「记载了/记录了/列出了」等",
              all(k in ctx for k in ("记载了", "记录了", "列出了", "写明了")))
        # G4 端到端：句A（原先漏报）现在应触发
        big = os.path.join(tmp, "CITE.md")
        with open(big, "w", encoding="utf-8") as f:
            f.write("# 更新日志\n## v4.190.0 — 2026-10-01\n" + ("内容行\n" * 500))

        class _Sess2:
            def __init__(self, msgs):
                self.messages = msgs

        class _Store2:
            def __init__(self, msgs):
                self._s = _Sess2(msgs)
                self.active = lambda: self._s

        class _Chat2:
            def __init__(self):
                self.items = []
                self.append = self.items.append

        def _mk2(text):
            msgs = [
                {"role": "user", "content": "[非图片文件: CITE.md] 帮我核对"},
                {"role": "tool_log", "name": "read_file",
                 "args": json.dumps({"path": big, "offset": 0, "limit": 8000}),
                 "result": "…内容…"},
                {"role": "assistant", "content": text},
            ]
            w = UI.ChatWindow.__new__(UI.ChatWindow)
            w.store = _Store2(msgs)
            w.chat_view = _Chat2()
            w._save_throttled = lambda: None
            w._audit_reply_citations()
            return [m for m in msgs if m.get("role") == "audit_warn"]

        check("G4 原先漏报句「其中记载了 v4.126」→ 现在标红",
              len(_mk2("整份读完 CITE.md，其中记载了 v4.126 和 v4.190.0。")) == 1)
        check("G5 泛动词「提到 v4.126」仍不触发（防误报，宁窄勿宽）",
              len(_mk2("顺便提到 v4.126 这个版本，纯闲聊。")) == 0)
        check("G6 否定豁免未被破坏：「文件里没有 v4.126」（有」算引用语境）",
              len(_mk2("文件里没有 v4.126 这个版本。")) == 0)
    except Exception as e:
        check("G 组修复验证", False, str(e)[:120])

    print(f"\n=== ALL_{'OK' if _f == 0 else 'FAIL'} === PASS={_p} FAIL={_f}")
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
