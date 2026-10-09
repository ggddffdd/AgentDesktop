# -*- coding: utf-8 -*-
"""v4.239.0 判据：「歧义澄清」闸门（对标 Codex 评估里性价比最高的补强项）

问题背景（对标评估实锤）：用户下**多义 / 含糊指令**时，原系统二选一——要么交给
LLM 自由发挥（大概率猜错方向），要么因判据保守被当成非指令直接吞掉。两条路都不好。
正确做法：主动反问，把歧义点摊开让用户选。

本闸门只覆盖**最干净、最无争议的**一类歧义（v1）：
    kind == action 且 force_tool 为 None 且 命中 >= 2 个候选工具
即「系统检测到了多个候选功能、却选不出唯一一个」——典型如
「帮我把视频和图片都处理一下」（命中 image_gen + video_gen，但没说清要生成还是编辑）。

设计纪律（与 intent.py 同源）：
  · 只加不减：Intent 旧字段（kind/force/needs_action/text_only…）结论一律不变，
    新增 needs_clarification / clarify_reason / clarify_options 三个字段，默认留空。
  · fail-open：任何异常 → 不澄清（退回旧行为），绝不阻断主循环。
  · 不引入误触发面：单工具 / 有明确强制动词 / 疑问句 / 纯文本创作 / 否定句
    一律不澄清（见 CL 反例组）。

判据是行为级的：直接调 intent.classify() 看 needs_clarification / clarify_options。
（judge-first：字段未实现时本套件整组翻红；实现后转绿。）
"""
import os
import sys

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


print("=" * 64)
print("v4.239.0 歧义澄清闸门判据")
print("=" * 64)

import intent  # noqa: E402


def _nc(text, prev=None):
    """取 needs_clarification；字段未实现（judge-first 红态）返回 None。"""
    it = intent.classify(text, prev)
    try:
        return bool(it.needs_clarification), it
    except AttributeError:
        return None, it


def _opts(text, prev=None):
    it = intent.classify(text, prev)
    try:
        return tuple(it.clarify_options)
    except AttributeError:
        return None


def _reason(text, prev=None):
    it = intent.classify(text, prev)
    try:
        return it.clarify_reason
    except AttributeError:
        return None


# ============================================================
# CL1 正例：多义指令 → 应主动澄清
# ============================================================
print("\n--- CL1 正例：多义指令应澄清 ---")

_POS = [
    "帮我把视频和图片都处理一下",          # 实测：force=None, req=(image_gen, video_gen)
    "把视频和图片改一改",                  # 双媒体 + 非生成动词（改）→ 选不出唯一工具
    "视频与图片都处理",                    # 同上（处理非生成动词）→ force=None
]
for t in _POS:
    nc, _ = _nc(t)
    check("CL1 正例 %r → needs_clarification=True" % t, nc is True, "got=%r" % nc)

# 澄清选项必须非空，且来自 ROUTE_REGISTRY（可枚举给用户选）
o0 = _opts(_POS[0])
check("CL2 澄清选项非空", o0 is not None and len(o0) >= 2, "opts=%r" % (o0,))
check("CL2b 澄清选项含候选工具名",
      o0 is not None and all(isinstance(x, (tuple, list)) and len(x) >= 1 for x in o0),
      "opts=%r" % (o0,))
# clarify_reason 必须是给人看的自然语言（非空字符串）
r0 = _reason(_POS[0])
check("CL3 clarify_reason 是非空字符串", isinstance(r0, str) and bool(r0), "reason=%r" % r0)


# ============================================================
# CL4-反例：这些一律不应澄清（误触发面必须为零）
# ============================================================
print("\n--- CL4 反例：清晰 / 非指令 / 疑问 / 否定 不应澄清 ---")

_NEG = [
    ("给我生成个视频", "明确单工具指令"),
    ("画一张图片", "明确单工具指令"),
    ("做视频需要什么工具", "疑问句（要答案不是执行）"),
    ("帮我写一段口播文案", "纯文本创作"),
    ("别生成视频了", "否定一票否决"),
    ("视频好了吗", "状态追问"),
    ("分析下生成视频这件事", "引用/质疑语境"),
    ("这个封面做的漂亮", "评价句"),
    ("打开 https://github.com/trending", "浏览器路由（单工具强制）"),
    ("先搜索再生成图片", "有序指令（强制 image_gen，不歧义）"),
]
for t, why in _NEG:
    nc, _ = _nc(t)
    check("CL4 反例 %r 不澄清（%s）" % (t, why), nc is False, "got=%r" % nc)


# ============================================================
# CL5 fail-open：异常输入不澄清、不抛
# ============================================================
print("\n--- CL5 fail-open ---")
try:
    nc_none, _ = _nc(None)
    check("CL5-1 classify(None) 不抛且 needs_clarification=False",
          nc_none is False, "got=%r" % nc_none)
    nc_num, _ = _nc(12345)
    check("CL5-2 classify(非str) 不抛且 needs_clarification=False",
          nc_num is False, "got=%r" % nc_num)
except Exception as e:
    check("CL5-1/2 fail-open 不抛异常", False, repr(e))


print("\n" + "=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
