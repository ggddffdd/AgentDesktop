# -*- coding: utf-8 -*-
"""扰动验证 v4.227 P2-1：不可信边界加固。

手法：备份原字节 → 逐条退化 untrusted_boundary.py / skill_loader.py 的加固点 →
跑 test_untrusted_hardening_227.py → 期望对应判据翻红 → 恢复原字节。

验收标准是**哑弹清零**：每条变异都必须真的让判据翻红。
本脚本专设三条「反向照妖镜」，防止判据写成「怎么改都红」的假红：
  · V7 中和改成「什么都不做」→ B1 组必须红（证明 B 组不是恒真）
  · V8 清单改成「全部判不可信」→ A3 必须红（防偷懒全包）
  · V9 中和改成「连非边界词也一起改」→ B3 必须红（证明 B3 精确性非恒真）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_untrusted_hardening_227.py")
FILES = ["untrusted_boundary.py", "skill_loader.py", "agent.py"]
_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def clean_pycache():
    """清掉全仓 __pycache__（跑前跑后各一次，防跨用例污染）。

    被扰动的 untrusted_boundary / skill_loader 在判据里是 import 进来的，
    .pyc 比源码旧就直接用缓存 → 变异等于没发生 → 红 0 条。
    跑完也必须清：否则上一条用例最后写下的 .pyc 成了下一条的起始状态，
    实测会让后一条用例读到**上一条的残留红点**，误判成「判据抓不住」。
    """
    for _root, _dirs, _files in os.walk(ROOT):
        if os.path.basename(_root) == "__pycache__":
            for _f in _files:
                if _f.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(_root, _f))
                    except Exception:
                        pass


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONDONTWRITEBYTECODE="1")
    clean_pycache()
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    reds = re.findall(r"\[FAIL\] ([^\n]+)", out)
    clean_pycache()
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

UB = "untrusted_boundary.py"

# 中和正则的真实形态（改源码时必须同步，否则这里会静默 SKIP）
_NEUTRALIZE_OLD = (
    '_NEUTRALIZE_RE = re.compile(\n'
    '    r"<(\\s*/?\\s*untrusted(?=[\\s/>\\"\'_]|\\Z))", re.IGNORECASE)'
)
_NEUTRALIZE_NOOP = "_NEUTRALIZE_RE = re.compile(r'(?!x)x')"

_IS_BODY_OLD = (
    '    n = str(name or "").strip()\n'
    '    if not n:\n'
    '        return False\n'
    '    if any(n.startswith(p) for p in _UNTRUSTED_PREFIXES):\n'
    '        return True\n'
    '    return n in _UNTRUSTED_EXACT'
)

CASES = [
    # ---- V1 清单退回 8 项枚举（v4.222 原状）：browser_*/legion_* 等全部失守 ----
    ("V1 前缀规则失效（退回枚举，browser_*/legion_* 失守）",
     UB,
     '_UNTRUSTED_PREFIXES = (\n'
     '    "browser_",     # 浏览器族：正文/标题/输入框内容全来自网页\n'
     '    "legion_",      # 军团子代理产出：里面可能转述它读到的网页\n'
     ')',
     '_UNTRUSTED_PREFIXES = ()',
     ["A2 报告点名的 16 个漏项全部覆盖"]),

    # ---- V2 只删动作留条件：删掉 exact 判定这一行 ----
    ("V2 只删动作留条件（删 exact 判定行）",
     UB,
     '    if any(n.startswith(p) for p in _UNTRUSTED_PREFIXES):\n'
     '        return True\n'
     '    return n in _UNTRUSTED_EXACT',
     '    if any(n.startswith(p) for p in _UNTRUSTED_PREFIXES):\n'
     '        return True',
     ["A1 v4.222 既有 8 项仍全部判定为不可信"]),

    # ---- V3 中和整个失效（防伪造闭合的洞重开）----
    ("V3 中和整体失效（伪造闭合标签不再钝化）",
     UB, _NEUTRALIZE_OLD, _NEUTRALIZE_NOOP,
     ["A6 越狱内容被包后全文只有 1 个真闭合标签",
      "B2 七种伪造形态全部命中（大小写/空白/截断）"]),

    # ---- V4 技能侧退回自带标签字面量（两处漂移 + 技能侧越狱洞重开）----
    # ⚠️ 原串只取**代码两行**，不碰 docstring —— docstring 里有中文标点与
    #    Markdown 记号，逐字节抄过来极易差一个字符（本次首跑就 SKIP 了）。
    # ⚠️ 变异体里的 `\n` 必须写成字面反斜杠+n：这里外层已是普通字符串，
    #    若写成真实换行会写坏源文件（本次踩过：py_compile 当场报
    #    unterminated string literal）。**不要用 heredoc 临时脚本手工改文件** ——
    #    还原顺序出错会把损坏版本写回磁盘。
    ("V4 技能包装退回自带字面量（不走统一中和）",
     "skill_loader.py",
     '    from untrusted_boundary import wrap_skill_prompt_text\n'
     '    return wrap_skill_prompt_text(text, name)',
     '    from untrusted_boundary import wrap_skill_prompt_text\n'
     '    if False:  # 扰动\n'
     '        return wrap_skill_prompt_text(text, name)\n'
     '    return \'<untrusted skill="%s">\\n%s\\n</untrusted skill>\' % (name, text)',
     ["A9 技能包装也防伪造闭合（全文只有 1 个真闭合）",
      "A9b 技能侧伪造闭合标签已钝化",
      "A9c 薄封装转发到统一中和实现（未自带标签字面量）",
      "C4b skill_loader 不再自带标签字面量（防两处漂移）"]),

    # ---- V5 agent.py 恢复第二份清单（判定真源不再唯一）----
    ("V5 agent.py 恢复第二份 _UNTRUSTED_TOOLS 清单",
     "agent.py",
     'from untrusted_boundary import (  # noqa: F401',
     '_UNTRUSTED_TOOLS = frozenset({"web_fetch", "web_search"})\n\n\n'
     'from untrusted_boundary import (  # noqa: F401',
     ["C3 agent.py 已无第二份 _UNTRUSTED_TOOLS 清单"]),

    # ---- V6 越狱不再留痕（闸门失效只有包装器知道）----
    #     注意：这正是 v4.227 首版 C5 判据红 0 的原因 —— 那条判据只查源码里
    #     有没有「伪造边界标签」这句中文，而本变异把整条 warning 调用换掉时
    #     连中文一起删了。C5 已改成行为级（真造越狱内容 + 挂 logging 捕获器）。
    ("V6 伪造标签命中不再 log.warning 留痕",
     UB,
     'logging.getLogger("dsdesktop").warning(\n'
     '                "不可信内容(%s)内含 %d 处伪造边界标签，已中和", source, forged)',
     'logging.getLogger("dsdesktop").warning(\n'
     '                "x")',
     ["C5 越狱命中会真的记 warning（行为级，非源码找串）"]),

    # ---- V7 照妖镜①：中和改成「什么都不做」，B 组必须红 ----
    #     ⚠️ 期望里**不能**写 B1：B1 断言的是「正常 HTML 不被改动」，
    #     中和失效后它照样成立 → 保持绿。把它写进期望就是「把本来就该绿的
    #     断言写进期望」，会让真 HIT 被判成 MISS（首跑就踩了）。
    ("V7 照妖镜：中和变成恒不匹配（证明 B 组非恒真）",
     UB, _NEUTRALIZE_OLD, _NEUTRALIZE_NOOP,
     ["B2 七种伪造形态全部命中（大小写/空白/截断）",
      "A6b 伪造的闭合标签已钝化成 &lt; 文本"]),

    # ---- V8 照妖镜②：清单改成「全部判不可信」，A3 必须红 ----
    ("V8 照妖镜：判定改成恒真（全包，防偷懒修法）",
     UB,
     '    n = str(name or "").strip()\n'
     '    if not n:\n'
     '        return False',
     '    n = str(name or "").strip()\n'
     '    if n:\n'
     '        return True\n'
     '    if not n:\n'
     '        return False',
     ["A3 可信/本工具自产的工具不被误判（未偷懒全包）"]),

    # ---- V9 照妖镜③：中和放宽到误伤普通词，B3 必须红 ----
    ("V9 照妖镜：中和放宽（误伤 <untrustedx> 普通词）",
     UB, _NEUTRALIZE_OLD,
     '_NEUTRALIZE_RE = re.compile(\n'
     '    r"<(\\s*/?\\s*untrusted)", re.IGNORECASE)',
     ["B3 非边界形态不被误吃（标签边界精确）"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
