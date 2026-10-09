# -*- coding: utf-8 -*-
"""intent.py —— v4.225 统一意图对象（审查报告 P3「路由规则重复」）

为什么要有它
------------
「这句话到底想干什么、该调哪个工具」这件事，在 v4.225 之前散在**五处**各自维护：

  1. `agent_text._route_force_tool`  —— 强制工具路由（step1 tool_choice）
  2. `agent_text._detect_action_intent` —— 「要不要执行动作」（text_mode vs agent_mode）
  3. `agent_text._content_creation_only` —— 「纯文本创作，别逼它调工具」
  4. `intent_guard` 的 `_NEG_PHRASES` / `_TOOL_DOMAIN_KW` / `_STATUS_KW` 等散词表
  5. `agent.py` 系统提示里**手写**的路由句（「跑代码用 run_python、搜索用 web_search、
     生图用 image_gen」）—— 加新工具要记得手改这句，忘了就静默失配。

本模块把这些收成**一个对象 + 一张注册表**：

  * `Intent` —— 统一意图值对象，字段固定、可序列化、可打日志、可判等。
  * `ROUTE_REGISTRY` —— 「工具 → 关键词 + 描述」单一真源；系统提示的路由段
    **从它自动生成**（`_route_hint_text()`），加工具只需登记一处。
  * `classify(text, prev_text=None)` —— **唯一**对外分类入口。内部按
    agent_text 已有判据算出结论后，装进 Intent 返回。

关键设计纪律（不变量）
--------------------
1. **不重写已有判据、不改行为**。`_route_force_tool` / `_detect_action_intent` /
   `_content_creation_only` 的内部逻辑一行不动（它们各有几十项判据在守着，
   改=重排行为=事故）。本模块只做「**壳**」：调用它们 → 把结果装进 Intent →
   补上 `target` / `requested_tools` / `confidence` 这些**以前没人有**的字段。
2. **只加不减**。以前判据返回什么，现在 Intent 就反映什么；新增字段一律
   「查得到才算，查不到留空」，绝不用推测值去驱动新行为。
3. **fail-open**。分类本身抛异常 → 返回 `Intent(kind="unknown")`，
   绝不因「分类失败」阻断对话。
4. **注册表只读**。`ROUTE_REGISTRY` 是模块级常量，禁止运行时改写
   （工具热插拔走 tools 注册表，不走这里）。

字段语义
--------
    kind             str   意图类别，见 KIND_* 常量
    confidence       float 0~1，**启发式可信度**，不是概率（无校准集，别当概率用）
    explicit         bool  用户是否**显式点名**了工具/对象（"用 Agnes 生图"=True）
    target           str   命中的具体产物对象（"视频"/"图片"/...），无则 ""
    requested_tools  tuple 候选工具名（按注册表关键词命中顺序，去重）
    force_tool       str|None  step1 该强制的工具（None = 不强制）
    needs_action     bool  是否算「需要执行动作」（决定 agent_mode）
    text_only        bool  是否是「纯文本创作」（不该逼它调工具）
    reason           str   人可读依据（打日志用，便于事后归因）

`confidence` 的算法（刻意保守，**只反映证据强度**、不影响任何既有决策）：
    base 0.30（仅分类出类别）
  + 0.25 命中显式工具名/关键词
  + 0.20 命中产物对象词
  + 0.15 未被否定/引用/状态类语境否决（语境干净）
  + 0.10 存在 force_tool（点过名且没被一票否决）
上限 1.0。**任何下游决策都不得只凭 confidence 做阈值判断** —— 它只用于日志与展示。
"""
import logging

log = logging.getLogger("dsdesktop")

VERSION = "v4.242.0"

# ============================================================
# 意图类别
# ============================================================
KIND_ACTION = "action"          # 要执行动作（调工具/改文件/跑代码）
KIND_CONTENT = "content"        # 纯文本创作（写文案/总结，不用调工具）
KIND_QUESTION = "question"      # 提问/咨询（要答案，不一定要动手）
KIND_DISCUSS = "discuss"        # 谈论/复盘某事（明确不该动手）
KIND_STATUS = "status"          # 状态追问（进度如何/好了吗）
KIND_NEGATED = "negated"        # 被否定一票否决（别生成/取消/停）
KIND_REFERENCE = "reference"    # 引用/质疑语境（在谈某生成动作，非下指令）
KIND_UNKNOWN = "unknown"

