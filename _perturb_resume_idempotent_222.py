# -*- coding: utf-8 -*-
"""扰动验证 P2 断点幂等：退化 ledger 登记 / 恢复查重，看判据是否翻红。

手法：备份原字节 → 退化 _record_exec_ledger（不登记）/ _is_resume_dup（恒 False）→
跑 test_resume_idempotent_222.py → 期望 A / B1 分别翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_resume_idempotent_222.py")
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
    ("_record_exec_ledger 退化（不登记）",
     "agent.py",
     '    def _record_exec_ledger(self, name, args_sig, ok):\n'
     '        """v4.222：登记一次工具执行，供断点恢复时幂等查询（防重复执行副作用）。"""\n'
     '        if not hasattr(self, "_exec_ledger"):\n'
     '            self._exec_ledger = []\n'
     '        self._exec_ledger.append({\n'
     '            "name": name,\n'
     '            "args_hash": _tool_args_hash(name, args_sig),\n'
     '            "side_effect_status": "done" if ok else "failed",\n'
     '            "finished_at": time.time(),\n'
     '        })',
     '    def _record_exec_ledger(self, name, args_sig, ok):\n'
     '        pass',
     ["A 执行 ledger 登记成功"]),
    ("_is_resume_dup 退化（恒 False）",
     "agent.py",
     '    def _is_resume_dup(self, name, args_sig):\n'
     '        """v4.222：恢复时查询——该不可幂等工具是否已在前次执行过（同参数）。"""\n'
     '        if name not in _NON_IDEMPOTENT_TOOLS:\n'
     '            return False\n'
     '        _done = getattr(self, "_resume_done_hashes", None)\n'
     '        if not _done:\n'
     '            return False\n'
     '        return _tool_args_hash(name, args_sig) in _done',
     '    def _is_resume_dup(self, name, args_sig):\n'
     '        return False',
     ["B1 不可幂等工具已执行"]),
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
