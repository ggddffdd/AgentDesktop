# -*- coding: utf-8 -*-
"""扰动验证（v4.219 exec_tool 最终闸门，审查 P1 #4）：故意把闸门改坏，看判据会不会红。

每条扰动：改 tools.py → 跑 tests/test_exec_tool_gate_219.py → 期望对应判据变红 → 恢复。

⚠️ 脚本运行期间 tools.py 视为锁定，不要手改。
⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_exec_tool_gate_219.py")
FILES = ["tools.py"]
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
CASES = [
    ("fail-closed 分支被绕过（无ctx一律放行）",
     "tools.py",
     "    if _risk == RiskClass.READ:",
     "    if True or _risk == RiskClass.READ:  # 扰动：绕过 fail-closed 拒绝",
     ["无ctx时EXEC工具(process_kill)被最终闸门拒绝",
      "无ctx时未登记工具(send_email)被最终闸门拒绝",
      "exec_tool集成:无ctx拒绝EXEC且未真正执行"]),
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
