# -*- coding: utf-8 -*-
r"""v4.260.0 扰动脚本：结构性双份常驻判据（MRO 遮蔽 / 判据测影子 / 工具注册唯一）。

反向照妖镜：确认常驻判据**不是空转的** —— 把扫描弄瞎、把基线清空、把源码改回
双份实现，判据都必须翻红。SKIP/MISS = 哑弹。

V1  扫描集清空                → 探针瞎了必须红（G1/G2），防"永远绿"
V2  基线清空                  → 存量项变成"新增"必须红（M1）
V3  委托桩识别失效            → tools.tool_screenshot 算回独立实现（T1/T2）
V4  MRO 扫描跳过 _tool_outcome → 基线扫不到必须红（G2），防"谓词被静默阉割"
V5  tests 目录扫不到           → 影子扫描空转必须红（C2）
V6  ★源码：tool_screenshot 改回内联实现 → 双份独立实现复活（T1）
V7  ★源码：AgentResultMixin 写回内联判定 → 豁免项不再是委托桩（M2）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_structure_twin_260.py")
TL = os.path.join(ROOT, "tools.py")
RM = os.path.join(ROOT, "agent_result_mixin.py")

DELEGATE = (
    "    from system_control_tools import tool_screenshot as _impl\n"
    "    return _impl(cfg, app_dir, args, progress=progress,\n"
    "                 stop_event=stop_event, should_stop=should_stop)"
)

CASES = [
    ("V1 扫描集清空", JUDGE,
     '            if fn.endswith(".py") and not SCAFFOLD_RE.match(fn):',
     '            if False:  # 扰动：扫描集清空\n'
     '                pass'),

    ("V2 基线清空", JUDGE,
     'BASELINE_MRO = {\n    ("AgentWorker", "_tool_outcome"):',
     'BASELINE_MRO = {\n    ("__none__", "__none__"):  # 扰动：清空基线'),

    ("V3 委托桩识别失效", JUDGE,
     "    if not body or len(body) > 3:",
     "    if not body or len(body) > 0:  # 扰动：委托桩识别失效"),

    ("V4 MRO 扫描跳过 _tool_outcome", JUDGE,
     'NOISE_NAMES = {\n    "__init__",',
     'NOISE_NAMES = {\n    "_tool_outcome",  # 扰动：把基线项当噪声\n    "__init__",'),

    ("V5 tests 目录扫不到", JUDGE,
     '    tdir = os.path.join(ROOT, "tests")',
     '    tdir = os.path.join(ROOT, "tests_扰动不存在")'),

    ("V6 ★源码 tool_screenshot 改回内联", TL, DELEGATE,
     "    return \"扰动：双份实现复活\", [], []"),

    ("V7 ★源码 result_mixin 写回内联判定", RM,
     "        return tool_contract.tool_outcome(\n"
     "            tool_result, name, result_str, failed_checker)",
     "        if tool_result is not None:  # 扰动：又自己判一份\n"
     "            try:\n"
     "                return (bool(tool_result.ok), "
     "str(getattr(tool_result, 'msg', '') or ''))\n"
     "            except Exception:\n"
     "                return (True, None)\n"
     "        return (True, None)"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [JUDGE, TL, RM]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            if cnt != 1:
                misses.append(name)
                print("SKIP %s: old 串出现 %d 次，非唯一（无法安全替换）" % (name, cnt))
                continue
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new, 1))
            try:
                rc, out = run_judge()
                failed = (rc != 0) and ("FAIL" in out)
            except Exception as e:  # noqa: BLE001
                failed, out = False, "judge 崩了: %r" % e
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
