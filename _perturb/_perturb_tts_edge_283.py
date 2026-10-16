# -*- coding: utf-8 -*-
"""v4.283.1 扰动：edge-tts 403 判据「改坏必红」验证。

按 _perturb_guard 统一契约：
- 每项扰动改坏源码/配置 → 跑判据必须 FAIL → 还原 → 复跑必须 PASS。
契约：文本模式读写（intent/requirements 均为 CRLF 风格时以 anchor 命中为准，
命中 0 条 = SKIP = 哑弹，本脚本按 FAIL 处理，绝不静默放过）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(ROOT, "requirements.txt")
VOICE = os.path.join(ROOT, "voice.py")
TEST = os.path.join(ROOT, "tests", "test_tts_edge_version_283.py")
PY = sys.executable

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("PASS: %s" % name)
    else:
        _f += 1
        print("FAIL: %s | %s" % (name, detail))


def run_test():
    r = subprocess.run([PY, TEST], capture_output=True, text=True,
                       timeout=120, cwd=ROOT)
    return "FAIL=0" in r.stdout and r.returncode == 0


def perturb(path, old, new):
    d = open(path, "rb").read()
    needle = old.encode()
    assert needle in d, "anchor not found in %s: %r" % (path, old[:60])
    open(path, "wb").write(d.replace(needle, new.encode(), 1))
    return d


def restore(path, data):
    open(path, "wb").write(data)


# ---------- P1: edge-tts 版本回退到 7.2.7（403 版）→ 必须红 ----------
req_data = open(REQ, "rb").read()
perturb(REQ, "edge-tts==7.3.1", "edge-tts==7.2.7")
check("P1 版本回退 7.2.7 → 判据红", not run_test(), "G1b 没红")
restore(REQ, req_data)
check("P1 还原后复跑绿", run_test())

# ---------- P2: requirements 漏列 edge-tts（整行删）→ 必须红 ----------
req_lines = open(REQ, "rb").read().splitlines(True)
dropped = [l for l in req_lines if l.strip() == b"edge-tts==7.3.1"]
assert len(dropped) == 1, "anchor line count != 1"
kept = [l for l in req_lines if l.strip() != b"edge-tts==7.3.1"]
open(REQ, "wb").writelines(kept)
check("P2 漏列 edge-tts → 判据红", not run_test(), "G1 没红")
restore(REQ, req_data)
check("P2 还原后复跑绿", run_test())

# ---------- P3: tts_failure_is_quota 改名（403 识别器丢失）→ 必须红 ----------
voice_data = open(VOICE, "rb").read()
perturb(VOICE, "def tts_failure_is_quota(", "def tts_is_quota_renamed(")
check("P3 403 识别器丢失 → 判据红", not run_test(), "G4 没红")
restore(VOICE, voice_data)
check("P3 还原后复跑绿", run_test())

print("PERTURB PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
