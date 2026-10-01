# -*- coding: utf-8 -*-
"""v4.196 批⑫：记忆准入关 —— 进长期记忆之前，先过机器验证。

## 为什么需要这一层

长期记忆是这个项目里**唯一会跨会话累积污染**的存储：一次错误的提炼会被
后续每一次对话注入系统提示，当成「用户说过的事实」反复引用，而且**越用越真**
（记忆越多、召回命中越高、模型越倾向相信它）。

原先 `_auto_remember` 的准入条件是唯一一条：**本轮有没有调过工具**。
它的潜台词是「只要干过活，提炼出来的东西就可以入库」，于是：

- 模型推断的结论（「用户应该是想做 X」）与用户原话同样入库；
- 工具失败时模型脑补的结果也被当成事实留下；
- 旧记忆里的数字被新的冲突数字直接覆盖（topic 合并 = 无条件覆盖）。

三者都不需要模型撒谎，只需要模型**不确定**就够了 —— 这跟幻觉是同一类根：
把「猜」和「知道」写进同一份介质。

## 准入规则（fail-closed）

| 来源        | 放行条件                                             | 否则     |
|-------------|------------------------------------------------------|----------|
| `user`      | 用户明确陈述（对话里用户亲口说的）                   | —        |
| `tool`      | 必须绑 ⟦EV#n⟧ 编号，且该证据存在、调用成功、          | reject   |
|             | 且事实里的确定性 token（数字/版本/路径/标识符）       |          |
|             | **逐个**在该证据原文里出现                           |          |
| `inference` | 模型推断 —— 不进长期记忆，落**待确认区**             | pending  |
| `ephemeral` | 临时任务态 —— 可入库但必须带过期时间                 | pending  |
| 未知/缺失   | 一律按 inference 处理（最保守)                       | pending  |

补充三条：
- confidence 低于阈值（默认 0.6）→ pending（拿不准不入库）
- 抽不出任何可核 token 的 tool 来源 → pending（无从验证不等于通过）
- 与已有同主题记忆**数字/版本冲突** → pending，绝不静默覆盖旧条目

## 设计约束

- **纯函数式**：不碰磁盘、不发网络，全部依赖注入（evidence_mod / old_text），
  探针可以在隔离环境里直接跑。
- **绝不阻断主流程**：调用方 try 包裹，本模块内部任何异常都降级为 pending，
  绝不 raise 到 `_auto_remember` 外层。
- **宁可待确认，不可误入库**：三分不肯定的事件往保守方向判。
"""

import re
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# ---------- 来源四类 ----------
SOURCE_USER = "user"
SOURCE_TOOL = "tool"
SOURCE_INFERENCE = "inference"
SOURCE_EPHEMERAL = "ephemeral"

# 来源别名（模型常常输出中文/大小写变体，认不全就等于整批 fallback 到 inference）
_SOURCE_ALIAS = {
    "user": SOURCE_USER, "用户": SOURCE_USER, "用户陈述": SOURCE_USER,
    "user_stated": SOURCE_USER, "stated": SOURCE_USER, "明确": SOURCE_USER,
    "tool": SOURCE_TOOL, "工具": SOURCE_TOOL, "工具结果": SOURCE_TOOL,
    "tool_result": SOURCE_TOOL, "evidence": SOURCE_TOOL, "证据": SOURCE_TOOL,
    "inference": SOURCE_INFERENCE, "推断": SOURCE_INFERENCE, "推测": SOURCE_INFERENCE,
    "猜测": SOURCE_INFERENCE, "model": SOURCE_INFERENCE, "模型推断": SOURCE_INFERENCE,
    "ephemeral": SOURCE_EPHEMERAL, "临时": SOURCE_EPHEMERAL, "临时状态": SOURCE_EPHEMERAL,
    "task_state": SOURCE_EPHEMERAL, "会话": SOURCE_EPHEMERAL,
}

