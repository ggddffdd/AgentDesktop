# -*- coding: utf-8 -*-
"""扰动验证：证明 tests/test_probe_graphics.py 的静态判据「会红在该红的地方」。

判据绿不等于判据有效。一份只会点头的判据比没有更糟——它让人以为防线还在。
本脚本的做法：**逐个删掉探针里的防御写法**，看对应判据是否真的转红。

与前面几份扰动脚本同一套规矩（见 L201/L211）：
  1. 期望采用**包含式**：expect ⊆ actual_red。只要求「该红的红了」，不禁止
     连带红其它项（改一处往往顺带塌一片，那正是我们想知道的）；
  2. 每条 case 都真的生成一份**变异后源码**落临时文件，再用环境变量指向它跑
     判据（PROBE_GFX_PATH / PROBE_GFX_STATIC），不在原文件上动刀；
  3. 期望为空 means 这条 case 应该保持全绿 —— 用来防「判据过宽、什么都判红」。

用法：python _perturb_probe_graphics.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
PROBE = os.path.join(ROOT, "probe_graphics.py")
JUDGE = os.path.join(ROOT, "tests", "test_probe_graphics.py")
SRC = open(PROBE, encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(mutated_src, tag):
    """把变异后的源码落临时文件，静态模式跑判据，返回 (红名集合, 原始输出)。"""
    fd, path = tempfile.mkstemp(prefix="probe_gfx_mut_", suffix=".py", dir=ROOT)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(mutated_src)
    env = dict(os.environ, PROBE_GFX_PATH=path, PROBE_GFX_STATIC="1")
    try:
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    red = set(re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M))
    return red, out


def case(name, mutate, expect_ids):
    """mutate(src) -> 变异后源码。expect_ids：期望转红的判据名（子串匹配即可）。"""
    global PASS_N, FAIL_N
    try:
        new_src = mutate(SRC)
    except Exception as e:
        print("  [FAIL] %s  — 变异函数本身出错：%s: %s" % (name, type(e).__name__, e))
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    if new_src == SRC:
        print("  [FAIL] %s  — 变异没有生效（源码没变），这条 case 是假的" % name)
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    red, _out = run_judge(new_src, name)
    missing = [e for e in expect_ids if not any(e in r for r in red)]
    ok = not missing
    print("  [%s] %s   期望红 %s | 实际红 %d 项%s"
          % ("OK  " if ok else "FAIL", name, "、".join(expect_ids) or "(无)",
             len(red), ("  缺：" + "、".join(missing)) if missing else ""))
    if ok:
        PASS_N += 1
    else:
        FAIL_N += 1
        FAILED_CASES.append(name)


def sub(old, new):
    """生成一个「把 old 替换成 new」的变异函数（自带生效校验）。"""
    def f(src):
        if old not in src:
            raise AssertionError("锚点不在源码里：%r" % old[:60])
        return src.replace(old, new, 1)
    return f


def drop_line(anchor):
    """删掉包含 anchor 的整行。"""
    def f(src):
        if anchor not in src:
            raise AssertionError("锚点不在源码里：%r" % anchor[:60])
        lines = src.splitlines(True)
        for i, ln in enumerate(lines):
            if anchor in ln:
                del lines[i]
                return "".join(lines)
        raise AssertionError("没找到可删的行")
    return f


print("\n=== 扰动：三坑防御被删 → 对应判据必须转红 ===")

# ---- 坑① windowed（stdout 兜底 / 日志兜底 / 无控制台模拟）----
case("P01 删 stdout 为 None 的兜底",
     sub("        if s is not None:\n", "        if True:\n"),
     ["A2"])
case("P02 self_emit 去掉日志文件兜底",
     sub('with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:',
         "if False:\n            _ = f"),
     ["A3"])
case("P03 NoConsole 不再把流置 None（改成空操作）",
     sub("        sys.stdout, sys.stderr = None, None", "        pass"),
     ["A6"])
case("P04 探针里塞一个裸 print",
     sub('LOG_FILE = _log_path()', 'LOG_FILE = _log_path()\nprint("hello windowed")'),
     ["A15"])
case("P05 撤掉全局 excepthook",
     sub("    sys.excepthook = _hook\n", "    pass\n"),
     ["A16"])

# ---- 坑② ffmpeg 子进程三件套 ----
case("P06 ffmpeg 子进程不给 stdin=DEVNULL",
     drop_line("            stdin=subprocess.DEVNULL,"),
     ["A8"])
case("P07 ffmpeg 子进程不带 CREATE_NO_WINDOW",
     drop_line("            creationflags=_NO_WINDOW,"),
     ["A9"])
case("P08 ffmpeg 子进程不传 startupinfo",
     drop_line("            startupinfo=_startupinfo(),"),
     ["A10"])
case("P09 ffmpeg 输出按 gbk 解码",
     sub('            encoding="utf-8",', '            encoding="gbk",'),
     ["A11"])
case("P10 不隐藏子窗口（丢 STARTF_USESHOWWINDOW）",
     drop_line("    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW"),
     ["A12"])
case("P11 子窗口改为显示（wShowWindow=1）",
     sub("    si.wShowWindow = 0  # SW_HIDE", "    si.wShowWindow = 1"),
     ["A13"])

# ---- 坑③ 高 DPI ----
case("P12 抽帧图不补 setDevicePixelRatio",
     sub("        pm2.setDevicePixelRatio(dpr)", "        pass"),
     ["A18"])
case("P13 撤掉「不补 DPR」对照组（整段赋值连同行尾一起删）",
     sub('            m["thumb_untreated_w"] = round(\n'
         '                float(QGraphicsPixmapItem(raw).boundingRect().width()), 3)\n',
         "            pass\n"),
     ["A19"])
case("P14 不再在 NoConsole 下真跑 ffmpeg",
     sub("        with NoConsole():\n", "        if True:\n"),
     ["A20"])
case("P15 把 QT_QPA_PLATFORM 移到 QApplication 之后",
     lambda s: s.replace('    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")\n', "", 1)
                 .replace('if __name__ == "__main__":\n',
                          'if __name__ == "__main__":\n'
                          '    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")\n', 1),
     ["A22"])

# ---- 反向：没坏就不许红（防判据过宽）----
print("\n=== 反向：原样通过时不许有任何红项 ===")
_red0, _out0 = run_judge(SRC, "baseline")
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % sorted(_red0))
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    print("  [OK  ] 未变异源码全绿（%d 项静态判据）"
          % len(re.findall(r"\[OK  \] A", _out0)))
    PASS_N += 1

print("\nPASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_PROBE_GRAPHICS_OK ===")
