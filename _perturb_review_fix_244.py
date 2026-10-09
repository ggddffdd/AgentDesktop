# -*- coding: utf-8 -*-
"""v4.244.0 扰动脚本：审查报告第二批修复（I-5/I-6/I-4/I-3/T-2/I-2尾巴）。

反向照妖镜：变异六处修复点，跑 tests/test_review_fix_244.py，确认判据必红（改坏必红）。
六个 case 分别针对六处修复，零哑弹。沿用「直接变异 + _perturb_guard 还原」模式，
变异标记统一用 `# 扰动：`（guard 约定）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
AGENT_TEXT = os.path.join(ROOT, "agent_text.py")
AGENT = os.path.join(ROOT, "agent.py")
INTENT = os.path.join(ROOT, "intent.py")
INTENT_DAG = os.path.join(ROOT, "intent_dag.py")
JUDGE = os.path.join(ROOT, "tests", "test_review_fix_244.py")

CASES = [
    # I-5：话术退回工具名 → 判据「clarify_reason 不含工具名」翻红
    ("V1 I-5 话术退回工具名", INTENT,
     '                _desc_parts.append(_d.split("（")[0] if "（" in _d else _d)',
     '                _desc_parts.append(_n)  # 扰动：话术退回工具名'),
    # I-6：加回裸字键「画」→ 「动画片」又造 image_gen → 判据翻红
    ("V2 I-6 加回裸字键「画」", INTENT_DAG,
     '("做图", "image_gen"),',
     '("做图", "image_gen"), ("画", "image_gen"),  # 扰动：裸字键复活'),
    # I-4：DISCUSS 分支退回 not req → 「分析下这个视频」又判 action → 判据翻红
    ("V3 I-4 DISCUSS 退回 not req", INTENT,
     "    elif weak_discuss:",
     "    elif weak_discuss and not req:  # 扰动：讨论被 req 架空"),
    # I-3：裸回指词退回一票否决 → 「明天什么时候下雨」又丢路由 → 判据翻红
    ("V4 I-3 裸回指词一票否决", AGENT_TEXT,
     "        return any(a in text for a in _REF_ANCHOR)",
     "        return True  # 扰动：裸回指词一票否决"),
    # T-2：不传 decs → 判据「批次级 decs 传给并发」翻红
    ("V5 T-2 不传 decs 给并发", AGENT,
     "self._run_concurrent(list(zip(tool_calls, decs)), mw, APP_DIR)",
     "self._run_concurrent(tool_calls, mw, APP_DIR)  # 扰动：不传 decs"),
    # I-2尾巴：疑问词判断退回未剥离 text → 「标题《如何》」又判疑问 → 判据翻红
    ("V6 I-2尾巴 疑问词不再剥离", AGENT_TEXT,
     '    if any(k in _stripped for k in ("怎么", "为什么", "为何", "是否", "是不是",',
     '    if any(k in text for k in ("怎么", "为什么", "为何", "是否", "是不是",  # 扰动：不再剥离'),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [AGENT_TEXT, AGENT, INTENT, INTENT_DAG]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            assert src.count(old) == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, src.count(old))
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
