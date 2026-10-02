# -*- coding: utf-8 -*-
"""临时脚本：对 ui.py 做三种"真实会犯的错"，验证 test_welcome_ia_199 的判据会变红。

跑完自动按原文恢复（含 md5 校验），用完删除。
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(ROOT, "ui.py")
TEST = os.path.join(ROOT, "tests", "test_welcome_ia_199.py")

_orig = open(UI, encoding="utf-8", newline="").read()
_h0 = hashlib.md5(_orig.encode("utf-8")).hexdigest()


def run():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    r = subprocess.run([sys.executable, TEST], capture_output=True, text=True, env=env)
    lines = r.stdout.splitlines()
    fails = [ln.strip() for ln in lines if "FAIL]" in ln]
    tail = [ln for ln in lines if ln.strip().startswith("汇总")]
    return fails, (tail[-1] if tail else "no-summary"), r.returncode


base_fails, base_tail, _ = run()
print("BASELINE:", base_tail, "returncode=0 说明基线全绿")

CASES = [
    # 注意 ui.py 是 CRLF，删除整行要用 \s* 而不是写死 \n
    ("① 改了卡片却忘了改 prompts（多一张卡 → 点了没反应）",
     lambda s: re.sub(r'"用军团编排完成这个任务：",\s*', '', s, count=1)),
    ("② 卡片色名拼错 purple→purpel（不崩溃，静默变默认色）",
     lambda s: s.replace('("purple", "数字人"', '("purpel", "数字人"')),
    ("③ placeholder 改回旧文案（承诺搜文件/工具，实际搜不到）",
     lambda s: s.replace('"搜索当前对话…（Enter 下一处）"', '"搜索对话、文件、工具…"')),
    ("④ 最近对话行高改回 40px（摘要会被裁）",
     lambda s: s.replace("            row.setFixedHeight(56)",
                         "            row.setFixedHeight(40)")),
]

for name, fn in CASES:
    patched = fn(_orig)
    if patched == _orig:
        print("\n### %s\n   ⚠️ 扰动文本没命中，用例失效" % name)
        continue
    open(UI, "w", encoding="utf-8", newline="").write(patched)
    try:
        fails, tail, rc = run()
    finally:
        open(UI, "w", encoding="utf-8", newline="").write(_orig)
    print("\n### %s" % name)
    print("   %s  returncode=%d" % (tail, rc))
    for f in fails:
        print("   ", f)
    if not fails:
        print("    ⛔ 没有任何判据变红 —— 这条性质没被守住")

_h1 = hashlib.md5(open(UI, encoding="utf-8", newline="").read().encode("utf-8")).hexdigest()
print("\nui.py 已恢复：%s  md5=%s" % (_h0 == _h1, _h0))
