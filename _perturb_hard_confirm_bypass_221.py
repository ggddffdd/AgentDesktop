# -*- coding: utf-8 -*-
"""扰动验证 P1#1：把四工具之一退化回单值 RiskClass.EXEC（退出 ALWAYS_CONFIRM），
看硬确认绕过判据 test_hard_confirm_bypass_221.py 是否翻红。

注意：risk.py 里四目标行**不连续**（中间夹 process_start / app_click /
app_focus），所以不能用「多行连续 old 串」——那永远匹配不上、变成 SKIP。
正确做法是拆成 4 个独立单行 CASE，逐行退化、逐行验证判据翻红、逐行还原。

手法（与 _perturb_impact_scope_220.py 同套）：备份原字节 → 原位改坏 → 跑判据 →
期望关键用例（全信任下该工具仍需确认）翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_hard_confirm_bypass_221.py")
FILES = ["risk.py"]
_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def decode(blob):
    # 归一为 LF 再比对，避免 CRLF 文件导致 old 串永远匹配不上
    return blob.decode("utf-8-sig").replace("\r\n", "\n")


def encode(text, crlf):
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.encode("utf-8-sig")


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

# 四工具各一行独立退化：把 (RiskClass.EXEC, None, True) 退回单值 RiskClass.EXEC
# （退出 ALWAYS_CONFIRM，会话信任即可绕过 → 判据 C 翻红）。
CASES = [
    ("process_kill 退出 ALWAYS_CONFIRM", "risk.py",
     '    "process_kill": (RiskClass.EXEC, None, True),\n',
     '    "process_kill": RiskClass.EXEC,\n',
     ["全信任下 process_kill 仍需确认"]),
    ("clean_recycle_bin 退出 ALWAYS_CONFIRM", "risk.py",
     '    "clean_recycle_bin": (RiskClass.EXEC, None, True),       # v4.217.0：清空回收站，不可逆，须手动确认\n',
     '    "clean_recycle_bin": RiskClass.EXEC,       # v4.217.0：清空回收站，不可逆，须手动确认\n',
     ["全信任下 clean_recycle_bin 仍需确认"]),
    ("app_kill 退出 ALWAYS_CONFIRM", "risk.py",
     '    "app_kill": (RiskClass.EXEC, None, True),\n',
     '    "app_kill": RiskClass.EXEC,\n',
     ["全信任下 app_kill 仍需确认"]),
    ("app_close 退出 ALWAYS_CONFIRM", "risk.py",
     '    "app_close": (RiskClass.EXEC, None, True),  # v4.218：关闭应用窗口，可能丢未保存内容，强制确认\n',
     '    "app_close": RiskClass.EXEC,  # v4.218：关闭应用窗口，可能丢未保存内容，强制确认\n',
     ["全信任下 app_close 仍需确认"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = decode(_backup[fp])
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        mutated = src.replace(old, new, 1)
        write_raw(fp, encode(mutated, _crlf[fp]))
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
