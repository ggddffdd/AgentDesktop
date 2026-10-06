# -*- coding: utf-8 -*-
"""扰动验证（v4.220 参数校验，审查第二优先级）：故意把参数校验改掉，看判据会不会红。

每条扰动：改 system_control_tools.py / software_control_tools.py →
跑 tests/test_param_validation_220.py → 期望对应判据变红 → 恢复。

⚠️ 脚本运行期间目标文件视为锁定，不要手改。
⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_param_validation_220.py")
FILES = ["system_control_tools.py", "software_control_tools.py"]
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

# (名称, 文件, 原串, 新串, 期望变红的判据关键字)
CASES = [
    ("process_kill 参数校验被移除（缺参将不再优雅失败）",
     "system_control_tools.py",
     '    _v, _verr = _validate_args(\n        args, [{"key": "name", "type": "str", "required": True},\n'
     '               {"key": "force", "type": "bool", "required": False}])\n'
     '    if _v is None:\n'
     '        return ToolResult.fail(_verr)\n'
     '    name = _v["name"]\n'
     '    force = _v.get("force", False)',
     '    name = args.get("name")  # 扰动：移除参数校验\n'
     '    force = args.get("force", False)',
     ["缺name时报『缺少必填参数』"]),

    ("app_kill 参数校验被移除（缺 target 将不再优雅失败）",
     "software_control_tools.py",
     '    _v, _verr = _validate_args(args, [{"key": "target", "type": "str", "required": True}])\n'
     '    if _v is None:\n'
     '        return ToolResult.fail(_verr)\n'
     '    target = _v["target"]',
     '    target = args.get("target")  # 扰动：移除参数校验',
     ["缺target时app_kill.ok=False且报缺参"]),
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
