# -*- coding: utf-8 -*-
"""v4.285.0 P2c 扰动：批内同名同参去重改坏必红。

P1 _run_serial 去重命中分支摘除（同参双发照单全收——v4.120 回潮）
P2 _seen_sigs 登记摘除（去重彻底失效）

跑 tests/test_tool_dedup_285.py。原串从真实文件取且唯一。
IO 纯 text mode（agent.py 磁盘为 CRLF，round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_tool_dedup_285.py")
F_AGENT = os.path.join(ROOT, "agent.py")

OLD_P1 = """            _sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if _sig in _seen_sigs:
                log.info("批内重复调用已去重: %s", name)
                self._handle_tool_result(tc, name, fn,
                                         "（与本次批内上一调用完全重复，已自动去重）",
                                         [], None)
                continue
            _seen_sigs.add(_sig)"""
NEW_P1 = """            _sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            _seen_sigs.add(_sig)  # 扰动：命中分支摘除"""

OLD_P2 = """    _seen_sigs = set()
        for _seq_idx, tc in enumerate(tool_calls):"""
NEW_P2 = """    _seen_sigs = None  # 扰动：登记失效
        for _seq_idx, tc in enumerate(tool_calls):"""

CASES = [
    ("P1 _run_serial 去重命中分支摘除", F_AGENT, OLD_P1, NEW_P1,
     ["A1 handler 只执行 2 次"]),
    ("P2 _seen_sigs 登记失效", F_AGENT, OLD_P2, NEW_P2,
     ["C1 _run_serial 有 _seen_sigs 去重"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_AGENT])
    with open(F_AGENT, "r") as fh:
        orig = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w") as fh:
                fh.write(orig.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w") as fh:
                fh.write(orig)
    finally:
        with open(F_AGENT, "w") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
