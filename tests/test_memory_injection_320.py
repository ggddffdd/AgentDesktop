# -*- coding: utf-8 -*-
"""v4.320.0 P3-2 判据：召回注入改隐藏注记（缓存前缀稳定优先）。

工单 P3-2：召回摘要不进 system prompt，作为隐藏注记附到会话；≤3000 字符；
含来源标注；system prompt 字节数不变（缓存命中不打破）；Agent 能引用记忆。

项目现状（v4.318）：prompt_cache 已有 CONTEXT_MARK 隐藏注记机制 + stable/dynamic
分离 + 末尾注入（缓存前缀稳定），记忆已在 dynamic 里、不进 stable。本轮收敛两点：
  ① recall_memory 默认上限 4000→3000（对齐工单）；
  ② 召回注入带明确来源标注。
判据三层：A 纯函数行为；B 源码契约语义锚定；C 缓存前缀/隐藏注记结构实测。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


MS_SRC = read_src(os.path.join(ROOT, "memory_store.py"))
UI_SRC = read_src(os.path.join(ROOT, "ui.py"))
PC_SRC = read_src(os.path.join(ROOT, "prompt_cache.py"))
try:
    import memory_store as msmod
    import prompt_cache as pcmod
except Exception as e:
    msmod = pcmod = None
    print("import ERR", e)


print("== A 层：召回上限与来源标注 ==")
# A1 recall_memory 默认上限 3000
import inspect
if msmod is not None:
    sig = inspect.signature(msmod.recall_memory)
    d = sig.parameters.get("max_chars")
    check("A1  recall_memory 默认 max_chars=3000",
          d is not None and d.default == 3000, "default=%r" % (d.default if d else None))
else:
    check("A1  recall_memory 默认 max_chars=3000", False, "module missing")

# A2-A3 format_recall_note 纯函数：来源标注 + 上限
fmt = getattr(msmod, "format_recall_note", None) if msmod else None
check("A2  format_recall_note 存在", callable(fmt))
if callable(fmt):
    note = fmt("【相关记忆】\n用户住在昆明", max_chars=3000)
    check("A3  注记含来源标注", "来源" in note, repr(note[:80]))
    long_note = fmt("甲" * 10000, max_chars=3000)
    check("A4  注记 ≤3000 字符", len(long_note) <= 3000, "len=%d" % len(long_note))
    check("A5  空输入返回空", fmt("", max_chars=3000) == "")

print("== B 层：源码契约（语义锚定） ==")
import re as _re
# B1 _prompt_section_memory 调用上限 3000
m = _re.search(r"def _prompt_section_memory\([^\n]*\)(?:(?!^    def )[\s\S])*", UI_SRC, _re.M)
PM_BODY = m.group(0) if m else ""
check("B1  注入处用 3000 上限", bool(_re.search(r"max_chars\s*=\s*3000", PM_BODY))
      or bool(_re.search(r"format_recall_note", PM_BODY)))
# B2 记忆不进 stable 前缀：_split_system_prompt 里 mem 追加到 dyn_parts
m2 = _re.search(r"def _split_system_prompt\([^\n]*\)(?:(?!^    def )[\s\S])*", UI_SRC, _re.M)
SP_BODY = m2.group(0) if m2 else ""
mem_line = bool(_re.search(r"mem\s*=\s*self\._prompt_section_memory\(\)", SP_BODY))
dyn_append = bool(_re.search(r"dyn_parts\.append\(mem\)", SP_BODY))
check("B2  mem 进入 dynamic 而非 stable", mem_line and dyn_append)
# B3 隐藏注记结构存在（CONTEXT_MARK）
check("B3  CONTEXT_MARK 隐藏注记结构存在", "CONTEXT_MARK" in PC_SRC)

print("== C 层：缓存前缀 / 隐藏注记结构实测 ==")
if pcmod is not None:
    # C1 动态背景作为独立 user 消息附在历史末尾（不进 system、不插中间）
    stable = "稳定系统提示"
    hist = [{"role": "user", "content": "历史问题"},
            {"role": "assistant", "content": "历史回答"}]
    msgs = pcmod.with_dynamic_context(pcmod.prefix_messages(stable, hist), "记忆背景")
    check("C1  首条仍是稳定 system", msgs[0]["role"] == "system" and msgs[0]["content"] == stable)
    check("C2  动态注记为末尾独立 user 消息",
          len(msgs) == 4 and msgs[-1]["role"] == "user"
          and msgs[-1]["content"].startswith(pcmod.CONTEXT_MARK))
    check("C3  注记消息带 _internal 标记（Agent 不可见约定）",
          msgs[-1].get("_internal") is True)
    check("C4  cacheable_prefix 剥掉注记后前缀稳定",
          pcmod.cacheable_prefix(msgs) == [msgs[0], msgs[1], msgs[2]])
    stripped = pcmod.strip_internal(msgs)
    check("C5  strip_internal 去掉 _internal 键",
          all("_internal" not in m for m in stripped) and len(stripped) == 4)
else:
    for n in ["C1", "C2", "C3", "C4", "C5"]:
        check(n + "  prompt_cache 可用", False, "module missing")

print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
