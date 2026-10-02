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


# v4.186.0（P1-11 修）：套件基线清单。原实现纯 glob 发现——套件文件被误删/改名后
# 静默不收集且退出码 0，回归覆盖面缩水无人知晓。改为：成功跑完一轮自动记录基线，
# 下一轮发现基线里的套件不见了 → 报 MISSING 并退出 1；新增套件自动并入基线。
# 故意删除套件时用 --refresh-manifest 重建基线。
MANIFEST = HERE / ".suite_manifest.txt"


def load_manifest():
    if MANIFEST.exists():
        try:
            return [l.strip() for l in
                    MANIFEST.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        except OSError:
            return []
    return []


def save_manifest(names):
    try:
        MANIFEST.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"⚠️ 无法写入套件基线清单（不影响本轮结果）: {e}")


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

    # v4.209.2：判定部分抽到 classify() —— 逻辑一行没动，只为让它可被直接调用
    return classify(path.name, rc, out, err, time.time() - t0)


def classify(name, rc, out, err, secs):
    """把单个套件的 (rc, stdout, stderr) 判成 (n_pass, n_fail, status, output)。

    v4.209.2（BUG 审核 P1-2）从 run_one 里**原样抽出**，不改任何判定结果。
    抽出来的理由：这段原先埋在子进程调用之后，判据只能靠"源码里有没有那个
    if 分支"来判断 —— 那叫**看到**，不叫**验到**。抽成纯函数后可以喂合成输出，
    直接问"这种输出会被判成什么"，假绿才真正堵住。
    """
    n_pass = n_fail = 0
    _has_stats = False  # v4.186.0（P1-11 修）：区分「显式输出 PASS=0」与「根本没输出统计」
    m = re.search(r"PASS\s*=\s*(\d+)", out)
    if m:
        n_pass = int(m.group(1))
        _has_stats = True
    m = re.search(r"FAIL\s*=\s*(\d+)", out)
    if m:
        n_fail = int(m.group(1))
        _has_stats = True
    else:
        # 套件未按约定输出 FAIL=，则以退出码兜底判定
        n_fail = 0 if rc == 0 else 1

    # v4.186.0（P1-11 修）：rc=0 但既无 PASS= 也无 FAIL=（套件空跑/崩在 import 前
    # 却吞了退出码/断言全被注释）—— 旧逻辑显示 [OK] PASS=0 假绿。改为 EMPTY 视为失败。
    if rc == 0 and not _has_stats:
        # 但要认项目既有的非标准格式（历史套件约定，非空跑）：
        # ① 成功横幅（=== ALL_HOTFIX15_OK === / ALL_TWIN_V2_OK / REGRESS_OK …）
        # ② REGRESS_FAIL 列表（audio_regress）—— 非空即真失败
        _m = re.search(r"REGRESS_FAIL:\s*(\[.*?\])", out)
        if _m and _m.group(1).strip() not in ("[]", ""):
            return name, 0, max(1, len(re.findall(r"'[^']*'", _m.group(1)))), \
                "FAILED", secs, out + err
        if re.search(r"^\s*(?:={2,}\s*)?(?:ALL_)?[A-Z0-9_]{2,}_OK\b",
                     out, re.M):
            _n = len(re.findall(r"^\s*(?:PASS:|\[OK\]|✅|✓)", out, re.M))
            # v4.209.2（BUG 审核 P1-2 修）：**约定写了不算数，得有人执行**。
            # 原逻辑只看"有没有 XXX_OK 横幅"，再用 `_n` 去数 stdout 里的
            # PASS:/[OK]/✅/✓。但 test_v4102_image_compress.py 是个手工验证脚本：
            # 它**有** OK 横幅，却一条 PASS:/[OK] 都没有 → `_n = 0`
            # → 返回 `PASS=0 status=ok` 假绿。后果：这类套件将来把断言全删光，
            # 只要留着横幅，回归照样全绿。现改为：`_n == 0` 同样按 EMPTY 处理。
            if _n == 0:
                return name, 0, 1, "EMPTY", secs, \
                    out + err + "\n[run_all] 有成功横幅但 0 条断言输出 —— " \
                    "按 EMPTY 处理（请补 PASS=/FAIL= 统计输出，" \
                    "见 run_all.py 头部约定第 3 条）"
            return name, _n, 0, "ok", secs, out + err
        return name, n_pass, n_fail, "EMPTY", secs, out + err
    status = "ok" if (rc == 0 and n_fail == 0) else "FAILED"
    return name, n_pass, n_fail, status, secs, out + err


def main():
    ap = argparse.ArgumentParser(description="小臭玩AI 回归测试统一入口")
    ap.add_argument("--list", action="store_true", help="只列出发现的套件")
    ap.add_argument("--only", default="", help="只跑文件名含该子串的套件")
    ap.add_argument("--verbose", action="store_true", help="失败时打印完整输出")
    ap.add_argument("--refresh-manifest", action="store_true",
                    help="重建套件基线清单（故意删除/改名套件后使用）")
    args = ap.parse_args()

    all_suites = discover()
    # v4.186.0（P1-11 修）：基线比对——基线里的套件不见了要炸出来，不能静默缩水
    baseline = load_manifest()
    missing = []
    if baseline and not args.refresh_manifest:
        have = {s.name for s in all_suites}
        missing = [n for n in baseline if n not in have]
    if all_suites and (not missing or args.refresh_manifest):
        save_manifest([s.name for s in all_suites])

    suites = all_suites
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
    if missing:
        for n in missing:
            rows.append((n, 0, 0, "MISSING", 0.0,
                         f"基线清单里的套件文件不存在：tests/{n}"))
            print(f"\n>>> {n}")
            print("   -> MISSING（基线里的套件不见了；故意删除请用 --refresh-manifest）")
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
        mark = {"ok": "OK  ", "EMPTY": "EMPT", "MISSING": "MISS"}.get(status, "FAIL")
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
