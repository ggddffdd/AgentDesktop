# -*- coding: utf-8 -*-
"""v4.254.0 扰动脚本：意图识别补强 A 包（A1 否定洞 / A2 词表归一 / A3 混合指令 / A4 澄清放宽）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_intent_strong_254.py，
确认判据必红（改坏必红、零哑弹）。

V1 工具动词否定不接入 _neg_hits  → 「别搜了」重新被强路由 web_search（安全洞复活）
V2 _norm_verb 不接入 _route_force_tool → 「用Agnes生视频」force=None（活没干）
V3 _norm_verb 前字闭集豁免失效   → 「女生图案设计」被规范成「女生成图案设计」
V4 route_judge 不共享 _MEDIA_PHRASE_SHARED → 两层词表再次漂移
V5 _REAL_TOOL_KW 移除配图信号    → 「写文章并配张封面图」重新被判纯文本（封面不生成）
V6 detect_implicit_imperative 不接入 classify → 「表格第三列对不上」不再反问
V7 隐含祈使长度闸失效            → 自动化任务正文每轮被反问打断（任务卡死）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
IG = os.path.join(ROOT, "intent_guard.py")
AT = os.path.join(ROOT, "agent_text.py")
RJ = os.path.join(ROOT, "route_judge.py")
IT = os.path.join(ROOT, "intent.py")
JUDGE = os.path.join(ROOT, "tests", "test_intent_strong_254.py")

CASES = [
    # V1：工具动词否定不接入 → A1「别搜了/不查了/别删了…」全部翻红
    ("V1 工具动词否定失效", IG,
     "    for m in _NEG_TOOL_VERB_RE.finditer(text):",
     "    for m in ():  # 扰动：工具动词否定失效"),
    # V2：不归一 → A2「用Agnes生视频/生一张猫的图片/帮我生个视频/生图」全部翻红
    ("V2 词表归一不接入", AT,
     "    text = _norm_verb(text)",
     "    text = text  # 扰动：不归一"),
    # V3：前字闭集豁免失效 → A2R「女生图案设计/生产车间视频/生日快乐」翻红
    ("V3 前字豁免失效", AT,
     "            if i > 0 and text[i - 1] in _SHENG_NOT_BEFORE:",
     "            if False:  # 扰动：前字豁免失效"),
    # V4：route_judge 不走共享词表 → A2N「NEEDS_TOOL_EXTRA 全量包含共享子集」翻红
    ("V4 共享词表断开", RJ,
     "NEEDS_TOOL_EXTRA = _MEDIA_SHARED + (",
     "NEEDS_TOOL_EXTRA = (  # 扰动：不共享"),
    # V5：配图信号移除 → A3「混合指令不豁免」「needs_action=True」翻红
    ("V5 配图信号移除", AT,
     "    \"配图\", \"配张图\", \"配张封面\", \"配个封面\", \"封面图\", \"生成封面\", \"做张封面\",\n"
     "    \"出张图\", \"配几张\",",
     "    # 扰动：配图信号移出"),
    # V6：隐含祈使不接入 classify → A4 四条「陈述异常」不再反问，全部翻红
    ("V6 隐含祈使不接入", IT,
     "    if not needs_clarify and kind in (KIND_UNKNOWN, KIND_DISCUSS, KIND_QUESTION):",
     "    if False:  # 扰动：不接隐含祈使"),
    # V8：「只X不Y」并列约束豁免失效 → A1R「只看不改」翻红，且全量回归
    #     test_no_stall_186「诊断类转 True：只看不改」跟着红（真实踩中的回归）
    ("V8 只…不… 豁免失效", IG,
     "        if m.group(1) in (\"不\", \"不用\", \"不要\") and \\\n"
     "                \"只\" in text[max(0, i - _NEG_ONLY_WINDOW):i]:\n"
     "            continue          # 「只X不Y」：限定做法范围，非喊停",
     "        if False:  # 扰动：只…不… 豁免失效\n"
     "            continue"),
    # V7：长度闸失效 → A4S「长文本不澄清」翻红（自动化任务会被反复反问卡死）
    ("V7 长度闸失效", IT,
     "        if len(text.strip()) > _IMPLICIT_MAX_LEN:",
     "        if False:  # 扰动：长度闸失效"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [IG, AT, RJ, IT]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old in [(c[0], c[1], c[2]) for c in CASES]:
            new = dict((c[0], c[3]) for c in CASES)[name]
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1200])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
