# -*- coding: utf-8 -*-
"""扰动验证 v4.230.0：修复 B 点 —— 失败节点下游显式标 skipped（不硬 break）。

手法：备份 task_graph.py 真实字节 → 逐条退化修复点 →
跑 tests/test_task_graph_fail_skip.py → 期望对应判据翻红 → 还原。

验收 = 哑弹清零（每个退化 case 都必须让至少一个期望 [FAIL] 出现）。

⚠️ 所有 old 串一律按真实字节程序化截取（grab），不手抄 —— 手抄含中文/缩进的
   源码必被转义吃掉（v4.228 踩过）。
⚠️ 退化 case 只取「误标 / 不记录 / 退回 break」三类 —— 任何「少标下游」的退化都会
   让 pending 永远不在 all_done 里 → 整图 continue 死循环，故刻意避开。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_task_graph_fail_skip.py")
TG = "task_graph.py"

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


def clean_pycache():
    for dirpath, dirnames, _files in os.walk(ROOT):
        if "_dev_history" in dirpath or "dist" in dirpath or "backup" in dirpath:
            continue
        for d in list(dirnames):
            if d == "__pycache__":
                import shutil
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    red = [l.strip() for l in p.stdout.splitlines() if "[FAIL]" in l]
    return (not red) and p.returncode == 0, red


def grab(fp, start, end=None):
    """从真实字节里按「起始标记 + 结束标记」截取连续原文。"""
    src = read_raw(fp).decode("utf-8").replace("\r\n", "\n")
    i = src.find(start)
    if i < 0:
        raise SystemExit("原串起始标记未命中：%s / %r" % (fp, start[:60]))
    if end is None:
        return src[i:]
    j = src.find(end, i + len(start))
    if j < 0:
        raise SystemExit("原串结束标记未命中：%s / %r" % (fp, end[:60]))
    return src[i:j]


# ---- 原串（全部 compute from real bytes）----
_HAS_FAILED_BLOCK = grab(TG, "                if has_failed:", "                if has_incomplete:")
_RESULT_SKIPPED = '"skipped": True,'
_STATUS_SKIPPED = 't.status = "skipped"'

CASES = [
    # V1 照妖镜：has_failed 分支退回原始硬 break → D 永远 pending → 判据翻红
    ("V1 退回原始硬 break（下游不再标 skipped）",
     TG, _HAS_FAILED_BLOCK,
     "                if has_failed:\n                    break  # 扰动：回到原始硬 break\n",
     ["D = skipped", "D 仍 skipped"]),

    # V2 反向照妖镜：result 不标 skipped 键 → part_d 观测判据翻红
    ("V2 result 不标 skipped 键（不可观测）",
     TG, _RESULT_SKIPPED,
     '  # 扰动：去掉 skipped 键',
     ["D result 标记 skipped 原因"]),

    # V3 照妖镜：status 误标成 failed（覆盖一级/多级/to_dict）→ 全红
    ("V3 status 误标成 failed（冒充失败而非跳过）",
     TG, _STATUS_SKIPPED,
     't.status = "failed"  # 扰动：误标成 failed\n',
     ["D = skipped", "D/E2/F 全 skipped", "D 仍 skipped", "D to_dict 暴露 skipped 状态"]),
]

HIT, MISS = [], []
try:
    _backup[TG] = read_raw(TG)
    _crlf[TG] = is_crlf(TG)

    print("基线自检（未变异时必须全绿，否则判据本身坏了）：")
    ok, red = run_test()
    if not ok:
        print("  [基线红] %s" % "; ".join(red[:6]))
        print("  基线不绿就停止扰动 —— 否则「命中」无意义。")
        sys.exit(2)
    print("  基线全绿 ✔\n")

    _norm = _backup[TG].decode("utf-8").replace("\r\n", "\n")
    _unmatched = [n for n, fp, old, _new, _e in CASES if old not in _norm]
    if _unmatched:
        print("  [中止] 以下用例原串未命中真实字节（防静默失效）：")
        for n in _unmatched:
            print("    - " + n)
        sys.exit(2)
    print("  原串预检：%d/%d 全部命中真实字节 ✔\n" % (len(CASES), len(CASES)))

    for name, fp, old, new, expect in CASES:
        write_raw(fp, _backup[fp])   # 每条都从原始备份重置
        clean_pycache()
        src = _norm
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] %s —— 原串未命中" % name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))

        _is_red, red = run_test()
        joined = "\n".join(red)
        hit = all(e in joined for e in expect)
        print("  [%s] %s → 红 %d 条%s"
              % ("HIT " if hit else "MISS", name, len(red),
                 "" if hit else "；期望含 %s" % expect))
        (HIT if hit else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
