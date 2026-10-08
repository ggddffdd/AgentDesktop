# -*- coding: utf-8 -*-
"""扰动验证 P1 任务验收：删掉 _infer_outcome 末段产物验收分支，看判据 B 是否翻红。

手法：备份原字节 → 原位删除产物级验收分支（声明了交付物但没落地仍判 success）→
跑 test_task_outcome_acceptance_222.py → 期望 B 翻红 → 恢复原字节。

v4.234.1：v4.195 九态重构误删了该分支（判据 B 长期被哑弹 SKIP 掩盖成「绿」），
本轮在 agent.py::_infer_outcome 恢复该闸门，本扰动锚点同步重定到恢复后的分支。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_task_outcome_acceptance_222.py")
FILES = ["agent.py"]
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


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    return red


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

CASES = [
    ("_infer_outcome 产物验收分支被移除",
     "agent.py",
     '        # v4.234.1（P1 回归修复）：声明了交付物但没真实落地 → partial。\n'
     '        # v4.195 九态重构时误删了 v4.222 的产物级验收，导致「模型说写完了但文件没生成」\n'
     '        # 被记入 success 轨迹。success 落地前补回该闸门。\n'
     '        _dlv = getattr(self, "_deliverables", None) or []\n'
     '        if _dlv:\n'
     '            _unmet = [d for d in _dlv if not _deliverable_satisfied(d)]\n'
     '            if _unmet:\n'
     '                return "partial"\n',
     '',
     ["B 声明产物未落地"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        write_raw(fp, (src.replace(old, new, 1).replace("\n", "\r\n")
                        if _crlf[fp] else src.replace(old, new, 1)).encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
