# -*- coding: utf-8 -*-
"""扰动验证 v4.223 结构化返回根治：退化契约判定 / 摘掉「猜的」标注 / 工厂不填错误码，
看判据是否翻红（哑弹 0 才算过）。

手法：备份原字节 → 改坏 → 跑 tests/test_structured_return_223.py → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_structured_return_223.py")
FILES = ["tool_contract.py"]
_backup = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return re.findall(r"\[FAIL\] ([^\n]+)", out)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

TC = "tool_contract.py"

CASES = [
    ("from_legacy 不走显式结局契约（退回按文案猜）", TC,
     "            if _ok is None and name:\n"
     "                _ok = resolve_ok(name, msg, args)\n"
     "                if _ok is not None:\n"
     "                    _verified = True",
     "            if False:\n"
     "                pass",
     ["SR1 文件真落地：即使文案说失败，ok 仍为 True"]),
    ("摘掉「结论是猜的」标注（verified 仍标 True）", TC,
     '                _verified = False\n'
     '                _ecode = "INFERRED" if _ok else "UNVERIFIED_OUTCOME"',
     '                _verified = True\n'
     '                _ecode = None',
     ["SR2 走 _infer_ok 兜底时 verified=False",
      "SR2 走 _infer_ok 兜底时 error_code=INFERRED"]),
    ("fail() 不再填 error_code/retryable", TC,
     '        return cls(ok=False, msg=msg, error=error or msg,\n'
     '                   error_code=error_code or "TOOL_FAILED",\n'
     '                   retryable=retryable, verified=True, **kw)',
     '        return cls(ok=False, msg=msg, error=error or msg, **kw)',
     ["SR4 fail() 填 error_code 且 verified=True",
      "SR4 fail() 可指定 error_code/retryable"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        is_crlf = "\r\n" in _backup[fp].decode("utf-8")
        patched = src.replace(old, new, 1)
        write_raw(fp, (patched.replace("\n", "\r\n") if is_crlf
                       else patched).encode("utf-8"))
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
