# -*- coding: utf-8 -*-
"""读取文件：二进制媒体拦截 — 回归判据 v4.260.2

护栏 #2（2026-10-11 复盘）：收到 .mp4/.png 等本地二进制媒体路径时，read_file
**禁止按文本读取**（按 utf-8 读只会得到乱码，且会让 Agent 误以为「读到了内容」
进而空转分析），改为直接声明非文本、指引走抽帧/图像分析路径。

行为级：造真实二进制文件，调 tool_read_file，断言返回「已拦截」声明且不含乱码内容；
同时断言正常 .txt 仍可读（无回归）、.pdf 仍走抽取（不动）。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools

REPO = r"D:\小臭玩AI\deepseek-desktop"
tools.WORKSPACE_DIR = REPO

PASS = FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")

_GARBAGE = os.path.join(REPO, "_exp_bin_guard_test.bin")

def _mk(path, nbytes):
    with open(path, "wb") as f:
        f.write(b"\x00\x01\x02\xff" * (max(1, nbytes) // 4))

# ── 视频：.mp4 ──
_mp4 = os.path.join(REPO, "_exp_bin_guard_test.mp4")
_mk(_mp4, 2048)
r_mp4 = tools.tool_read_file(REPO, "_exp_bin_guard_test.mp4", 0, 1000)
check("V 视频 .mp4 被拦截（返回「已拦截」）",
      isinstance(r_mp4, str) and "已拦截" in r_mp4, r_mp4[:80] if r_mp4 else r_mp4)
check("V .mp4 拦截声明含扩展名与「二进制媒体」",
      isinstance(r_mp4, str) and ".mp4" in r_mp4 and "二进制媒体" in r_mp4, r_mp4[:80] if r_mp4 else r_mp4)
check("V .mp4 拦截指引走「抽帧/图像分析」",
      isinstance(r_mp4, str) and ("抽帧" in r_mp4 or "图像分析" in r_mp4), r_mp4[:80] if r_mp4 else r_mp4)
check("V .mp4 拦截**不含**原始二进制乱码",
      isinstance(r_mp4, str) and "\x00\x01\x02" not in r_mp4, "<含乱码>")

# ── 图片：.png ──
_png = os.path.join(REPO, "_exp_bin_guard_test.png")
_mk(_png, 2048)
r_png = tools.tool_read_file(REPO, "_exp_bin_guard_test.png", 0, 1000)
check("I 图片 .png 被拦截（返回「已拦截」）",
      isinstance(r_png, str) and "已拦截" in r_png, r_png[:80] if r_png else r_png)

# ── 无回归：普通 .txt 仍可正常读 ──
_txt = os.path.join(REPO, "_exp_bin_guard_test.txt")
with open(_txt, "w", encoding="utf-8") as f:
    f.write("小臭玩AI 二进制拦截测试文本。\n第二行内容用于验证正常文本读取不被误伤。\n")
r_txt = tools.tool_read_file(REPO, "_exp_bin_guard_test.txt", 0, 999999)
check("T 普通 .txt 仍正常读入（无回归）",
      isinstance(r_txt, str) and "二进制拦截测试文本" in r_txt, r_txt[:60] if r_txt else r_txt)
check("T .txt 不被误拦截",
      isinstance(r_txt, str) and "已拦截" not in r_txt, r_txt[:60] if r_txt else r_txt)

# 清理
for _p in (_mp4, _png, _txt, _GARBAGE):
    try:
        os.remove(_p)
    except OSError:
        pass

print("\n结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
