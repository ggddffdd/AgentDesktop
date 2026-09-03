# -*- coding: utf-8 -*-
"""工具预算分析器 —— 回答「功能怪兽的代价到底有多大，能不能降下来」。

背景（v4.110 实测）：
  实测每次 API 调用平均输入 **18,646 tok**，而 68 个工具的定义就有
  26,835 字符（≈ 12K tok）——**工具定义单项就吃掉输入的一半以上**。
  更狠的是 Agent 循环：知乎那条会话 323 次工具调用，每次都重发全量工具定义，
  光这一项就是几百万 tok。

  这直接卡死了「功能怪兽」路线：功能越多 → 工具数越多 → 每轮固定成本线性上涨。
  **要让功能能无限堆，必须让每轮注入量不随工具总数增长。**

本脚本干的事（沿用 route_judge.py 的「先量后改」方法论）：
  1. 量出每个工具的定义体积（字符 / 估算 token）
  2. 从真实历史会话里取 ground truth：每一轮 user 消息实际触发了哪些工具
  3. 模拟几种「按需注入」策略，算出各自的 **体积** 与 **召回率**
  4. 用留一会话交叉验证（A 会话建核心集、B 会话上测试），避免自己人测自己人

**只分析不动手**：本脚本不修改任何代码，不改动运行时行为。

用法：
    python tool_budget.py              # 默认跑全部策略
    python tool_budget.py --top 12 8   # 指定常驻/召回数量
"""

import json
import os
import re
import sys
import collections

# 估算系数：工具定义是 JSON，中英混合（大量 ASCII 键名/引号/标点）
# 中文按 ~1 字 1 tok、ASCII 按 ~4 字符 1 tok，JSON 骨架拉低均值。
# 取 2.0 字符/tok，并在报告里给出 1.6~2.4 的区间，避免把估算当实测。
CHARS_PER_TOK = 2.0
CHARS_PER_TOK_RANGE = (1.6, 2.4)


# ---------------------------------------------------------------- 数据加载

def cfg_path():
    return os.path.join(os.path.expanduser("~"), "Documents",
                        "小臭玩AI", "config.json")


def sessions_path():
    return os.path.join(os.path.expanduser("~"), "Documents",
                        "小臭玩AI", "sessions.json")


