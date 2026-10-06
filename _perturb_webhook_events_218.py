# -*- coding: utf-8 -*-
"""扰动验证（v4.218.0 webhook_events 重复键修复）：故意把修复改坏，看判据会不会红。

每条扰动：改源码 → 跑 tests/test_risk_webhook_events_218.py → 期望某个判据变红 → 恢复。
判据全绿不等于判据有效 —— 只有「改坏了就红」才说明它真的在看。

⚠️ 脚本运行期间目标文件视为锁定，不要手改。
⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_risk_webhook_events_218.py")
FILES = ["risk.py"]
_backup = {}
_backup_raw = {}
_crlf = {}


def read(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write(fp, s):
    body = s.replace("\n", "\r\n") if _crlf[fp] else s
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(body.encode("utf-8"))


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return b"\r\n" in f.read()


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

# (名称, 文件, 原串, 新串, 期望变红的判据关键字)
# 修复方向：删除多余的 READ 重复键、保留唯一 EXTERNAL 条目（与 v4.170.0 行为零变化）。
# 因此「改坏」= ① 重新引入重复键；② 把 EXTERNAL 错改成 READ。
CASES = [
    ("webhook_events 重复键复活（重新引入第二处 EXTERNAL）",
     "risk.py",
     '    "webhook_events": RiskClass.EXTERNAL,',
     '    "webhook_events": RiskClass.EXTERNAL,  # 扰动：重复键复活\n    "webhook_events": RiskClass.EXTERNAL,',
     ["webhook_events 在 RISK_MAP 中唯一（无重复键）"]),

    ("webhook_events 被改错分类为 READ（丢失 EXTERNAL，破坏 v4.170.0 行为）",
     "risk.py",
     '    "webhook_events": RiskClass.EXTERNAL,',
     '    "webhook_events": RiskClass.READ,          # 扰动：改错分类',
     ["webhook_events 风险归 EXTERNAL"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read(fp)
        _backup_raw[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp]
        if old not in src:
            MISS.append(f"{name}（原串没匹配上，扰动没生效）")
            print(f"  [SKIP] {name} — 原串未命中")
            continue
        write(fp, src.replace(old, new, 1))
        red, stat, out = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print(f"  [{tag}] {name} → FAIL 数 {stat[1]}，命中 {len(red)} 条"
              + ("" if ok else f"；期望 {expect}，实际 {[r[:50] for r in red[:3]]}"))
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
