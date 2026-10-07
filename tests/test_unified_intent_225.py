# -*- coding: utf-8 -*-
"""tests/test_unified_intent_225.py —— v4.225 P3 统一意图 Intent 判据

核心不变量（改坏必红）：
  IN1  Intent 是真对象：字段固定 / 可序列化 / 可判等
  IN2  classify 只做「壳」：force_tool / needs_action / text_only 结论
      **逐条等于** agent_text 既有判据的原始输出（换壳不改行为）
  IN3  一票否决优先：否定 / 引用 / 状态 → force_tool 必为 None
  IN4  ROUTE_REGISTRY 是单一真源：系统提示路由段从它自动生成，且每行
      都真能在 tools 注册表里找到同名工具（防登记了不存在的工具）
  IN5  fail-open：空串 / 非 str / agent_text 挂掉 → 不抛异常
  IN6  agent.py 已改读 Intent（静态接线钉子）
"""
import os
import sys
import inspect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


print("=" * 62)
print("v4.225 P3 统一意图 Intent 判据")
print("=" * 62)

import intent  # noqa: E402
import agent_text  # noqa: E402


# ============================================================
# IN1  Intent 是真对象
# ============================================================
print("\n--- IN1 对象契约 ---")
i0 = intent.Intent()
check("IN1-1 默认 kind=unknown", i0.kind == intent.KIND_UNKNOWN, i0.kind)
check("IN1-2 默认 confidence=0.0", i0.confidence == 0.0, i0.confidence)
check("IN1-3 默认 force_tool=None", i0.force_tool is None, i0.force_tool)
check("IN1-4 requested_tools 是 tuple",
      isinstance(i0.requested_tools, tuple), type(i0.requested_tools))
d = i0.to_dict()
check("IN1-5 to_dict 含全部 9 字段",
      set(d.keys()) == set(intent.Intent.__slots__),
      str(sorted(d.keys())))
check("IN1-6 __slots__ 固定（防随手加属性漂移）",
      set(intent.Intent.__slots__) == {
          "kind", "confidence", "explicit", "target", "requested_tools",
          "force_tool", "needs_action", "text_only", "reason"},
      str(intent.Intent.__slots__))
a = intent.Intent(kind="action", confidence=0.5)
b = intent.Intent(kind="action", confidence=0.5)
check("IN1-7 同字段对象可判等", a == b)
c = intent.Intent(kind="content", confidence=0.5)
check("IN1-8 不同字段不等", a != c)
check("IN1-9 与非 Intent 比较返回 NotImplemented",
      a.__eq__({"kind": "action"}) is NotImplemented)
check("IN1-10 可 hash（可放进 set/dict）",
      isinstance(hash(a), int))
check("IN1-11 repr 含 kind", "action" in repr(a), repr(a))
check("IN1-12 summary 非空", bool(a.summary()), a.summary())


# ============================================================
# IN2  classify 只是壳：结论逐条等于既有判据
# ============================================================
print("\n--- IN2 只做壳，结论等于既有判据 ---")
_CASES = [
    "给我生成个视频",
    "画一张图片",
    "别生成视频了",
    "视频好了吗",
    "进度如何",
    "分析下生成视频这件事",
    "这个封面做的漂亮",
    "帮我写一段口播文案",
    "打开 https://github.com/trending",
    "为什么会出现这个bug",
    "把 Chrome 杀掉",
    "写一段文案然后保存成md文件",
    "",
]
mism = []
for t in _CASES:
    got = intent.classify(t)
    want_force = agent_text._route_force_tool(t, None)
    if got.force_tool != want_force:
        mism.append("force(%r): got=%r want=%r" % (t, got.force_tool, want_force))
check("IN2-1 force_tool 逐条等于 _route_force_tool",
      not mism, "; ".join(mism[:3]))

mism2 = []
for t in _CASES:
    got = intent.classify(t)
    want_na = agent_text._detect_action_intent([{"role": "user", "content": t}])
    if bool(got.needs_action) != bool(want_na):
        mism2.append("na(%r): got=%r want=%r" % (t, got.needs_action, want_na))
check("IN2-2 needs_action 逐条等于 _detect_action_intent",
      not mism2, "; ".join(mism2[:3]))

mism3 = []
for t in _CASES:
    got = intent.classify(t)
    want_to = agent_text._content_creation_only(t)
    if bool(got.text_only) != bool(want_to):
        mism3.append("to(%r): got=%r want=%r" % (t, got.text_only, want_to))
check("IN2-3 text_only 逐条等于 _content_creation_only",
      not mism3, "; ".join(mism3[:3]))

check("IN2-4 classify 返回真 Intent",
      all(isinstance(intent.classify(t), intent.Intent) for t in _CASES))
# prev_text 兜底：当前句只有裸续接动词（「生成」）时，用上一句推断对象。
# 用例不能选「用agnes生成」—— agnes 分支会先拦截（返回 None），
# 那样断言"能兜底"就是拿一个永不成立的前提当判据（假绿来源）。
_pf = intent.classify("生成", "帮我生成个视频")
check("IN2-5 prev_text 参与路由（裸续接动词兜底）",
      _pf.force_tool == "video_gen", repr(_pf.force_tool))