def load_tools():
    """拿线上真实工具集（config.get_all_tools），含 MCP / 动态注册的那批。"""
    import config
    with open(cfg_path(), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    tools = config.get_all_tools(cfg)
    out = []
    for t in tools:
        fn = (t or {}).get("function") or {}
        name = fn.get("name") or t.get("name") or "?"
        size = len(json.dumps(t, ensure_ascii=False))
        out.append({"name": name,
                    "desc": fn.get("description") or "",
                    "params": list(((fn.get("parameters") or {}).get("properties")
                                    or {}).keys()),
                    "chars": size})
    return out


def load_turns():
    """从真实会话里抽 (user文本, 该轮实际调用的工具名**集合**)。

    ⚠ 口径差异：本函数统计的是**轮次数**（同一轮里调 3 次算 1），
    而 tool_manager_ui._tool_usage_counts 统计的是**调用次数**（算 3）。
    两边数字对不上是正常的，别当成 bug 去"修"。

    归因规则：从一条 user 消息开始，到**下一条 user 消息之前**出现的所有
    tool_log 都算这一轮触发的。
    """
    with open(sessions_path(), "r", encoding="utf-8") as f:
        data = json.load(f)
    sess = data.get("sessions") if isinstance(data, dict) else data
    turns = []          # [(session_idx, user_text, {tool,...}), ...]
    if not isinstance(sess, list):
        return turns
    for si, s in enumerate(sess):
        if not isinstance(s, dict):
            continue
        cur_txt, cur_tools = None, set()
        for m in (s.get("messages") or []):
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "user":
                if cur_txt is not None:
                    turns.append((si, cur_txt, cur_tools))
                cur_txt = _msg_text(m)
                cur_tools = set()
            elif role == "tool_log" and cur_txt is not None:
                n = (m.get("name") or "").strip()
                if n:
                    cur_tools.add(n)
        if cur_txt is not None:
            turns.append((si, cur_txt, cur_tools))
    # 只保留"确实调了工具"的轮次——没调工具的不影响召回率
    return [(si, t, ts) for si, t, ts in turns if ts]


def _msg_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------- 关键词召回

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def kw_set(text):
    """把一段文本拆成匹配用的关键词集合：英文词 + 中文 2-gram。

    为什么用 2-gram：中文没有空格，纯单字噪声太大（"的""了"到处命中），
    2-gram 能在"搜索/搜索互联网"这类描述上稳定命中"搜索"。
    """
    ks = set()
    for w in _WORD.findall(text or ""):
        ks.add(w.lower())
        # snake_case 工具名拆开：web_search -> web / search
        for part in w.split("_"):
            if len(part) > 1:
                ks.add(part.lower())
    for seg in _CJK.findall(text or ""):
        if len(seg) == 1:
            ks.add(seg)
        for i in range(len(seg) - 1):
            ks.add(seg[i:i + 2])
    return ks


def build_index(tools):
    """给每个工具建关键词索引。名字权重高，描述权重低。"""
    idx = []
    for t in tools:
        name_kw = kw_set(t["name"])
        desc_kw = kw_set(t["desc"])
        idx.append({"name": t["name"], "name_kw": name_kw, "desc_kw": desc_kw,
                    "all_kw": name_kw | desc_kw})
    return idx


def retrieve(index, query, topk):
    """按关键词重叠打分取 TopK。名字命中权重 3，描述命中权重 1。"""
    q = kw_set(query)
    if not q:
        return []
    scored = []
    for it in index:
        s = 3 * len(q & it["name_kw"]) + len(q & it["desc_kw"])
        if s > 0:
            scored.append((s, it["name"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [n for _, n in scored[:topk]]


# ---------------------------------------------------------------- 策略模拟

def tool_size_map(tools):
    return {t["name"]: t["chars"] for t in tools}


def eval_plan(turns, tools, train_sessions, core_n, recall_n, carry="sess"):
    """评估一套「常驻 core_n + 按需召回 recall_n + 会话携带 carry」方案。

    留一会话交叉验证：常驻集只用 train_sessions 的历史频次建，
    测试只在**其余会话**的轮次上跑——避免"用未来数据建集再测过去"。

    ⚠ carry 为什么必须有（v4.110 实测）：
      纯关键词召回在 Agent 会话里只有 59% 轮次召回，因为——
        ① 平均一轮横跨 **3.6 个工具**（最多 13 个），关键词很难全中；
        ② **33% 的轮次 user 消息 ≤6 字**（"继续"/"什么情况"/"我登录了"），
           压根没有关键词可提。
      这两条决定了：真正的信号不在当前消息里，在**会话上下文**里。

      carry 三档：
        "none"  只靠常驻 + 关键词（对照用，证明它不行）
        "prev"  带上**上一轮**用过的工具（单步粘性）
        "sess"  带上**本会话此前**用过的所有工具（全会话粘性）

    返回 dict：
      chars_avg    平均每轮注入的工具定义字符数
      tok_avg      估算 token
      recall       轮次级召回率：该轮实际用到的工具**全部**在注入集里的比例
      tool_recall  工具级召回率：单次工具调用被覆盖的比例
      miss         漏掉的 (工具, 轮次文本) 样例
    """
    sizes = tool_size_map(tools)
    index = build_index(tools)

    # 常驻集：训练会话里的高频工具
    freq = collections.Counter()
    for si, _, ts in turns:
        if si in train_sessions:
            freq.update(ts)
    core = [n for n, _ in freq.most_common(core_n)]

    test = [(si, t, ts) for si, t, ts in turns if si not in train_sessions]
    if not test:
        return None

    tot_chars = 0
    hit_turn = 0
    tot_calls = 0
    hit_calls = 0
    miss = []
    prev_used = set()       # carry="prev"
    sess_used = collections.defaultdict(set)   # carry="sess"，按会话隔离
    for si, txt, used in test:
        injected = set(core) | set(retrieve(index, txt, recall_n))
        if carry == "prev":
            injected |= prev_used
        elif carry == "sess":
            injected |= sess_used[si]
        tot_chars += sum(sizes.get(n, 0) for n in injected)

        missing = used - injected
        if not missing:
            hit_turn += 1
        else:
            miss.append((sorted(missing), txt[:50]))
        for n in used:
            tot_calls += 1
            if n in injected:
                hit_calls += 1
        prev_used = set(used)
        sess_used[si] |= set(used)
    n = len(test)
    return {"core_n": core_n, "recall_n": recall_n, "carry": carry, "turns": n,
            "chars_avg": tot_chars / n, "tok_avg": tot_chars / n / CHARS_PER_TOK,
            "recall": 100.0 * hit_turn / n,
            "tool_recall": 100.0 * hit_calls / max(1, tot_calls),
            "core": core, "miss": miss}


def eval_full(turns, tools, test_sessions):
    """基线：全量注入。"""
    sizes = tool_size_map(tools)
    test = [(si, t, ts) for si, t, ts in turns if si in test_sessions]
    if not test:
        return None
    tot = sum(sizes.get(n, 0) for n in sizes)
    return {"name": "全量", "turns": len(test), "chars_avg": float(tot),
            "tok_avg": tot / CHARS_PER_TOK, "recall": 100.0,
            "tool_recall": 100.0, "miss": []}


# ---------------------------------------------------------------- 报告

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    core_list = [0, 8, 12, 16, 20]
    recall_list = [8, 12, 16, 24]

    tools = load_tools()
    turns = load_turns()
    if "--suggest" in argv:
        return suggest(tools, turns, argv)
    if not turns:
        print("历史里没找到带工具调用的轮次，无法分析。")
        return 1

    sess_ids = sorted({si for si, _, _ in turns})
    used = collections.Counter()
    for _, _, ts in turns:
        used.update(ts)

    print("=" * 74)
    print("工具预算分析（真实历史回放）")
    print("=" * 74)
    tot_chars = sum(t["chars"] for t in tools)
    print("工具总数 %d，定义 %d 字符 ≈ %s tok（按 %.1f 字符/tok 估，区间 %s）"
          % (len(tools), tot_chars, _fmt_tok(tot_chars), CHARS_PER_TOK,
             _fmt_range(tot_chars)))
    print("历史 %d 个会话、%d 个「确实调了工具」的轮次，共用到 %d 个不同工具"
          % (len(sess_ids), len(turns), len(used)))
    never = [t["name"] for t in tools if t["name"] not in used]
    print("从未被用过的工具：%d 个" % len(never))
    if never:
        print("   " + " / ".join(never[:24]) + (" …" if len(never) > 24 else ""))
    print()

    print("-" * 74)
    print("体积最大的 10 个工具定义")
    print("-" * 74)
    for t in sorted(tools, key=lambda x: -x["chars"])[:10]:
        flag = "用 %3d 次" % used.get(t["name"], 0) if t["name"] in used else "从未用过"
        print("  %-26s %6d 字符 %8s  %s"
              % (t["name"], t["chars"], _fmt_tok(t["chars"]), flag))
    print()

    base = eval_full(turns, tools, set(sess_ids))

    # 留一会话交叉验证：常驻集只用其余会话建，在留出会话上测
    agg = collections.defaultdict(list)
    for hold in sess_ids:
        train = [s for s in sess_ids if s != hold]
        for carry in ("none", "prev", "sess"):
            for cn in core_list:
                for rn in recall_list:
                    r = eval_plan(turns, tools, train, cn, rn, carry=carry)
                    if r:
                        agg[(carry, cn, rn)].append(r)

    rows = []
    for key, rs in agg.items():
        n = len(rs)
        rows.append({
            "carry": key[0], "core_n": key[1], "recall_n": key[2],
            "chars": sum(r["chars_avg"] for r in rs) / n,
            "recall": sum(r["recall"] for r in rs) / n,
            "tool_recall": sum(r["tool_recall"] for r in rs) / n,
            "miss": rs[0]["miss"],
        })

    for carry, title in (("none", "① 纯关键词召回（无粘性）—— 对照组"),
                         ("prev", "② 上一轮粘性"),
                         ("sess", "③ 全会话粘性（本会话用过的工具都留着）")):
        print("-" * 74)
        print(title)
        print("-" * 74)
        print("%-14s %9s %11s %10s %10s" %
              ("方案", "注入体积", "估算 tok", "轮次召回", "省"))
        print("-" * 74)
        sub = [r for r in rows if r["carry"] == carry]
        # 只展示体积最小的前 6 档 + 召回率最高的前 3 档，免得刷屏
        by_size = sorted(sub, key=lambda x: x["chars"])[:6]
        by_rec = sorted(sub, key=lambda x: (-x["recall"], x["chars"]))[:3]
        shown, seen = [], set()
        for r in by_size + by_rec:
            k = (r["core_n"], r["recall_n"])
            if k in seen:
                continue
            seen.add(k)
            shown.append(r)
        shown.sort(key=lambda x: x["chars"])
        for r in shown:
            print("  常驻%2d+召回%2d   %7d 字 %11s %9.1f%% %9.0f%%"
                  % (r["core_n"], r["recall_n"], int(r["chars"]),
                     _fmt_tok(r["chars"]), r["recall"],
                     100.0 * (1 - r["chars"] / base["chars_avg"])))
        print()

    print("=" * 74)
    print("%-14s %9s %11s %10s" % ("基线", "注入体积", "估算 tok", "轮次召回"))
    print("%-14s %7d 字 %11s %9.1f%%"
          % ("全量 68 个", int(base["chars_avg"]),
             _fmt_tok(base["chars_avg"]), 100.0))
    print("=" * 74)
    print()
    print("⚠ 召回率口径：**轮次召回** = 该轮用到的工具是否**全都**在注入集里。")
    print("   必须 100%% 才安全——漏一个，模型就只能干瞪眼。")
    print()
    return 0


def suggest(tools, turns, argv):
    """给出「关掉哪些最划算」的建议，并输出可直接粘贴进 config.json 的清单。

    按**体积 ÷ 使用次数**倒序排：体积大又没人用的排最前，省得最狠。
    ⚠ 只给建议，不自动改配置——有些工具是新的（大哥还没来得及用），
    该不该关只有人能判断。

    预设档位：
      精简   留高频（历史用过 ≥3 次）
      均衡   留用过的（≥1 次）
      全开   全部保留（默认，行为零变化）
    """
    used = collections.Counter()
    for _, _, ts in turns:
        used.update(ts)

    rows = []
    for t in tools:
        n = t["name"]
        u = used.get(n, 0)
        # 每字符价值：用得越多、体积越小 → 越该留
        rows.append({"name": n, "chars": t["chars"], "used": u,
                     "waste": t["chars"] if u == 0 else t["chars"] / u})
    tot = sum(r["chars"] for r in rows)

    print("=" * 74)
    print("工具保留建议（按「体积 ÷ 使用次数」倒序，越靠前越该关）")
    print("=" * 74)
    print("全量 %d 个工具 / %d 字符 ≈ %s tok" % (len(rows), tot, _fmt_tok(tot)))
    print()
    print("%-26s %7s %6s %10s" % ("工具", "体积", "用过", "每字符/次"))
    print("-" * 74)
    for r in sorted(rows, key=lambda x: (-x["waste"], -x["chars"]))[:34]:
        print("  %-24s %6d %5d 次 %9s"
              % (r["name"], r["chars"], r["used"],
                 ("∞（从没用过）" if r["used"] == 0
                  else "%.0f" % r["waste"])))
    print()

    presets = (("精简", 3), ("均衡", 1), ("全开", 0))
    print("=" * 74)
    print("预设档位（把对应清单写进 config.json 的 enabled_tools 即生效）")
    print("=" * 74)
    for label, min_used in presets:
        keep = [r["name"] for r in rows if r["used"] >= min_used] if min_used else [r["name"] for r in rows]
        drop = [r["name"] for r in rows if r["name"] not in set(keep)]
        keep_chars = sum(r["chars"] for r in rows if r["name"] in set(keep))
        print()
        print("[%s] 留 %d 个 / 关 %d 个 → 注入 %d 字符 ≈ %s tok，省 %.0f%%"
              % (label, len(keep), len(drop), keep_chars,
                 _fmt_tok(keep_chars), 100.0 * (1 - keep_chars / tot)))
        if label == "全开":
            print("  （enabled_tools 留空数组 [] 即代表全开，不用写清单）")
            continue
        lst = json.dumps(sorted(keep), ensure_ascii=False)
        print("  enabled_tools = %s" % (lst if len(lst) < 300 else lst[:300] + " …"))
    print()
    print("⚠ 关掉的工具模型就看不见了，功能等于不存在——但随时能开回来。")
    print("   建议先关『从没用过 + 体积大』的那批，用两天没影响再关第二批。")
    return 0


def _fmt_tok(chars):
    lo = chars / CHARS_PER_TOK_RANGE[1]
    hi = chars / CHARS_PER_TOK_RANGE[0]
    return "%d~%d" % (lo, hi)


def _fmt_range(chars):
    return _fmt_tok(chars)


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except Exception as _e:
        print("分析失败：%r" % (_e,))
        code = 1
    raise SystemExit(code)
