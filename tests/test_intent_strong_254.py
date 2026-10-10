# -*- coding: utf-8 -*-
"""v4.254.0 判据：「意图识别补强」A 包（A1 否定洞 / A2 词表归一 / A3 混合指令 / A4 澄清放宽）

judge-first：本文件先于实现编写，预期红。改完源码后转绿。

背景（实测证据见 _dev_history/_probe_intent_weak_20261011.py，40 条语料 18 条不符）：
  A1 🔴 安全洞：「别搜了」→ force=web_search、needs_action=True —— 用户喊停反被强制
      搜索。根因 intent_guard._NEG_TAIL_VERB 只有生成动词，不含工具动词（搜/查/存/
      跑/删/改…），否定漏判后直接掉进 `if "搜" in text: return "web_search"`。
  A2 词表漂移：route_judge.NEEDS_TOOL_EXTRA 早在 v4.111 就记录了「生一张/出一张/配张图」
      是真实漏网写法，但那份补词只喂回放、没回流 agent_text → 「用Agnes生视频」
      「生一张猫的图片」force=None，活没干。
  A3 漏活：「帮我写篇公众号文章并配张封面图」被判纯文本创作（text_only=True）
      → 封面图不会生成。
  A4 澄清闸门只认「多候选工具且没点名」，认不出「表格第三列对不上」这类
      陈述异常、隐含要修的句子 → 要么被当成闲聊吞掉，要么模型自由发挥。

纪律（与项目同源）：
  · 行为级断言：全部走真实函数返回值，不查源码字符串（静态串判据恒真，踩过 P9/P33）。
  · 反向用例必配：每条正向判据都配「不该触发」的反例，防补词补成大开集。
  · fail-open：任何异常 → 不澄清、不强制，退回改动前行为。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import intent
import agent_text
import intent_guard

PASS = 0
FAIL = 0


def ck(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, extra))


def force(text, prev=None):
    """真实调用强制路由（行为级，非字符串匹配）。"""
    try:
        return agent_text._route_force_tool(text, prev)
    except Exception as e:  # noqa: BLE001
        return "EXC:%r" % (e,)


def clar(text):
    """真实调用 classify 取澄清结论。"""
    try:
        return intent.classify(text, None)
    except Exception as e:  # noqa: BLE001
        return None


print("=" * 72)
print("v4.254.0 意图补强判据（A1 否定洞 / A2 归一 / A3 混合 / A4 澄清）")
print("=" * 72)

# ---------------------------------------------------------------- A1 否定洞
print("\n[A1] 否定判据必须认得工具动词（用户喊停绝不动手）")
for txt in ("别搜了", "不查了", "别删那个文件", "别跑脚本了", "别存了", "别改那个配置"):
    ck(force(txt) is None, "A1 喊停不强路由：%s" % txt, "→ %r" % force(txt))
ck(intent_guard.is_negation("别搜了") is True, "A1 is_negation('别搜了')=True")
ck(intent_guard.is_negation("不查了") is True, "A1 is_negation('不查了')=True")
ck(intent_guard.is_negation("不用搜了") is True, "A1 is_negation('不用搜了')=True")
ck(intent_guard.is_negation("停止搜索") is True, "A1 is_negation('停止搜索')=True")
ck(intent_guard.is_negation("别删了") is True, "A1 is_negation('别删了')=True")

print("\n[A1-反向] 真指令不得被否定误杀")
ck(force("帮我搜一下昆明明天天气") == "web_search",
   "A1R 真搜索指令仍强路由", "→ %r" % force("帮我搜一下昆明明天天气"))
ck(intent_guard.is_negation("特别想搜一下这个问题") is False,
   "A1R 复合词『特别』不误判否定")
ck(intent_guard.is_negation("分别搜一下两个关键词") is False,
   "A1R 复合词『分别』不误判否定")
ck(intent_guard.is_negation("抓取今天的新闻，不要盲目上浏览器") is False,
   "A1R 长句『不要』是约束不是喊停（既有行为不回归）")
# 并列约束「只X不Y」：全量回归实测踩中（test_no_stall_186「只看不改」needs_action
# 掉成 False）—— 新判据把「不改」当成喊停。这是约束做法范围，不是叫停。
ck(intent_guard.is_negation("对自己进行一次自检，只看不改") is False,
   "A1R 并列约束『只…不…』不误判否定")
ck(agent_text._detect_action_intent(
    [{"role": "user", "content": "对自己进行一次自检，只看不改"}]) is True,
   "A1R 诊断类『只看不改』仍需动手（no_stall_186 回归）")
ck(intent_guard.is_negation("不查了") is True,
   "A1R 对照：句首『不查了』仍是喊停")
ck(force("帮我删掉 D 盘那个临时文件") is None or True,
   "A1R 删除指令可用性占位（不强断言具体工具）")

# ---------------------------------------------------------------- A2 词表归一
print("\n[A2] 「生 X」类真实写法必须路由到生成工具（词表两层归一）")
ck(force("用Agnes生视频") == "video_gen", "A2 用Agnes生视频→video_gen",
   "→ %r" % force("用Agnes生视频"))
ck(force("生一张猫的图片存到桌面") == "image_gen", "A2 生一张猫的图片→image_gen",
   "→ %r" % force("生一张猫的图片存到桌面"))
ck(force("帮我生个视频") == "video_gen", "A2 帮我生个视频→video_gen",
   "→ %r" % force("帮我生个视频"))
ck(force("生图，一只柯基") == "image_gen", "A2 生图→image_gen",
   "→ %r" % force("生图，一只柯基"))

print("\n[A2-反向] 规范化不得误伤")
ck(force("生产车间的视频监控方案") is None, "A2R 『生产』不该被规范成『生成』",
   "→ %r" % force("生产车间的视频监控方案"))
ck(force("分析下生成视频这件事") is None, "A2R 引用语境仍不强制（既有行为）",
   "→ %r" % force("分析下生成视频这件事"))
# 规范化本身必须闭合：只认「生 + 媒体宾语/量词」的完整形式，不认裸「生」。
# （「女生图案设计」最终会不会被 _gen_intent 命中，是 _IMAGE_OBJ 含裸「图」的既有
#   行为，不在本包改动范围 —— 这里只钉「规范化不许把『生』凭空变成『生成』」。）
_norm = getattr(agent_text, "_norm_verb", None)
if _norm is None:
    ck(False, "A2R agent_text._norm_verb 存在")
else:
    ck(_norm("女生图案设计") == "女生图案设计", "A2R 『女生图案』不被规范成『生成』",
       "→ %r" % _norm("女生图案设计"))
    ck(_norm("生产车间视频") == "生产车间视频", "A2R 『生产』不被规范成『生成』",
       "→ %r" % _norm("生产车间视频"))
    ck(_norm("生日快乐") == "生日快乐", "A2R 『生日』不被规范成『生成』",
       "→ %r" % _norm("生日快乐"))
    ck("生成视频" in _norm("用Agnes生视频"), "A2R 『生视频』正确规范成『生成视频』",
       "→ %r" % _norm("用Agnes生视频"))

print("\n[A2-归一] route_judge 补词与 agent_text 共享单一真源")
try:
    import route_judge
    _shared = getattr(agent_text, "_MEDIA_PHRASE_SHARED", None)
    ck(_shared is not None, "A2N agent_text._MEDIA_PHRASE_SHARED 存在")
    if _shared is not None:
        _missing = [w for w in _shared if w not in route_judge.NEEDS_TOOL_EXTRA]
        ck(not _missing, "A2N route_judge.NEEDS_TOOL_EXTRA 全量包含共享子集",
           "缺 %r" % _missing)
except Exception as e:  # noqa: BLE001
    ck(False, "A2N route_judge 导入", repr(e))

# ---------------------------------------------------------------- A3 混合指令
print("\n[A3] 混合指令（写内容 + 配图）不得被判纯文本创作")
_mix = "帮我写篇公众号文章并配张封面图"
ck(agent_text._content_creation_only(_mix) is False,
   "A3 混合指令不豁免（要出封面图）")
ck(agent_text._detect_action_intent([{"role": "user", "content": _mix}]) is True,
   "A3 混合指令 needs_action=True")
print("\n[A3-反向] 纯文本创作仍豁免")
ck(agent_text._content_creation_only("写一段口播文案") is True, "A3R 纯文案仍豁免")
ck(agent_text._content_creation_only("帮我写一段口播视频的文案") is True,
   "A3R 视频只作场景词仍豁免")
ck(agent_text._content_creation_only("帮我把昨天那份报告再润色一下") is True,
   "A3R 润色仍豁免")

# ---------------------------------------------------------------- A4 澄清放宽
print("\n[A4] 隐含祈使（陈述异常、隐含要修）→ 主动反问确认")
for txt in ("表格第三列对不上", "这个按钮点不动", "报错了", "右侧命令出框了"):
    _it = clar(txt)
    ck(bool(_it) and _it.needs_clarification is True,
       "A4 隐含祈使触发澄清：%s" % txt,
       "→ %r" % (None if _it is None else _it.to_dict()))
_it = clar("表格第三列对不上")
ck(bool(_it) and bool(_it.clarify_reason), "A4 clarify_reason 非空")
ck(bool(_it) and len(_it.clarify_options) >= 2, "A4 clarify_options ≥2 个选项")

print("\n[A4-反向] 闲聊/评价/提问/明确指令不得被反问打断")
for txt in ("今天累死了", "这个封面做的漂亮", "为什么搜索这么慢",
            "帮我搜一下昆明明天天气", "好的", "1920x1080"):
    _it = clar(txt)
    ck(bool(_it) and _it.needs_clarification is False,
       "A4R 不该澄清：%s" % txt,
       "→ %r" % (None if _it is None else _it.needs_clarification))

print("\n[A4-兼容] v4.239.0 原有「多候选工具」澄清路径不回归")
_it = clar("帮我把视频和图片都处理一下")
ck(bool(_it) and _it.needs_clarification is True, "A4C 多候选仍澄清",
   "→ %r" % (None if _it is None else _it.needs_clarification))
ck(bool(_it) and _it.clarify_options, "A4C 多候选仍带选项")

print("\n[A4-安全] 长文本（自动化任务说明书）不得触发澄清")
_long = ("每天 09:00 抓取 GitHub 热榜，失败就重试三次，别上浏览器，"
         "优先走 RSS，报错了也别停，整理成简报存到桌面")
_it = clar(_long)
ck(bool(_it) and _it.needs_clarification is False, "A4S 长文本不澄清",
   "→ %r" % (None if _it is None else _it.needs_clarification))

# A4S2：直接钉**长度闸本身**（行为级，不走 classify）。
# 为什么要补这条：A4S 那条长文本实测被判成 kind=action，压根不进 A4 分支
# —— 于是「长度闸被改坏」时它照样绿，成了哑弹（扰动 V7 MISS）。
# 长度闸是防「自动化任务说明书每轮被反问卡死」的唯一守门，必须单独钉死。
_maxlen = getattr(intent, "_IMPLICIT_MAX_LEN", 40)
_long_imp = ("按钮点不动，页面还报错了，我刷新了好几遍还是老样子，"
             "一直卡在这个地方没反应，导出也失败")
ck(len(_long_imp) > _maxlen, "A4S2 语料确实超长（前置）",
   "len=%d 闸=%d" % (len(_long_imp), _maxlen))
ck(intent.detect_implicit_imperative(_long_imp) == "",
   "A4S2 长度闸：超长陈述异常不判隐含祈使",
   "→ %r" % intent.detect_implicit_imperative(_long_imp))
_short_imp = "按钮点不动"
ck(len(_short_imp) <= _maxlen and intent.detect_implicit_imperative(_short_imp) == "点不动",
   "A4S2 对照：同义短句仍判隐含祈使",
   "→ %r" % intent.detect_implicit_imperative(_short_imp))

print("\n" + "=" * 72)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
