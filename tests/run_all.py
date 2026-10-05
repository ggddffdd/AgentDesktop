# -*- coding: utf-8 -*-
"""小臭玩AI — 回归测试统一入口

用法：
    python tests/run_all.py                 # 跑全部套件
    python tests/run_all.py --list          # 只列出发现的套件，不执行
    python tests/run_all.py --only bridge   # 只跑文件名含 bridge 的套件
    python tests/run_all.py --with-perturb  # 追加跑根目录的扰动脚本（元测试，数分钟）

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
6. `tests/test_zz_*.py` 是**扰动脚本的临时夹具**（`_perturb_*.py` 的 case_newfile
   从无到有造出来、用完即删），业务套件**不得**占用此前缀 —— 本入口发现即排除，
   并在每轮开始前顺手清理残留（被强杀时 finally 不执行，夹具会留下来）。

扰动脚本（**元测试**，与套件是两回事）
--------------------------------------
根目录 `_perturb_*.py` 不是判据套件，而是「判据的检验器」：每个脚本都
改坏一处源码 → 跑判据 → 期望它转红 → 还原。因此：
  · 耗时是判据套件的数倍（实测单个 12~44s），且**必须独占**（它改的是源码）；
  · 默认**不跑**（日常回归不必每轮都做元测试），用 `--with-perturb` 打开；
  · 但**基线清单比对默认就做** —— 见下。
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMEOUT = 180  # 单套件超时（秒），防止挂死拖住整轮
PERTURB_TIMEOUT = 600  # 单扰动脚本超时（秒）；最重的扰动脚本实测 44s

# 临时夹具前缀：扰动脚本的 case_newfile 会从无到有造 tests/test_zz_*.py 来测
# 「集合登记式判据」（光靠字符串替换测不到）。它**不是套件**，本入口不得收集它，
# 基线清单里也不许留它的名字 —— 否则被强杀残留一次，之后每轮都多出一个 EMPTY
# 套件、并把污染固化进基线。清理职责在 _perturb_guard.sweep_scratch()。
FIXTURE_PREFIX = "test_zz_"

# 回归期的日志改道（v4.211.5）——
# 套件子进程里 config.app_log_path() 缺省落 WORKSPACE_DIR/debug.log，而那**是用户的
# 真实诊断日志**。实测 2026-10-04：该文件单日 1032 条 WARNING 里 828 条（80%）出自
# 判据套件（empty_state 的桩按钮 / permissions 的白名单用例 / system_control 的模拟
# RuntimeError / tool_audit 的失败用例 / legion_chat 的 Temp\lc_probe_* 路径），
# 且全部集中在回归窗口。测试噪音灌进真实日志 = 把线索池搅浑、让「今天有多少告警」
# 这类读数失真。config 早就支持 XC_LOG_DIR 改道，这里统一注入。
# 用 setdefault：尊重套件自设（如 test_agent_node_failure 已硬设 _SBX/logs）。
# ⚠️ 要验「**默认**路由」的套件（test_workspace_routing）须自行 pop 本变量，见其文件头。
TEST_LOG_DIR = os.path.join(tempfile.gettempdir(), "dsb_test_logs")


def is_real_suite(p):
    """是不是「真套件」（排除扰动脚本造的临时夹具）。"""
    return not p.name.startswith(FIXTURE_PREFIX)


def clean_stale_fixtures(verbose=True):
    """清掉上次扰动被强杀留下的 tests/test_zz_*.py。

    为什么放在本入口：残留夹具会让被测的集合登记式判据**每轮假红**（它把夹具当成
    「第 4 个同类文件」），而看到假红的人会先怀疑业务代码。跑回归前顺手扫一遍，
    成本近乎为零（一次 glob）。
    """
    gone = []
    for fp in sorted(HERE.glob(FIXTURE_PREFIX + "*.py")):
        removed = False
        for i in range(5):
            try:
                fp.unlink()
                removed = True
                break
            except FileNotFoundError:
                removed = True
                break
            except OSError:
                if i < 4:
                    time.sleep(0.15)
        if removed:
            gone.append(fp.name)
        elif verbose:
            print(f"[WARN] 无法清理残留夹具 {fp.name}（可能被占用，请手动删）")  # v4.213.0: ASCII 前缀，GBK 重定向下不再 UnicodeEncodeError
    if gone and verbose:
        print(f"[run_all] 已清理上次扰动残留的临时夹具: {', '.join(gone)}")
    return gone


def discover():
    """收集 tests/ 下的测试套件（排除 _perturb_*.py 造的临时夹具）。"""
    files = sorted(p for p in HERE.glob("test_*.py") if is_real_suite(p))
    reg = HERE / "regression.py"
    if reg.exists():
        files.append(reg)
    return files


# ---------------------------------------------------------------- 扰动脚本
# 2026-10-03 补：这批「元测试」原先**完全在本入口的视野之外** —— 它们在仓库
# 根目录（discover 只扫 tests/test_*.py），也没有基线比对，因此被误删/改名会
# 静默消失；更糟的是没人复跑，跨版本漂移后 case 会集体变哑弹（实测一次跑出
# 8 条：锚点写死旧版本号 / 变异落在判据观测范围之外 / 判据代理太弱）。
# 故：① 清单比对默认就做（成本为零，保住「脚本还在」）；② 真跑用 --with-perturb。
PERTURB_PREFIX = "_perturb_"
PERTURB_EXCLUDE = {"_perturb_guard.py"}   # 护栏自身，不是扰动 case
PERTURB_MANIFEST = HERE / ".perturb_manifest.txt"
PERTURB_GUARD = ROOT / "_perturb_guard.py"


def discover_perturb():
    """收集仓库根目录下的扰动脚本（_perturb_*.py，排除护栏自身）。"""
    return sorted(p for p in ROOT.glob(PERTURB_PREFIX + "*.py")
                  if p.name not in PERTURB_EXCLUDE)


def diff_perturb_manifest(files, baseline, skip=False):
    """清单比对：返回「基线里有、磁盘上却没有」的扰动脚本名。

    抽成纯函数，理由同 classify()（见 v4.209.2 注释）：内联在 main 里时，
    判据只能靠「源码里有没有那个 if」来判断 —— 那叫**看到**，不叫**验到**。
    抽出来就能喂合成清单，直接问「少了 X 会不会报」。
    """
    if skip or not baseline:
        return []
    have = {p.name for p in files}
    return [n for n in baseline if n not in have]


# v4.186.0（P1-11 修）：套件基线清单。原实现纯 glob 发现——套件文件被误删/改名后
# 静默不收集且退出码 0，回归覆盖面缩水无人知晓。改为：成功跑完一轮自动记录基线，
# 下一轮发现基线里的套件不见了 → 报 MISSING 并退出 1；新增套件自动并入基线。
# 故意删除套件时用 --refresh-manifest 重建基线。
MANIFEST = HERE / ".suite_manifest.txt"


def load_manifest(path=None):
    path = path or MANIFEST
    if path.exists():
        try:
            return [l.strip() for l in
                    path.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        except OSError:
            return []
    return []


def save_manifest(names, path=None):
    path = path or MANIFEST
    try:
        path.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[WARN] 无法写入基线清单 {path.name}（不影响本轮结果）: {e}")


def run_guard_preflight():
    """跑一次 _perturb_guard 自检：清 scratch 临时副本 + 查源码残留变异标记。

    为什么父进程必须自己兜一道：本入口用 subprocess 的超时杀子进程，而 Windows
    上**没有可捕获的 SIGTERM** —— TerminateProcess 是无条件终止，脚本的 finally
    与 atexit 统统不执行，源码会被留在变异态（`if False:  # 扰动：...`）。
    护栏装在子进程里，挡不住这一刀；只能由父进程在跑前跑后各查一次。

    返回 (rc, out)；rc is None 表示护栏脚本不存在（跳过，不算失败）。
    """
    if not PERTURB_GUARD.is_file():
        return None, "（未找到 _perturb_guard.py，跳过残留检查）"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.run([sys.executable, str(PERTURB_GUARD)],
                           cwd=str(ROOT), env=env,
                           capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 1, "护栏检查超时（>120s）"
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")


def run_one(path, timeout=None):
    """在子进程中跑单个套件，返回 (name, n_pass, n_fail, status, seconds, output)。"""
    timeout = timeout or TIMEOUT
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"   # 需要 Qt 的套件不弹窗
    env.setdefault("XC_LOG_DIR", TEST_LOG_DIR)   # 套件日志→临时目录，不污染用户真实 debug.log

    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT), env=env,
            capture_output=True, timeout=timeout,
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return path.name, 0, 1, f"TIMEOUT(>{timeout}s)", time.time() - t0, ""

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
    # 扰动（元测试）脚本的输出里会混着「被它检验的那个判据套件」的统计行 ——
    # 脚本为了留诊断信息会把套件汇总一并打出来（见 _perturb_199.py 的
    # `print("   %s  returncode=%d" % (tail, rc))`），于是裸 `PASS=` 会**先**被
    # re.search 抓到，把「套件的 38 项」当成「扰动的 4 条命中」。
    # 故**带命名空间前缀的契约行优先**：`PERTURB ` 不会出现在任何套件输出里。
    # （与 v4.186.0 那条教训不冲突：那里拒绝的是「为同一种东西——套件统计——
    #   加第 N 种正则」，格式还会继续长；这里是另一种东西的专用前缀。）
    m = re.search(r"PERTURB\s+PASS=(\d+)\s+FAIL=(\d+)", out)
    if m:
        n_pass, n_fail = int(m.group(1)), int(m.group(2))
        return (name, n_pass, n_fail,
                "ok" if (rc == 0 and n_fail == 0) else "FAILED", secs, out + err)
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
    ap.add_argument("--with-perturb", action="store_true",
                    help="追加跑根目录的扰动脚本（元测试：改坏源码验判据，数分钟）")
    ap.add_argument("--perturb-only", default="",
                    help="只跑文件名含该子串的扰动脚本（隐含 --with-perturb）。"
                         "分段跑用：沙箱对每轮删除次数有上限，而扰动脚本"
                         "每个 case 都要建/删一个临时副本，很吃配额")
    ap.add_argument("--refresh-perturb-manifest", action="store_true",
                    help="重建扰动脚本基线清单（故意删除/改名扰动脚本后使用）")
    args = ap.parse_args()

    clean_stale_fixtures()          # 先清上次残留的 tests/test_zz_*.py，再发现套件
    all_suites = discover()
    # v4.186.0（P1-11 修）：基线比对——基线里的套件不见了要炸出来，不能静默缩水
    # 2026-10-03 补：基线自身也要过滤夹具前缀。否则「夹具曾被写进基线 → 这次被
    # discover 排除」会让每一轮都报一条永不复原的 MISSING（假故障）。
    baseline = [n for n in load_manifest() if not n.startswith(FIXTURE_PREFIX)]
    missing = []
    if baseline and not args.refresh_manifest:
        have = {s.name for s in all_suites}
        missing = [n for n in baseline if n not in have]
    if all_suites and (not missing or args.refresh_manifest):
        save_manifest([s.name for s in all_suites])

    # 扰动脚本清单比对：**默认就做**（只查文件在不在，不执行，成本为零）。
    # 这批脚本不在 discover() 的扫描范围内，没有这道比对就是「删了也没人知道」；
    # 有 --only 时跳过（否则只想跑一个套件也会被一堆 MISSING 淹没）。
    perturb_files = discover_perturb()
    p_baseline = load_manifest(PERTURB_MANIFEST)
    p_missing = diff_perturb_manifest(
        perturb_files, p_baseline,
        skip=(args.refresh_perturb_manifest or bool(args.only)))
    if perturb_files and not p_missing:
        save_manifest([p.name for p in perturb_files], PERTURB_MANIFEST)

    suites = all_suites
    if args.only:
        suites = [s for s in suites if args.only in s.name]
    if not suites:
        # 允许「只跑扰动」：--perturb-only 常常要配合「不跑套件」（分段跑省时间、
        # 也省沙箱删除配额），此时 suites 为空是预期状态而不是错误。
        if not (args.with_perturb or args.perturb_only):
            print("未发现任何测试套件（tests/test_*.py 或 tests/regression.py）")
            return 0 if args.list else 1

    if args.list:
        print(f"发现 {len(suites)} 个测试套件：")
        for s in suites:
            print(f"  - tests/{s.name}")
        print(f"\n发现 {len(perturb_files)} 个扰动脚本（元测试，需 --with-perturb 才执行）：")
        for p in perturb_files:
            print(f"  - {p.name}")
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

    # ---- 扰动脚本（元测试）------------------------------------------------
    # 默认不跑：每个脚本都要「改坏源码 → 跑判据 → 期望转红 → 还原」，耗时是判据
    # 套件的数倍，且必须独占（它动的是源码）。用 --with-perturb 打开。
    p_rows = []
    for n in p_missing:
        p_rows.append((n, 0, 0, "MISSING", 0.0,
                       f"扰动基线清单里的脚本不存在：{n}"))
        print(f"\n>>> {n}")
        print("   -> MISSING（基线里的扰动脚本不见了；"
              "故意删除请用 --refresh-perturb-manifest）")

    # 注：--only 只筛**套件**，不影响扰动（扰动用自己的 --perturb-only）。
    if args.with_perturb or args.perturb_only:
        _p_list = perturb_files
        if args.perturb_only:
            _p_list = [p for p in perturb_files if args.perturb_only in p.name]
        print("\n" + "=" * 62)
        print(f"扰动脚本（元测试）· 共 {len(_p_list)} 个"
              + (f"（过滤：{args.perturb_only}）" if args.perturb_only else ""))
        print("=" * 62)
        # 前置护栏：清 scratch 副本 + 查源码残留变异标记。残留着跑 → 判据基线
        # 本身就红 → 每个 case 都报「变异后仍绿」，看起来像业务代码坏了，排查成本极高。
        grc, gout = run_guard_preflight()
        if grc is None:
            print(f"   {gout}")
        elif grc != 0:
            print("   [护栏] 源码疑似残留上次的变异标记，已中止扰动（先还原再跑）：")
            print("\n".join("   " + l for l in gout.splitlines()[-40:]))
            p_rows.append(("<护栏预检>", 0, 1, "FAILED", 0.0, gout))
            _p_list = []
        for s in _p_list:
            print(f"\n>>> {s.name}")
            name, n_pass, n_fail, status, secs, out = run_one(s, PERTURB_TIMEOUT)
            # v4.210.x：把「环境性删除配额失败」单独标出来。
            # 沙箱对**每轮**删除次数有阈值，超了之后的删除会被拦、进程直接被杀；
            # 而扰动脚本每个 case 都要 tempfile.mkstemp + os.remove 一份临时副本，
            # 21 个脚本全量跑必然会撞到。表现是「脚本 PASS=0 且几乎没有输出」——
            # 极易被误判成代码回归。实测 2026-10-03 全量跑废掉 11 个脚本才发现。
            if status != "ok" and "SAFE_DELETE_BULK_CONFIRM_REQUIRED" in out:
                status = "BLOCKED"
                print("   [WARN] 环境性失败：沙箱「每轮删除配额」已耗尽 —— "
                      "脚本删临时副本时被拦、进程被杀，**不是代码问题**。")
            p_rows.append((name, n_pass, n_fail, status, secs, out))
            for line in out.splitlines():
                if ("PERTURB PASS=" in line or "[FAIL]" in line or "[未命中]" in line
                        or "ERR " in line or "未命中" in line):
                    print("   " + line.strip())
            print(f"   -> {status}  (PASS={n_pass} FAIL={n_fail}, {secs:.1f}s)")
            if status != "ok" and args.verbose and out.strip():
                print("   ---- 完整输出 ----")
                print("\n".join("   " + l for l in out.splitlines()[-80:]))
            if status != "ok":
                # 超时/崩溃时子进程走不到 finally（Windows 上 TerminateProcess
                # 不可捕获），源码可能被留在变异态 —— 立刻兜底还原，否则后面
                # 每个脚本都会假红，把一次超时放大成满屏失败。
                _grc, _gout = run_guard_preflight()
                if _grc not in (0, None):
                    print("   [护栏] 已尝试兜底还原：")
                    print("\n".join("   " + l for l in _gout.splitlines()[-12:]))
        if _p_list:
            run_guard_preflight()      # 后置兜底：确认收工时源码是干净的

    total_pass = sum(r[1] for r in rows)
    total_fail = sum(r[2] for r in rows)
    failed = [r for r in rows if r[3] != "ok"]
    p_pass = sum(r[1] for r in p_rows)
    p_fail = sum(r[2] for r in p_rows)
    p_failed = [r for r in p_rows if r[3] != "ok"]

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

    # 扰动（元测试）单独统计：它们不是判据套件，不该混进上面的判据总数 ——
    # 否则 release_check 解析到的「回归 PASS 数」会随跑不跑扰动而跳变。
    if p_rows:
        print("-" * 62)
        print(f"扰动脚本：{len(p_rows)} 个 / PASS={p_pass} FAIL={p_fail}")
        for name, n_pass, n_fail, status, secs, _ in p_rows:
            mark = {"ok": "OK  ", "EMPTY": "EMPT", "MISSING": "MISS",
                    "BLOCKED": "BLKD"}.get(status, "FAIL")
            print(f"  [{mark}] {name:<34} PASS={n_pass:<4} FAIL={n_fail}")
        if p_failed:
            print(f"\n失败扰动：{', '.join(r[0] for r in p_failed)}")
        _blocked = [r for r in p_rows if r[3] == "BLOCKED"]
        if _blocked:
            print(f"\n[WARN] 其中 {len(_blocked)} 个是**环境性**「删除配额」失败（不是代码问题）："
                  f"{', '.join(r[0] for r in _blocked)}")
            print("   沙箱对每轮删除次数有阈值，超了之后的删除会被拦、进程被杀；"
                  "扰动脚本每个 case 都要建/删一份临时副本，最吃配额。")
            print("   对策：① 换一轮重跑；② 用 --perturb-only <子串> 分段跑。")
    elif not (args.with_perturb or args.perturb_only) and perturb_files:
        print("-" * 62)
        print(f"扰动脚本：{len(perturb_files)} 个已在基线内"
              f"（用 --with-perturb 执行，本次未跑）")
    print("=" * 62)
    return 1 if (failed or p_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
