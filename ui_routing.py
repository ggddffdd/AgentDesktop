# -*- coding: utf-8 -*-
"""对话路由的纯判定（v4.267.0 从 ChatWindow 外移的第一刀）。

只搬判定，不改口径：
  · 复杂度默认走 route_judge.is_complex_v2；config 里 route_complex_v1 为真时回退旧实现
  · 工具意图只看最后一条非 _internal 的用户消息，并先过 intent_guard
  · 「不要调工具」按拒绝短语和其后动作词的位置比较

ChatWindow 上的同名方法只转发到这里，route_judge.bind_online 仍绑类方法。
媒体组合判定 _is_media_gen_request 仍留在 ChatWindow（词表是类常量），
_needs_tool_intent 通过 host 回调它。
"""
import logging
import re

log = logging.getLogger("dsdesktop")

def _complexity_reason(cfg, messages, routing):
    """复杂度判定的具体归因（命中哪个关键词 / 长度超阈值），供旁路日志复盘。

    v4.169.0（审查 P1-4）：与 `_is_complex` **同源**切到 route_judge ——
    两边口径必须一致，否则会写出"判成复杂了、归因却说不出为什么"的自相矛盾日志。
    """
    if cfg.get("route_complex_v1"):
        return _complexity_reason_v1(messages, routing)
    try:
        import route_judge
        hints = routing.get("complex_hint") or []
        threshold = routing.get("length_threshold", 1500)
        ok, why = route_judge.is_complex_v2(messages, hints, threshold)
        return why or ("complex" if ok else "simple")
    except Exception:
        return _complexity_reason_v1(messages, routing)


def _complexity_reason_v1(messages, routing):
    """旧归因（v4.109 原实现）：遍历整个 messages。保留供回退开关使用。"""
    hints = routing.get("complex_hint") or []
    threshold = routing.get("length_threshold", 1500)
    total = 0
    for m in messages or []:
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        total += len(c)
        for h in hints:
            if h in c:
                return "kw:" + h
    if total > threshold:
        return "len:%d>%s" % (total, threshold)
    return "complex"


def _is_complex(cfg, messages, routing):
    """复杂任务判定（v4.169.0 审查 P1-4：改用 `route_judge.is_complex_v2`）。

    原实现遍历**整个 messages** —— 含 system prompt、历史 assistant 回复、
    tool 结果 —— 累计长度并扫 complex_hint 关键词。而**系统提示本身就含
    「代码 / 分析 / 报告 / 设计」这类词**，于是几乎每轮都判"复杂"，
    Agent 长期升舱到付费模型（钱与速度都受影响）。

    新判定只看**最近 2 条 user 消息的纯文本**（用户真正说的话），
    词表与阈值都没动，只把"判定读什么"换掉。项目里其实早就写好了
    `route_judge.is_complex_v2()`（它明确记录了 v1 的这个根因），只是一直没接上。

    回退开关：config.json 里 `"route_complex_v1": true` → 用回旧行为。
    """
    if cfg.get("route_complex_v1"):
        return _is_complex_v1(messages, routing)
    try:
        import route_judge
        hints = routing.get("complex_hint") or []
        threshold = routing.get("length_threshold", 1500)
        ok, _why = route_judge.is_complex_v2(messages, hints, threshold)
        return bool(ok)
    except Exception:
        # 判定模块不可用 → 退回旧实现，绝不因它把路由搞挂
        return _is_complex_v1(messages, routing)


def _is_complex_v1(messages, routing):
    """旧判定（线上原实现）：遍历整个 messages。保留供回退开关使用。"""
    hints = routing.get("complex_hint") or []
    threshold = routing.get("length_threshold", 1500)
    total = 0
    for m in messages or []:
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        total += len(c)
        if any(h in c for h in hints):
            return True
    return total > threshold


