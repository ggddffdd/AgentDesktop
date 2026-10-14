"""扰动：数字人自定义背景图（P2）接线改坏必红。

证明 v4.263.0 的「选背景图 → 图生图合成首帧 → keyframe 强锁」一旦接线被改回去，
会被 tests/test_twin_bg_image_264.py 抓红 —— 不是靠教，是靠判据护栏。

变体：
  V1 tool_image_gen 的 image= 被去掉（不再传本人+背景两张参考图）
     → 合成首帧失效，U3/U4 红
  V2 指纹不纳入背景图（"bg" 字段被删）
     → 换背景图不被识别、旧缓存可能被复用，U1a/U1b 红
  V3 合成失败不再回退（改成直接抛）
     → bg 模式失败时整段崩、成片消失，U7/U8 红（绝不允许哑弹）
  V4 bg 模式不再强制 keep_bg=True（反向置 False）
     → 合成图背景不被锁，U6 红
"""

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import _perturb_guard  # noqa: E402  （快照 + SIGTERM/SIGINT/atexit 三重还原）

SRC = os.path.join(HERE, "digital_twin_panel.py")
JUDGE = os.path.join(HERE, "tests", "test_twin_bg_image_264.py")

CASES = [
    ("V1 tool_image_gen 的 image= 被去掉（不传本人+背景）",
     "                size=self.resolution, image=[portrait_uri, self.bg_image],",
     "                size=self.resolution, image=None,",
     ["U3", "U4"]),
    ("V2 指纹不纳入背景图（\"bg\" 字段被删）",
     '"bg": b_img,',
     '""',
     ["U1a", "U1b"]),
    ("V3 合成失败不再回退（改成直接抛）",
     '                self.log.emit(f"⚠️ 背景合成失败（{comp}），"\n'
     '                              f"回退用原参考图（保持原背景）")',
     '                raise RuntimeError("PERTURB: 合成失败回退被移除")',
     ["U7", "U8"]),
    ("V4 bg 模式不再强制 keep_bg=True（反向置 False）",
     "            self.keep_bg = True",
     "            self.keep_bg = False",
     ["U6"]),
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
        if s.startswith("❌"):
            # 取断言名（❌ 后的首个 token）
            red.append(s.split(" ", 1)[-1] if " " in s else s)
    return red, out


def main():
    _perturb_guard.arm([SRC])  # 防被强杀后变异残留（C1 契约）
    npass = nfail = 0
    verbose_tail = []

    base_red, _ = run_judge()
    if base_red:
        print("BASELINE_RED 基线本身就是红的，判据不可用：")
        for x in base_red[:5]:
            print("   ", x)
        nfail += 1
    else:
        print("BASELINE GREEN（判据当前全绿）")

    for label, old, new, want in CASES:
        src = io.open(SRC, encoding="utf-8").read()
        if old not in src:
            print("SKIP  %s —— 变异点在源码中找不到" % label)
            nfail += 1
            continue
        io.open(SRC, "w", encoding="utf-8").write(src.replace(old, new, 1))
        try:
            red, out = run_judge()
        finally:
            io.open(SRC, "w", encoding="utf-8").write(src)

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
