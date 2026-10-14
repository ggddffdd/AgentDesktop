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
    continuation     bool  v4.255.0：是否「接着上一轮续干」（会话粘性事实判据）

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

VERSION = "v4.278.0"

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

# v4.260.x（意图识别护栏，借鉴业界 LLM-Router 第二判据 + 本项目 fail-closed 哲学）
# --------------------------------------------------------------------------
# 高风险外部生成工具：误调代价大（耗 token / 时长，且易触发 503 队列满空转，
# 见 tools.py tool_video_gen 熔断）。低置信意图下**不直接调**，fail-closed 回退澄清。
HIGH_RISK_TOOLS = ("video_gen", "image_gen")
# 低置信澄清回退阈值：规则层仅靠关键词命中（无显式 force_tool）时 conf 偏低，
# 低于此值且命中高风险工具 + 非明确指令语境 → 澄清而非直接调。
# （conf 算法见文件头：纯关键词命中≈0.30~0.50，远低于此阈值；显式点名含
#  force_tool 或 tgt 命中，仍可能低于 0.85，但那些路径被 `not force` / kind 条件排除。）
LOW_CONF_THRESHOLD = 0.85


# v4.260.x：能力确认疑问（「你会做X吗」「能不能做」）—— 这类低置信提到高风险
# 工具才视为「可能要生成」，触发澄清回退；排除「问需要什么工具 / 怎么生成」(问信息)
# 与「分析下…这件事」(讨论)。避免误触发 CL4 类反例（test_clarify_gate_238.py）。
_CAPABILITY_KW = ("会做", "能做", "你会", "可以", "能不能", "会不会",
                  "能吗", "会吗", "可以吗", "行吗")


def _is_capability_confirm_question(text):
    """是否「能力确认疑问」—— 确认对方能否做某事（会做 / 能做 / 可以）。

    纯函数、fail-open（异常 → False）。非疑问句或不含能力确认词 → False。
    """
    try:
        if not isinstance(text, str) or not text:
            return False
        if not any(k in text for k in _CAPABILITY_KW):
            return False
        # 疑问句（尾 吗/? 或 agent_text 共认）才算能力确认，否则是陈述/讨论
        if text.rstrip().endswith(("吗", "？", "?")):
            return True
        return _is_question(text)
    except Exception:
        return False


def _low_conf_high_risk_guard(kind, force, req, conf, text):
    """v4.260.x：低置信高风险生成工具澄清回退（fail-closed）。

    规则层 detect 到「命中高风险生成工具(video_gen/image_gen) + 无显式强制(force=None)
    + 非指令语境(question/unknown/discuss/reference) + confidence<阈值
    + 能力确认疑问(你会做X吗/能不能做)」→ 返回
    (needs_clarify, reason, options)，调用方据此置澄清、不直接调工具。
    纯函数、无副作用；不满足条件返回 (False, "", ())。
    """
    if (not force
            and kind in (KIND_QUESTION, KIND_UNKNOWN, KIND_DISCUSS, KIND_REFERENCE)
            and conf < LOW_CONF_THRESHOLD
            and any(t in HIGH_RISK_TOOLS for t in req)
            and _is_capability_confirm_question(text)):
        _hr = [t for t in req if t in HIGH_RISK_TOOLS]
        _hr_desc = "、".join(
            (_ROUTE_BY_NAME[t]["desc"].split("（")[0] if _ROUTE_BY_NAME.get(t) else t)
            for t in _hr)
        return (True,
                ("这句话里提到了%s，但不确定你是想让我生成，还是只是确认能力 / 在问。"
                 "为避免做错方向，先确认一下？" % _hr_desc),
                (("confirm_generate", "生成/做"), ("just_asking", "只是问问")))
    return (False, "", ())


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
                 "needs_clarification", "clarify_reason", "clarify_options",
                 "continuation")

    def __init__(self, kind=KIND_UNKNOWN, confidence=0.0, explicit=False,
                 target="", requested_tools=(), force_tool=None,
                 needs_action=False, text_only=False, reason="",
                 needs_clarification=False, clarify_reason="", clarify_options=(),
                 continuation=False):
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
        # v4.255.0（B 包 会话粘性）：本句是否属于「接着上一轮继续干」。
        # 纯增量字段，默认 False；只在 classify 拿到 messages 且事实判据成立时置 True。
        self.continuation = bool(continuation)

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


