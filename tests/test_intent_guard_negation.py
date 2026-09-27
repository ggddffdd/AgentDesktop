# -*- coding: utf-8 -*-
"""否定判据两道闸回归测试（v4.168.3）。

钉住的 BUG：

    自动化任务「每日 AI 新闻抓取」全文 759 字，里面有
      「不要盲目上浏览器」「不要据此判定」
    → intent_guard.is_negation() 一见「不要」就一票否决
    → 整条消息判为非指令 → ui._stream_once 强制 tool_choice="none"
    → **任务被禁止调工具**（抓新闻必须调工具），任务直接瘫痪。

  同一坑还有高频日常句：
      「抓取今天的新闻，不要盲目上浏览器，优先 RSS」（23 字）
      「写一段文案，不要超过 50 个字」

根因：negation 只看「有没有否定词」，不看「否定词在说什么」。
中文里「不要 X」在指令里最常见的用法恰恰是**加约束**，不是叫停任务。

两道闸：
  · 长度闸  —— 喊停是短句（实测 ≤30 字）；上百字是任务书/需求描述。
  · 位置判据 —— 否定词出现在正向任务祈使**之后** ⇒ 前面已在派任务，这句否定是约束。

本套件同时钉住**反方向**：真喊停句必须仍被拦住（不能为了放行约束而放跑喊停）。
纯标准库，不导入 Qt / ui / agent。
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent_guard as ig   # noqa: E402

_p = _f = 0
_IG_SRC = os.path.join(ROOT, "intent_guard.py")


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [OK] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  <- {detail}" if detail else ""))


# 内置的任务文本副本（脱敏，保留触发特征：以「抓取」开头 + 含多处「不要」约束）。
# 不直接读用户 automation_tasks.json —— 测试要自包含，换台机器也能跑。
TASK_NEWS = (
    "抓取今天最新的 AI 领域新闻（国内+国外），整理成 8 条以内要点简报"
    "（每条：标题 + 一句话摘要 + 来源），写入 ~/Documents/小臭玩AI/产物/ai_news_latest.md，"
    "并在回复里展示简报内容。\n\n"
    "【抓取通道优先级，按序尝试，不要盲目上浏览器】\n"
    "T0 首选 RSS（零对抗、最稳）：\n"
    "- 爱范儿 https://www.ifanr.com/feed\n"
    "T0 次选 requests + 真实 Chrome UA 直连。\n\n"
    "【重要提醒】Playwright 抛 ERR_ABORTED 不代表被反爬，不要据此判定「被拦」。\n\n"
    "【如实原则】当日国内源确实抓不到时，注明该来源文章的真实日期，"
    "禁止编造日期或来源。"
)

TASK_TRENDING = (
    "抓取今天最新的 GitHub trending，整理成 8 条以内要点简报，"
    "写入 ~/Documents/小臭玩AI/产物/github_trending_latest.md。\n"
    "首选 requests 直连 https://github.com/trending?since=daily（无需浏览器）。\n"
    "Star 数走 GitHub API 逐条复核，不要照抄列表页。"
)

# 「只有长度闸能拦住」的样本：超长 + 含「不要」+ **不含任何任务动词**。
# 纯约束/规格类文本就是这种形态（没有"抓取/整理/写入"这种祈使开头），
# 位置判据对它无能为力 —— 必须靠长度闸。
LONG_CONSTRAINT_NO_VERB = (
    "【说明】" + "这段是背景资料，与本次无关。" * 6 + "不要盲目上浏览器。"
)


# ---------------------------------------------------------------------------
def part_a_real_task_texts():
    print("\n-- A) 真实任务文本不得被判「喊停」（旧版全错，核心回归） --")
    for name, txt in (("每日 AI 新闻抓取", TASK_NEWS), ("每日GitHub热榜", TASK_TRENDING)):
        e = ig.explain(txt)
        check(f"{name}（{len(txt)} 字）不判非指令", not ig.is_non_action_message(txt), f"{e}")
        check(f"{name} 不拦工具调用", not ig.blocks_tool_call(txt), f"why={ig.why_blocked(txt)}")
        check(f"{name} negation 为 False", e["negation"] is False, f"{e}")
    # 带 UI 前缀（自动化任务消息的真实形态）
    check("带【自动化任务】前缀同样不误判",
          not ig.blocks_tool_call("【自动化任务】" + TASK_NEWS))
    check("确认这些文本里**确实**含有「不要」（否则本测试是空跑）",
          "不要" in TASK_NEWS and "不要" in TASK_TRENDING)


def part_b_short_constraints():
    print("\n-- B) 短约束句（长度闸拦不住，靠位置判据 —— 第二档回归） --")
    cases = [
        "抓取今天的新闻，不要盲目上浏览器，优先 RSS",
        "整理成简报，不要编造日期或来源",
        "写一段文案，不要超过 50 个字，语气平实一点",
        "生成视频，不要加字幕，横版 16:9",
        "搜索今天的 AI 新闻，不用管国外源",
        "做个视频，别太长，8 秒就行",
        "写篇文章，不要出现真实地名",
    ]
    for s in cases:
        e = ig.explain(s)
        check(f"不误判为喊停：{s[:26]}", not ig.is_negation(s), f"{e}")
        check(f"  不拦工具：{s[:20]}", not ig.blocks_tool_call(s))
    check("确认这些短句都 < 长度闸（证明是靠位置判据拦下的，不是靠长度）",
          all(len(s) <= ig._NEG_MAX_LEN for s in cases),
          f"最长 {max(len(s) for s in cases)} vs 闸 {ig._NEG_MAX_LEN}")


def part_c_real_stops():
    print("\n-- C) 反方向：真喊停句必须仍被拦住（不能放跑喊停） --")
    stops = [
        "别生成视频了", "不要做了", "先别弄了", "算了", "取消", "停一下", "暂停",
        "别做这个了", "别再弄了", "不用了", "别画了", "别了",
        "别生成视频了，我这边网络太慢",
        "刚才那个不要了，重新来一个",
        "这个视频不要了，我刚才试了一下发现效果不太行，还是算了",
    ]
    for s in stops:
        check(f"仍拦喊停：{s[:34]}", ig.is_negation(s), f"explain={ig.explain(s)}")
        check(f"  仍关工具：{s[:20]}", ig.blocks_tool_call(s))


def part_d_two_gates_separately():
    print("\n-- D) 两道闸各自独立生效（缺一不可） --")
    # 闸一：长度闸 —— 必须用「无任务动词」的长文才验证得到它。
    # （含任务动词的长文会被位置判据先拦下，此时长度闸是冗余的，
    #   拿它当样本会得到「通过」但归因错误 —— 本套件吃过这个亏。）
    check("D1 样本确实不含任何任务动词（只能靠长度闸拦）",
          not ig._TASK_VERB_RE.search(LONG_CONSTRAINT_NO_VERB),
          f"命中={ig._TASK_VERB_RE.search(LONG_CONSTRAINT_NO_VERB)}")
    check(f"D1 超长无动词（{len(LONG_CONSTRAINT_NO_VERB)} 字）→ 长度闸放行",
          not ig.is_negation(LONG_CONSTRAINT_NO_VERB),
          f"explain={ig.explain(LONG_CONSTRAINT_NO_VERB)}")
    # 闸二：位置判据 —— 短句 + 任务动词在前 + 否定在后
    short_with_verb = "抓取新闻，不要上浏览器"
    check("D2 短样本确实在长度闸内（只能靠位置判据拦）",
          len(short_with_verb) <= ig._NEG_MAX_LEN, f"{len(short_with_verb)} 字")
    check(f"D2 短但有任务动词在前（{len(short_with_verb)} 字）→ 位置判据放行",
          not ig.is_negation(short_with_verb), f"explain={ig.explain(short_with_verb)}")
    # 位置判据比的是「先后」，不是「有没有任务动词」
    check("D3 否定词打头（任务动词在其后）→ 仍判喊停",
          ig.is_negation("不要上浏览器，去抓新闻"),
          f"explain={ig.explain('不要上浏览器，去抓新闻')}")
    check("D3 任务动词在否定之后 → 不算约束",
          ig.is_negation("别做了，改成写文案") is True,
          f"explain={ig.explain('别做了，改成写文案')}")


def part_e_other_judges_untouched():
    print("\n-- E) 其他判据不受影响（本次只动 negation） --")
    check("评价句仍拦（动补结构）", ig.blocks_tool_call("这个视频生成得真不错"))
    check("评价句仍拦（短语）", ig.blocks_tool_call("刚才图片做得太惊艳了"))
    check("方法疑问仍拦", ig.blocks_tool_call("视频怎么生成的？"))
    check("学习咨询仍拦", ig.blocks_tool_call("AI 视频怎么学"))
    check("引用质疑仍拦", ig.blocks_tool_call("你刚才说的生成个视频，是BUG"))
    check("『别出心裁做个视频』仍不误判（裸别字闭集豁免）",
          not ig.is_negation("别出心裁做个视频"))
    check("『辨别一下这张图』仍不误判", not ig.is_negation("辨别一下这张图"))
    check("状态追问不受影响（Agent 要用 task_status）",
          not ig.blocks_tool_call("任务进度如何"))


def part_f_single_source_of_truth():
    print("\n-- F) 识别口径唯一来源（防判据漂移） --")
    src = open(_IG_SRC, encoding="utf-8-sig").read()
    tree = ast.parse(src)
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    check("_neg_hits 已抽出（口径唯一来源）", "_neg_hits" in fns)
    check("_neg_is_constraint 已抽出", "_neg_is_constraint" in fns)
    check("is_negation 改用 _neg_hits", "if not _neg_hits(text)" in src)
    check("_neg_is_constraint 也用 _neg_hits（两处同源）",
          "_neg_hits(text)" in ast.get_source_segment(
              src, next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == "_neg_is_constraint")))
    # 旧写法（把词表遍历直接写在 is_negation 里）必须消失，否则口径又会分叉
    is_neg_src = ast.get_source_segment(
        src, next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "is_negation"))
    check("is_negation 里不再直接遍历 _NEG_PHRASES",
          "_NEG_PHRASES" not in is_neg_src)
    check("长度闸常量存在且为 40", ig._NEG_MAX_LEN == 40, f"{ig._NEG_MAX_LEN}")
    check("任务动词表存在（位置判据依赖）", hasattr(ig, "_TASK_VERB_RE"))
    check("『抓取』『整理』在任务动词表内",
          bool(ig._TASK_VERB_RE.search("抓取") and ig._TASK_VERB_RE.search("整理")))


def part_g_negative_verification():
    print("\n-- G) 负面验证：拆掉任一道闸，回归必须变红 --")
    src = open(_IG_SRC, encoding="utf-8-sig").read()
    tree = ast.parse(src)
    is_neg_src = ast.get_source_segment(
        src, next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "is_negation"))

    def _run_with(patched_src):
        ns = {"re": __import__("re")}
        for nm in ("_NEG_PHRASES", "_NEG_COMPOUND_BEFORE", "_NEG_TAIL_VERB",
                   "_NEG_TAIL_PARTICLE", "_NEG_MAX_LEN", "_TASK_VERB_RE", "_norm"):
            ns[nm] = getattr(ig, nm)
        for nm in ("_neg_hits", "_neg_is_constraint"):
            ns[nm] = getattr(ig, nm)
        exec(patched_src, ns)
        return ns["is_negation"]

    # G1 拆掉长度闸 → 用「只有它拦得住」的样本验证它必需
    g1 = is_neg_src.replace(
        "    if len(_norm(text)) > _NEG_MAX_LEN:\n        return False\n", "")
    check("G1 锚点命中（长度闸可拆）", g1 != is_neg_src)
    if g1 != is_neg_src:
        f1 = _run_with(g1)
        check("G1 拆掉长度闸 → 纯长约束文又被判喊停（证明长度闸必需）",
              f1(LONG_CONSTRAINT_NO_VERB) is True)

    # G2 拆掉位置判据
    g2 = is_neg_src.replace(
        "    if _neg_is_constraint(text):\n        return False\n", "")
    check("G2 锚点命中（位置判据可拆）", g2 != is_neg_src)
    if g2 != is_neg_src:
        f2 = _run_with(g2)
        check("G2 拆掉位置判据 → 短约束句又被判喊停（证明闸必需）",
              f2("抓取新闻，不要上浏览器") is True)

    # G3 两道都拆 → 回到旧行为（真实任务 / 短约束 / 长约束 全灭）
    if g1 != is_neg_src and g2 != is_neg_src:
        both = g2.replace(
            "    if len(_norm(text)) > _NEG_MAX_LEN:\n        return False\n", "")
        f3 = _run_with(both)
        check("G3 两道都拆 → 三类样本全被判喊停（旧版行为完整复现）",
              f3(TASK_NEWS) is True
              and f3("抓取新闻，不要上浏览器") is True
              and f3(LONG_CONSTRAINT_NO_VERB) is True)


def part_h_optional_real_data():
    """若本机有真实自动化任务文件，用真文本再加一轮（可选，缺失不失败）。"""
    print("\n-- H) 真实自动化任务文本（本机有才跑） --")
    p = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI",
                     "automation_tasks.json")
    if not os.path.isfile(p):
        print("  [SKIP] 未找到 automation_tasks.json，跳过（不影响结论）")
        return
    import json
    try:
        tasks = json.load(open(p, encoding="utf-8")).get("tasks") or []
    except Exception as e:
        print(f"  [SKIP] 解析失败：{e}")
        return
    for t in tasks:
        msg = t.get("message") or ""
        if not msg:
            continue
        nm = (t.get("name") or "?")[:16]
        for label, txt in (("裸", msg), ("带前缀", "【自动化任务】" + msg)):
            check(f"真实任务 {nm} {label}（{len(txt)} 字）不拦工具",
                  not ig.blocks_tool_call(txt),
                  f"why={ig.why_blocked(txt)}")


def main():
    part_a_real_task_texts()
    part_b_short_constraints()
    part_c_real_stops()
    part_d_two_gates_separately()
    part_e_other_judges_untouched()
    part_f_single_source_of_truth()
    part_g_negative_verification()
    part_h_optional_real_data()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
