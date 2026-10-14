# -*- coding: utf-8 -*-
"""扰动：v4.264 安全与语义对齐判据必须能翻红。

每条都是「改坏一处 → tests/test_security_align_264.py 对应断言变红 → 还原」。
SKIP / MISS 算哑弹。变异行带 `# 扰动`，供护栏在强杀后扫到残留。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_security_align_264.py")
_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def decode(blob):
    return blob.decode("utf-8-sig").replace("\r\n", "\n")


def encode(text, crlf):
    if crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.encode("utf-8-sig")


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    return red


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm([os.path.join(ROOT, n) for n in
              ("agent.py", "permissions.py", "risk.py", "config.py")])

CASES = [
    ("P0-2 来源闸退出 FORCE_CONFIRM", "agent.py",
     '    "task_risk_critical",   # v4.196 批⑬：高风险任务的写入与执行\n'
     '    "implicit_intent",      # v4.264 P0-2：来源闸。decide 虽在会话信任之前返回\n'
     '                            # needs_user，但弹窗层 force=False 仍会被 session_trusted 短路\n'
     ')\n',
     '    "task_risk_critical",   # v4.196 批⑬：高风险任务的写入与执行\n'
     ')  # 扰动：去掉 implicit_intent\n',
     ["P02 会话全信任下来源闸必须真弹确认"]),
    ("P0-3 auto 重新忽略 manual 档", "permissions.py",
     '            if risk == RiskClass.EXEC or tier == "manual":\n',
     '            if risk == RiskClass.EXEC:  # 扰动：auto 忽略 manual 档\n',
     ["P03 auto 下 write_file 等 manual 档仍需确认"]),
    ("P1-3 run_workflow 信任不带参数", "agent.py",
     '                engine.trust_tool("run_workflow", args or {})\n',
     '                engine.trust_tool("run_workflow")  # 扰动：信任不带参数\n',
     ["P13 确认后的信任键含本次参数"]),
    ("P1-4 read_xlsx_json 退出 RISK_MAP", "risk.py",
     '    "read_xlsx_json": RiskClass.READ,  # v4.264：显式 READ，不再靠 read_ 前缀兜底\n',
     '    "read_xlsx_json_gone": RiskClass.READ,  # 扰动：read_xlsx_json 退出 RISK_MAP\n',
     ["P14 read_xlsx_json 显式写入 RISK_MAP 且为 READ"]),
    ("P1-4 一致性函数恒返回空", "config.py",
     '    gaps = []\n'
     '    def_names = set()\n',
     '    gaps = []\n'
     '    return gaps  # 扰动：一致性恒空\n'
     '    def_names = set()\n',
     ["P14 反向：未登记 EXTERNAL 必须被报出",
      "P14 反向：DEFS 无 handler 必须被报出"]),
]

HIT, MISS = [], []
try:
    for _name, fp, _old, _new, _expect in CASES:
        if fp not in _backup:
            _backup[fp] = read_raw(fp)
            _crlf[fp] = b"\r\n" in _backup[fp]

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
              + ("" if ok else ("；期望 %s；实际 %s" % (expect, red[:8]))))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