# v4.254.0（A4 澄清放宽）：**隐含祈使** —— 用户在陈述异常现象（「表格第三列对不上」
# 「这个按钮点不动」「报错了」），没写「帮我修」但意思就是要修。
#
# 现状：这类句子既没有动宾结构（进不了 action）、也没有疑问词（进不了 question），
# 被判 KIND_UNKNOWN 直接吞掉 —— 于是只有两条路：模型自由发挥猜方向，或被当闲聊
# 不干活。两条都不好。修法：认出它是「在报问题」，反问一句确认再动手。
#
# 词表刻意**不收**裸「不行/不好/不对」：那是主观评价/感受（「这个方案我觉得不行」），
# 不是要修。只收**事实性异常陈述**（闭集、可穷举）。
_IMPLICIT_IMPERATIVE_KW = (
    "对不上", "不一致", "错了", "有误", "报错", "崩了", "出框", "点不动",
    "打不开", "没反应", "卡住", "卡了", "乱了", "丢了", "少了", "多了",
    "不生效", "没生效", "显示不对", "位置不对", "大小不对", "跑不起来",
    "失败了", "失效", "排版乱", "重叠了", "错位", "漏了", "重复了",
)
# 排除：疑问句 / 主观感受 / 假设语气 —— 这些只是「在说」，不是要修
_IMPLICIT_EXCLUDE_KW = (
    "觉得", "认为", "感觉", "是不是", "为什么", "怎么", "吗", "呢", "应该",
    "最好", "建议", "如果", "要是", "万一", "可能",
)
# 与 intent_guard._NEG_MAX_LEN 同源：超长文本是任务说明书，不是陈述异常。
# ⚠ 判据实测踩中：自动化任务正文里常写「失败就重试三次」「报错了也别停」，
# 不加这条长度闸，日常任务每轮都会被反问打断 → 任务直接卡死。
_IMPLICIT_MAX_LEN = 40


def detect_implicit_imperative(text):
    """是否「隐含祈使」——陈述异常、隐含要修（v4.254.0 A4）。

    返回命中的异常词（无则 ""）。纯函数、fail-open（任何异常 → ""）。
    """
    try:
        if not isinstance(text, str) or not text:
            return ""
        if len(text.strip()) > _IMPLICIT_MAX_LEN:
            return ""
        if _is_question(text):
            return ""
        if any(k in text for k in _IMPLICIT_EXCLUDE_KW):
            return ""
        for k in _IMPLICIT_IMPERATIVE_KW:
            if k in text:
                return k
        return ""
    except Exception:
        return ""


