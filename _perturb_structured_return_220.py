# -*- coding: utf-8 -*-
"""扰动验证（v4.220 结构化返回，审查第二优先级 全面重构）：故意让 exec_tool 跳过
ToolResult 归一，看判据会不会红。

每条扰动：改 tools.py → 跑 tests/test_structured_return_220.py → 期望对应判据变红 → 恢复。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_structured_return_220.py")
FILES = ["tools.py"]
_backup_raw = {}
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
    r = subprocess.run([PY, TEST], capture_output=True, text=True,
                       cwd=ROOT, timeout=300, env=env,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    m = re.search(r"PASS=(\d+) FAIL=(\d+)", out)
    return red, (m.group(1), m.group(2)) if m else ("?", "?"), out


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

CASES = [
    # ⚠️ 原串必须逐字等于当前源码（框架是纯字符串替换）。
    # v4.224 在「归一 → return」之间插入了 apply_post_verification 段，
    # 原串停留在 v4.223 形态 → 原串未命中 → 这条变异**静默 SKIP 了一整个版本**
    # （即「登记了却从未被消耗」的哑弹）。凡是拿源码片段当原串的，都要跟着
    # 插入段一起更新，否则失效方式是「删了没人发现」。
    ("exec_tool 跳过 ToolResult 归一（直接 return 原始 _r）",
     "tools.py",
     "            _tr.evidence = _mask_recursive(_tr.evidence)\n"
     "            # v4.224：执行后验证（查副作用是否真生效；只降级不升级）\n"
     "            try:\n"
     "                apply_post_verification(_tr, name, args)\n"
     "            except Exception:\n"
     "                pass\n"
     "            return _tr",
     "            _tr.evidence = _mask_recursive(_tr.evidence)\n"
     "            return _r  # 扰动：跳过 ToolResult 归一",
     ["exec_tool返回ToolResult", "tuple工具归一为ToolResult"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup_raw[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup_raw[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(f"{name}（原串没匹配上，扰动没生效）")
            print(f"  [SKIP] {name} — 原串未命中")
            continue
        write_raw(fp, (src.replace(old, new, 1).replace("\n", "\r\n")
                        if _crlf[fp] else src.replace(old, new, 1)).encode("utf-8"))
        red, stat, out = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print(f"  [{tag}] {name} → FAIL 数 {stat[1]}，命中 {len(red)} 条"
              + ("" if ok else f"；期望 {expect}，实际 {[r[:60] for r in red[:3]]}"))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup_raw[fp])
finally:
    for fp, blob in _backup_raw.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print(f"\n=== 扰动汇总：命中 {len(HIT)}/{len(CASES)} ===")
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
for m in MISS:
    print("   ✗", m)
sys.exit(1 if MISS else 0)
