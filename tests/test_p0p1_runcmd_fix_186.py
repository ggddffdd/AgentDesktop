# -*- coding: utf-8 -*-
"""P0-1 run_python deny + P1-1 非 dict args 回归（v4.186.0 审查修复）
文件方式运行，避开 bash 反斜杠转义问题。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools, permissions, agent

P = F = 0
def check(name, cond, detail=""):
    global P, F
    if cond: P += 1; print("  PASS %s %s" % (name, detail))
    else: F += 1; print("  FAIL %s %s" % (name, detail))

print("=== [A] P0-1: run_python deny（文本模式可命中项）===")
deny_cases = [
    ("import os; os.system('format d:')", "format"),
    ("import subprocess; subprocess.run('shutdown /s /t 0', shell=True)", "shutdown"),
    ("import subprocess; subprocess.run('rm -rf /', shell=True)", "shell 串 rm -rf /"),
    ("import subprocess; subprocess.run('rm -rf C:\\\\', shell=True)", "shell 串 rm -rf C:\\"),
    ("import subprocess; subprocess.run('del /f /s /q C:\\\\', shell=True)", "del C:\\"),
    ("import os; os.system('diskpart /s x.txt')", "diskpart"),
]
for code, label in deny_cases:
    out, _ = tools.tool_run_python(".", code)
    check("deny %s" % label, out.startswith("⛔"), "-> %s" % out[:40])

print("=== [B] P0-1: 正常代码 0 误伤 ===")
ok_cases = [
    ("print(1+1)", "算术"),
    ("open('report_tmp_probe.md','w').write('hi')", "写文件"),
    ("import json,os,re;print(json.dumps({'a':os.listdir('.')[:1]}))", "正常 import"),
    ("import subprocess; subprocess.run('ls -la', shell=True)", "普通 ls"),
    ("import subprocess; subprocess.run(['ls','-la'])", "argv 列表 ls"),
]
for code, label in ok_cases:
    out, _ = tools.tool_run_python(".", code)
    check("放行 %s" % label, not out.startswith("⛔"), "-> %s" % out[:30].replace("\n", " "))

print("=== [C] P0-1 已知局限（诚实记录）：argv 列表/shutil 形式不走文本模式 ===")
lim_cases = [
    ("import subprocess; subprocess.run(['rm','-rf','/'])", "argv 列表 rm -rf"),
    ("import shutil; shutil.rmtree('C:/')", "shutil.rmtree"),
]
for code, label in lim_cases:
    out, _ = tools.tool_run_python(".", code)
    print("  [LIMIT] %-18s deny=%-5s（文本模式外，靠②-B/manual 确认层兜底）" % (label, out.startswith("⛔")))

print("=== [D] P1-1: decide() 任意畸形 args 不崩 + fail-closed 方向 ===")
e = permissions.PermissionEngine()
for args in (["rm -rf /"], "str", 42, None, 3.14, True):
    try:
        d = e.decide("run_command", args)
        check("decide(%s) 不崩" % type(args).__name__, True,
              "allowed=%s needs_user=%s rule=%s" % (d.allowed, d.needs_user, d.rule))
    except Exception as ex:
        check("decide(%s) 不崩" % type(args).__name__, False, repr(ex))
try:
    d = e.decide("write_file", "not-a-dict")
    check("decide(write_file, str) 不崩", True, "allowed=%s rule=%s" % (d.allowed, d.rule))
except Exception as ex:
    check("decide(write_file, str) 不崩", False, repr(ex))
try:
    d = e.decide("run_command", {"command": ["rm", "-rf", "/"]})
    check("decide(cmd=list) 不崩", True, "allowed=%s needs_user=%s" % (d.allowed, d.needs_user))
except Exception as ex:
    check("decide(cmd=list) 不崩", False, repr(ex))

print("=== [E] P0-1 核心论证复核：会话信任下 run_python 是否还拦得住 ===")
e2 = permissions.PermissionEngine()
e2.session_trusted = True   # 模拟「本次会话全部信任」
e2.mode = "auto"
for name, args in (("run_python", {"code": "print(1)"}),
                   ("run_python", {"code": "import os;os.system('format d:')"}),
                   ("run_command", {"command": "format d:"}),
                   ("run_command", {"command": "ls"})):
    d = e2.decide(name, args, explicit_intent=True)
    print("  trust+auto: %-12s %-30s -> allowed=%s needs_user=%s rule=%s"
          % (name, str(args)[:28], d.allowed, d.needs_user, d.rule))

print("=== [F] _safe_args 归一化（期望：任何输入都返回 dict）===")
class TC:
    def __init__(self, s): self._d = {"function": {"arguments": s}}
    def get(self, k, d=None): return self._d.get(k, d)
w = agent.AgentWorker.__new__(agent.AgentWorker)
for s in ('{"a":1}', "[1,2]", '"str"', "42", "{bad json", "null", ""):
    r = w._safe_args(TC(s))
    check("_safe_args(%-10s) 返回 dict" % s[:10], isinstance(r, dict), "-> %r" % (r,))

print("\n汇总：PASS=%d FAIL=%d" % (P, F))
sys.exit(1 if F else 0)
