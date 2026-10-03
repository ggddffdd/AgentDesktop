# -*- coding: utf-8 -*-
"""扰动脚手架自身的判据（run_all 的「元测试」这一路）。

为什么给「测试基建」也写判据：

  这套机制存在的理由是**防止扰动脚本静默腐烂** —— 那 21 个脚本原先不在
  discover() 的扫描范围内、也没有基线清单，被误删/改名没人知道；
  更隐蔽的是没人复跑，跨版本漂移后 case 会集体变哑弹（2026-10-03 实测一次
  跑出 8 条：锚点写死旧版本号 / 变异落在判据观测范围之外 / 判据代理太弱）。

  而**机制本身坏掉**（契约漏了某个脚本 / 清单比对失效 / 护栏没接上）的表现，
  恰恰是「什么都不发生」—— 防线空转，且只在该起作用的那一刻才暴露。
  所以这里把「必须为真」的性质钉死。

判据分三类（独立运行：python tests/test_perturb_harness.py）：

  A 契约覆盖   —— 每个扰动脚本都输出 `PERTURB PASS=/FAIL=`，回归入口才看得见
  B 机制纯函数 —— classify 解析 / diff_perturb_manifest 清单比对（喂合成输入）
  C 接线       —— 护栏全覆盖、清单与磁盘一致、run_all 开关存在、临时夹具不污染回归

C 组里 C8~C16 守的是「防线自身的假阳性/盲区」：护栏把文档里的标记误判成残留
（瘫痪整条防线），以及 case_newfile 造的临时夹具被强杀后残留（三重污染）。
这两条都不是「功能少了一块」，而是「机制在错误的方向上生效」—— 更难发现。

本套件全部是**静态 + 纯函数**判据，不跑任何扰动（秒级）。
"""
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run_all as R  # noqa: E402

FAIL = []
CHECKED = 0


def check(name, cond, detail=""):
    global CHECKED
    CHECKED += 1
    if cond:
        print("  [OK  ] %s" % name)
    else:
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))
        FAIL.append(name)


