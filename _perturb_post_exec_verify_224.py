# -*- coding: utf-8 -*-
"""扰动验证 P2 执行后验证：退化验证层，看判据是否翻红。

手法：备份原字节 → 退化 `apply_post_verification` / 放开「只降级不升级」/
让「不表态」也强行表态 → 跑 test_post_exec_verify_224.py → 期望对应判据翻红 → 恢复。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_post_exec_verify_224.py")
FILES = ["tool_contract.py"]
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
    ("apply_post_verification 退化（完全不验证）",
     "tool_contract.py",
     '    v = verify_after(name, args, tr)',
     '    return False\n    v = verify_after(name, args, tr)',
     ["PV2-1 验证不通过", "PV5-2 执行后验证不通过"]),
    ("验证通过时允许「升级」（把失败翻成成功）",
     "tool_contract.py",
     '    if vok:\n'
     '        # 验证通过：只留证据，绝不把失败翻成成功\n'
     '        try:\n'
     '            if ev and not getattr(tr, "evidence", None):\n'
     '                tr.evidence = ev\n'
     '        except Exception:\n'
     '            pass\n'
     '        return False',
     '    if vok:\n'
     '        try:\n'
     '            tr.ok = True\n'
     '        except Exception:\n'
     '            pass\n'
     '        return False',
     ["PV2-6 验证通过也不许把失败翻成成功"]),
    ("验证器「不表态」也强行表态（破坏 fail-open）",
     "tool_contract.py",
     '    if r is None:\n'
     '        return None\n'
     '    if isinstance(r, tuple):',
     '    if r is None:\n'
     '        return (True, "")\n'
     '    if isinstance(r, tuple):',
     ["PV3-4 验证器不表态", "PV4-1 name 为纯数字"]),
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