# ============================================================
# v4.255.0（B 包）：续接指令 —— 接事实，不猜词
# ============================================================
# 症状（探针 _dev_history/_probe_sticky_255.py 实测 21 条 13 条不符）：
#   「再来一个」「再详细点」「换一个风格」→ kind=unknown、needs_action=False。
#   needs_action 是 agent.py 两道防线的总开关：
#     · 946  force_required = step==1 and self._needs_action   → 首步不强制
#     · 1183 nudge 门槛同样看 _needs_action                    → 空转也不催
#   两道同时失效 = 用户看到「好的，我来」然后没下文（空转）。
#   「继续」能用只是因为它碰巧躺在 agent_text._ACTION_KEYWORDS 里 —— 靠词表
#   蒙对，换个说法立刻失效。**词表枚举补不完**，这是根本问题。
#
# 修法：把 v4.111 回放已证明的事实判据 `route_judge.has_recent_tool_any` 接进来。
#   这个会话刚刚还在调工具吗？调了 ⇒ 活没干完 ⇒ 续接句是「接着干」。
#   没调 ⇒ 凭一句「再来一个」不该凭空派活（S3 反向用例专门钉这条）。
#
# 三道闸缺一不可，各自都有反向用例：
#   ① 文本命中续接闭集（下方 _CONTINUATION_MARKERS）
#   ② 非疑问、非主观/假设（复用 A 包 _IMPLICIT_EXCLUDE_KW，两处同一口径）
#   ③ 事实：最近 k 条确有工具调用
_CONTINUATION_MARKERS = (
    # 再来 X 族
    "再来", "再做一个", "再做", "再写", "再生成", "再出", "再画", "再拍",
    "再跑", "再试", "再改", "再调", "再加点", "再加", "再多", "再来点",
    "再来些", "再来一遍", "再来一次", "再换",
    # 重做 / 换一个
    "重新", "重来", "换一个", "换个", "换张", "换种", "换一版",
    # 详略 / 深浅
    "再详细", "详细点", "详细些", "再细", "再深入", "深入一点", "展开",
    "不够详细", "不够具体", "太简略", "简单了点", "再丰富", "丰富一下",
    "加点细节",
    # 接着上一轮
    "继续", "接着", "还差", "还缺", "同上", "照这个", "按这个再来",
)
# 与 A 包同源：主观评价 / 疑问 / 假设语气不是下指令（「我觉得不够详细」是评价，
# 不是要你再写一遍）。复用同一张表，避免两边各存一份又漂。
_CONTINUATION_EXCLUDE_KW = _IMPLICIT_EXCLUDE_KW
# 长度闸：自动化任务说明书里常写「失败了再试一次」「重新生成简报」——
# 那是一条完整新指令，不是「接着上一轮」。不闸住会被判成续接。
_CONTINUATION_MAX_LEN = 60
# 粘性窗口：与 route_judge.STICKY_WINDOW 同源（4）—— 任务一结束粘性自然衰减，
# 不需要额外的"结束"判据。
_CONTINUATION_WINDOW = 4


def detect_continuation(text, messages=None, k=_CONTINUATION_WINDOW):
    """是否「续接指令」—— 接着上一轮继续干（v4.255.0 B 包）。

    返回 bool。纯函数、fail-open（模块缺失 / 参数异常 / 任何错误 → False）。
    """
    try:
        if not isinstance(text, str) or not text:
            return False
        t = text.strip()
        if len(t) > _CONTINUATION_MAX_LEN:
            return False
        if _is_question(t):
            return False
        if any(w in t for w in _CONTINUATION_EXCLUDE_KW):
            return False
        if not any(mk in t for mk in _CONTINUATION_MARKERS):
            return False
        import route_judge
        if not route_judge.has_recent_tool_any(messages, k):
            return False
        return True
    except Exception:
        return False