def _needs_tool_intent(messages, host):
    """v4.98 工具意图检测：用户明显要 Agent 用工具干活（改/写/编辑文件、跑 python、
    搜索、生图生视频、读取分析文件、调用工具/自动化等）。这类任务弱模型（Agnes）极易
    退化成"文字演工具"（伪造 [工具]/✅ 已保存/run_python(），直接强制走 complex_model
    并把 tool_choice 设为 required，从根上杜绝撒谎。命中最后一条用户原话即视为工具意图。"""
    KEYWORDS = (
        # 文件读写
        "写文件", "保存", "导出", "生成文件", "创建文件", "新建文件", "建个文件",
        "改文件", "编辑文件", "修改文件", "打开文件", "读取文件", "读文件", "写入",
        # v4.172.0 补缺口：文件操作类动词整类缺失（与 _ACTION_HINTS 对齐）——
        # 实测「帮我删掉昨天的临时文件」不命中本表 → 不强制 required →
        # 弱模型容易退化成"文字演工具"。
        "删除", "删掉", "移动文件", "重命名", "复制文件", "清理文件", "整理目录",
        # 代码执行
        "运行", "跑", "执行", "python", "代码", "脚本", "py 脚本",
        # 搜索（v4.172.0：补口语变体，"搜索"二字太书面，用户更常说"搜一下"）
        "搜索", "搜一下", "搜下", "搜搜", "查一下", "查下", "查查", "上网查",
        "fetch", "爬虫", "爬取",
        # 生图生视频
        "生图", "画图", "画一张", "画图片", "生成图片", "生成一张", "一张图片",
        "一张图", "作图", "生视频", "生成视频", "做视频", "口播视频",
        "数字人视频", "数字人口播", "剪辑", "配音", "旁白", "做成视频", "做口播",
        "重新生成", "再画", "换一张", "重画",
        # 工具/自动化/技能（v4.172.0：补技能安装类）
        "调用工具", "用工具", "自动化", "定时", "提醒",
        "装技能", "装个技能", "装一下技能", "安装技能", "导入技能",
        # 数据分析
        "分析", "统计", "报表", "数据处理", "excel", "表格", "csv",
        # 自省/能力盘点（命中即强制走 DeepSeek 并 required，确保真调 sys_info 工具，
        # 避免弱模型退化成吐文本、被循环误判为『没调工具』）
        "你的能力", "你会什么", "你能做什么", "你会哪些", "能力清单", "功能清单",
        "有哪些功能", "有哪些工具", "会干啥", "能干什么", "能调用什么",
        "介绍一下你自己", "介绍你自己", "自我介绍", "列个清单", "清单给我",
        "你有什么功能", "你能调用",
    )
    last_user = ""
    for msg in reversed(messages or []):
        # v4.186.0（P1-4 修）：跳过框架注入的内部消息（nudge / 强制指令）。
        # 原实现取最后一条 user 消息不看 `_internal`，而 nudge 文本含
        # 「写文件/搜索」等词 → 每轮 nudge 重试都被判工具意图 →
        # 付费模型升舱 + required，一次空转一轮付费。
        if msg.get("role") == "user" and not msg.get("_internal"):
            c = msg.get("content", "")
            if isinstance(c, str):
                last_user = c
            break
    if not last_user:
        return False
    # v4.172.0（P0-2 补漏）：**这条判据也必须与 intent_guard 同源。**
    #
    # 实测事故（大哥复现）：用户问「为什么会自动搜索？」
    #   · intent_guard 判得对：non_action:discuss_tool ✓
    #   · 层1 也修好了：普通模式不会自动进 Agent ✓
    #   · 但**用户开着 Agent 模式**时 `use_agent = (agent_mode or ...)` 短路，
    #     照样进 Agent → `_agent_call` 里用本函数判"工具意图"
    #   · 本函数的 KEYWORDS 里有裸词**「搜索」** → 命中 → `_tool_intent=True`
    #   · → 升舱付费模型 + 意图置 `tool_choice="required"`
    #   · 而层3 的 `_guard_block` 又要设 `tool_choice="none"`
    #   · 模型被夹在「必须调工具」和「禁止调工具」之间 →
    #     **用文字演了一个工具调用**（输出 `<tool_call>list_automation</tool_call>`，
    #     并没有真的 tool_calls），UI 把它渲染成卡片，看起来就像真调了工具。
    #
    # 所以这里是第 4 处判据，必须前置短路 —— 否则「禁止」与「必须」会互相打架。
    try:
        import intent_guard as _ig
        if _ig.is_non_action_message(last_user):
            log.info("工具意图判定：判为非指令消息（%s），不强制 required",
                     _ig.why_blocked(last_user))
            return False
    except Exception:
        pass
    # v4.102 fix11：咨询问句豁免——「配音怎么学 / 剪辑怎么入门 / 数字人是什么」这类
    # 纯咨询不该被当成工具意图升舱，否则误走 DeepSeek + required 空转。
    # 命中这些非工具意图问句特征直接返回 False（交由普通对话直答）。
    if host._looks_like_learning_question(last_user):
        return False
    # v4.155 fix3：纯评价/夸赞/感慨豁免——「这能力颠覆认知」「效果真好」之类不应被当成
    # 工具意图升舱或强制 required（否则误走 DeepSeek 空转，甚至被系统提示助推去调生成工具）。
    if host._looks_like_praise(last_user):
        return False
    if any(kw in last_user.lower() for kw in KEYWORDS):
        return True
    # v4.102 fix11：媒体生成组合判定兜底——"生成口播视频 / 做个数字人"这类
    # 分开写的表述连写词表会漏判，用「对象词×动作词」同现识别。
    if host._is_media_gen_request(last_user):
        return True
    # v4.107：导演指令已从主路由摘除（统一在导演台底部对话条处理），不再升舱。
    return False


