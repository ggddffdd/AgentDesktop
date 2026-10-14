# -*- coding: utf-8 -*-
"""_perturb_intent_low_conf_260.py —— 零哑弹扰动（改坏必红）

对 intent.py 的低置信高风险澄清回退做三类源级变异：
  V1 让 _low_conf_high_risk_guard 的触发条件永不成立（主 return + reference 分支都不澄清）
  V2 删掉 reference 分支的 guard 接线（指着 Grokbot 视频问的疑问不再澄清）
  V3 阈值改 0.0（conf<0.0 永不成立，回退整体不触发）

确认 test_intent_low_conf_clarify_260.py 的对应断言改坏必红、还原必绿。
不依赖行为以外的任何东西（只观测判据的 FAIL 行）。

契约（tests/test_perturb_harness.py）：
  A2/A3 真实代码里 print("... PERTURB PASS=N FAIL=M ...")
  A4    有 sys.exit 非零退出口
  C1    接入 _perturb_guard（快照 + 三重还原 + 残留预检）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TESTS = [
    os.path.join(ROOT, "tests", "test_intent_low_conf_clarify_260.py"),
]
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


def run_tests():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    reds = []
    for t in TESTS:
        try:
            r = subprocess.run([PY, t], capture_output=True, text=True, cwd=ROOT,
                               timeout=300, env=env, encoding="utf-8",
                               errors="replace")
            reds += re.findall(r"(?:\[FAIL\]|FAIL)\s+([^\n]+)",
                                (r.stdout or "") + (r.stderr or ""))
        except Exception as e:
            reds.append("崩溃:%s" % e)
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (名称, 文件, 原串, 新串, 期望翻红的判据片段)
CASES = [
    # V1：helper 永不触发 → 主 return + reference 分支都不澄清（A1..A5 必红）
    ("V1 让 _low_conf_high_risk_guard 永不触发（主 return + reference 分支都不澄清）",
     "intent.py",
     "    if (not force\n"
     "            and kind in (KIND_QUESTION, KIND_UNKNOWN, KIND_DISCUSS, KIND_REFERENCE)\n"
     "            and conf < LOW_CONF_THRESHOLD\n"
     "            and any(t in HIGH_RISK_TOOLS for t in req)\n"
     "            and _is_capability_confirm_question(text)):",
     "    if False:  # 扰动：强制不触发回退",
     ["A1 动画疑问", "A2 动画疑问", "A3 视频疑问", "A4 图片疑问", "A5 动画能力确认"]),

    # V2：删 reference 分支 guard 接线 → 指着 Grokbot 视频问的「这种…你会做吗」不澄清（A1 必红），
    #     其余非 reference 疑问句(A2..A5)走主 return，仍绿，验证分支接线。
    ("V2 删 reference 分支的 guard 接线（指着现有产物问的疑问不澄清）",
     "intent.py",
     "            _gc, _gr, _go = _low_conf_high_risk_guard(\n"
     "                KIND_REFERENCE, None, _ref_req, 0.55, text)\n"
     "            if _gc:\n"
     "                _ref_intent.needs_clarification = True\n"
     "                _ref_intent.clarify_reason = _gr\n"
     "                _ref_intent.clarify_options = _go\n"
     "                _ref_intent.reason = \"引用语境+高风险工具疑问，澄清回退\"",
     "            # 扰动：删除 reference 分支 guard 调用（不澄清）",
     ["A1 动画疑问"]),

    # V3：阈值改 0.0 → conf<0.0 永不成立，回退整体不触发（A1..A5 必红）
    ("V3 阈值=0.0（conf<0.0 永不成立，回退不触发）",
     "intent.py",
     "LOW_CONF_THRESHOLD = 0.85",
     "LOW_CONF_THRESHOLD = 0.0  # 扰动：阈值失效",
     ["A1 动画疑问", "A2 动画疑问", "A3 视频疑问", "A4 图片疑问", "A5 动画能力确认"]),
]

HIT, MISS = [], []
try:
    for _n, fp, _o, _nw, _e in CASES:
        if fp not in _backup:
            _backup[fp] = read_raw(fp)
            _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        mut = src.replace(old, new, 1)
        write_raw(fp, (mut.replace("\n", "\r\n")
                       if _crlf[fp] else mut).encode("utf-8"))
        red = run_tests()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望含 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
