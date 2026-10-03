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

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检。
# 本脚本是**模块顶层执行**（import 完就开始改 ui.py），只在 case 内用 try/finally 还原；
# 而 Python 默认的 SIGTERM 处理器直接终止进程（不抛异常、不走 finally），
# 被超时强杀时 ui.py 会留在变异态（真实事故见 _perturb_guard.py docstring）。
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm([UI])

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

BAD = []          # 退出码契约用：记录「扰动没生效 / 没打红任何判据」的用例
for name, fn in CASES:
    patched = fn(_orig)
    if patched == _orig:
        print("\n### %s\n   ⚠️ 扰动文本没命中，用例失效" % name)
        BAD.append(name)
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
        BAD.append(name)

_h1 = hashlib.md5(open(UI, encoding="utf-8", newline="").read().encode("utf-8")).hexdigest()
print("\nui.py 已恢复：%s  md5=%s" % (_h0 == _h1, _h0))

# 退出码契约（2026-10-03 补）：哑弹必须能被自动化发现。
# 本脚本原先**恒 return 0** —— 「扰动文本没命中」与「没有任何判据变红」都只打印一行字，
# 门禁/巡检拿到 exit=0 就以为通过；全仓巡检时这种静默失效只能靠人工读输出才发现，
# 而那恰恰是最不会被做的事。md5 不一致则是更严重的事故（源码没还原），单独给码。
if _h0 != _h1:
    print("**源码未按原样恢复，请检查！**")
    sys.exit(2)
# 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总扰动结果，
# 与 tests/ 下的判据套件同格式（见 run_all.py 头部约定第 3 条）。
# 语义：PASS=命中（判据确实转红）的 case 数，FAIL=哑弹数。放在 md5 还原校验之后，
# 因为「源码没还原」是事故（exit 2），不该再报出好看的 PASS。
print("PERTURB PASS=%d FAIL=%d" % (len(CASES) - len(BAD), len(BAD)))
if BAD:
    print("\n失效用例（%d 条）：%s" % (len(BAD), "、".join(BAD)))
    sys.exit(1)
sys.exit(0)
