# -*- coding: utf-8 -*-
"""v4.253.0 扰动脚本：军团收尾优化（死代码清理 + 授权超时提醒 + 抓取去噪）。

反向照妖镜：变异三处，跑 tests/test_legion_polish_253.py，确认判据必红（改坏必红）。
V1 提醒文本失效；V2 提醒不接入 _request_auth；V3 去噪失效。零哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
WORKER = os.path.join(ROOT, "legion_worker.py")
BCT = os.path.join(ROOT, "browser_control_tools.py")
JUDGE = os.path.join(ROOT, "tests", "test_legion_polish_253.py")

CASES = [
    # V1：提醒文本失效 → 判据「超时提醒·文本含剩余秒数」翻红
    ("V1 提醒文本失效", WORKER,
     "    return (f\"⏳ 授权即将超时（还剩 {remain} 秒）——请尽快写「放行 / 打回 / 终止」，\"\n"
     "            f\"否则将按不授权处理、停止推进。\")",
     "    return \"x\"  # 扰动：提醒文本失效", False),
    # V2：提醒不接入 _request_auth → 判据「超时提醒·_request_auth接入」翻红
    ("V2 提醒不接入", WORKER,
     "                _warned = True\n"
     "                try:\n"
     "                    self.log_line.emit(auth_warn_text(_remain))",
     "                _warned = True\n"
     "                try:\n"
     "                    pass  # 扰动：不发提醒", False),
    # V3：去噪失效 → 判据「去噪·砍导航短句」翻红
    ("V3 去噪失效", BCT,
     "    out = '\\n'.join(lines).strip()\n"
     "    return out if out else text",
     "    out = '\\n'.join(lines).strip()\n"
     "    return text  # 扰动：去噪失效", False),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [WORKER, BCT]
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
