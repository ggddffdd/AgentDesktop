# -*- coding: utf-8 -*-
"""v4.272.0 扰动：setHtml 的 data:text/html 特例，以及白名单仍拒绝 data:。

挖掉放行、把点击/子框架也放行、短路特例、短路页面调用、或把 data: 放进
白名单，tests/test_webengine_sethtml_272.py 必须翻红。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
POL = os.path.join(ROOT, "webengine_policy.py")
CHAT = os.path.join(ROOT, "chat_web.py")
JUDGE = os.path.join(ROOT, "tests", "test_webengine_sethtml_272.py")

CASES = [
    ("V1 setHtml 的 data:text/html 也拒绝", POL,
     '    return low.startswith("data:text/html")\n',
     '    return False  # 扰动：setHtml 的 data:text/html 也拒绝\n',
     False),
    ("V2 点击和子框架的 data:text/html 也放行", POL,
     "    if not is_main_frame or is_link_click:\n",
     "    if False:  # 扰动：点击和子框架的 data:text/html 也放行\n",
     False),
    ("V3 关掉 setHtml 特例", POL,
     "    if allow_sethtml_navigation(url, is_main_frame, is_link_click):\n",
     "    if False and allow_sethtml_navigation(url, is_main_frame, is_link_click):  # 扰动：关掉 setHtml 特例\n",
     False),
    ("V4 聊天页白名单调用被短路", CHAT,
     "            if not _wep.decide_chat_whitelist(\n",
     "            if False and _wep.decide_chat_whitelist(  # 扰动：白名单调用被短路\n",
     False),
    ("V5 data: 放进白名单", POL,
     '    if low.startswith(("javascript:", "data:", "blob:", "vbscript:")):\n'
     "        return False\n",
     '    if low.startswith("data:"):\n'
     "        return True  # 扰动：data: 放进白名单\n"
     '    if low.startswith(("javascript:", "blob:", "vbscript:")):\n'
     "        return False\n",
     False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [POL, CHAT]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new, replace_all in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (os.path.basename(fp), cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and ("FAIL" in out)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