KIND_ALL = (KIND_ACTION, KIND_CONTENT, KIND_QUESTION, KIND_DISCUSS,
            KIND_STATUS, KIND_NEGATED, KIND_REFERENCE, KIND_UNKNOWN)

# ============================================================
# ROUTE_REGISTRY —— 「工具 → 关键词」单一真源
# ============================================================
# 说明：
#   * keywords 用于（a）requested_tools 候选、（b）系统提示路由段自动生成；
#     **不参与** force_tool 的最终判定（那仍由 agent_text 数十项判据说了算），
#     避免引入新的误触发面。
#   * desc 是给模型看的一句「什么时候用我」，从注册表生成 → 加工具只改这一处。
#   * 新增工具时：在这里登记一行 + 工具自身 @register_tool，二者缺一会在
#     test_unified_intent_225.py 的「注册表 vs tools 注册表一致性」检查里翻红。
ROUTE_REGISTRY = (
    {
        "name": "web_search",
        "desc": "联网搜索/查资料（要外部信息时用）",
        "keywords": ("搜索", "搜一下", "搜搜", "查一下", "查查", "联网", "google",
                     "百度", "最新消息", "新闻"),
    },
    {
        "name": "browser_open",
        "desc": "打开网页/浏览器操作（要看渲染页面、点按钮、填表单时用）",
        "keywords": ("打开网页", "打开网站", "浏览器", "访问", "网页", "网址"),
    },
    {
        "name": "image_gen",
        "desc": "生成图片（用户要画面/配图/封面/插画时用）",
        "keywords": ("生图", "生成图片", "画一张", "画个", "做图", "配图", "封面",
                     "插画", "海报", "图片", "照片"),
    },
    {
        "name": "video_gen",
        "desc": "生成视频（用户要成片/视频/动画时用）",
        "keywords": ("生视频", "生成视频", "做视频", "拍视频", "视频", "动画",
                     "短视频", "口播"),
    },
    {
        "name": "write_file",
        "desc": "写文件/落盘（要产出文件时用）",
        "keywords": ("写文件", "保存到", "写入", "落盘", "存成", "生成文件",
                     "导出", "建个文件"),
    },
    {
        "name": "read_file",
        "desc": "读文件（要看已有文件内容时用）",
        "keywords": ("读文件", "看看文件", "打开文件", "查看文件", "读取"),
    },
    {
        "name": "run_python",
        "desc": "跑代码/数据计算（要算数、跑脚本、处理数据时用）",
        "keywords": ("跑代码", "运行代码", "写代码", "脚本", "计算", "算一下",
                     "数据分析", "统计"),
    },
    {
        "name": "process_kill",
        "desc": "终止进程（用户要关/杀掉某个程序时用）",
        "keywords": ("结束进程", "杀掉", "终止", "关掉进程", "kill"),
    },
    {
        "name": "clean_recycle_bin",
        "desc": "清空回收站",
        "keywords": ("清空回收站", "清回收站"),
    },
    {
        "name": "send_email",
        "desc": "发邮件",
        "keywords": ("发邮件", "发邮箱", "发送邮件", "邮件发给"),
    },
    {
        "name": "remember",
        "desc": "记住用户偏好/事实（要长期保存信息时用）",
        "keywords": ("记住", "记一下", "记住这个", "以后都"),
    },
    {
        "name": "sys_info",
        "desc": "查本机真实能力清单（不确定有哪些功能时先用它核实）",
        "keywords": ("有什么功能", "你能做什么", "能力清单", "有哪些工具"),
    },
)

# 产物对象词（用于填 Intent.target）
_TARGET_WORDS = ("视频", "图片", "文件", "表格", "ppt", "pdf", "word", "excel",
                 "截图", "csv", "海报", "插画", "配音", "文案", "大纲", "脚本")