def classify(text, prev_text=None, messages=None):
    """v4.225：**唯一**对外分类入口，返回 Intent。

    流程（严格照搬 agent_text 既有判据的判定顺序，本模块不新增否决逻辑）：
      1. 先问「被一票否决了吗」——否定 / 引用质疑 / 状态追问 / 讨论复盘
         → 对应 kind，`force_tool=None`。
      2. 再问「强制路由到哪个」——直接用 `agent_text._route_force_tool` 的结论。
      3. 再问「要不要动手」——`agent_text._detect_action_intent`（注意它吃 messages）。
      4. 再问「是不是纯文本创作」——`agent_text._content_creation_only`。
      5. 最后算 confidence（**只用于日志展示**，不参与上面任何判定）。
      6. v4.255.0：若传入 `messages`，再判一次「续接」—— 弱意图（unknown /
         action 但未认定要动手）+ 续接闭集 + **最近确有工具调用** → 放宽
         needs_action。只放宽、不收紧；不传 messages 则本步整体跳过。

    messages 是**可选**的：`classify(text)` 的老调用点行为逐字不变（continuation
    恒 False）。只有把会话喂进来的调用点（agent.py）才享受粘性判据。

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
            # v4.260.x：reference 语境也可能是指着产物问「这种你会做吗」
            # （用户指着 Grokbot 视频说「这种动画你会做吗」）。若同时命中高风险生成
            # 工具且为疑问句 → 与 question 语境同待遇，澄清回退，不直接调。
            _ref_req = tuple(h[0] for h in _matched_routes(text))
            _ref_intent = Intent(kind=KIND_REFERENCE, confidence=0.55,
                                  target=tgt_early, force_tool=None,
                                  needs_action=False, reason="引用/质疑既有产物语境")
            _gc, _gr, _go = _low_conf_high_risk_guard(
                KIND_REFERENCE, None, _ref_req, 0.55, text)
            if _gc:
                _ref_intent.needs_clarification = True
                _ref_intent.clarify_reason = _gr
                _ref_intent.clarify_options = _go
                _ref_intent.reason = "引用语境+高风险工具疑问，澄清回退"
            return _ref_intent
    except Exception:
        pass
    # v4.225：状态追问必须**结合产物语境**才算。
    # v4.243.0（审查报告 I-2）：状态词改走 agent_text._is_status_query（共现判据），
    # 与 _route_force_tool 同源，消灭第二个真源。产物语境（tgt_early）仍保留：
    # _is_status_query 只认「完成态词」或「裸状态词 + 疑问尾」，「今天天气怎么样」
    # 即便带问号也无产物词，仍由 tgt_early 挡住（归 question 而非 status）。
    try:
        if agent_text._is_status_query(text) and tgt_early:
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
    elif weak_discuss:
        # v4.244.0（审查报告 I-4）：讨论判定优先于候选工具。此前 `not req` 让
        # 「分析下这个视频」因「视频」命中 req 被判 action；讨论句提到产物词是常态，
        # 不该因此翻成「要干活」。
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

    # v4.255.0（B 包 会话粘性接线）：续接指令 —— 接事实，不猜词。
    # ------------------------------------------------------------------
    # 只在**判据没看出要干活**时放宽（只加不减）：
    #   · kind 为 unknown（压根没认出来）或 action（认出方向但没认定要动手）
    #   · 没有强制工具（有点名工具就按点名的走，不抢方向盘）
    #   · needs_action 目前是 False（已经是 True 的不用管）
    #   · 文本命中续接闭集 + 疑问/主观排除 + 长度闸
    #   · 事实：最近 _CONTINUATION_WINDOW 条确有工具调用
    # 少一道闸都会误触发：最危险的是漏掉事实闸 —— 那本包就退化成「又补一批词」，
    # 与 B 方案的立论（不猜词）自相矛盾，所以 S3 反向用例专门钉它。
    continuation = False
    if messages is not None and kind in (KIND_UNKNOWN, KIND_ACTION) \
            and not force and not needs_action:
        if detect_continuation(text, messages):
            continuation = True
            needs_action = True
            reason += " 续接(会话粘性:近期确有工具调用)"

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
            _desc_parts = []
            for _n in req:
                _r = _ROUTE_BY_NAME.get(_n)
                _opts.append((_n, _r["desc"] if _r else _n))
                # v4.244.0（审查报告 I-5）：面向用户的话术用中文短名（desc 括号前），
                # 工具名只进 clarify_options / log，不进用户可见文案。
                _d = _r["desc"] if _r else _n
                _desc_parts.append(_d.split("（")[0] if "（" in _d else _d)
            clarify_options = tuple(_opts)
            clarify_reason = ("这条指令可能指向多个功能：%s。为避免做错方向，"
                              "请先确认你想要哪一个？" % "、".join(_desc_parts))
    except Exception:
        needs_clarify = False
        clarify_reason = ""
        clarify_options = ()

    # v4.254.0（A4）：隐含祈使 → 反问确认。与上面「多候选工具」那条**互斥**：
    # kind==action 说明模型已经知道要干什么，再多问一句只是打扰；只有判据没看出
    # 要干活（unknown / discuss / question）时才值得问一句「要我改吗」。
    if not needs_clarify and kind in (KIND_UNKNOWN, KIND_DISCUSS, KIND_QUESTION):
        _hit = detect_implicit_imperative(text)
        if _hit:
            needs_clarify = True
            clarify_reason = ("你在说「%s」—— 是要我动手改，还是只是说一声？" % _hit)
            clarify_options = (("confirm_fix", "动手改"),
                               ("chat_only", "只是说说"))

    # v4.260.x：低置信高风险工具澄清回退（借鉴点 3 / fail-closed）
    # ------------------------------------------------------------------
    # 真实事故：用户说「这种代码做的动画你会做吗」「你会做视频吗」，规则层仅靠
    # 关键词（动画/视频）命中 video_gen，但用户是在**确认能力 / 提问**，并非明确
    # 下指令；旧逻辑 confidence 不参与决策 → 下游直接调 video_gen 空转（与
    # tools.py tool_video_gen 熔断同一类根因）。
    # 修法：规则层 detect 到「命中高风险生成工具 + 无显式强制(force=None) +
    # 语境是非指令(question/unknown/discuss/reference) + confidence<阈值」→ 置
    # needs_clarification，反问确认意图，不直接调。
    # 只加不减：force 有值（明确点名）或 kind 已是 action 语境时不触发；
    # 与上方多候选/隐含祈使澄清互斥叠加时保留原话术（用 `not needs_clarify` 守）。
    _gc, _gr, _go = _low_conf_high_risk_guard(kind, force, req, conf, text)
    if not needs_clarify and _gc:
        needs_clarify = True
        clarify_reason = _gr
        clarify_options = _go

    return Intent(kind=kind, confidence=conf, explicit=explicit, target=tgt,
                  requested_tools=req, force_tool=force,
                  needs_action=needs_action, text_only=text_only,
                  reason=reason,
                  needs_clarification=needs_clarify,
                  clarify_reason=clarify_reason,
                  clarify_options=clarify_options,
                  continuation=continuation)


def route_second_opinion(text, intent, llm_router):
    """v4.260.x（借鉴点 1：LLM Router 作第二判据）—— 可选扩展位。

    当规则层 classify 给出「低置信 + 命中高风险工具」的模糊结论时，调用方**可选**
    传入 llm_router（callable: (text) -> dict）做轻量 LLM 二分类，细化意图：
        LLM 返回 {"confirm": True}  → 视为明确要生成（消澄清，直接派活）
        LLM 返回 {"confirm": False} 或异常 → 视为只是问 / 确认能力（保持澄清）
    本函数不强制联网、不影响 classify 既有路径；未接入时规则层 fail-closed 回退已
    足够防误调。fail-open：llm_router 非可调用 / 抛异常 / 返回异常 → 返回原 intent。

    设计纪律（与 intent.py 同源）：
      · 不重写既有判据、不改行为；只在 needs_clarification 已为真时**按需消解**。
      · 只加不减：LLM 否认或不回 → 沿用规则层澄清，绝不反向强制派活。
    """
    if not callable(llm_router):
        return intent
    if not (getattr(intent, "needs_clarification", False)
            and any(t in HIGH_RISK_TOOLS for t in getattr(intent, "requested_tools", ()))):
        return intent
    try:
        res = llm_router(text)
        if isinstance(res, dict) and res.get("confirm") is True:
            intent.needs_clarification = False
            intent.clarify_reason = ""
            intent.clarify_options = ()
    except Exception:
        pass
    return intent


__all__ = [
    "VERSION", "Intent", "classify", "detect_continuation",
    "route_second_opinion", "HIGH_RISK_TOOLS", "LOW_CONF_THRESHOLD",
    "ROUTE_REGISTRY", "KIND_ACTION", "KIND_CONTENT", "KIND_QUESTION",
    "KIND_DISCUSS", "KIND_STATUS", "KIND_NEGATED", "KIND_REFERENCE",
    "KIND_UNKNOWN", "KIND_ALL",
]