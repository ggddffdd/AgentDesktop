# -*- coding: utf-8 -*-
"""v4.111 路由判定：纯函数版 + 历史回放 A/B。

为什么要有这个模块
------------------
线上判定（`ui.ChatWindow._is_complex`）有两个结构性缺陷，都不是词表问题，
而是**判定用的输入范围不对**：

1. 它遍历整个 `messages` 列表——历史对话、assistant 回灌、tool 结果、
   tool_log 全算进去。于是：
   - 长度阈值 1500 在多轮下**恒真**（实测单轮就 2 万字符起）；
   - 关键词扫全文，「代码/设计/分析」这类高频词**几乎必然命中**
     （assistant 自己说过一次，后面每一轮都算命中）。
2. Agent 路径（ui.py `_start_agent`）把**未清洗**的 `session.messages`
   丢给判定（实测 34 万字符），而真正发给 API 的是 `_sanitize_msg_for_api`
   清洗 + `max_history` 截断后的 `hist`。**判定输入 ≠ 实发输入**。

本模块把判定抽成不依赖 Qt 的纯函数，用于两件事：
- `replay()`：拿真实历史会话跑新旧两套判定，直接给出差异清单，不用等一周埋点。
- 被 ui.py 的影子判定调用（旁路，只记录不改行为）。

设计铁律
--------
- **纯函数**：不 import Qt、不读全局状态，方便单测与离线回放。
- **v1 是线上行为的忠实复刻**，不是为了对比而弱化。改线上逻辑前必须先确认
  v1 与 `ui._is_complex` 行为一致。
- **v2 只改输入范围，不改词表**：这样 A/B 差异全部归因到"看什么"，干净。
"""

# 判定只看最近几条 user 消息。depth=1 最保守（只看当前这句），
# depth=2 能兼顾"继续/再详细点"这类依赖上一句的追问。
DEFAULT_DEPTH = 2


# ---------------------------------------------------------------- 文本抽取

def iter_text(msg):
    """抽出一条消息的纯文本。

    str 直接返回；list（多模态）只拼 text 分片，图像分片不算文本；
    其它类型（None / dict）一律空串。
    """
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                v = p.get("text")
                if isinstance(v, str):
                    parts.append(v)
        return "".join(parts)
    return ""