MIN_CONFIDENCE = 0.6          # 低于此置信度只能进待确认区
DEFAULT_EPHEMERAL_DAYS = 7    # 临时记忆默认寿命

# 决策取值
ADMIT = "admit"
PENDING = "pending"
REJECT = "reject"

# ---------- 可核 token（逐字可验证的那部分事实） ----------
_TOKEN_PATTERNS = (
    ("version", re.compile(r"(?<![\w.])v?\d+\.\d+(?:\.\d+)*(?![\w.])")),
    ("percent", re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*%")),
    ("path", re.compile(r"(?:[A-Za-z]:[\\/][^\s，。；）】\"]+|~?/[^\s，。；）】\"]{3,})")),
    ("unit", re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:条|个|节|页|次|行|秒|分钟|小时|天|周|月|年|"
                        r"MB|GB|KB|TB|px|万|亿|元|块|人|台|张|款|版|次)")),
    ("year", re.compile(r"(?<![\w.])20\d{2}(?:年|[-/]\d{1,2})?(?![\w.])")),
    ("ident", re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_\-]{4,}(?![A-Za-z0-9])")),
    ("num", re.compile(r"(?<![\w.])\d{2,}(?![\w.])")),
)

# 这些"标识符"其实是普通英文词，拿去逐字回验必然命中不了，会制造假拒绝
_IDENT_STOPWORDS = frozenset("""
also from that this with your have been will would could should there their which
about into than then them they when where while after before other some such only
just more most less very much many over under again further once here both each few
http https json true false none null true false using used uses like want need make
made take takes give gives does done doing says said text file path name type size
""".split())