check("IN2-5b 兜底结论与 _route_force_tool 一致",
      _pf.force_tool == agent_text._route_force_tool("生成", "帮我生成个视频"))
# 无 prev_text 时同样的裸动词不该凭空路由
check("IN2-5c 无 prev_text 时裸动词不强路由",
      intent.classify("生成", None).force_tool is None,
      repr(intent.classify("生成", None).force_tool))


# ============================================================
# IN3  一票否决优先
# ============================================================
print("\n--- IN3 一票否决优先 ---")
_neg = intent.classify("别生成视频了")
check("IN3-1 否定 → kind=negated", _neg.kind == intent.KIND_NEGATED, _neg.kind)
check("IN3-2 否定 → force_tool=None", _neg.force_tool is None, _neg.force_tool)
check("IN3-3 否定 → needs_action=False", _neg.needs_action is False)

_st = intent.classify("视频好了吗")
check("IN3-4 状态追问 → kind=status", _st.kind == intent.KIND_STATUS, _st.kind)
check("IN3-5 状态追问 → force_tool=None", _st.force_tool is None, _st.force_tool)

_ref = intent.classify("分析下生成视频这件事")
check("IN3-6 引用质疑 → kind=reference",
      _ref.kind == intent.KIND_REFERENCE, _ref.kind)
check("IN3-7 引用质疑 → force_tool=None", _ref.force_tool is None, _ref.force_tool)

_pr = intent.classify("这个封面做的漂亮")
check("IN3-8 评价句 → kind=discuss（不是 action）",
      _pr.kind == intent.KIND_DISCUSS, _pr.kind)
check("IN3-9 评价句 → force_tool=None", _pr.force_tool is None, _pr.force_tool)

# 状态追问必须结合产物语境（词表里的「怎么样/如何」是通用疑问词）
check("IN3-10 「怎么样」无产物词不判 status（词面撞车防护）",
      intent.classify("今天天气怎么样").kind != intent.KIND_STATUS,
      intent.classify("今天天气怎么样").kind)
check("IN3-11 「视频好了吗」有产物词 → status",
      intent.classify("视频好了吗").kind == intent.KIND_STATUS)

check("IN3-12 疑问句 kind=question",
      intent.classify("做视频需要什么工具").kind == intent.KIND_QUESTION,
      intent.classify("做视频需要什么工具").kind)
check("IN3-13 疑问句即使命中关键词也不判 action",
      intent.classify("做视频需要什么工具").kind != intent.KIND_ACTION)


# ============================================================
# IN4  ROUTE_REGISTRY 是单一真源
# ============================================================
print("\n--- IN4 注册表单真源 ---")
check("IN4-1 注册表非空", len(intent.ROUTE_REGISTRY) >= 10,
      len(intent.ROUTE_REGISTRY))
_names = [r["name"] for r in intent.ROUTE_REGISTRY]
check("IN4-2 注册表工具名不重复", len(_names) == len(set(_names)),
      str([n for n in _names if _names.count(n) > 1]))
check("IN4-3 每行都有 name/desc/keywords 三键",
      all(set(r.keys()) == {"name", "desc", "keywords"} for r in intent.ROUTE_REGISTRY))
check("IN4-4 每行 keywords 非空",
      all(r["keywords"] for r in intent.ROUTE_REGISTRY))

hint = intent._route_hint_text()
check("IN4-5 路由段含标题", "【工具路由】" in hint)
check("IN4-6 路由段列出全部工具名",
      all(n in hint for n in _names),
      str([n for n in _names if n not in hint]))
check("IN4-7 路由段含全部描述",
      all(r["desc"] in hint for r in intent.ROUTE_REGISTRY))

# 注册表里的工具必须真存在（tools 注册表），否则是幽灵路由
try:
    import config
    _cfg = dict(config.DEFAULT_CONFIG)
    _all_tools = set()
    for _t in (config.get_all_tools(_cfg) or []):
        _fn = _t.get("function", {}) if isinstance(_t, dict) else {}
        if _fn.get("name"):
            _all_tools.add(_fn["name"])
    _ghost = [n for n in _names if n not in _all_tools]
    check("IN4-8 注册表无幽灵工具（每个名字都能在 tools 注册表找到）",
          not _ghost, str(_ghost))
except Exception as _e:
    check("IN4-8 注册表无幽灵工具（每个名字都能在 tools 注册表找到）",
          False, "config.get_all_tools 失败: %s" % _e)

# ui.py 必须从注册表生成，而不是继续手写
_ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
check("IN4-9 ui.py 调 _route_hint_text（系统提示自动生成）",
      "_route_hint_text" in _ui)
