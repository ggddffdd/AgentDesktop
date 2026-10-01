# -*- coding: utf-8 -*-
"""v4.195 批⑨ 探针：Evidence Registry（证据登记处）—— 根治架构的地基。

验证目标不是「这个模块能不能跑」，而是**根治的前提条件是否成立**：

1. 工具返回的完整原文有没有被真正留存（此前三处保存点全是压缩过的）
2. 失败的工具调用有没有被标记成「不可作为证据」
3. 证据能不能被**精确到行**地回验（行级精度 = 回验的可信度）
4. 接入主流程后有没有破坏既有的 tool_log 数据结构（零回归）

跑法：python tests/test_evidence_registry_195.py
"""

import os
import sys
import json
import time
import tempfile
import shutil

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


def main():
    _tmp = tempfile.mkdtemp(prefix="ev195_")
    try:
        import evidence
        evidence.set_dir(_tmp)
        run_all(evidence)
    finally:
        try:
            shutil.rmtree(_tmp, ignore_errors=True)
        except Exception:
            pass

    print("\n" + "=" * 66)
    print("批⑨ 结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    if _FAILS:
        print("失败项：")
        for f in _FAILS:
            print("   - %s" % f)
    print("=" * 66)
    return 0 if FAIL == 0 else 1


def run_all(ev):
    # ============ [A] 登记与完整留存 ============
    print("=== [A] 完整原文留存（根治的前提） ===")
    big = "\n".join("第%d行：本行是一段用于验证无损留存的内容。" % i
                    for i in range(1, 601))
    eid = ev.register("read_file", {"path": "BIG.md", "offset": 0, "limit": 8000},
                      big)
    check("A1 register 返回编号", isinstance(eid, int) and eid > 0, "eid=%r" % eid)
    r = ev.get(eid)
    check("A2 取回原文完全一致（>10000 字符无损）",
          r and r["raw"] == big and len(r["raw"]) > 10000,
          "len=%s" % (len(r["raw"]) if r else "None"))
    check("A3 行数记录正确（600）", r and r["n_lines"] == 600,
          "=%s" % (r["n_lines"] if r else None))
    check("A4 哈希非空且稳定",
          bool(r["sha"]) and ev.get(eid)["sha"] == r["sha"], r["sha"] if r else "")
    check("A5 工具名与参数原样留存",
          r["tool"] == "read_file" and "BIG.md" in (r["args"] or ""), r["args"])
    check("A6 默认 ok=1", r["ok"] == 1, "=%s" % r["ok"])

    # —— 对照：这正是被替换掉的旧行为（result 被裁到 500）
    old_way = big[:500] + "…(截断)"
    check("A7 对照：旧 tool_log 存储只留 500 字符（证据沿用则必失真）",
          len(old_way) < len(big) and "第600行" not in old_way,
          "旧方式长度=%d 原文长度=%d" % (len(old_way), len(big)))
    check("A8 新登记含旧方式丢掉的尾部内容",
          "第600行" in (r["raw"] or ""), "尾部行丢失则回验必然失真")

    # ============ [B] 失败调用不被当成证据 ============
    print("\n=== [B] 失败调用识别（工具失败禁止补全） ===")
    try:
        import agent as AG
        W = AG.AgentWorker
    except Exception as e:
        check("B 组导入 agent", False, repr(e)[:120])
        return
    lf = W._tool_result_looks_failed
    check("B1 正常长文本不误判失败", not lf("read_file", big[:400]), "")

    fail_cases = [
        ("[RESULT NOT FOUND] 文件不存在：NO.md", "[RESULT NOT FOUND]"),
        ("文件不存在：C:\\x\\y.md", "文件不存在"),
        ("抓取失败：urlopen error timed out", "抓取失败"),
        ("timeout after 30s", "timeout"),
        ("Traceback (most recent call last):\n  File ...", "Traceback"),
    ]
    for i, (txt, label) in enumerate(fail_cases, 1):
        check("B1.%d 判失败：%s" % (i, label), lf("read_file", txt), repr(txt[:40]))
    check("B2 工具名=错误 → 判失败", lf("错误", "随便什么内容"), "")
    check("B3 空内容不崩且不判失败", lf("read_file", "") is False, "")
    check("B4 None 不崩", lf("read_file", None) is False, "")
    # 关键：标记必须在头部 400 字符内才认（避免正文里恰好出现「抓取失败」被误判）
    deep = "A" * 500 + "抓取失败"
    check("B5 失败标记出现在正文深处不误判（>400 字符）",
          not lf("read_file", deep),
          "位于500字符后，属正文内容而非工具状态")

    fev = ev.register("read_file", {"path": "NO.md"},
                      "[RESULT NOT FOUND] 文件不存在：NO.md", ok=False, err="不存在")
    fr = ev.get(fev)
    check("B6 失败证据 ok=0 且记了 err", fr["ok"] == 0 and bool(fr["err"]),
          "ok=%s err=%s" % (fr["ok"], fr["err"]))
    check("B7 失败证据抬头含「禁止据此补全」",
          "禁止据此补全" in ev.model_header(fev, tool="read_file", ok=False,
                                        err="不存在"),
          "")

    # ============ [C] ⟦EV#n⟧ 解析（含行号精度） ============
    print("\n=== [C] 引用标记解析 ===")
    t1 = "本项目的版本是 v4.194.1 ⟦EV#7⟧。"
    got = ev.parse_ev_tags(t1)
    check("C1 解析基本标记", got == [(7, None, None)], str(got))
    got = ev.parse_ev_tags("见 ⟦EV#7:L120-180⟧ 处")
    check("C2 解析带行号标记", got == [(7, 120, 180)], str(got))
    got = ev.parse_ev_tags("先 ⟦EV#7⟧ 概括，细节见 ⟦EV#7:L10-12⟧")
    check("C3 同证据重复引用 → 升级为带行号版本（行号不丢）",
          got == [(7, 10, 12)], str(got))
    check("C4 多证据按序去重", ev.parse_ev_tags("⟦EV#1⟧ ⟦EV#2⟧ ⟦EV#1⟧")
          == [(1, None, None), (2, None, None)],
          str(ev.parse_ev_tags("⟦EV#1⟧ ⟦EV#2⟧ ⟦EV#1⟧")))
    check("C5 ASCII 兼容写法 [EV#3]", ev.parse_ev_tags("参见 [EV#3] ") == [(3, None, None)],
          str(ev.parse_ev_tags("参见 [EV#3] ")))
    # 防误伤：普通方括号内容不能被当成引用
    for s in ["参考文献见 [1] 和 [2]", "[注2] 说明", "版本 [v4.194] 之后", "[TODO]"]:
        check("C6 不误伤普通方括号：%s" % s, ev.parse_ev_tags(s) == [],
              str(ev.parse_ev_tags(s)))
    check("C7 strip 去标记（给用户的干净文本）",
          ev.strip_ev_tags("结论 ⟦EV#7⟧ 结束").replace(" ", "") == "结论结束",
          repr(ev.strip_ev_tags("结论 ⟦EV#7⟧ 结束")))

    # ============ [D] 回验：这一条证据到底支不支持这句断言 ============
    print("\n=== [D] 证据回验（根治的核心判定） ===")
    check("D1 证据含该事实 → supports=True",
          ev.supports(eid, "第250行"), "")
    check("D2 证据不含该事实 → supports=False",
          not ev.supports(eid, "第2500行"), "")
    # 精度测试：同一句话在全文里有，但**指定行区间里没有** → 应为 False。
    # 这是根治相对「全文扫关键词」的决定性优势。
    check("D3 行区间限定：全文有但指定区间没有 → False",
          (ev.supports(eid, "第250行")) and
          (not ev.supports(eid, "第250行", 1, 10)),
          "full=%s slice=%s" % (ev.supports(eid, "第250行"),
                                ev.supports(eid, "第250行", 1, 10)))
    check("D4 行区间限定：区间内有 → True",
          ev.supports(eid, "第250行", 245, 255), "")
    check("D5 行切片内容正确",
          ev.lines_slice(eid, 250, 250).startswith("第250行"),
          repr(ev.lines_slice(eid, 250, 250)[:20]))
    check("D6 越界行号自动裁剪不崩",
          ev.lines_slice(eid, 599, 9999).count("\n") >= 1, "")
    check("D7 空证据 supports=False", not ev.supports(999999, "任何东西"), "")

    # ============ [E] UI 接入：零回归（旧字段必须还在） ============
    print("\n=== [E] UI 接入与既有字段零回归 ===")
    class _Store:
        def __init__(self):
            self.messages = []

        def active(self):
            return self

    st = _Store()
    _win = type("_W", (object,), {"store": st, "_save_throttled": lambda s: None,
                                  "_render_throttled": lambda s: None})()
    # 直接取 ui.ChatWindow._on_tool_log 函数，绑到假 self 上
    try:
        import ui as UI
        UI.ChatWindow._on_tool_log(
            _win, {"name": "read_file", "args": '{"path":"BIG.md"}',
                   "result": big, "evidence_id": eid, "evidence_ok": True})
        rec = st.messages[-1]
        check("E1 tool_log 落库存了 evidence_id", rec.get("evidence_id") == eid,
              str(rec.get("evidence_id")))
        check("E2 既有 result 字段仍在（批⑤/⑦ 依赖它，不能被删）",
              "result" in rec and rec["result"].endswith("…(截断)"), str(rec.keys()))
        check("E3 既有 args/name 字段仍在", "args" in rec and "name" in rec, "")
        # 无 evidence_id 的老数据兼容
        UI.ChatWindow._on_tool_log(
            _win, {"name": "run_command", "args": "x", "result": "ok"})
        rec2 = st.messages[-1]
        check("E4 无 evidence_id 时结构不变（老路径兼容）",
              "evidence_id" not in rec2 and rec2["result"] == "ok", str(rec2))
    except Exception as e:
        check("E 组 UI 接入", False, repr(e)[:160])

    # ============ [F] 端到端：_handle_tool_result 真的登记了 ============
    print("\n=== [F] 端到端（工具结果管线） ===")
    try:
        class _Sig:
            def __init__(self):
                self.payload = None

            def emit(self, *a):
                self.payload = a[0] if len(a) == 1 else a

        w = W.__new__(W)
        w.tool_log = _Sig()
        w.render = _Sig()
        w.deliverable_added = _Sig()
        w.schedule_reminder = _Sig()
        w.messages = []
        tc = {"id": "call_1"}
        fn = {"arguments": '{"path": "BIG.md"}'}
        w._handle_tool_result(tc, "read_file", fn, big, [], None)
        content = w.messages[-1]["content"]
        check("F1 模型可见内容带 [EV#n] 抬头", "[EV#" in content,
              content[:60])
        check("F2 模型可见内容带钉子（引用纪律）", "⟦EV#" in content, content[-90:])
        check("F3 tool_log emit 带 evidence_id",
              isinstance(w.tool_log.payload, dict)
              and isinstance(w.tool_log.payload.get("evidence_id"), int),
              str(w.tool_log.payload)[:120] if isinstance(w.tool_log.payload, dict) else str(type(w.tool_log.payload)))
        check("F4 成功工具 evidence_ok=True",
              w.tool_log.payload.get("evidence_ok") is True, "")
        _e = w.tool_log.payload.get("evidence_id")
        check("F5 登记内容与完整原文一致（模型看不到全文，但证据库里有）",
              ev.raw_text(_e) == big, "len=%d" % len(ev.raw_text(_e)))
        check("F6 模型上下文确实被压缩（不等同于原文）",
              len(content) < len(big), "ctx=%d raw=%d" % (len(content), len(big)))

        # 失败工具端到端
        w2 = W.__new__(W)
        w2.tool_log = _Sig(); w2.render = _Sig()
        w2.deliverable_added = _Sig(); w2.schedule_reminder = _Sig()
        w2.messages = []
        w2._handle_tool_result(
            {"id": "c2"}, "read_file", {"arguments": '{"path":"NO.md"}'},
            "[RESULT NOT FOUND] 文件不存在：NO.md", [], None)
        c2 = w2.messages[-1]["content"]
        check("F7 失败结果抬头标记失败", "本次调用失败" in c2, c2[:80])
        check("F8 失败结果 emit evidence_ok=False",
              w2.tool_log.payload.get("evidence_ok") is False,
              str(w2.tool_log.payload.get("evidence_ok")))
    except Exception as e:
        check("F 组端到端", False, repr(e)[:160])

    # ============ [G] 体积控制 ============
    print("\n=== [G] 体积控制 ===")
    before = ev.count()
    for i in range(30):
        ev.register("run_command", {"cmd": "echo %d" % i}, "out %d" % i)
    check("G1 多次登记后可统计", ev.count() >= before, "count=%d" % ev.count())
    check("G2 prune 限制条数生效",
          isinstance(ev.prune(max_rows=5, max_age_days=30), int)
          and ev.count() <= 5, "count=%d" % ev.count())
    check("G3 clear 清空", ev.clear() and ev.count() == 0, "count=%d" % ev.count())
    check("G4 清空后再取回返回 None（不抛错）", ev.get(1) is None, "")
    check("G5 空证据 supports 不抛错", ev.supports(None, "x") is False, "")


if __name__ == "__main__":
    sys.exit(main())