def _user_refuses_tools(messages):
    """v4.100：检测用户是否明确要求『不要调用工具/纯聊天』。
    若用户说过此类约束，且其后没有下达新的明确工具指令，则本轮禁止调工具
    （由调用方设 tool_choice=none），尊重用户约束，避免闲聊被 remember 等
    工具自发调用打断。
    判断依据：扫描全部消息，取最后一次『拒绝调工具』与最后一次『明确工具动作』
    的位置——若拒绝在动作之后（或从未有动作），视为当前仍应尊重『不调工具』。"""
    # v4.188 P3：_internal 消息是程序注入的（强制工具指令/首步提示，见 10081/
    # 10112 构造点），不是用户说的——若不过滤，注入文本里的「生成/执行」等
    # 动作词会顶掉用户的『不调工具』约束（9743 行 last_user 提取已有同款过滤）。
    REFUSE_KW = (
        "不要调用工具", "不要使用工具", "不要用工具", "别调用工具", "别用工具", "不用工具",
        "不要调工具", "别调工具", "纯聊天", "只是聊", "只是聊天", "光聊天",
        "不要动工具", "先别用工具", "不要开工具", "不用开工具", "别开工具",
    )
    ACTION_KW = (
        "搜", "写", "生成", "运行", "执行", "python", "创建", "导出", "分析",
        "读文件", "打开文件", "读一下", "做图", "生图", "生视频", "截图", "下载",
        "安装", "提醒", "调用工具", "用工具", "查一下", "上网查", "爬", "整理",
        "发", "监控", "剪辑", "配音", "做视频", "画图",
    )
    last_refuse = -1
    last_action = -1
    for i, m in enumerate(messages or []):
        if m.get("role") != "user" or m.get("_internal"):
            continue
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        if any(k in c for k in REFUSE_KW):
            last_refuse = i
            # v4.188 P3+（探针暴露的既有 bug）：拒绝句里的「用工具/调用工具」
            # 是**被否定的动作**，不得同时记成工具指令——否则「不要用工具」
            # 这句最自然的拒绝会因同句命中 ACTION_KW 而自我抵消
            #（last_refuse == last_action → 拒绝永远不生效）。
            # 做法：抹掉拒绝短语后再查动作词；「不要用工具，帮我生成个视频」
            # 这类**同句带新动作**的仍会让动作胜出（语义正确）。
            c = re.sub("|".join(re.escape(k) for k in REFUSE_KW), "■", c)
        if any(k in c for k in ACTION_KW):
            last_action = i
    return last_refuse >= 0 and last_refuse > last_action


def _looks_like_learning_question(text):
    """v4.102 fix11：判断是否为「学习/科普/了解的纯咨询」问句。
    『配音怎么学』『剪辑怎么入门』『数字人是什么』『AI视频怎么做的』这类句子里
    虽含「配音/剪辑/视频」等词，但用户是在**询问知识**而非**让我执行制作**，
    不应视为工具意图（否则误升舱 DeepSeek + 强制 required 空转）。
    命中返回 True → 上层不做工具意图判定。

    v4.172.0：实现**整体迁到 `intent_guard.is_learning_question`**，
    本方法保留为薄包装（与 `_looks_like_praise` 同一处理）。
    原因：这里原本是一份**独立词表**，而 `intent_guard._LEARNING_KW` 才是
    UI / Agent / 模型调用三层共用的唯一判据 —— 两份必然漂移
    （v4.169.0 往共享判据里补了「怎么装 / 如何安装 / 怎么配置」这批词，
    本函数一份都没同步），于是同一句话在不同层得出不同结论。
    """
    try:
        import intent_guard as _ig
        return _ig.is_learning_question(text)
    except Exception:
        pass
    # 兜底：intent_guard 不可用时退回最小判据（绝不因此放行误触）
    t = (text or "").lower()
    return any(k in t for k in ("怎么学", "怎么入门", "如何学", "如何入门",
                                "怎么开始", "是什么", "什么意思", "啥意思",
                                "了解一下", "了解下", "介绍下", "介绍一下",
                                "怎么弄的", "怎么做出来", "原理", "教程"))


def _looks_like_praise(text):
    """v4.155 fix3：判断是否为「纯评价 / 夸赞 / 感慨」而非动作指令。
    『这能力颠覆认知』『效果真好』『太强了』『生成得不错』这类句子虽可能含
    『生成 / 视频』等词，但用户是在评价而非让我执行，不应触发生成类工具路由。
    命中评价特征词、且不含明确祈使动词 → 视为纯评价，返回 True。

    v4.168.0：实现**整体迁到 intent_guard.is_praise** —— 这是 UI / Agent /
    模型调用三层共用的唯一判据。本方法保留为薄包装（外部调用点不动），
    避免再出现「每层各修一点、规则各自漂移」的老问题。
    """
    try:
        import intent_guard as _ig
        return _ig.is_praise(text)
    except Exception:
        pass
    # 兜底：intent_guard 不可用时退回最小判据（绝不因此放行误触）
    t = (text or "").lower()
    return any(k in t for k in ("颠覆认知", "真好用", "太强了", "真强", "效果真",
                                "好厉害", "绝了", "厉害", "惊艳", "真不错", "做得好"))