# 「不许出现 X」类断言必须先剔注释行：v4.225 改动说明里就写了「替代此前
# agent.py 里手写的『跑代码用 run_python…』」，那句话在**注释**里，
# 直接子串匹配会把注释当成残留代码 → 假红。
_ui_code = "\n".join(ln for ln in _ui.split("\n")
                     if not ln.lstrip().startswith("#"))
check("IN4-10 ui.py 不再手写那句路由（跑代码用 run_python…）",
      "跑代码用 run_python" not in _ui_code,
      "剔除注释后仍存在 → 是真残留代码")
# 反向钉子：注册表驱动的生成逻辑必须真在非注释代码里
check("IN4-10b 注册表生成逻辑在非注释代码里",
      "_intent_mod._route_hint_text()" in _ui_code)

# 手工加一行注册表 → 路由段自动跟着变（真·单一真源验证）
_backup = list(intent.ROUTE_REGISTRY)
try:
    intent.ROUTE_REGISTRY = tuple(list(_backup) + [{
        "name": "_probe_tool_zzz",
        "desc": "探针工具（测试用）",
        "keywords": ("探针词",),
    }])
    check("IN4-11 注册表加一行 → 路由段自动出现（无需改别处）",
          "_probe_tool_zzz" in intent._route_hint_text())
finally:
    intent.ROUTE_REGISTRY = tuple(_backup)
check("IN4-12 恢复后幽灵行已消失",
      "_probe_tool_zzz" not in intent._route_hint_text())


# ============================================================
# IN5  fail-open
# ============================================================
print("\n--- IN5 fail-open ---")
try:
    i_none = intent.classify(None)
    check("IN5-1 classify(None) 不抛异常且 kind 合法",
          isinstance(i_none, intent.Intent)
          and i_none.kind in intent.KIND_ALL, i_none.kind)
    check("IN5-2 classify(None).force_tool=None", i_none.force_tool is None)
    _r = intent.classify(12345)
    check("IN5-3 classify(非 str) 不抛异常且 kind 合法",
          isinstance(_r, intent.Intent) and _r.kind in intent.KIND_ALL,
          repr(_r))
    check("IN5-4 目标词识别", intent.classify("生成视频").target == "视频",
          intent.classify("生成视频").target)
    check("IN5-5 无目标词时 target=''",
          intent.classify("你好").target == "",
          repr(intent.classify("你好").target))
    check("IN5-6 confidence 落在 [0,1]",
          all(0.0 <= intent.classify(t).confidence <= 1.0 for t in _CASES))
except Exception as e:
    check("IN5-1 classify(None) 不抛异常且 kind 合法", False, repr(e))

# agent_text 不可用时 fail-open
try:
    import builtins
    _real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name == "agent_text":
            raise ImportError("模拟 agent_text 不可用")
        return _real_import(name, *a, **kw)

    builtins.__import__ = _boom
    try:
        _i = intent.classify("给我生成个视频")
    finally:
        builtins.__import__ = _real_import
    check("IN5-7 agent_text 不可用 → 返回 unknown 不抛",
          isinstance(_i, intent.Intent)
          and _i.kind == intent.KIND_UNKNOWN
          and _i.force_tool is None, repr(_i))
except Exception as e:
    check("IN5-7 agent_text 不可用 → 返回 unknown 不抛", False, repr(e))


# ============================================================
# IN6  agent.py 接线钉子
# ============================================================
print("\n--- IN6 agent.py 接线 ---")
_ag = open(os.path.join(ROOT, "agent.py"), encoding="utf-8").read()
_mx = open(os.path.join(ROOT, "agent_task_mixin.py"), encoding="utf-8").read()
check("IN6-1 agent.py import intent", "import intent" in _ag)
# v4.225：_last_user_text 已外移到 AgentTaskMixin（为守住 agent.py<2400 拆分红线），
# 这里跟着核 mixin，不再钉 agent.py 内联实现。
check("IN6-2 _last_user_text 在 AgentTaskMixin 里（已随接线外移）",
      "def _last_user_text(self):" in _mx
      and "AgentTaskMixin" in _ag,
      "应定义在 agent_task_mixin.AgentTaskMixin 并被 agent mixin 引用")
check("IN6-3 run() 里 classify 建 Intent", "intent.classify(" in _ag)
check("IN6-4 step1 强制路由读 Intent.force_tool",
      "self._intent.force_tool" in _ag)
check("IN6-5 不再在主循环直接调 _route_force_tool",
      "agent_text._route_force_tool(_cur_user" not in _ag,
      "step1 应改读 Intent")
# step1 那处仍必须留有 _needs_action 原始判据（不能被 Intent 顶掉）
check("IN6-6 _needs_action 结论仍来自 _detect_action_intent",
      "self._needs_action = agent_text._detect_action_intent(self.messages)" in _ag)

# _last_user_text 必须是真方法且行为正确
try:
    src = inspect.getsource(intent.classify)
    check("IN6-7 classify 源码可见（接线判据非空谓词）",
          "agent_text._route_force_tool" in src, src[:120])
except Exception as e:
    check("IN6-7 classify 源码可见（接线判据非空谓词）", False, repr(e))


print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)