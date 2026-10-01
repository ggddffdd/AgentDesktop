# -*- coding: utf-8 -*-
"""v4.196 批⑬：拒答阈值 —— 统一的不确定性决策表。

## 它解决什么

前八层 + 批⑨⑩⑪ 全部在做同一件事的两半：**有没有编**。但还有一半没人管：
**没编，但说得比证据允许的程度更满**。例如：

- 只有一份来源，却写「答案是 X」（单一弱来源只能做「初步判断」）；
- 两个来源互相打架，只挑顺手的那个下结论（冲突必须摆出来）；
- 问今天股价 / 最新版本 / 当前天气，一个工具都没调，却直接给出数字；
- 工具调用失败了，还把失败前的猜测当成结论说出来。

这些句子里没有任何一处「假」，但它们的**确定性水平超过了证据水平**，
用户读到的仍然是错的。这类问题 detective 主线抓不到 —— 因为你没法正则出
「这句话太自信了」。所以做一张**决策表**：证据水平决定允许说到什么程度。

## 四级灰度（只允许往保守方向偏）

| 等级        | 触发条件                                      | 允许的表述               |
|-------------|-----------------------------------------------|--------------------------|
| `answer`    | ≥2 条**独立成功**证据支撑，且无工具失败       | 「是 X」                 |
| `hedged`    | 只有 1 条成功证据（单一弱来源）                | 「初步判断…待核实」      |
| `conflict`  | 多条证据但关键要素互相矛盾                     | 「两处说法不一致：A / B」|
| `refuse`    | 零证据 / 用了失败调用当依据 / 需实时信息但没工具 | 「我没查到，不敢下结论」 |

## 设计约束

- **纯函数**：不碰磁盘网络，证据列表由调用方喂进来（批⑨ 的 `evidence.recent()`）。
- **宁漏勿错**（复用批⑩ 铁律③）：判不出就升级到更高一档的保守措辞，
  绝不因为判不出就放行到 answer。
- 只出**建议措辞**，不改写模型输出 —— 措辞提示由 UI 层展示。
"""

import re
import logging

log = logging.getLogger(__name__)

# ---------- 四级灰度 ----------
LEVEL_ANSWER = "answer"
LEVEL_HEDGED = "hedged"
LEVEL_CONFLICT = "conflict"
LEVEL_REFUSE = "refuse"

# 严格度排序（比较大小用）
_LEVEL_ORDER = {LEVEL_ANSWER: 0, LEVEL_HEDGED: 1, LEVEL_CONFLICT: 2, LEVEL_REFUSE: 3}

# 每级允许/要求的措辞（模型照抄即可，机器只看有没有 hedge 词）
_REQUIRED_TONE = {
    LEVEL_ANSWER: "可以下肯定结论（引用 ⟦EV#n⟧ 说明出处）",
    LEVEL_HEDGED: "只能用「初步判断 / 大概率 / 待核实」，不得写「确定是」",
    LEVEL_CONFLICT: "必须同时列出冲突的两处来源与各自说法，不得单方下结论",
    LEVEL_REFUSE: "应当直接说明没查到/无工具可查，不给结论、不补全",
}

# ---------- 强肯定句式（超过 hedged 就不许出现） ----------
_STRONG_RX = re.compile(
    r"(确定是|肯定是|一定是|必然是|事实就是|答案就是|完全可以放心|毫无疑问|"
    r"百分之百|百分百|可以肯定|就是\s*\d|板上钉钉|明摆着|不用怀疑|"
    r"^\s*答案是|结论是\s*[:：]?\s*[^\n，。]{2,})")

# ---------- 已带保留 Markers（有这些就不算越界） ----------
_HEDGE_RX = re.compile(
    r"(可能|大概|也许|或许|初步判断|初步看|大概率|倾向于|估计|推测|疑似|"
    r"待核实|待确认|尚未确认|不敢确定|不能确定|建议核实|仅供参考|看起来|"
    r"似乎是|似乎|从现有材料看|就目前所见|需要进一步|如果我读到|如有出入)")

# ---------- 需要实时信息的话题（无工具 = 必须拒答） ----------
_REALTIME_RX = re.compile(
    r"(今天|今日|现在|此刻|当前|目前|最新|最近一次|实时|刚刚|此刻的|"
    r"股价|股价是多少|天气|气温|汇率|币价|金价|实时价格|最新一版|最新版本|"
    r"最新的版本号|余额|净值|收盘|开盘|涨停|跌停|最新政策|新规)")

# 具备实时获取能力的工具
REALTIME_TOOLS = frozenset((
    "web_search", "web_fetch", "browser_open", "browser_read", "browser_click",
    "webhook_events", "sys_info", "run_command", "run_python", "search_memory",
))

