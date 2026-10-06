# -*- coding: utf-8 -*-
"""扰动验证 P2#3：把路由表改回 software_run 幽灵名，看判据是否翻红。

手法：备份原字节 → 原位把 6 行真实工具组退化回单行 software_run →
跑 test_system_prompt_routing_221.py → 期望 A 用例翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_system_prompt_routing_221.py")
FILES = ["config.py"]
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
    ("路由表退化回 software_run 幽灵名",
     "config.py",
     '        "| 打开应用 | app_launch | 软件控制 |\\n"\n'
     '        "| 关闭应用 | app_close | 软件控制 |\\n"\n'
     '        "| 点击控件/按钮 | app_click | 软件控制 |\\n"\n'
     '        "| 输入文本 | app_type | 软件控制 |\\n"\n'
     '        "| 窗口操作/最大化最小化 | app_window_state | 软件控制 |\\n"\n'
     '        "| 查看控件树 | app_list_controls | 软件控制 |\\n"',
     '        "| 打开文件/打开应用/控制软件/输入文字/点按钮 | software_run | software_* 软件控制 |\\n"',
     ["不存在 software_run"]),
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
