# -*- coding: utf-8 -*-
"""扰动验证 v4.223 参数校验补全：逐个维度退化，看判据是否翻红（哑弹 0 才算过）。

手法：备份原字节 → 把某一维度改坏 → 跑 tests/test_param_validation_223.py →
期望对应 PV 判据翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_param_validation_223.py")
FILES = ["tool_contract.py", "tools.py"]
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
TO = "tools.py"

CASES = [
    ("退化路径标准化（path 不再归一）", TC,
     "            out[k] = _normalize_path(v)",
     "            out[k] = str(v)",
     ["PV1 路径被标准化"]),
    ("退化路径越界围栏（base_dir 不生效）", TC,
     "            if _base and not _within_base(out[k], _base):",
     "            if False:",
     ["PV2 越界路径被拒"]),
    ("退化长度上限（max_len 不校验）", TC,
     '        if "max_len" in spec:',
     "        if False:",
     ["PV3 超长参数被拒"]),
    ("退化未知字段策略（strip/reject 不生效）", TC,
     '    if unknown in ("reject", "strip"):',
     "    if False:",
     ["PV4 strip 丢弃未知字段", "PV4 reject 拒绝未知字段"]),
    ("退化超时封顶（不 clamp 到 cap）", TC,
     "    return int(min(f, cap))",
     "    return int(f)",
     ["PV5 超限封顶到 cap"]),
    ("exec_tool 摘掉统一参数校验", TO,
     "    if isinstance(args, dict):\n        _va, _ve = validate_for_tool(name, args)",
     "    if False:\n        _va, _ve = validate_for_tool(name, args)",
     ["PV7 非法参数被拦"]),
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
