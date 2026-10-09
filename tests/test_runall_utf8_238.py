# -*- coding: utf-8 -*-
"""v4.238.1 判据：run_all.py 父进程 stdout 必须容忍 cp1252 控制台。

背景（2026-10-09 GitHub Actions run 37867023478 实锤）：windows-latest 的
英文控制台代码页是 cp1252，run_all.py 第一个中文 print 直接
UnicodeEncodeError —— 5m15s 全花在装依赖，测试一个没跑就红。
子进程早有 PYTHONIOENCODING=utf-8 兜底（run_one / run_guard_preflight），
漏的只有父进程自己。

判据是行为级的：真开一个 PYTHONIOENCODING=cp1252 的子进程跑 --list，
父进程没 reconfigure 就必红（改坏必红的靶子在这里）。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUN_ALL = os.path.join(HERE, "run_all.py")
PY = sys.executable


def _run_list(extra_env):
    """以指定额外 env 跑 run_all.py --list，返回 (rc, out, err)（均 utf-8 解码）。"""
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)   # 干掉可能存在的 UTF-8 模式，强制走老路径
    env.pop("PYTHONIOENCODING", None)
    env.update(extra_env)
    p = subprocess.run([PY, RUN_ALL, "--list"], cwd=ROOT, env=env,
                       capture_output=True, timeout=180)
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out, err


def main():
    _p = _f = 0

    def check(name, cond):
        nonlocal _p, _f
        if cond:
            _p += 1
            print("[PASS] %s" % name)
        else:
            _f += 1
            print("[FAIL] %s" % name)

    # 注意：套件列表标记用 ASCII（"tests/test_"）而非中文「个测试套件」——
    # B2 子进程走 locale 编码（本地 GBK / CI cp1252），中文会随环境变字形；
    # ASCII 在任何代码页下都是同一字节，判据才跨环境稳定。
    # B1：cp1252 控制台下父进程必须活下来并列出套件（CI 首跑的原始死法）
    rc, out, err = _run_list({"PYTHONIOENCODING": "cp1252"})
    check("B1 cp1252 控制台 --list 退出码为 0", rc == 0)
    check("B1 无 UnicodeEncodeError", "UnicodeEncodeError" not in out + err)
    check("B1 列出了测试套件", "tests/test_" in out)

    # B2：正常环境不被 reconfigure 破坏（本地 GBK/UTF-8 控制台回归保护）
    rc2, out2, err2 = _run_list({})
    check("B2 正常环境 --list 退出码为 0", rc2 == 0)
    check("B2 正常环境列出套件", "tests/test_" in out2)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