def normalize_source(raw):
    """把模型写的各种来源写法归一到四类之一；认不出返回 None（按最保守处理）。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s in _SOURCE_ALIAS:
        return _SOURCE_ALIAS[s]
    for k, v in _SOURCE_ALIAS.items():
        if k in s:
            return v
    return None


def _to_float(v, default=None):
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except Exception:
        return default


def extract_tokens(text):
    """抽可逐字回验的确定性 token 列表（去重保序）。

    只抽「错了就是错了、不存在 paraphrase 空间」的那些 —— 数字、版本号、路径、
    百分比、标识符。这正是 tool 来源能否入境的检验对象。
    """
    if not text:
        return []
    out, seen = [], set()
    try:
        s = str(text)
        for kind, rx in _TOKEN_PATTERNS:
            for m in rx.finditer(s):
                tok = (m.group(0) or "").strip()
                if not tok or tok in seen:
                    continue
                if kind == "ident" and tok.lower() in _IDENT_STOPWORDS:
                    continue
                # 单位/百分比里可能带空格，回验时按原样比即可
                seen.add(tok)
                out.append(tok)
    except Exception as e:
        log.warning("token 抽取失败: %s", e)
    return out


def parse_expires(raw, now=None):
    """把过期写法换算成 ISO 日期串。认 '7天'/'30天'/'7d'/'一周'/'永久'/None/数字。

    返回 (expires_at_iso_or_None, days_or_None)。认不出来返回 (None, None)，
    由调用方按 pending 处理（临时记忆没有过期时间 = 永久污染，不允许）。
    """
    if raw is None:
        return (None, DEFAULT_EPHEMERAL_DAYS)  # 临时态没写就按默认 7 天，不拒绝
    s = str(raw).strip().lower()
    if not s:
        return (None, DEFAULT_EPHEMERAL_DAYS)
    if s in ("永久", "forever", "permanent", "长期", "none", "null"):
        return (None, None)
    days = None
    m = re.search(r"(\d+)\s*(天|日|d|days?)", s)
    if m:
        days = int(m.group(1))
    elif re.search(r"一周|一个星期", s):
        days = 7
    elif re.search(r"两周|半个月", s):
        days = 14
    elif re.search(r"一个月|1个月", s):
        days = 30
    elif re.fullmatch(r"\d+", s):
        days = int(s)
    if days is None or days <= 0:
        return (None, None)
    try:
        base = now or datetime.now()
        at = (base + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return (None, days)
    return (at, days)


def has_conflict(fact, old_text):
    """新旧记忆数字/版本级冲突检测（保守：只有双方都能抽出可核 token 才判）。

    同主题记忆出现互斥的数字（旧「房贷剩 5 万」vs 新「房贷剩 0 万」），
    静默覆盖会把旧事实抹掉、还可能留下自相矛盾的一份。这里只做「挡」，
    由人在待确认区定夺 —— 机器无权决定哪个是真的。
    """
    try:
        new_toks = set(extract_tokens(fact))
        old_toks = set(extract_tokens(old_text or ""))
        if not new_toks or not old_toks:
            return False
        # 只看数字类（纯英文标识符 old 里常是别处的单词，不参与冲突判断）
        def _num_only(ts):
            return set(t for t in ts if re.search(r"\d", t))
        new_nums, old_nums = _num_only(new_toks), _num_only(old_toks)
        if not new_nums or not old_nums:
            return False
        return new_nums.isdisjoint(old_nums)
    except Exception:
        return False


def _verify_tokens(tokens, eid, start_line=None, end_line=None, evidence_mod=None):
    """逐个 token 回验；返回 (ok, miss_list)。"""
    if evidence_mod is None:
        try:
            import evidence as evidence_mod  # noqa: F811
        except Exception:
            return (False, list(tokens))
    miss = []
    try:
        for t in tokens:
            if not evidence_mod.supports(eid, t, start_line=start_line, end_line=end_line):
                miss.append(t)
    except Exception as e:
        log.warning("证据回验异常: %s", e)
        return (False, list(tokens))
    return (not miss, miss)


def admit(item, evidence_mod=None, old_text="", now=None):
    """记忆准入判定。返回 dict（绝不抛异常）。

    item 字段：content / topic / category / source / ev / evidence / confidence /
               expires / start_line / end_line

    返回: {"decision": ADMIT|PENDING|REJECT, "reason": str, "fact":..., ...}
    """
    try:
        it = item if isinstance(item, dict) else {"content": str(item or "")}
        fact = str(it.get("content") or "").strip()
        out = {
            "decision": PENDING, "reason": "", "fact": fact,
            "topic": it.get("topic"), "category": it.get("category"),
            "source": None, "confidence": None, "expires_at": None,
            "expires_days": None, "verified": False, "evidence_id": None,
        }
        if not fact:
            out["reason"] = "内容为空"
            return out

        src = normalize_source(it.get("source") or it.get("origin") or it.get("kind"))
        conf = _to_float(it.get("confidence") or it.get("conf"))
        out["source"] = src
        out["confidence"] = conf

        # ① 来源缺失 / 认不出 → 一律按推断处理（fail-closed）
        if src is None:
            out["reason"] = ("未声明来源，按「模型推断」处理——推断不进长期记忆，"
                             "已放入待确认区")
            return out

        # ② 置信度闸门：所有来源一律适用（连declared user source 也不例外 ——
        #    模型自我感觉良好时常常把 user 标得很高，但 0.6 都不到的事实
        #    说明它自己也不确定）
        if conf is not None and conf < MIN_CONFIDENCE:
            out["reason"] = f"置信度 {conf:.2f} 低于阈值 {MIN_CONFIDENCE}，放入待确认区"
            return out

        # ③ 工具来源：必须绑证据编号，且证据原文逐个 token 都对得上
        if src == SOURCE_TOOL:
            eid = it.get("ev") or it.get("evidence") or it.get("evidence_id")
            try:
                eid_i = int(str(eid).replace("EV#", "").strip()) if eid is not None else None
            except Exception:
                eid_i = None
            if eid_i is None:
                out["decision"] = REJECT
                out["reason"] = "来源标为工具结果但未绑定 ⟦EV#n⟧ 编号，无法回验，已拒绝入库"
                return out
            out["evidence_id"] = eid_i
            mod = evidence_mod
            if mod is None:
                try:
                    import evidence as mod  # noqa: F811
                except Exception:
                    mod = None
            rec = None
            if mod is not None:
                try:
                    rec = mod.get(eid_i)
                except Exception:
                    rec = None
            if not rec:
                out["decision"] = REJECT
                out["reason"] = f"引用的证据 ⟦EV#{eid_i}⟧ 本轮不存在，已拒绝入库"
                return out
            if not rec.get("ok"):
                out["decision"] = REJECT
                out["reason"] = (f"引用的证据 ⟦EV#{eid_i}⟧ 是**失败调用**，"
                                 f"据其得出的结论不可入库")
                return out
            toks = extract_tokens(fact)
            if not toks:
                out["reason"] = ("事实中抽不出可核 token（无数字/版本/路径/标识符），"
                                 "无从回验，放入待确认区")
                return out
            ok, miss = _verify_tokens(
                toks, eid_i,
                start_line=it.get("start_line"), end_line=it.get("end_line"),
                evidence_mod=mod)
            if not ok:
                out["decision"] = REJECT
                out["reason"] = (f"证据 ⟦EV#{eid_i}⟧ 原文里查不到要素 "
                                 f"{'、'.join(str(x) for x in miss[:3])}，判定不支持，已拒绝入库")
                return out
            out["verified"] = True

        # ④ 用户明确陈述 —— 直接放行（用户说自己事，机器无权质疑）
        elif src == SOURCE_USER:
            out["verified"] = True

        # ⑤ 临时任务态 —— 可入库但必须带过期时间
        elif src == SOURCE_EPHEMERAL:
            at, days = parse_expires(it.get("expires") or it.get("expires_at"), now=now)
            if days is None and at is None:
                out["reason"] = "临时状态记忆未给出有效过期时间，放入待确认区"
                return out
            out["expires_at"] = at
            out["expires_days"] = days
            out["verified"] = True

        # ⑥ 模型推断 —— 不进长期记忆
        else:
            out["reason"] = "来源为模型推断，推断不进长期记忆，已放入待确认区"
            return out

        # ⑦ 冲突：与已有同主题记忆数字/版本互斥 → 待确认，绝不静默覆盖
        if old_text and has_conflict(fact, old_text):
            out["decision"] = PENDING
            out["reason"] = ("与已有同主题记忆的数字/版本冲突（旧：%s），"
                             "需要你定夺后再入库，未覆盖旧条目"
                             % str(old_text)[:60].replace("\n", " "))
            return out

        out["decision"] = ADMIT
        out["reason"] = {
            SOURCE_USER: "用户明确陈述，直接入库",
            SOURCE_TOOL: "已绑定证据且要素逐个回验通过",
            SOURCE_EPHEMERAL: "临时状态记忆，%s 天后过期" % (out["expires_days"] or "指定"),
        }.get(src, "通过准入校验")
        return out
    except Exception as e:
        log.warning("记忆准入判定异常（降级为待确认）: %s", e)
        return {"decision": PENDING, "reason": f"准入判定异常：{e}",
                "fact": (item or {}).get("content") if isinstance(item, dict) else str(item),
                "topic": None, "category": None, "source": None,
                "confidence": None, "expires_at": None, "expires_days": None,
                "verified": False, "evidence_id": None}


def summarize(results):
    """把一批判定结果压成一句状态文案（给状态栏用）。"""
    n_admit = sum(1 for r in results if r.get("decision") == ADMIT)
    n_pend = sum(1 for r in results if r.get("decision") == PENDING)
    n_rej = sum(1 for r in results if r.get("decision") == REJECT)
    bits = []
    if n_admit:
        bits.append(f"{n_admit} 条入库")
    if n_pend:
        bits.append(f"{n_pend} 条待确认")
    if n_rej:
        bits.append(f"{n_rej} 条无据驳回")
    return ("记忆准入关：" + "，".join(bits)) if bits else "记忆准入关：本次无新条目"