_ROUTE_BY_NAME = {r["name"]: r for r in ROUTE_REGISTRY}


def _route_hint_text():
    """v4.225：从 ROUTE_REGISTRY 自动生成系统提示里的工具路由段。

    **替代** agent.py 里手写的「跑代码用 run_python、搜索用 web_search、生图用
    image_gen」那句 —— 以前加新工具要记得手改，忘了就静默失配（模型不知道有这工具）。
    现在加工具 = 在 ROUTE_REGISTRY 加一行，提示自动跟着变。
    """
    lines = ["【工具路由】按你要达成的结果选工具（不确定有什么工具先问 sys_info）："]
    for r in ROUTE_REGISTRY:
        lines.append("- %s：%s" % (r["name"], r["desc"]))
    return "\n".join(lines)


def _matched_routes(text):
    """按注册表关键词命中，返回 [(name, hit_words), ...]（按注册表顺序，去重）。"""
    t = (text or "").lower()
    hits = []
    for r in ROUTE_REGISTRY:
        got = [k for k in r["keywords"] if k.lower() in t]
        if got:
            hits.append((r["name"], got))
    return hits


def _target_of(text):
    for w in _TARGET_WORDS:
        if w in (text or ""):
            return w
    return ""


class Intent(object):
    """统一意图值对象（v4.225）。

    刻意做成一等对象而不是 dict：它要跨 agent / ui / 日志三处传递，
    字段固定 + 可打日志 + 可比较，比到处 `d.get("kind")` 稳。
    `to_dict()` 供落日志/调试，`__eq__` 供判据断言。
    """

    __slots__ = ("kind", "confidence", "explicit", "target", "requested_tools",
                 "force_tool", "needs_action", "text_only", "reason",
                 "needs_clarification", "clarify_reason", "clarify_options")

    def __init__(self, kind=KIND_UNKNOWN, confidence=0.0, explicit=False,
                 target="", requested_tools=(), force_tool=None,
                 needs_action=False, text_only=False, reason="",
                 needs_clarification=False, clarify_reason="", clarify_options=()):
        self.kind = kind
        self.confidence = float(confidence)
        self.explicit = bool(explicit)
        self.target = target
        self.requested_tools = tuple(requested_tools or ())
        self.force_tool = force_tool
        self.needs_action = bool(needs_action)
        self.text_only = bool(text_only)
        self.reason = reason
        # v4.239.0 歧义澄清闸门：默认 False（不澄清）；仅当 classify 判定多义时置 True。
        # 这三个字段纯增量，绝不影响 kind/force/needs_action/text_only 任何既有结论。
        self.needs_clarification = bool(needs_clarification)
        self.clarify_reason = clarify_reason
        self.clarify_options = tuple(clarify_options or ())

    def to_dict(self):
        return {s: getattr(self, s) for s in self.__slots__}

    def __eq__(self, other):
        if not isinstance(other, Intent):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self):
        return hash((self.kind, self.target, self.requested_tools, self.force_tool))

    def __repr__(self):
        return ("Intent(kind=%r, conf=%.2f, force=%r, req=%r, target=%r)"
                % (self.kind, self.confidence, self.force_tool,
                   self.requested_tools, self.target))

    def summary(self):
        """人可读一行（状态栏 / 日志用）。"""
        bits = [self.kind]
        if self.target:
            bits.append("目标=" + self.target)
        if self.force_tool:
            bits.append("强制=" + self.force_tool)
        elif self.requested_tools:
            bits.append("候选=" + "/".join(self.requested_tools[:3]))
        bits.append("置信=%.2f" % self.confidence)
        return " ".join(bits)


def _is_question(text):
    """疑问句判定。**直接复用** `agent_text._is_question`（v4.159.5 补过闭集疑问
    代词：什么/啥/哪些/哪个/多少/多久）—— 本模块刻意不自己维护一份简版，
    免得两处词表漂移。agent_text 不可用时退回最小尾缀判据。"""
    try:
        import agent_text
        return bool(agent_text._is_question(text))
    except Exception:
        t = (text or "").strip()
        return t.endswith(("？", "?", "吗", "呢", "么"))


