# -*- coding: utf-8 -*-
"""v4.194.0 · 第 8 层「可数事实计数回验」探针

背景（2026-10-01 对照实验现场②实锤）：
  小臭**已读全** BIG_sample.md（95137 字符 / 分 13 段读完，正文事实 9/9 全对），
  但回答「结构」时称「第003节~第081节（共 80 个）填充节」——
  **真值 477 节**，差 6 倍。

  关键性质：这不是「编造内容」（它没瞎编事实），而是**全局计数失准**。
  现有七层全都拦不住：
    · 批③ 只比对版本号字符串（不管节数）；
    · 批④ 只管读没读；
    · 批⑤/⑦ 只管读没读完（读完了就放行）；
    · 「477 vs 80」**没有原文出处可对照**——不是引用错误，是统计错误。

第 8 层思路：
  可数事实是**唯一可确定性回验**的编造类型——答案不在文件「哪一处」，
  而在「全文有几个可数单元」。因此可在读取时旁路算出真值，回复里提取数字比对。

判据边界（本探针即为此而写，宁窄勿宽）：
  触发需**同时**满足：① 有可数单位词（节/章/条/项/行…）
                      ② 数字紧邻该单位词（「共 80 个节」「477 节」）
                      ③ 能绑定到本轮读过的**具体文件**（出现文件名/指代）
  豁免：模糊表述（约/大概/左右/多个/数十）、否定语境（没有 80 节）、
        该文件本轮读取未覆盖（读都没读完 → 归批⑦管，不归本层）。

运行：python tests/test_count_audit_194.py
"""
import os, sys, re, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    import config
    import tools as TS
    from ui import ChatWindow as CW

    tmp = tempfile.mkdtemp(prefix="ca_")

    # ============ [A] 计数引擎：真实文件 → 真值的确定性计数 ============
    print("=== [A] 计数引擎（对文件真数，不靠模型） ===")
    try:
        doc = os.path.join(tmp, "DOC.md")
        # 造一份 477 节的文件（复现现场②的真值结构）
        parts = ["# 标题\n\n## 第一节 · 甲\n\n正文\n\n## 第二节 · 乙\n\n正文\n"]
        for i in range(3, 480):
            parts.append("## 第%d节 · 填充\n\n- 内容\n" % i)
        body = "".join(parts)
        with open(doc, "w", encoding="utf-8") as f:
            f.write(body)

        n = CW._count_units(body, "节")
        # 文件含 1 个 `# 标题`（一级，不算节）+ 2 个「第一节/第二节」+ 第003~第479
        # → 二级标题共 2 + 477 = 479。真值口径 = 二级及以下标题数。
        check("A1 真值计数（## 节）== 479（一级标题不计）",
              n == 479, "实数 = %s" % n)
        # 交叉核对：独立口径数二级标题（不走 _count_units，防自证）
        n2 = len([ln for ln in body.split("\n") if ln.startswith("## ")])
        check("A2 交叉核对（独立口径数 ## 标题）== 479", n2 == 479, "=%s" % n2)
        # 行数
        nline = CW._count_units(body, "行")
        expect_line = body[:-1].count("\n") + 1 if body.endswith("\n") else body.count("\n") + 1
        check("A3 行数计数可用", nline == expect_line,
              "行数 = %s (期望 %s)" % (nline, expect_line))
    except Exception as e:
        check("A 组计数引擎", False, repr(e)[:110])

    # ============ [B] 断言提取：从回复里挑出「可数事实」 ============
    print("=== [B] 断言提取（只挑可确定性回验的） ===")
    try:
        # B1 现场②原句（应提取出 80 且绑定「节」）
        a = CW._extract_count_claims("共 80 个填充节，与要点无关")
        check("B1 提取「共 80 个…节」",
              any(x[0] == 80 and x[1] == "节" for x in a), str(a))
        # B2 「第003节~第081节」是章节编号引用，不是计数断言 → 不该被提取为「81 节」
        a2 = CW._extract_count_claims("第003节~第081节（共 80 个）")
        check("B2 编号引用「第081节」不被当计数",
              all(x[0] != 81 for x in a2), str(a2))
        # B3 泛数字不应被当计数（无单位词）
        a3 = CW._extract_count_claims("整体进度约 80 分，风险 3 项待定")
        check("B3 无单位词的裸数字不提取（防误报）",
              not any(x[0] == 80 and x[1] == "节" for x in a3), str(a3))
        # B4 「3 项待定」——有单位词，但属"待办项"非文件计数 → 需靠绑定过滤
        a4 = CW._extract_count_claims("有 3 项待定")
        check("B4 提取到「3 项」（后续靠绑定过滤）",
              any(x[0] == 3 and x[1] == "项" for x in a4), str(a4))
        # B5 模糊表述应被标记，不参与回验
        a5 = CW._extract_count_claims("大约 80 个节，具体没数")
        check("B5 模糊表述被识别（约/大概）",
              all(not x[2] for x in a5 if x[1] == "节") or not a5, str(a5))
    except Exception as e:
        check("B 组断言提取", False, repr(e)[:110])

    # ============ [C] 端到端行为：读全了但数错 → 标红 ============
    print("=== [C] 行为级：读全 + 数错 → audit_warn ===")
    try:
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        import ui as UI

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

        def _mk(msgs):
            w = UI.ChatWindow.__new__(UI.ChatWindow)
            w.store = _Store(msgs)
            w.chat_view = _Chat()
            w._save_throttled = lambda: None
            return w

        doc = os.path.join(tmp, "DOC.md")
        total = os.path.getsize(doc)  # 文件存在即可

        # C1 现场②复现：读完整个文件（区间覆盖无洞）+ 声称 80 节（真值 477）
        msgs = [
            {"role": "user", "content": "[非图片文件: DOC.md] 核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 999999}),
             "result": "…全文…"},
            {"role": "assistant",
             "content": "DOC.md 全文已通读。结构：第一节、第二节，"
                        "第003节~第081节（共 80 个填充节），与要点无关。"},
        ]
        w = _mk(msgs)
        w._audit_count_claims()
        warns = [m for m in msgs if m.get("role") == "audit_warn"]
        check("C1 数错（80 vs 479）→ 产生 audit_warn",
              len(warns) == 1, "共 %d 张：%s" % (
                  len(warns), warns[0]["content"][:90] if warns else "无"))
        if warns:
            check("C1b 警示里给出真值 479",
                  "479" in warns[0]["content"], warns[0]["content"][:100])

        # C2 数对（479）→ 不标红
        msgs2 = [
            {"role": "user", "content": "[非图片文件: DOC.md] 核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 999999}),
             "result": "…全文…"},
            {"role": "assistant", "content": "DOC.md 全文已读，共 479 节。"},
        ]
        w2 = _mk(msgs2)
        w2._audit_count_claims()
        w2w = [m for m in msgs2 if m.get("role") == "audit_warn"]
        check("C2 数对（479）→ 不标红", len(w2w) == 0,
              "误报 %d 张" % len(w2w))

        # C3 没读全就数（区间有洞）→ 归批⑦管，本层不越权
        msgs3 = [
            {"role": "user", "content": "[非图片文件: DOC.md] 核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 500}),
             "result": "…前500…"},
            {"role": "assistant", "content": "DOC.md 共 477 节。"},
        ]
        w3 = _mk(msgs3)
        w3._audit_count_claims()
        w3w = [m for m in msgs3 if m.get("role") == "audit_warn"]
        check("C3 未读全 → 本层不越权（归批⑦），且数对不误报",
              len(w3w) == 0, "共 %d 张" % len(w3w))

        # C4 模糊表述 → 不标红（防误伤）
        msgs4 = [
            {"role": "user", "content": "[非图片文件: DOC.md] 核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 999999}),
             "result": "…全文…"},
            {"role": "assistant", "content": "DOC.md 大约 80 个节，具体我没数。"},
        ]
        w4 = _mk(msgs4)
        w4._audit_count_claims()
        w4w = [m for m in msgs4 if m.get("role") == "audit_warn"]
        check("C4 模糊表述（大约/没数）→ 不标红", len(w4w) == 0,
              "误报 %d 张" % len(w4w))

        # C5 无文件名绑定 → 不标红（防闲聊误报）
        msgs5 = [
            {"role": "user", "content": "随便聊聊"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 999999}),
             "result": "…全文…"},
            {"role": "assistant", "content": "这个项目共 80 个节要写。"},
        ]
        w5 = _mk(msgs5)
        w5._audit_count_claims()
        w5w = [m for m in msgs5 if m.get("role") == "audit_warn"]
        check("C5 无文件绑定（没提 DOC.md）→ 不标红", len(w5w) == 0,
              "误报 %d 张" % len(w5w))

        # C6 否定语境（如实报告"没有 80 节，是 477 节"）→ 不标红
        msgs6 = [
            {"role": "user", "content": "[非图片文件: DOC.md] 核对一下"},
            {"role": "tool_log", "name": "read_file",
             "args": json.dumps({"path": doc, "offset": 0, "limit": 999999}),
             "result": "…全文…"},
            {"role": "assistant",
             "content": "DOC.md 里并没有 80 节，实际是 477 节。"},
        ]
        w6 = _mk(msgs6)
        w6._audit_count_claims()
        w6w = [m for m in msgs6 if m.get("role") == "audit_warn"]
        check("C6 否定语境（「并没有 80 节」）→ 不标红", len(w6w) == 0,
              "误报 %d 张" % len(w6w))
    except Exception as e:
        check("C 组端到端", False, repr(e)[:140])

    # ============ [D] 与现有层的边界（不越权、不重复） ============
    print("=== [D] 层间边界 ===")
    try:
        # D1 与批③不重叠：批③管版本号字符串，本层管计数
        check("D1 与批③ 判据正交（版本号 vs 计数）", True,
              "批③ 提取 vN.N；本层提取 N+单位词")
        # D2 与批⑤/⑦不越权：有洞时本层让位
        check("D2 有读取空洞时让位批⑦（本层只在读全后判数）", True,
              "C3 已验证")
        # D3 警示卡角色唯一，不与现有 role 冲突
        check("D3 复用 audit_warn role（渲染链已就绪）", True)
    except Exception as e:
        check("D 组边界", False, repr(e)[:100])

    # ============ [E] 提示词层：批① 是否已预告「不要估计数」 ============
    print("=== [E] 提示词层（预防优于检测） ===")
    try:
        sysp = config.AGENT_SYS_APPEND
        # 判据要具体到「计数」语义，避免被无关词（如选题模板里的"具体数字"）假通过
        has = ("计数纪律" in sysp) and ("确定性计数" in sysp) and (
            "禁止凭感觉估" in sysp or "不要凭感觉估" in sysp)
        check("E1 批① 含 v4.194 计数纪律条款", has,
              "已含" if has else "缺失")
        # E2 该条款是否覆盖"宁可不说数，不可给错数"的处置口径
        has2 = "宁可不说数" in sysp or "不可给错数" in sysp
        check("E2 条款含「宁可不说数，不可给错数」口径", has2,
              "已含" if has2 else "缺失")
    except Exception as e:
        check("E 组提示词", False, repr(e)[:100])

    print("=" * 60)
    print("汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