# 这些词出现表示模型在**承认不知道**（豁免巡检）
_UNKNOWN_RX = re.compile(
    r"(我不知道|没查到|查不到|找不到|无法确认|不确定|不清楚|不敢说|"
    r"没有读到|没有找到|没有证据|缺乏证据|没有来源|没法验证|未能获取)")


def requires_realtime(text):
    """文本是否在询问「必须联网/读实时数据才能答」的信息。"""
    if not text:
        return False
    return bool(_REALTIME_RX.search(str(text)))


def used_realtime_tool(tools):
    """本轮是否真的调用过具备实时获取能力的工具。"""
    try:
        return any(str(t or "") in REALTIME_TOOLS for t in (tools or []))
    except Exception:
        return False


def _tokens_of(text):
    """抽取可比较的事实要素（数字/版本/百分比）—— 冲突判定用。"""
    try:
        import memory_gate
        return set(memory_gate.extract_tokens(text or ""))
    except Exception:
        return set(re.findall(r"\d+(?:\.\d+)?", str(text or "")))


_QTY_RX = re.compile(
    r"(?<![\w.])(v?\d+(?:\.\d+)+)(?![\w.])"                       # 版本号 v4.195.0
    r"|(?<![\w.])(\d+(?:\.\d+)?)\s*"
    r"(条|个|节|页|次|行|遍|段|步|秒|分钟|小时|天|周|月|年|周|倍|人|台|张|款|件|章|"
    r"MB|GB|KB|TB|px|万|亿|元|块|%)(?![\w.])")


def _quantity_map(text):
    """把「有量纲的数量」抽成 {量纲键: 值串} —— 同一个量纲出现不同值才算真冲突。

    早期版本拿**全部数字集合**做不相交判断，结果几乎恒为真：两份毫无关系的
    材料（一份说 477 节、一份说 2026-10-01 打包）也会被判成「来源冲突」，
    等于把「证据多」本身当成了异常。冲突必须是**同一件事上数字不一致**，
    所以要比的是同一个量纲下的值：都提到版本号却一个 v4.188 一个 v4.195，
    或都提到节数却一个 477 一个 88。没有共同量纲 = 没有可比性 = 不冲突。
    """
    out = {}
    try:
        for m in _QTY_RX.finditer(str(text or "")):
            if m.group(1):
                out.setdefault("version", m.group(1).lstrip("vV"))
            elif m.group(2) and m.group(3):
                k = m.group(3)
                if re.match(r"^\d{4}$", m.group(2) or ""):
                    continue  # 年份不是"数量"，不参与冲突
                out.setdefault(k, m.group(2))
    except Exception:
        pass
    return out


def _conflict_between(text_a, text_b):
    """两条证据是否在同一**量纲**上互斥（同一件事数字不一致）。

    没有共同量纲 → 不冲突（宁放过）：把正常的多来源判成冲突，
    会让模型每次都得写「两处说法不一致」，比漏判更烦人。
    """
    try:
        ma, mb = _quantity_map(text_a), _quantity_map(text_b)
        if not ma or not mb:
            return False
        common = set(ma) & set(mb)
        if not common:
            return False
        return any(str(ma[k]) != str(mb[k]) for k in common)
    except Exception:
        return False


