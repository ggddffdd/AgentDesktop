# -*- coding: utf-8 -*-
"""v4.227 P2-3 任务状态机：把「只认字面点名」钉成可检出的设计决定。

v4.218《Agent 能力审查报告》P2 有一条「任务状态机只认工具名，不解析自然语言
目标」，报告当缺陷提出。**核实结论：这是刻意设计，不是缺陷**，本轮不改行为，
改为把这条决定钉死，防日后被当「同类漏项」顺手改掉。

为什么刻意（不是偷懒）：
  `intent.Intent.requested_tools` 是**关键词**命中，语义宽得多 ——
  「帮我写一段口播文案」会命中 video_gen（词表里有「口播」），但用户要的是
  一段文字，没让你生视频。拿它当硬要求 = 逼模型为一个不存在的产物去调视频工具。
  「只认字面点名」宁可漏（漏了顶多少收尾 nudge），不可错（错了逼出假产物）。

判据分四组：
  A 行为级 正例（字面点名 → 是硬要求）/ 反例（口播文案 → 绝不能命中 video_gen）
  B 词边界 my_image_genner 不能命中 image_gen（子串误伤）
  C 登记事实 HARD_REQUIREMENT_POLICY + 被否决口径及理由必须在
  D 判据自证 A 组非恒真（真改成关键词命中必须翻红）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


import task_state as ts            # noqa: E402
from task_state import required_from_text, requirement_policy_report  # noqa: E402


def group_a_behavior():
    print("\n-- A) 行为级：字面点名才算硬要求 --")

    # A1 正例：用户把工具名当词写出来 → 是硬要求
    pos = [
        ("用 video_gen 生成一段视频", "video_gen"),
        ("调web_search 查一下最新榜单", "web_search"),
        ("请 write_file 把结果存下来", "write_file"),
    ]
    bad = []
    for text, want in pos:
        got = [n for n, _w in required_from_text(text)]
        if want not in got:
            bad.append((text, want, got))
    check("A1 字面点名 → 认定为硬要求（3 例）", not bad, "未命中=%r" % bad)

    # A2 核心反例：报告点名的那个场景 —— 「写口播文案」绝不能命中 video_gen
    #    这是本条设计存在的**全部理由**，必须钉死。
    anti = [
        "帮我写一段口播文案",
        "写个口播脚本给我",
        "生成个视频文案",
        "写一段视频介绍词",
    ]
    hit = []
    for text in anti:
        got = [n for n, _w in required_from_text(text)]
        if "video_gen" in got:
            hit.append((text, got))
    check("A2 反例：「口播/文案」类文本请求绝不命中 video_gen", not hit,
          "误命中=%r" % hit)

    # A3 反例扩面：其它工具也不能被关键词误伤
    anti2 = [
        ("查一下最近的股票行情", "web_search"),
        ("把结果整理成表格", "write_file"),
        ("搜点资料看看", "rag_search"),
    ]
    hit2 = []
    for text, tool in anti2:
        got = [n for n, _w in required_from_text(text)]
        if tool in got:
            hit2.append((text, tool, got))
    check("A3 反例：泛化描述不命中任何具体工具", not hit2, "误命中=%r" % hit2)

    # A4 返回值形态：[(tool, 用户原文写法)]，去重保序
    got = required_from_text("用 video_gen 生成，然后再用 video_gen 一次")
    check("A4 重复点名去重（只留一条）",
          len([g for g in got if g[0] == "video_gen"]) == 1, "got=%r" % (got,))
    check("A4b 第二元素是用户原文写法（不是规范化名）",
          got and got[0][1] == "video_gen", "got=%r" % (got,))

    # A5 空输入不崩
    check("A5 空/None 输入不崩且返回空",
          required_from_text("") == [] and required_from_text(None) == [])


def group_b_word_boundary():
    print("\n-- B) 词边界：子串不得误伤 --")
    # B1 image_gen 不该被 my_image_genner / image_gen_v2 之类命中
    cases = [
        "my_image_genner 这个变量",
        "prefix_image_gen_suffix",
        "my_web_search_helper",
    ]
    hit = []
    for text in cases:
        got = [n for n, _w in required_from_text(text)]
        if any(n in got for n in ("image_gen", "web_search")):
            hit.append((text, got))
    check("B1 前后缀粘连的标识符不误命中", not hit, "误命中=%r" % hit)

    # B2 词边界该拦的仍要放过正常形态
    got = required_from_text("image_gen 这个工具")
    check("B2 正常形态仍命中（词边界没拦过头）",
          "image_gen" in [n for n, _w in got], "got=%r" % (got,))


def group_c_registry():
    print("\n-- C) 设计决定已登记成可检出的事实 --")
    rep = requirement_policy_report()
    check("C1 当前口径已登记",
          rep.get("active") == "literal_tool_name_only",
          "active=%r" % rep.get("active"))
    check("C2 被否决的口径带理由（非空壳）",
          bool(rep.get("rejected")), "rejected=%r" % rep.get("rejected"))
    # C3 每条理由都要说清「为什么不用」，不能只写名字
    noreason = [k for k, v in (rep.get("rejected") or {}).items()
                if not str(v).strip() or len(str(v).strip()) < 8]
    check("C3 每条否决理由都是实质说明", not noreason, "太短=%r" % noreason)
    # C4 被否决的口径必须点名 intent 关键词命中（报告误判的核心）
    check("C4 明确否决 intent 关键词命中（报告误判的那条）",
          "intent_keyword" in (rep.get("rejected") or {}))
    # C5 常量确实落在源码里（判据要钉得住，不能只在运行时动态造）
    src = open(os.path.join(ROOT, "task_state.py"), "rb").read().decode("utf-8-sig")
    check("C5 HARD_REQUIREMENT_POLICY 落在源码里（可 grep）",
          'HARD_REQUIREMENT_POLICY = "literal_tool_name_only"' in src)
    # C6 实现仍只字面点名 —— 登记与实现不许漂移。
    #    ⚠️ 必须剔注释/docstring 再查：`required_from_text` 的 docstring 里
    #    正面写着 `requested_tools`（正是在解释「为什么不用它」），
    #    拿 docstring 当证据必然假红（v4.227 首版就栽在这）。
    import ast
    import io
    import tokenize
    ttree = ast.parse(src)
    fa = None
    for nd in ttree.body:
        if isinstance(nd, ast.FunctionDef) and nd.name == "required_from_text":
            fa = nd
            break
    check("C6a required_from_text 可定位（否则本组无意义）", fa is not None)
    body_all = ""
    if fa is not None:
        body_all = "\n".join(src.split("\n")[fa.lineno - 1:fa.end_lineno])
    # 只取 docstring 之外的代码行（ast 给出 docstring 的行号区间）
    body_code = body_all
    if fa is not None:
        doc0 = fa.body[0]
        if (isinstance(doc0, ast.Expr)
                and isinstance(getattr(doc0, "value", None), ast.Constant)):
            lines = body_all.split("\n")
            doc_end = doc0.end_lineno - fa.lineno
            body_code = "\n".join(lines[:1] + lines[doc_end + 1:])
    check("C6 required_from_text 代码体内无关键词命中（实现与登记一致）",
          "requested_tools" not in body_code,
          "实现里出现了 requested_tools 关键词命中")
    check("C6b 实现仍用词边界正则（未被改成裸 in）",
          "(?<![A-Za-z0-9_])" in body_all)
    check("C6c 文档仍解释「为何不用 requested_tools」（说明保留）",
          "requested_tools" in body_all)


def group_d_judge_selfproof():
    print("\n-- D) 判据自证：A 组非恒真 --")
    # 复刻 required_from_text 的核心逻辑（词边界 + 字面点名），
    # 构造「改成关键词命中」的坏实现，证明 A2 会翻红。
    def literal_impl(text, names):
        out = []
        low = str(text or "").lower()
        for nm in sorted(names):
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(nm.lower())
                             + r"(?![A-Za-z0-9_])")
            if pat.search(low):
                out.append((nm, nm))
        return out

    def keyword_impl(text, names, kw_map):
        """坏实现：按关键词表命中（报告建议的那种做法）。"""
        out = []
        low = str(text or "").lower()
        for nm in sorted(names):
            # 关键词表里必须**含工具名本身** —— 真实的关键词命中器一定会把
            # 工具名收进词表（否则「用 video_gen」这种最明确的点名都识别不了，
            # 那不是关键词命中器，是残废品）。v4.227 首版漏了这一项，
            # 导致 D3「正例在两种实现下都命中」假红。
            for kw in kw_map.get(nm, []):
                if kw in low:
                    out.append((nm, kw))
                    break
        return out

    names = ["video_gen", "web_search", "write_file"]
    kw_map = {
        "video_gen": ["video_gen", "口播", "视频", "文案"],
        "web_search": ["web_search", "查", "搜"],
        "write_file": ["write_file", "存", "保存"],
    }

    # D1 抗案例：字面实现下「口播文案」不命中 video_gen
    r1 = [n for n, _w in literal_impl("帮我写一段口播文案", names)]
    check("D1 字面实现对「口播文案」不命中 video_gen（判据前提成立）",
          "video_gen" not in r1, "got=%r" % r1)

    # D2 关键词实现下同一句命中 video_gen → A2 判据会翻红
    r2 = [n for n, _w in keyword_impl("帮我写一段口播文案", names, kw_map)]
    check("D2 关键词实现下同一句命中 video_gen（证明 A2 非恒真）",
          "video_gen" in r2, "got=%r" % r2)

    # D3 正例在两种实现下都命中（说明差异只在反例侧，不误伤正常用法）
    p1 = [n for n, _w in literal_impl("用 video_gen 生成", names)]
    p2 = [n for n, _w in keyword_impl("用 video_gen 生成", names, kw_map)]
    check("D3 正例在两种实现下都命中（差异只在反例侧）",
          "video_gen" in p1 and "video_gen" in p2,
          "字面=%r 关键词=%r" % (p1, p2))

    # D4 词边界自证：裸 in 会误伤 my_image_genner
    names2 = ["image_gen"]
    naive = "image_gen" in "my_image_genner"
    bounded = bool(re.search(r"(?<![A-Za-z0-9_])image_gen(?![A-Za-z0-9_])",
                             "my_image_genner"))
    check("D4 裸 in 会误伤 my_image_genner，词边界不会（B 组非恒真）",
          naive and not bounded, "naive=%r bounded=%r" % (naive, bounded))


def main():
    print("v4.227 P2-3 任务状态机：设计决定钉子")
    group_a_behavior()
    group_b_word_boundary()
    group_c_registry()
    group_d_judge_selfproof()
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
