# -*- coding: utf-8 -*-
"""v4.247.0 扰动脚本：军团「UI 精简 + 管理命令对话化」。

反向照妖镜：变异三处修复点，跑 tests/test_legion_chat_ui.py，确认判据必红（改坏必红）。
三个 case 分别针对管理识别/纯文本前缀/管理路由，零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LEGION = os.path.join(ROOT, "legion.py")
CHAT = os.path.join(ROOT, "legion_chat.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_chat_ui.py")

CASES = [
    # V1：管理识别失效 → 判据「管理·项目信息」等 6 个正向全红
    ("V1 管理识别失效", LEGION,
     "    return _MANAGE_INTENTS.get(t)",
     "    return None  # 扰动：管理识别失效", False),
    # V2：纯文本前缀失效 → 判据「纯文本·你前缀 / PM前缀」翻红
    ("V2 纯文本前缀失效", CHAT,
     "        prefix = {\"你\": \"你：\", \"项目经理\": \"项目经理：\", \"成果\": \"成果：\"}.get(role)\n"
     "        if prefix:",
     "        prefix = {\"你\": \"你：\", \"项目经理\": \"项目经理：\", \"成果\": \"成果：\"}.get(role)\n"
     "        if False:  # 扰动：前缀失效", False),
    # V3：管理命令路由失效 → 判据「管理命令→回调」翻红
    ("V3 管理路由失效", CHAT,
     "        _mk = legion.parse_manage_intent(text)\n        if _mk:",
     "        _mk = legion.parse_manage_intent(text)\n        if False:  # 扰动：管理路由失效", False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [LEGION, CHAT]
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
            if replace_all:
                assert cnt >= 1, "old 串在 %s 未命中" % fp
                mutated = src.replace(old, new)
            else:
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
