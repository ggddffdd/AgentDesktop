# -*- coding: utf-8 -*-
"""分镜导演提示词方法论 — 回归判据 v4.261.0

大哥反馈「导演台导演写分镜提示词的功力弱了点」（2026-10-11）。
根因（源码坐实）：`_gen_shots` 的 system 原本只有 3 句角色设定
（「你是专业的短视频分镜导演…」），**没有任何方法论**；en 字段规范也只写了
「1~2 句话，只写视觉内容」——等于让模型裸写提示词。产出于是系统性偏弱：
en 常是一句笼统的「美丽的海边日落」，没主体外观、没可执行的单一动作，
镜头运动与内容互相打架，视频模型无从下手。

修复：注入 SHOT_DIRECTOR_SYSTEM（三段式方法论 + 铁律）
与 SHOT_EN_SPEC（en 规范 + 负面清单 + few-shot 完整范例）。

本判据钉住两件事：
  ① 方法论要素真的写在常量里（B/C 组）；
  ② **真的拼进了发给模型的 payload**（D 组，行为级）——
     只查常量不算数：常量定义了却没接进 prompt，等于没改，
     这正是「改了但功力还是弱」的复发方式。
反向照妖镜 D7：断言旧的那句裸 en 说明已不在 prompt 里 ——
  证明引用确实被替换，而不是新常量孤零零挂着。
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import video_pipeline as vp  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")


# ── A 组：常量存在（不是空壳）──
check("A1 SHOT_DIRECTOR_SYSTEM 常量存在",
      hasattr(vp, "SHOT_DIRECTOR_SYSTEM"))
check("A2 SHOT_DIRECTOR_SYSTEM 非空且有实质长度（>200 字符）",
      isinstance(getattr(vp, "SHOT_DIRECTOR_SYSTEM", ""), str)
      and len(vp.SHOT_DIRECTOR_SYSTEM) > 200,
      len(getattr(vp, "SHOT_DIRECTOR_SYSTEM", "")))
check("A3 SHOT_EN_SPEC 常量存在且非空",
      isinstance(getattr(vp, "SHOT_EN_SPEC", ""), str)
      and len(vp.SHOT_EN_SPEC) > 100,
      len(getattr(vp, "SHOT_EN_SPEC", "")))

SYS = getattr(vp, "SHOT_DIRECTOR_SYSTEM", "") or ""
EN = getattr(vp, "SHOT_EN_SPEC", "") or ""

# ── B 组：三段式方法论 + 铁律 ──
check("B1 三段式·①主体（可辨识外观细节）", "① 主体" in SYS and "外观细节" in SYS)
check("B2 三段式·②动作（现在进行时、单动作）", "② 动作" in SYS and "现在进行时" in SYS)
check("B3 三段式·③环境+镜头（光线氛围）", "③ 环境" in SYS and "光线氛围" in SYS)
check("B4 铁律·单镜单一动作", "单镜单一动作" in SYS)
check("B5 铁律·首尾帧接力（同 scene 连续）", "首尾帧接力" in SYS)
check("B6 铁律·动作量与时长匹配", "动作量与时长匹配" in SYS)
check("B7 铁律·相邻镜运镜有变化", "运镜要有变化" in SYS)

# ── C 组：en 规范 + 负面清单 + few-shot ──
check("C1 负面清单·禁空泛形容词", "空泛形容词" in EN)
check("C2 负面清单·禁剪辑词（then/cut to）", "剪辑词" in EN)
check("C3 负面清单·禁画面内文字水印", "水印" in EN)
check("C4 负面清单·禁抽象概念/情绪名词", "抽象概念" in EN)
check("C5 few-shot 完整好例子（含可辨识细节 denim jacket）",
      "✅" in EN and "denim jacket" in EN)
check("C6 en 规范要求按三段式写", "三段式" in EN)

# ── D 组：行为级 —— 真的进了 payload ──
_captured = {}


def _fake_chat(cfg, messages, model=None, temperature=0.7,
               thinking=False, max_tokens=None):
    _captured["messages"] = messages
    return "[]"


_orig = vp._agnes_chat
vp._agnes_chat = _fake_chat
try:
    _fake_self = types.SimpleNamespace(
        cfg={}, ref_image_path=None, style_prompt="", portrait_mode=False,
        duration=5, smart=True, spec_text=lambda: "",
        # 用未绑定实例方法调用时，类上的静态方法不会自动挂到 fake 上，手动带上
        _parse_shots=vp.VideoPipeline._parse_shots,
    )
    vp.VideoPipeline._gen_shots(_fake_self, "测试剧本正文：一个人在海边日出时起身。", 6)
finally:
    vp._agnes_chat = _orig

_msgs = _captured.get("messages") or []
_sysm = ""
_userm = ""
for _m in _msgs:
    if isinstance(_m, dict) and _m.get("role") == "system":
        _sysm = _m.get("content") or ""
    elif isinstance(_m, dict) and _m.get("role") == "user":
        _userm = _m.get("content") or ""

check("D0 _gen_shots 确已发起模型调用（stub 抓到 messages）",
      bool(_msgs) and bool(_sysm) and bool(_userm))
check("D1 行为级：system 真带三段式方法论", "三段式" in _sysm, _sysm[:60])
check("D2 行为级：system 保留原叙事要求（无回归）",
      "全片同一主角贯穿" in _sysm and "有因果推进" in _sysm)
check("D3 行为级：user 真带 en 规范与负面清单",
      "三段式" in _userm and "空泛形容词" in _userm)
check("D4 行为级：user 真带 few-shot 范例", "denim jacket" in _userm)
check("D5 行为级：人设句不重复注入（只出现一次）",
      _sysm.count("你是专业的短视频分镜导演") == 1,
      _sysm.count("你是专业的短视频分镜导演"))
check("D6 行为级：镜头词表仍在（CAM_VOCAB 未被顶掉）",
      "镜头语言词汇表" in _userm)
check("D7 反向照妖镜：旧的裸 en 说明已从 prompt 移除",
      "1~2 句话，只写视觉内容" not in _userm)

print("\n结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