def read(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def strip_comments(src):
    """把 `#` 之后的注释剥掉（保留行结构）。

    契约行必须出现在**真实代码**里：写在注释里当说明不算数 —— 否则
    「源码里搜得到 PERTURB PASS=」可以在没有实际输出的情况下为真。
    """
    out = []
    for line in src.splitlines():
        i = line.find("#")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


PERTURB_FILES = R.discover_perturb()

print("=" * 66)
print("扰动脚手架自身判据（%d 个扰动脚本）" % len(PERTURB_FILES))
print("=" * 66)

# ---------------------------------------------------------------- A 契约覆盖
print("\n--- A 契约覆盖 ---")
check("A1 发现了扰动脚本（且数量不低于已知规模）",
      len(PERTURB_FILES) >= 20,
      "只发现 %d 个 —— discover_perturb 可能被改坏了" % len(PERTURB_FILES))

_no_contract, _not_print, _no_exit = [], [], []
for p in PERTURB_FILES:
    src = read(str(p))
    probe = strip_comments(src)
    if "PERTURB PASS=" not in probe:
        # 剥掉注释后仍在 → 证明不是「注释里写了一句说明」冒充契约
        _no_contract.append(p.name)
    elif "print(" not in src.split("PERTURB PASS=")[0][-80:].replace("\n", " "):
        _not_print.append(p.name)
    if ("sys.exit" not in probe) and ("SystemExit" not in probe):
        _no_exit.append(p.name)

check("A2 每个扰动脚本都在真实代码里输出 PERTURB PASS=（不是注释里的说明）",
      not _no_contract, "缺契约：%s" % _no_contract)
check("A3 契约行确实由 print 输出（而非别的字符串字面量）",
      not _not_print, "疑似不在 print 里：%s" % _not_print)
check("A4 每个扰动脚本都有非零退出口（哑弹必须能被自动化发现）",
      not _no_exit, "没有 exit 路径：%s" % _no_exit)

# ------------------------------------------------------------ B 机制纯函数
print("\n--- B 机制纯函数（喂合成输入）---")
_c = [
    ("全命中（rc=0）",       0, "PERTURB PASS=13 FAIL=0\n",         13, 0, "ok"),
    ("有哑弹（rc=1）",       1, "PERTURB PASS=12 FAIL=1\n未命中\n", 12, 1, "FAILED"),
    ("哑弹但 rc=0（防漏）",  0, "PERTURB PASS=12 FAIL=1\n",         12, 1, "FAILED"),
    ("rc=1 且无统计",        1, "nothing\n",                          0, 1, "FAILED"),
    # 实测踩到：扰动脚本会把**被检验套件的汇总行**一并打出来（含裸 PASS=），
    # 而 re.search 先抓到它 → 把「套件的 38 项」当成「扰动的 4 条命中」。
    # 契约行必须**优先**于裸 PASS= 被采纳。
    ("混合套件输出（契约优先）", 0,
     "汇总：38 项 / PASS=38 FAIL=0\nPERTURB PASS=4 FAIL=0\n",          4, 0, "ok"),
]
_ok_all = True
for _name, _rc, _out, _wp, _wf, _ws in _c:
    _, _p, _f, _st, _, _ = R.classify("x.py", _rc, _out, "", 0.0)
    if not (_p == _wp and _f == _wf and _st == _ws):
        _ok_all = False
        print("        ↳ %s 得到 PASS=%d FAIL=%d status=%s" % (_name, _p, _f, _st))
check("B1 classify 正确解析扰动契约行（含 rc=0 但 FAIL>0 的防漏用例）", _ok_all)

check("B2 classify 对「rc=0 且无统计」判 EMPTY（视为失败，不是假绿）",
      R.classify("x.py", 0, "nothing\n", "", 0.0)[3] == "EMPTY")

_fs = [Path("_perturb_199.py"), Path("_perturb_200.py")]
check("B3 diff：脚本都在 → 无缺项",
      R.diff_perturb_manifest(_fs, ["_perturb_199.py", "_perturb_200.py"]) == [])
check("B4 diff：基线里有、磁盘上没有 → 报出来",
      R.diff_perturb_manifest(_fs, ["_perturb_199.py", "_perturb_GONE.py"]) == ["_perturb_GONE.py"])
check("B5 diff：首次跑（空基线）不报缺项",
      R.diff_perturb_manifest(_fs, []) == [])
check("B6 diff：--only 时跳过清单比对",
      R.diff_perturb_manifest(_fs, ["_perturb_GONE.py"], skip=True) == [])

# ------------------------------------------------------------------- C 接线
print("\n--- C 接线 ---")
_no_guard = [p.name for p in PERTURB_FILES
             if "_perturb_guard" not in strip_comments(read(str(p)))]
check("C1 每个扰动脚本都接入统一护栏（快照 + 三重还原 + 残留预检）",
      not _no_guard, "未接入：%s" % _no_guard)

check("C2 护栏自身不算作扰动 case",
      R.PERTURB_GUARD.name in R.PERTURB_EXCLUDE)

_man = R.PERTURB_MANIFEST
check("C3 扰动基线清单存在（由 run_all 跑一轮后生成并入库）",
      os.path.isfile(str(_man)),
      "缺 %s —— 跑一次 python tests/run_all.py 生成" % _man.name)

if os.path.isfile(str(_man)):
    _base = R.load_manifest(_man)
    _disk = {p.name for p in PERTURB_FILES}
    _only_disk = sorted(_disk - set(_base))     # 新增脚本没进清单 → 清单没跟上
    _only_base = sorted(set(_base) - _disk)     # 清单里有、磁盘上没了 → 被删/改名
    check("C4 清单与磁盘双向一致（新增脚本被记入、删除脚本会被报出）",
          not _only_disk and not _only_base,
          "只在磁盘：%s / 只在清单：%s" % (_only_disk, _only_base))

_src = read(os.path.join(HERE, "run_all.py"))
check("C5 回归入口暴露 --with-perturb 与 --refresh-perturb-manifest",
      "--with-perturb" in _src and "--refresh-perturb-manifest" in _src)
check("C6 扰动超时对最重的脚本留有余量（实测最重 44s）",
      R.PERTURB_TIMEOUT >= 300, "PERTURB_TIMEOUT=%s" % R.PERTURB_TIMEOUT)
check("C7 跑扰动前后都做护栏检查（父进程兜底 Windows 不可捕获的 TerminateProcess）",
      _src.count("run_guard_preflight()") >= 3,
      "只出现 %d 次" % _src.count("run_guard_preflight()"))

# ---------------------------------------------------- C8/C9 护栏标记识别
# 真实事故（2026-10-03）：run_all.py 的模块文档里举了一个变异例子
# （形如 `if False:  # 扰动：...`），护栏把它当成「上次强杀留下的残留」，
# **直接中止了整轮扰动** —— 21 个脚本一个都没跑。
# 一个假阳性就能让整条防线瘫痪，所以这条性质必须钉死。
import _perturb_guard as guard  # noqa: E402

_DIRTY = []   # 探针文件清理失败的清单（不许静默吞掉）


def _probe(fname, content):
    """在 tests/ 下临时落一个探针文件，跑一次残留检测后删掉。

    清理必须带重试、并且**把失败暴露出来**：实测撞到过 os.remove 被 Windows
    短暂占用挡下（Defender 实时扫描/索引），而 `except OSError: pass` 会静默
    留下一份**带变异标记的垃圾文件** —— 本套件自己制造脏数据，比不清理更糟
    （而且会给后续的残留预检添乱）。这里复用护栏里那套带重试的删除。
    """
    p = os.path.join(HERE, fname)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    try:
        return any(fname in h[0] for h in guard.find_leftovers())
    finally:
        if not guard._remove_retry(p):
            _DIRTY.append(p)


check("C8 文档/字符串里引用标记不算残留（假阳性会瘫痪整条防线）",
      not _probe("zz_h_probe_doc.py", '"""举例：if False:  # 扰动：省略校验"""\nY = 1\n')
      and not _probe("zz_h_probe_cmt.py", "# 扰动：整行注释\nZ = 1\n"),
      "docstring / 整行注释里的标记被误判为残留")
check("C9 真残留仍必须被抓到（代码行 + 行尾注释，含缩进情形）",
      _probe("zz_h_probe_res.py", "if False:  # 扰动：短路\n    pass\n")
      and _probe("zz_h_probe_ind.py", "def f():\n    if True:  # 扰动：短路\n        return 1\n"),
      "真的变异残留没被抓到 —— 护栏失去意义")
check("C10 交付态源码里没有残留变异标记（干净仓库上预检为真）",
      not guard.find_leftovers(),
      "发现残留：%s" % (guard.find_leftovers() or [])[:3])

# ------------------------------------------------- C11~C14 临时夹具（新发现）
# 真实事故（2026-10-03）：_perturb_2092.py 的 case_newfile 从无到有造了一个
# tests/test_zz_banner_probe.py 来测「集合登记式判据」。它**既不匹配护栏的
# scratch 命名约定、也不在 arm() 的快照里**，于是被父进程强杀（TerminateProcess
# 不走 finally）后留在了磁盘上，一次残留造成三重污染：
#   ① 被测套件 B11 把它当成「第 4 个同类文件」→ 之后每轮都假红；
#   ② run_all 的 discover() 把它当套件收集 → 写进 .suite_manifest.txt 固化；
#   ③ 它带 OK 横幅却无统计输出 → classify 判 EMPTY → 回归入口整体退出 1。
# 下面四条把这个盲区钉死。
_fx = os.path.join(HERE, "test_zz_h_probe_fixture.py")
with open(_fx, "w", encoding="utf-8", newline="") as f:
    f.write('print("=== FIXTURE_OK ===")\n')
try:
    _in_scratch = os.path.abspath(_fx) in {os.path.abspath(p)
                                          for p in guard._scratch_files()}
    R.clean_stale_fixtures(verbose=False)      # 真调一次，看它清不清
    _swept = not os.path.exists(_fx)
finally:
    if not guard._remove_retry(_fx):
        _DIRTY.append(_fx)
check("C11 护栏把 tests/test_zz_*.py 纳入 scratch 预检清理（残留夹具必须被自动清掉）",
      _in_scratch,
      "夹具不在 _scratch_files() 扫描范围内 —— 强杀一次就永久残留")

check("C12 run_all 不把临时夹具当套件收集（test_zz_* 前缀被排除）",
      (not R.is_real_suite(Path("test_zz_banner_probe.py"))
       and R.is_real_suite(Path("test_toast_204.py"))
       and not any(s.name.startswith(R.FIXTURE_PREFIX) for s in R.discover())),
      "discover 会把夹具算成套件 —— 残留一次就固化进基线清单")

check("C13 基线清单里不含夹具行（陈旧清单不产生永不复原的假 MISSING）",
      not any(n.startswith(R.FIXTURE_PREFIX) for n in R.load_manifest()),
      "基线含夹具：%s" % [n for n in R.load_manifest()
                          if n.startswith(R.FIXTURE_PREFIX)])

# 注意：这一条**不能**写成「源码里搜得到 clean_stale_fixtures()」—— 变异验证时
# 把调用改成 `pass  # clean_stale_fixtures()` 后，注释里的字符串照样让判据为真，
# 判据没红。这正是 _perturb_2092.py 注释里那条教训：判据扫的是字符，不是意图。
# 故拆成两层：C14 验行为（真删得掉），C15 验接线（在语句位置、且在 discover 之前）。
check("C14 清理函数行为正确（真把 tests/test_zz_*.py 删掉）",
      _swept, "clean_stale_fixtures() 跑了但夹具还在")

_call = re.search(r"^\s*clean_stale_fixtures\(\)", _src, re.M)
check("C15 回归入口在发现套件**之前**先清理残留夹具（语句位置，注释骗不过）",
      bool(_call) and _call.start() < _src.find("all_suites = discover()"),
      "没有在语句位置调用，或调用发生在 discover() 之后")

check("C16 套件自己的探针文件已清理干净（不留下带标记的垃圾）",
      not _DIRTY, "清理失败：%s" % _DIRTY)

# ------------------------------------------------------------------- 收尾
print("\n" + "=" * 66)
print("判据: %d  通过: %d  失败: %d" % (CHECKED, CHECKED - len(FAIL), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
print("PASS=%d FAIL=%d" % (CHECKED - len(FAIL), len(FAIL)))
if FAIL:
    print("RESULT: HAS FAIL")
sys.exit(1 if FAIL else 0)