def classify(text, prev_text=None):
    """v4.225：**唯一**对外分类入口，返回 Intent。

    流程（严格照搬 agent_text 既有判据的判定顺序，本模块不新增否决逻辑）：
      1. 先问「被一票否决了吗」——否定 / 引用质疑 / 状态追问 / 讨论复盘
         → 对应 kind，`force_tool=None`。
      2. 再问「强制路由到哪个」——直接用 `agent_text._route_force_tool` 的结论。
      3. 再问「要不要动手」——`agent_text._detect_action_intent`（注意它吃 messages）。
      4. 再问「是不是纯文本创作」——`agent_text._content_creation_only`。
      5. 最后算 confidence（**只用于日志展示**，不参与上面任何判定）。

    fail-open：agent_text 不可用或抛异常 → 返回 KIND_UNKNOWN 的 Intent，
    调用方按「没分类出来」处理（= 退回本轮改动前的默认行为）。
    """
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    try:
        import agent_text
    except Exception:
        return Intent(kind=KIND_UNKNOWN, confidence=0.0,
                      reason="agent_text 不可用（fail-open）")

    try:
        import intent_guard as _ig
    except Exception:
        _ig = None

    t = text.lower()
    tgt_early = _target_of(text)
    # 1) 一票否决三兄弟（顺序照旧：否定 → 引用 → 状态）
    try:
        if agent_text._neg_hit(text):
            return Intent(kind=KIND_NEGATED, confidence=0.60, explicit=False,
                          target=tgt_early, force_tool=None,
                          needs_action=False, reason="否定语境一票否决")
    except Exception:
        pass
    try:
        if agent_text._ref_by_position(text) or agent_text._ref_existing_artifact(text):
            return Intent(kind=KIND_REFERENCE, confidence=0.55,
                          target=tgt_early, force_tool=None,
                          needs_action=False, reason="引用/质疑既有产物语境")
    except Exception:
        pass
    # v4.225：状态追问必须**结合产物语境**才算。
    # 词表里的「怎么样 / 如何」本身是通用疑问词——「今天天气怎么样」命中它
    # 只是词面撞车，不是「在追问某个已生成任务」。所以额外要求：同时出现产物
    # 对象词（「视频好了吗」✓ / 「天气怎么样」✗）。这条只影响 Intent.kind 的
    # 归类口径，**不改动 force_tool**（`_route_force_tool` 内部照旧一律 None）。
    try:
        if any(k in text for k in agent_text._STATUS_KW) and tgt_early:
            return Intent(kind=KIND_STATUS, confidence=0.55,
                          target=tgt_early, force_tool=None,
                          needs_action=False, reason="状态追问，不重复触发")
    except Exception:
        pass
    # 评价句式（「这个封面做的漂亮」）——谈的是**已完成**的结果，不是下新指令。
    # `_route_force_tool` 已据此返回 None，这里只把 kind 落到 discuss。
    if _ig is not None:
        try:
            if _ig.is_praise(text):
                return Intent(kind=KIND_DISCUSS, confidence=0.50,
                              target=tgt_early, force_tool=None,
                              needs_action=False, reason="评价既有产物，非新指令")
        except Exception:
            pass

    # 2) 强制工具路由（结论原样搬运，不解释、不改写）
    force = None
    try:
        force = agent_text._route_force_tool(text, prev_text)
    except Exception:
        force = None

    # 3) 要不要动手 / 4) 纯文本创作
    needs_action = False
    text_only = False
    try:
        # _detect_action_intent 吃 messages 列表；单条时与原调用点
        # （agent.run 传 self.messages）语义一致 —— 它只看最近用户那条。
        needs_action = bool(agent_text._detect_action_intent(
            [{"role": "user", "content": text}]))
    except Exception:
        needs_action = bool(force)
    try:
        text_only = bool(agent_text._content_creation_only(text))
    except Exception:
        text_only = False

    # kind 归类：force 有值 / 纯文本 / 讨论复盘 / 问句 / 否则按需动作
    hits = _matched_routes(text)
    req = tuple(h[0] for h in hits)
    tgt = _target_of(text)
    explicit = bool(force) or any(
        r["name"].lower() in t for r in ROUTE_REGISTRY)

    weak_discuss = False
    try:
        weak_discuss = any(k in text for k in agent_text._WEAK_DISCUSS_KW)
    except Exception:
        pass

    if force:
        kind = KIND_ACTION
    elif text_only:
        kind = KIND_CONTENT
    elif _is_question(text):
        # 疑问句优先于「命中过关键词」——「做视频需要什么工具」句里有视频/工具，
        # 但用户要的是**答案**不是执行。`force_tool` 已在上游单独判过（这里
        # force 为 None），所以把它归为 question 不会影响任何既有行为。
        kind = KIND_QUESTION
    elif weak_discuss and not req:
        kind = KIND_DISCUSS
    elif req or needs_action:
        kind = KIND_ACTION
    else:
        kind = KIND_UNKNOWN

    # 5) confidence（保守启发式；**不参与任何行为决策**）
    conf = 0.30
    if explicit:
        conf += 0.25
    if tgt:
        conf += 0.20
    if force:
        conf += 0.10
    conf = min(1.0, conf)

    reason = "force=%s needs_action=%s text_only=%s req=%s" % (
        force, needs_action, text_only, ",".join(req[:4]))

    # v4.239.0 歧义澄清闸门（对标 Codex 评估里性价比最高的补强项）
    # ------------------------------------------------------------------
    # 问题：用户下「多义 / 含糊指令」时，原系统二选一——要么交给 LLM 自由发挥
    # （大概率猜错方向），要么因判据保守被当成非指令直接吞掉。两条路都不好。
    # 修法：检测到「多义」就主动反问，把歧义点摊开让用户选。
    #
    # v1 只覆盖**最干净、最无争议**的一类：
    #     kind == action 且 force_tool 为 None 且 命中 >= 2 个候选工具
    # 即「系统检测到了多个候选功能、却选不出唯一一个」。典型：
    # 「帮我把视频和图片都处理一下」→ 命中 image_gen + video_gen，但没说清要生成还是编辑。
    #
    # 设计纪律（与 intent.py 同源，见文件头）：
    #   · 只加不减：上面 kind/force/needs_action/text_only 的结论一行未动；
    #     本块只额外算 needs_clarification，默认 False。
    #   · fail-open：任何异常 → 不澄清（退回旧行为），绝不阻断。
    #   · 不引入误触发面：单工具 / 有明确强制动词 / 疑问 / 纯创作 / 否定 一律不澄清
    #     （这些路径走的是上方 early-return，needs_clarification 取默认 False）。
    needs_clarify = False
    clarify_reason = ""
    clarify_options = ()
    try:
        if kind == KIND_ACTION and not force and len(req) >= 2:
            needs_clarify = True
            _opts = []
            for _n in req:
                _r = _ROUTE_BY_NAME.get(_n)
                _opts.append((_n, _r["desc"] if _r else _n))
            clarify_options = tuple(_opts)
            clarify_reason = ("这条指令同时指向多个功能（%s），为避免做错方向，"
                              "请先确认你想让我做哪一步 / 具体要什么？"
                              % "、".join(req))
    except Exception:
        needs_clarify = False
        clarify_reason = ""
        clarify_options = ()

    return Intent(kind=kind, confidence=conf, explicit=explicit, target=tgt,
                  requested_tools=req, force_tool=force,
                  needs_action=needs_action, text_only=text_only,
                  reason=reason,
                  needs_clarification=needs_clarify,
                  clarify_reason=clarify_reason,
                  clarify_options=clarify_options)


__all__ = [
    "VERSION", "Intent", "classify",
    "ROUTE_REGISTRY", "KIND_ACTION", "KIND_CONTENT", "KIND_QUESTION",
    "KIND_DISCUSS", "KIND_STATUS", "KIND_NEGATED", "KIND_REFERENCE",
    "KIND_UNKNOWN", "KIND_ALL",
]