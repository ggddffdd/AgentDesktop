# -*- coding: utf-8 -*-
"""小臭玩AI — 回归测试统一入口

用法：
    python tests/run_all.py                 # 跑全部套件
    python tests/run_all.py --list          # 只列出发现的套件，不执行
    python tests/run_all.py --only bridge   # 只跑文件名含 bridge 的套件

退出码：0=全部通过，1=有失败。

约定（新增套件请遵守）
-----------------------
1. 文件放 tests/ 下，命名 test_*.py（regression.py 为历史套件，一并收集）。
2. 必须能直接 `python tests/xxx.py` 独立运行，不依赖 pytest。
3. 输出里须含 `PASS=<n>` 与 `FAIL=<n>`（便于本入口汇总），
   并以非 0 退出码表示失败。
4. 每个套件在**独立子进程**中运行 —— 因为多个套件会改写模块级全局
   （如 memory_store._configure、browser_bridge._token），同进程跑会互相污染。
5. 需要 Qt 的套件请自行设置离屏平台（本入口已预设 QT_QPA_PLATFORM=offscreen）。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMEOUT = 180  # 单套件超时（秒），防止挂死拖住整轮


def discover():
    """收集 tests/ 下的测试套件。"""
    files = sorted(HERE.glob("test_*.py"))
    reg = HERE / "regression.py"
    if reg.exists():
        files.append(reg)
    return files


def run_one(path):
    """在子进程中跑单个套件，返回 (name, n_pass, n_fail, status, seconds, output)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"   # 需要 Qt 的套件不弹窗

    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT), env=env,
            capture_output=True, timeout=TIMEOUT,
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return path.name, 0, 1, f"TIMEOUT(>{TIMEOUT}s)", time.time() - t0, ""

    n_pass = n_fail = 0
    m = re.search(r"PASS\s*=\s*(\d+)", out)
    if m:
        n_pass = int(m.group(1))
    m = re.search(r"FAIL\s*=\s*(\d+)", out)
    if m:
        n_fail = int(m.group(1))
    else:
        # 套件未按约定输出 FAIL=，则以退出码兜底判定
        n_fail = 0 if rc == 0 else 1

    status = "ok" if (rc == 0 and n_fail == 0) else "FAILED"
    return path.name, n_pass, n_fail, status, time.time() - t0, out + err


def main():
    ap = argparse.ArgumentParser(description="小臭玩AI 回归测试统一入口")
    ap.add_argument("--list", action="store_true", help="只列出发现的套件")
    ap.add_argument("--only", default="", help="只跑文件名含该子串的套件")
    ap.add_argument("--verbose", action="store_true", help="失败时打印完整输出")
    args = ap.parse_args()

    suites = discover()
    if args.only:
        suites = [s for s in suites if args.only in s.name]
    if not suites:
        print("未发现任何测试套件（tests/test_*.py 或 tests/regression.py）")
        return 0 if args.list else 1

    if args.list:
        print(f"发现 {len(suites)} 个测试套件：")
        for s in suites:
            print(f"  - tests/{s.name}")
        return 0

    print("=" * 62)
    print(f"小臭玩AI 回归测试 · 共 {len(suites)} 个套件")
    print("=" * 62)

    rows = []
    for s in suites:
        print(f"\n>>> {s.name}")
        name, n_pass, n_fail, status, secs, out = run_one(s)
        rows.append((name, n_pass, n_fail, status, secs, out))
        for line in out.splitlines():
            if "[PASS]" in line or "[FAIL]" in line or "✅" in line or "❌" in line:
                print("   " + line.strip())
        print(f"   -> {status}  (PASS={n_pass} FAIL={n_fail}, {secs:.1f}s)")
        if status != "ok" and args.verbose and out.strip():
            print("   ---- 完整输出 ----")
            print("\n".join("   " + l for l in out.splitlines()[-80:]))

    total_pass = sum(r[1] for r in rows)
    total_fail = sum(r[2] for r in rows)
    failed = [r for r in rows if r[3] != "ok"]

    print("\n" + "=" * 62)
    print(f"汇总：{len(rows)} 个套件 / PASS={total_pass} FAIL={total_fail}")
    for name, n_pass, n_fail, status, secs, _ in rows:
        mark = "OK  " if status == "ok" else "FAIL"
        print(f"  [{mark}] {name:<34} PASS={n_pass:<4} FAIL={n_fail}")
    if failed:
        print(f"\n失败套件：{', '.join(r[0] for r in failed)}")
        print("提示：加 --verbose 可看失败套件的完整输出。")
    else:
        print("\n全部通过。")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