def assess(query, evidence_rows=None, tools_used=None, cited_ids=None):
    """按证据水平判定「允许说到什么程度」。

    参数：
      query         —— 本轮用户问题（判是否需要实时信息）
      evidence_rows —— 本轮登记证据列表（每项含 id/ok/raw，可来自 evidence.recent()）
      tools_used    —— 本轮调用过的工具名列表
      cited_ids     —— 最终回复里 ⟦EV#n⟧ 引用到的编号集合

    返回 dict：
      {"level": 四级之一, "reason": str, "allowed_tone": str,
       "ok_rows": [...], "failed_used": bool}
    """
    out = {"level": LEVEL_ANSWER, "reason": "", "allowed_tone": _REQUIRED_TONE[LEVEL_ANSWER],
           "ok_rows": [], "failed_used": False}
    try:
        rows = [r for r in (evidence_rows or []) if isinstance(r, dict)]
        ok_rows = [r for r in rows if r.get("ok")]
        cited = set(int(x) for x in (cited_ids or []) if str(x).isdigit())
        cited_rows = [r for r in rows if r.get("id") in cited]
        cited_ok = [r for r in cited_rows if r.get("ok")]
        cited_bad = [r for r in cited_rows if not r.get("ok")]
        out["ok_rows"] = cited_ok or ok_rows

        # ① 用失败调用当支撑 → 直接拒答档
        if cited_bad:
            out["level"] = LEVEL_REFUSE
            out["failed_used"] = True
            out["reason"] = ("引用了失败的工具调用 ⟦EV#%s⟧ —— 失败调用不能当依据，"
                             "应如实说明没获取到" % cited_bad[0].get("id"))
            out["allowed_tone"] = _REQUIRED_TONE[LEVEL_REFUSE]
            return out

        # ② 需要实时信息却没调过任何实时工具 → 拒答档
        if requires_realtime(query) and not used_realtime_tool(tools_used):
            out["level"] = LEVEL_REFUSE
            out["reason"] = "问题需要实时/联网数据，但本轮没有调用任何查询工具"
            out["allowed_tone"] = _REQUIRED_TONE[LEVEL_REFUSE]
            return out

        # ③ 零证据（且确实给了结论句）→ 拒答档
        usable = cited_ok if cited else ok_rows
        if not usable:
            out["level"] = LEVEL_REFUSE
            out["reason"] = "本轮没有任何成功证据支撑"
            out["allowed_tone"] = _REQUIRED_TONE[LEVEL_REFUSE]
            return out

        # ④ 多来源之间关键要素互斥 → 冲突档
        if len(usable) >= 2:
            try:
                texts = [str(r.get("raw") or "")[:4000] for r in usable[:6]]
                for i in range(len(texts)):
                    for j in range(i + 1, len(texts)):
                        if _conflict_between(texts[i], texts[j]):
                            out["level"] = LEVEL_CONFLICT
                            out["reason"] = ("引用的证据 ⟦EV#%s⟧ 与 ⟦EV#%s⟧ 关键要素不一致"
                                             % (usable[i].get("id"), usable[j].get("id")))
                            out["allowed_tone"] = _REQUIRED_TONE[LEVEL_CONFLICT]
                            return out
            except Exception as e:
                log.warning("冲突判定异常: %s", e)

        # ⑤ 单一来源 → hedged（不得下肯定结论）
        if len(usable) == 1:
            out["level"] = LEVEL_HEDGED
            out["reason"] = "只有一条独立来源（⟦EV#%s⟧）" % usable[0].get("id")
            out["allowed_tone"] = _REQUIRED_TONE[LEVEL_HEDGED]
            return out

        # ⑥ ≥2 条独立成功证据 → 可以下结论
        out["level"] = LEVEL_ANSWER
        out["reason"] = "有 %d 条独立成功证据支撑" % len(usable)
        return out
    except Exception as e:
        log.warning("不确定性评估异常（降级为最保守）: %s", e)
        out["level"] = LEVEL_HEDGED
        out["reason"] = f"评估异常：{e}"
        out["allowed_tone"] = _REQUIRED_TONE[LEVEL_HEDGED]
        return out


def tone_allowed(sentence, level):
    """这句话的语气有没有越过它的证据等级。返回 (ok, 原因)。

    只在 evidence 等级为 answer 之外时才可能判越界；answer 一律放行。
    宁漏勿错：句子本身已经在承认不知道，或已经带了保留词，一律放行。
    """
    try:
        s = str(sentence or "")
        if not s.strip():
            return (True, "")
        if level == LEVEL_ANSWER:
            return (True, "")
        if _UNKNOWN_RX.search(s):
            return (True, "")
        if _HEDGE_RX.search(s):
            return (True, "")
        # conflict 档：既要有 hedge、也要摆出分歧；只摆分歧也算过关
        if level == LEVEL_CONFLICT and re.search(r"(不一致|冲突|矛盾|另有|另有一处|两处|"
                                                 r"说法不同|与.*相反)", s):
            return (True, "")
        m = _STRONG_RX.search(s)
        if m:
            tip = {LEVEL_HEDGED: "只有单一来源，不能写死",
                   LEVEL_CONFLICT: "来源互相冲突，得把两边都摆出来",
                   LEVEL_REFUSE: "没有可用证据，应当直接说查不到"}.get(level, "语气超出证据水平")
            return (False, f"「{m.group(0)[:12]}」——{tip}：{_REQUIRED_TONE.get(level, '')}")
        return (True, "")
    except Exception as e:
        log.warning("语气核查异常: %s", e)
        return (True, "")


def stricter(a, b):
    """返回两个等级中更保守的那个。"""
    return a if _LEVEL_ORDER.get(a, 0) >= _LEVEL_ORDER.get(b, 0) else b


def system_hint(level):
    """给系统提示/UI 用的一句话纪律提示。"""
    return _REQUIRED_TONE.get(level, "")
