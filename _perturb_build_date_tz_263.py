"""扰动：构建日期新鲜度判据（v4.262.0 fix1 时区跨日）改坏必红。

背景：v4.262.0 推送后 CI #39 报红
    [FAIL] APP_BUILD_DATE 新鲜（≤2 天） —— 2026-10-12（-1 天前）
原因：CI runner 走 UTC，构建机在 GMT+8。凌晨（本地 01:05）打包时本机日历日
已经是 10-12，而 CI 的今天（UTC 17:33）还是 10-11 → age = -1，被
`0 <= age <= 2` 判成不合格。**这是假阳性，不是日期忘了更新。**

修正把区间放宽到下界 -1，但保持 fail-closed（未来 ≥2 天 / 真过期仍红）。
本脚本要证明：<｜hy_place▁holder▁no▁813｜>任何一边（回滚剔除 -1、放宽上界、整个函数躺平）都会被
判据抓住 —— 即这次是**纠偏**，不是放水。

变体：
  V1 BUILD_DATE_MIN_AGE 回退 -1 → 0   （等于撤回时区修正 → D9 红）
  V2 build_date_fresh 直接 return True（整个门禁躺平 → D7 红）
  V3 BUILD_DATE_MAX_AGE 2 → 999       （过期也放行 → D7 红）
"""

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _perturb_guard  # noqa: E402  （快照 + SIGTERM/SIGINT/atexit 三重还原）

RC = os.path.join(HERE, "release_check.py")
JUDGE = os.path.join(HERE, "tests", "test_core_fix_188.py")


def grab(path, start, end):
    """从 path 截取 start..end（含端点）原文，作为替换目标。"""
    txt = io.open(path, encoding="utf-8").read()
    i = txt.index(start)
    j = txt.index(end, i) + len(end)
    return txt[i:j]


# ① 下界常量
_OLD_MIN = "BUILD_DATE_MIN_AGE = -1"
_NEW_MIN = "BUILD_DATE_MIN_AGE = 0"

# ② 新鲜度判定本体
_OLD_BODY = grab(RC, "def build_date_fresh(age_days) -> bool:",
                 "    return BUILD_DATE_MIN_AGE <= age <= BUILD_DATE_MAX_AGE")
_NEW_BODY = (
    "def build_date_fresh(age_days) -> bool:\n"
    '    """age_days：今天 − 构建日期（天）。负=构建日期在未来。"""\n'
    "    return True"
)

# ③ 上界常量（单独替换、不要把 _OLD_MIN 也吃掉）
_OLD_MAX = "BUILD_DATE_MAX_AGE = 2"
_NEW_MAX = "BUILD_DATE_MAX_AGE = 999"

CASES = [
    ("V1 下界回退 -1→0（撤回时区修正）", RC, _OLD_MIN, _NEW_MIN, ["D9"]),
    ("V2 build_date_fresh 躺平恒真", RC, _OLD_BODY, _NEW_BODY, ["D7"]),
    ("V3 上界放宽 2→999（过期放行）", RC, _OLD_MAX, _NEW_MAX, ["D7"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    r = subprocess.run([sys.executable, JUDGE], cwd=HERE,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    red = []
    for line in out.splitlines():
        s = line.strip()
        # 三种失败行样式：release_check 的 `[FAIL] xxx`、fix188 的 `FAIL D9 xxx`、
        # 以及 run_all 汇总里的 ❌。命中判定要按「断言名」匹配，故统一取正文。
        if s.startswith("❌"):
            red.append(s)
        elif s.startswith("[FAIL]"):
            red.append(s[len("[FAIL]"):].strip())
        elif s.startswith("FAIL "):
            red.append(s[len("FAIL "):].strip())
    return red, out


def main():
    _perturb_guard.arm([RC])  # 防被强杀后变异残留（C1 契约）
    npass = nfail = 0
    verbose_tail = []

    # 基线：未变异时必须全绿（否则变异后的「红」没有意义）
    base_red, _ = run_judge()
    if base_red:
        print("BASELINE_RED 基线本身就是红的，判据不可用：")
        for x in base_red[:5]:
            print("   ", x)
        nfail += 1
    else:
        print("BASELINE GREEN（判据当前全绿）")

    for label, path, old, new, want in CASES:
        src = io.open(path, encoding="utf-8").read()
        if old not in src:
            print("SKIP  %s —— 变异点在源码中找不到" % label)
            nfail += 1
            continue
        io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
        try:
            red, out = run_judge()
        finally:
            io.open(path, "w", encoding="utf-8").write(src)

        hit = [w for w in want if any(w in x for x in red)]
        if hit:
            print("PASS  %s → 命中 %s" % (label, "、".join(hit)))
            npass += 1
        else:
            print("MISS  %s → 期望命中 %s，实际 %s"
                  % (label, "、".join(want), red[:3] or "无红行"))
            nfail += 1
            if not red:
                verbose_tail.append(out[-600:])

    print()
    for t in verbose_tail:
        print("---- 变异体输出尾 ----")
        print(t)
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
