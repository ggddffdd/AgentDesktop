# -*- coding: utf-8 -*-
"""扰动：run_python cwd 统一 / traceback 末行保留 / read_xlsx_json 三道护栏的防复发锁。

根治 2026-10-11「读附件空转 + 甩锅全角冒号 + 编造题型」事件。
- V1 破坏 cwd 统一（run_cwd -> ws）   -> A1 判据翻红
- V2 删末行保留调用                    -> A2 判据翻红
- V3 删 read_xlsx_json 注册           -> A3 判据翻红
还原必绿。统一护栏契约：import _perturb_guard + arm() 快照/三重还原 +
变异写 `# 扰动` 标记（被强杀可扫到）+ 输出统一契约统计行 + 非零退出（哑弹）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools.py")
CRIT = os.path.join(ROOT, "tests", "test_python_cwd_xxx.py")
PY = sys.executable

import _perturb_guard as _guard


def _write(blob):
    with open(TOOLS, "wb") as f:
        f.write(blob)


def _run_crit():
    r = subprocess.run([PY, CRIT], capture_output=True, text=True, cwd=ROOT)
    out = (r.stdout or "") + (r.stderr or "")
    fails = len(re.findall(r"(?:\[FAIL\]|FAIL)\s+\S+", out))
    return fails, out


def _mutate_cwd(blob):
    s = blob.decode("utf-8")
    s = s.replace(
        "proc = subprocess.Popen([exe, fpath], cwd=run_cwd,",
        "proc = subprocess.Popen([exe, fpath], cwd=ws,  # 扰动：破坏 cwd 统一", 1)
    return s.encode("utf-8")


def _mutate_tail(blob):
    s = blob.decode("utf-8")
    s = s.replace(
        "out = _preserve_traceback_tail(raw, out)",
        "out = out  # 扰动：破坏末行保留", 1)
    return s.encode("utf-8")


def _mutate_reg(blob):
    s = blob.decode("utf-8")
    # 整行替换：删注册装饰器，且注释不含 @register_tool 子串（否则 A3 仍匹配到，成哑弹）
    s = s.replace(
        '@register_tool("read_xlsx_json")',
        '# 扰动：删 read_xlsx_json 注册', 1)
    return s.encode("utf-8")


def main():
    snap = _guard.arm([TOOLS])
    orig = snap[TOOLS]  # 原始字节：每个 case 都在此之上注入，避免叠加
    cases = [("V1 cwd 统一", _mutate_cwd),
             ("V2 末行保留", _mutate_tail),
             ("V3 read_xlsx_json 注册", _mutate_reg)]
    miss = []
    pass_n = 0
    for name, mut in cases:
        _write(mut(orig))
        fails, _ = _run_crit()
        if fails == 0:
            miss.append(name)  # 哑弹：变异没让判据翻红
        else:
            pass_n += 1
            print("[PERTURB] %s -> 判据翻红(FAIL=%d) 符合预期" % (name, fails))
    _write(orig)  # 还原（atexit 还会再还原一次，幂等）
    if miss:
        print("[PERTURB] 哑弹(改坏未红): %s" % ", ".join(miss))
        print("PERTURB PASS=%d FAIL=%d" % (pass_n, len(miss)))
        sys.exit(1)
    print("PERTURB PASS=%d FAIL=0" % pass_n)
    sys.exit(0)


if __name__ == "__main__":
    main()