def last_user_texts(messages, depth=DEFAULT_DEPTH):
    """取最近 depth 条 user 消息的纯文本，按时间正序返回。

    跳过 assistant / tool / tool_log —— 它们的措辞不该影响"用户想干嘛"的判定。
    """
    out = []
    for m in reversed(messages or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        t = iter_text(m)
        if t:
            out.append(t)
        if len(out) >= depth:
            break
    out.reverse()
    return out


def has_image(messages, depth=1):
    """最近 depth 条 user 消息里是否带图。

    v1（线上）是扫全历史：只要会话里曾经发过一张图，之后**每一轮**都会被
    判成"有图"，于是整段会话永久锁死在视觉模型上——这是纯浪费。
    v2 只看最近这几条 user 消息。
    """
    n = 0
    for m in reversed(messages or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    return True
        n += 1
        if n >= depth:
            break
    return False


# ---------------------------------------------------------------- 新旧判定

def is_complex_v1(messages, hints, threshold=1500):
    """复刻线上 `ui._is_complex`：遍历整个 messages，命中关键词或总长超阈值。

    注意：连 tool_log（content 为 None）也算进遍历，只是贡献 0 长度；
    tool 结果（content 为 str）是实际的长度与关键词来源之一。
    """
    hints = hints or []
    total = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        total += len(c)
        for h in hints:
            if h in c:
                return True, "kw:" + h
    if total > threshold:
        return True, "len:%d>%d" % (total, threshold)
    return False, ""


def is_complex_v2(messages, hints, threshold=1500, depth=DEFAULT_DEPTH):
    """新判定：只看最近 depth 条 user 消息的纯文本。

    不改词表、不改阈值，只把"判定读的东西"换成"用户真正说的话"。
    返回值与 v1 同构 (bool, reason)，便于直接对比。
    """
    hints = hints or []
    scope = "\n".join(last_user_texts(messages, depth))
    if not scope:
        return False, ""
    for h in hints:
        if h in scope:
            return True, "kw:" + h
    if len(scope) > threshold:
        return True, "len:%d>%d" % (len(scope), threshold)
    return False, ""


def judge_scope(messages, depth=DEFAULT_DEPTH):
    """v2 判定实际看到的文本（供日志/回放展示，调试用）。"""
    return "\n".join(last_user_texts(messages, depth))


# ---------------------------------------------------------------- 配套补丁

# 回放暴露的事实：只改 _is_complex 的输入范围，会让一批**真的要调工具**的任务
# 掉到主模型（Agnes）——旧判定靠"扫全历史"误打误撞把它们蒙对了。
# 例如「打开知乎页面截张图」「帮我写一篇AI新闻文章并配张封面图」「生一张 1920x1080 封面」
# 都不在 _needs_tool_intent 词表里（表里有「截图」没有「截张图」，有「生成一张」
# 没有「生一张」）。所以**改输入范围必须配套补词表**，否则省钱变成"省了但活干不了"。
NEEDS_TOOL_EXTRA = (
    # 截图/取图
    "截图", "截张图", "截个图", "截屏", "截下来", "截一",
    # 发布/投稿
    "发布", "投稿", "发到", "发文章",
    # 配图/出图（「生一张」「出一张」是回放里的实际漏网写法）
    "配图", "配张图", "配张封面", "封面图", "张图", "做几张", "生成封面",
    "出张图", "生一张", "出一张", "做张", "做张封面",
    # 网页操作
    "打开网页", "打开页面", "打开网站", "打开知乎",
    # 写作落地
    "写文章", "写一篇", "写一段", "做个方案", "方案给我",
    # 查找/核对
    "找一下", "找找", "看看目录", "找不着", "找不到",
    # 表单填写/调整（浏览器自动化续接）
    "调一下", "调好", "填正文", "填内容", "填进去",
)

STICKY_WINDOW = 4


def needs_tool_extra(text):
    """候选补词命中判定——只补词，不改 `_needs_tool_intent` 已有的任何逻辑。"""
    t = (text or "").lower()
    return any(k in t for k in NEEDS_TOOL_EXTRA)


def has_recent_tool(messages, k=STICKY_WINDOW):
    """会话粘性：最近 k 条消息里出现过工具调用 → 任务还在进行中，能力需求不降级。

    为什么需要它：「继续」「出什么问题了」「右边的命令出框了」这类**续接型消息**
    用关键词永远猜不准——它们是延续上一轮的能力需求，本身不含任何动作词。
    与其猜，不如看事实：这个会话刚刚还在调工具吗？调了就说明活没干完。

    这条规则会自然衰减：任务一结束（assistant 不再调工具），粘性自动消失，
    下一轮闲聊就正常走主模型。不需要额外的"结束"判据。
    """
    if k <= 0:
        return False
    for m in (messages or [])[-k:]:
        if isinstance(m, dict) and m.get("role") == "tool":
            return True
    return False


# ---------------------------------------------------------------- 线上判定绑定

# 需要搬到哑对象上的类常量（词表）。方法名见下。
_CONST_NAMES = ("_MEDIA_OBJ_KW", "_MEDIA_VERB_KW")
_METHOD_NAMES = ("_needs_tool_intent", "_is_complex",
                 "_looks_like_learning_question", "_is_media_gen_request",
                 "_message_is_statement_only")


def bind_online():
    """把 `ui.ChatWindow` 上真实的判定实现绑到一个哑对象上，供回放直接调用。

    **为什么不复制词表**：`_needs_tool_intent` 有 60+ 关键词，且还依赖
    `_looks_like_learning_question` / `_is_media_gen_request` / `_message_is_statement_only`
    三条豁免与兜底链。抄一份必然漂移，漂了之后的 A/B 就是假数据。
    直接绑真方法（项目里 test_v495.py 有先例），保证回放用的就是线上那份。

    需要 PySide6 环境（系统 Python 3.12）。拿不到返回 None，回放自动降级为
    "只比 _is_complex"——结论会偏保守，但不会假。
    """
    try:
        import types
        import ui
        stub = types.SimpleNamespace()
        cls = ui.ChatWindow
        for n in _METHOD_NAMES:
            setattr(stub, n, getattr(cls, n).__get__(stub))
        for n in _CONST_NAMES:
            setattr(stub, n, getattr(cls, n))
        return stub
    except Exception:
        return None


def _img_anywhere(messages):
    """复刻线上 `_has_img`：只要 messages 里**任何一条**带 image_url 就 True。"""
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for p in c:
            if isinstance(p, dict) and p.get("type") == "image_url":
                return True
    return False


# ---------------------------------------------------------------- 历史回放

def _load_sessions(path=None):
    """读 sessions.json，返回 [[messages,...], ...]。异常一律返回空列表。"""
    import os
    import json
    if path is None:
        path = os.path.join(os.path.expanduser("~"), "Documents",
                            "小臭玩AI", "sessions.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    sess = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(sess, list):
        return []
    out = []
    for s in sess:
        if isinstance(s, dict) and isinstance(s.get("messages"), list):
            out.append((s.get("title") or s.get("sid") or "?", s["messages"]))
    return out


def _snippet(text, n=60):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n] + "…"


def turn_used_tools(msgs, i):
    """ground truth：第 i 条 user 消息发出后，助理**实际**有没有调工具。

    扫 i+1 起、到下一条 user 消息为止的所有消息，命中任一项即算"用了工具"：
      - role 是 tool / tool_log（工具回执回灌）
      - assistant 消息里带 tool_calls / function_call / tool
      - content 里出现本项目工具回执的标记

    ⚠ 为什么非它不可：旧链路升舱率 100%，"新判定更激进"这个反向指标恒等于 0，
    是个**空指标**，证明不了任何安全性。唯一有意义的安全性证据是——
    那些被新判定降级的轮次，历史上到底有没有真的调过工具。
    """
    for m in (msgs or [])[i + 1:]:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            break
        if m.get("role") in ("tool", "tool_log", "function"):
            return True
        if m.get("tool_calls") or m.get("function_call") or m.get("tool"):
            return True
        c = m.get("content")
        if isinstance(c, str) and ("tool_call_id" in c or "【工具" in c):
            return True
    return False


def replay(hints, threshold=1500, depth=DEFAULT_DEPTH, path=None, verbose=True,
           online=None, extra_kw=False, sticky=0):
    """拿真实历史会话回放 **完整升舱链路**，逐轮对比新旧判定。

    线上一次"是否升舱"由三件事决定（见 ui.py）：
        force_complex = 工具意图(_needs_tool_intent) or 有图(_has_img)
        升舱           = force_complex or _is_complex(messages, routing)
    其中 `_needs_tool_intent` 本身只看最后一条 user 消息，**没有输入范围问题**，
    所以本回放不动它；要修的是 `_is_complex`（扫全历史）和 `_has_img`（扫全历史）。

    参数 online：bind_online() 的返回值。给了就跑完整链路；
    没给（无 PySide6）就只比 `_is_complex`，结论偏保守但不假。

    返回 dict：
      turns     有效 user 轮次
      v1 / v2   旧 / 新链路下"最终会升舱"的次数
      saved     旧升新不升（＝可省下的付费调用）
      risky     旧不升新升（＝新判定更激进，必须人工确认）
      img_v1/img_v2 / ti  带图与工具意图命中次数
      details   逐条差异
    """
    if online is None:
        online = bind_online()
    res = {"turns": 0, "v1": 0, "v2": 0, "saved": 0, "risky": 0,
           "img_v1": 0, "img_v2": 0, "ti": 0, "ti_extra": 0,
           "sticky": 0, "sticky_only": 0, "leak": 0,
           "complex_v1": 0, "complex_v2": 0,
           "details": [], "sessions": 0, "full": online is not None}
    for title, msgs in _load_sessions(path):
        res["sessions"] += 1
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            if not iter_text(m):
                continue
            prefix = msgs[:i + 1]     # 模拟"这一轮发出时"模型看到的历史
            res["turns"] += 1

            a, ra = is_complex_v1(prefix, hints, threshold)
            b, rb = is_complex_v2(prefix, hints, threshold, depth)
            res["complex_v1"] += 1 if a else 0
            res["complex_v2"] += 1 if b else 0

            ia = _img_anywhere(prefix)          # 线上：扫全历史
            ib = has_image(prefix, depth)       # 新：只看最近几条 user
            res["img_v1"] += 1 if ia else 0
            res["img_v2"] += 1 if ib else 0

            ti0 = False
            if online is not None:
                try:
                    ti0 = bool(online._needs_tool_intent(prefix))
                except Exception:
                    ti0 = False
            res["ti"] += 1 if ti0 else 0

            # ⚠ 补词只喂新链路。旧链路必须保持"线上真实行为"当基准——
            # 否则省下的次数里会混进"词表补大了"的功劳，说不清到底是谁的改进。
            ti_new = ti0
            if extra_kw and not ti_new:
                ti_new = needs_tool_extra(judge_scope(prefix, 1))
            if ti_new and not ti0:
                res["ti_extra"] += 1

            st = bool(sticky) and has_recent_tool(prefix, sticky)
            if st:
                res["sticky"] += 1

            up_old = bool(ti0 or ia or a)
            up_new = bool(ti_new or ib or b or st)
            res["v1"] += 1 if up_old else 0
            res["v2"] += 1 if up_new else 0

            # 纯靠粘性托住：旧不升，新链路除了粘性以外没有任何命中
            purely_sticky = bool(up_new and not up_old
                                 and not (ti_new or ib or b))
            if purely_sticky:
                res["sticky_only"] += 1

            if up_old and not up_new:
                res["saved"] += 1
                used = turn_used_tools(msgs, i)
                res["leak"] += 1 if used else 0
                res["details"].append(
                    {"kind": "saved", "session": title, "v1_reason": ra,
                     "used_tools": used,
                     "scope": _snippet(judge_scope(prefix, depth))})
            elif up_new and not up_old:
                res["risky"] += 1
                res["details"].append(
                    {"kind": "risky", "session": title,
                     "v2_reason": "sticky" if purely_sticky else (rb or "img"),
                     "scope": _snippet(judge_scope(prefix, depth))})
    if verbose:
        print_report(res, depth=depth)
    return res


def print_report(res, depth=DEFAULT_DEPTH, limit=40):
    """把回放结果打成可读报告。"""
    t = res["turns"] or 1
    tag = "完整链路" if res.get("full") else "仅 _is_complex（未绑定线上实现）"
    print("=" * 64)
    print("路由升舱回放对比（depth=%d，%s）" % (depth, tag))
    print("=" * 64)
    print("会话 %d 个，有效 user 轮次 %d" % (res["sessions"], res["turns"]))
    print()
    print("  最终升舱：  旧链路 %4d 次  %3.0f%%" % (res["v1"], 100.0 * res["v1"] / t))
    print("              新链路 %4d 次  %3.0f%%" % (res["v2"], 100.0 * res["v2"] / t))
    print()
    print("  其中 _is_complex 单独命中：旧 %d 次 → 新 %d 次"
          % (res["complex_v1"], res["complex_v2"]))
    print("  工具意图 force（线上原逻辑，只看最后一句）：%d 次"
          % res["ti"], end="")
    if res.get("ti_extra"):
        print(" + 补词新增 %d 次（只喂新链路，不动旧基准）" % res["ti_extra"])
    else:
        print()
    print("  带图：旧（扫全历史）%d 次 → 新（只看最近 %d 条 user）%d 次"
          % (res["img_v1"], depth, res["img_v2"]))
    if res["img_v1"] != res["img_v2"]:
        print("        → 旧判定让 %d 个轮次被历史里的旧图锁死在视觉模型上"
              % (res["img_v1"] - res["img_v2"]))
    print()
    sv = res["saved"]
    leak = res.get("leak", 0)
    clean = sv - leak
    print("  ↓ 被降级（旧升、新不升）： %d 次  %3.0f%%" % (sv, 100.0 * sv / t))
    print("       ├─ 真省下：历史上没调工具   %3d 次  %3.0f%%"
          % (clean, 100.0 * clean / t))
    print("       └─ 🔴 漏检：历史上真调了工具 %3d 次  %3.0f%%"
          % (leak, 100.0 * leak / t))
    if sv:
        print("       漏检率 %.0f%%（漏检 / 被降级）"
              % (100.0 * leak / sv))
    print()
    print("  ↑ 反向风险（旧不升、新升）： %d 次  %3.0f%%"
          % (res["risky"], 100.0 * res["risky"] / t))
    if res["v1"] >= res["turns"] and res["turns"]:
        print("       ⚠ 旧链路升舱率 %d/%d = 100%%，此指标恒为 0 —— **空指标**，"
              % (res["v1"], res["turns"]))
        print("         当不了安全性证据。真正的安全性看上面的『漏检』行。")
    if res.get("sticky"):
        print()
        print("  会话粘性：命中 %d 次，其中纯靠粘性托住 %d 次（安全网的代价，"
              % (res["sticky"], res["sticky_only"]))
        print("            这些轮次本身没命中任何词表/长度/带图，全靠粘性保住能力）。")
    print()
    for kind, label in (("saved", "被降级清单"), ("risky", "新判定更激进")):
        rows = [d for d in res["details"] if d["kind"] == kind]
        if not rows:
            continue
        if kind == "saved":
            # 漏检优先展示——那才是要修的东西
            rows.sort(key=lambda d: not d.get("used_tools"))
        print("-" * 64)
        print("%s（%d 条，最多列 %d；🔴 = 历史上真调了工具）" % (label, len(rows), limit))
        for d in rows[:limit]:
            flag = "🔴" if d.get("used_tools") else "  "
            print("  %s [%s] %s" % (flag, d.get("v1_reason") or d.get("v2_reason"),
                                    d["scope"]))
        if len(rows) > limit:
            print("  ... 另有 %d 条" % (len(rows) - limit))
        print()


def replay_matrix(hints, threshold=1500, depth=DEFAULT_DEPTH, path=None,
                  online=None):
    """把四套方案放一起跑，输出一张决策表。

    四套方案的区别只在"新链路"怎么算：
      A 裸改 _is_complex 输入范围          —— 最省，但工具任务会退化
      B A + 补 _needs_tool_intent 词表      —— 补回放暴露的漏网动作
      C B + 会话粘性（最近 4 条有工具调用）  —— 治「继续」这类续接消息
      D B + 会话粘性（最近 6 条）
    旧链路（100% 扫全历史）作为分母基准。
    """
    if online is None:
        online = bind_online()
    plans = (("A 裸改输入范围", False, 0),
             ("B + 补词表", True, 0),
             ("C + 粘性 k4", True, 4),
             ("D + 粘性 k6", True, 6))
    print("=" * 72)
    print("方案决策表（depth=%d，分母 = 旧链路）" % depth)
    print("=" * 72)
    print("%-16s %7s %7s %7s %8s %7s"
          % ("方案", "升舱", "被降级", "真省下", "🔴漏检", "漏检率"))
    print("-" * 72)
    for label, extra, sticky in plans:
        r = replay(hints, threshold, depth=depth, path=path, verbose=False,
                   online=online, extra_kw=extra, sticky=sticky)
        t = r["turns"] or 1
        sv, lk = r["saved"], r.get("leak", 0)
        lr = (100.0 * lk / sv) if sv else 0.0
        print("%-16s %5d 次 %5d 次 %5d 次 %6d 次 %6.0f%%"
              % (label, r["v2"], sv, sv - lk, lk, lr))
    print("-" * 72)
    r0 = replay(hints, threshold, depth=depth, path=path, verbose=False,
                online=online)
    print("旧链路基准：%d / %d 轮升舱（%d 条会话）"
          % (r0["v1"], r0["turns"], r0["sessions"]))
    print()
    return True


def _main():
    import os
    import json
    import sys
    cfg_path = os.path.join(os.path.expanduser("~"), "Documents",
                            "小臭玩AI", "config.json")
    hints, threshold = [], 1500
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            r = (json.load(f).get("model_routing") or {})
        hints = r.get("complex_hint") or []
        threshold = r.get("length_threshold", 1500)
    except Exception:
        pass
    print("词表：%s" % " / ".join(hints))
    print("阈值：%d" % threshold)
    on = bind_online()
    print("线上实现绑定：%s" % ("成功（完整链路回放）" if on else "失败（降级，仅比 _is_complex）"))
    print()
    for dd in (1, 2):
        replay_matrix(hints, threshold, depth=dd, online=on)
        replay(hints, threshold, depth=dd, online=on,
               extra_kw=True, sticky=STICKY_WINDOW)
    return 0


if __name__ == "__main__":
    sys_exit = 0
    try:
        sys_exit = _main()
    except Exception as _e:
        print("回放失败：%r" % (_e,))
        sys_exit = 1
    raise SystemExit(sys_exit)
