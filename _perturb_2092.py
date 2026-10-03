# -*- coding: utf-8 -*-
"""v4.209.2 判据扰动验证 —— 改坏必须红，且**红在指定的那几条上**。

用法：python _perturb_2092.py

与前几版的两处不同（都是吃了亏才改的）：
1. **真比对期望**：前几版的 `expect` 只写给人看，脚本只判"红了没"。
   这一版要求 `expect ⊆ 实际红名` —— 判据红在别的地方也算未命中
   （红错地方 = 诊断信息是错的，跟没红一样危险）。
2. **支持"新增文件"型扰动**：B11 是集合登记式判据（第 4 个同类文件出现即红），
   光靠字符串替换测不到它，得真的造一个文件出来。

沿用 L186/L187（保留行尾）、L191（崩了红 ≠ 判据生效，必须解析 FAIL 名）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_bugfix_2092.py"

CASES = []


def case(name, fname, old, new, expect):
    CASES.append(("replace", name, fname, old, new, expect))


def case_newfile(name, fname, content, expect):
    CASES.append(("newfile", name, fname, content, None, expect))


# ============================================================
# P1-1：compact 副文案 token（A 组）
# ============================================================

# 最要紧的一条：把 BUG 原样注回去
case("BUG 原状：compact 副文案改回裸 background:transparent",
     "empty_state.py",
     "sub.setStyleSheet(empty_hint_compact())",
     'sub.setStyleSheet("background:transparent;")',
     ["A4", "A5", "A7", "A9", "A11", "A13"])

# ⚠️ 锚点教训：`    return label_micro()\n` 在 theme_qss.py 里有**两处**
# （empty_hint 与 empty_hint_compact 各一个）。第一版直接拿它当锚点，
# replace(...,1) 命中的是**完整形态**那个，compact token 压根没被动
# → 三条扰动全部误判成"判据没抓到"。锚点必须带上下文明明指向哪一个。
# 用 docstring 末句做前置锚点（唯一），只替换紧跟其后的 return。
_ANCHOR_HEAD = "    \"\"\"\n    return label_micro()"

# 只漏掉 color（留着 font-size）—— 层级仍在，但颜色错了
case("compact token 掉了 color（只剩 font-size）",
     "theme_qss.py", _ANCHOR_HEAD,
     '    """\n    return "font-size:12px;"',
     ["A2", "A3", "A7", "A13"])

# 颜色给成深色 → 副文案比主文案重，层级倒置（A9 专门守这个）
case("副文案用了比主文案更深的颜色（层级又倒置）",
     "theme_qss.py", _ANCHOR_HEAD,
     '    """\n    return label_micro(color_key="text")',
     ["A3", "A7", "A9"])

# 只漏掉 font-size（留着 color）—— A11 守"显式设了字号"
case("compact token 掉了 font-size（只剩 color）",
     "theme_qss.py", _ANCHOR_HEAD,
     '    """\n    return f"color:{THEME[\'faint\']};"',
     ["A2", "A11"])

# 误伤主文案：把 compact 主文案也改成 faint（主副同色 = 没层级）
case("把 compact 主文案也改成 faint（主副同色，层级消失）",
     "theme_qss.py",
     '    return label_second(color_key="dim")',
     '    return label_second(color_key="faint")',
     ["A9", "A10"])

# 改坏整行高
case("compact 整行高被撑到 40（补样式时手滑）",
     "empty_state.py",
     "COMPACT_H = 32",
     "COMPACT_H = 40",
     ["A15"])

# 文档回退
case("DESIGN 抹掉 compact 规格表里的「副文案」行",
     "DESIGN.md",
     "| **副文案** | 12px/faint | **12px/faint** |",
     "| 副文案 |（待补）|（待补）|",
     ["A16"])

# ============================================================
# P1-2：回归入口假绿（B 组）
# ============================================================

# 核心：把新加的 `_n == 0` 守卫短路掉，BUG 原状复活
case("BUG 原状：_n == 0 的守卫被短路（横幅 0 断言又变 ok）",
     "tests/run_all.py",
     "            if _n == 0:",
     "            if False:",
     ["B2"])

# 把分类函数改名：B1 该红，且 B 组行为判据跟着红（不是崩）
case("classify 被改名（判据 B1 抓不到，行为验证一起失效）",
     "tests/run_all.py",
     "def classify(name, rc, out, err, secs):",
     "def classify_renamed(name, rc, out, err, secs):",
     ["B1", "B2", "B3", "B4"])

# 修过头：把正常横幅套件也打死（B3 守这个）
case("修过头：横幅分支一律判 EMPTY（正常横幅套件被误杀）",
     "tests/run_all.py",
     "            if _n == 0:",
     "            if _n >= 0:",
     ["B3", "B3b"])

# 正则被改坏：数不到 PASS:/[OK]。
# 注意这条**不该**让 B2 红 —— 数不到时 `_n=0`，横幅 0 断言照样落进 EMPTY 分支，
# 结论碰巧还是对的（fail-closed）。第一版我把 B2 写进期望，实测没红，
# 是我的期望错了，不是判据错了：真正被坑的是**正常横幅套件**（B3/B3b）。
case("断言计数正则被改坏（数不到 PASS:/[OK]）",
     "tests/run_all.py",
     'r"^\\s*(?:PASS:|\\[OK\\]|✅|✓)"',
     'r"^\\s*(?:ZZZ)"',
     ["B3", "B3b"])

# 那个套件退回裸 assert
# 只红 B8：B10 判的是"判据条数 ≥3"，退回 1 条 assert 后还剩 3 条，仍不算空壳 ——
# 这是 B10 的**设计意图**（抓"只剩壳"），不是疏漏。第一版把 B10 写进期望是我想岔了。
case("image_compress 退回裸 assert（又变回无输出断言的壳）",
     "tests/test_v4102_image_compress.py",
     '_check("压缩后体积变小", final_kb < orig_kb, f"{final_kb} >= {orig_kb}")',
     'assert final_kb < orig_kb, f"压缩后应更小，但 {final_kb} >= {orig_kb}"',
     ["B8"])

# 那个套件删掉统计输出（只留横幅）
case("image_compress 删掉 PASS=/FAIL= 输出（只留 OK 横幅）",
     "tests/test_v4102_image_compress.py",
     'print(f"PASS={_CHECKED - len(_FAILS)} FAIL={len(_FAILS)}")\nif _FAILS:',
     'if _FAILS:',
     ["B9"])

# B11 是集合登记式：得真的造第 4 个同类文件出来才测得到。
# 文件名必须叫 test_*.py —— 套件和 run_all 都只发现这个前缀。
# 第一版我起名 `_tmp_banner_probe.py`，glob 压根看不见它，判据当然不红。
# ⚠️ 注释里千万别写 "PASS=" —— 第一版我在注释里写了「源码无 PASS=」，
# 这三个字符自己就把 `"PASS=" not in _s` 这个条件骗过去了，判据当然不红。
# 这条乌龙本身就是白名单式判据的活教材：**判据扫的是字符，不是意图。**
case_newfile("冒出第 4 个「有横幅无统计」的套件（B11 必须红）",
             "tests/test_zz_banner_probe.py",
             'print("做了一些事")\nprint("=== SOME_NEW_OK ===")  # 有横幅、没有统计输出\n',
             ["B11"])

# ============================================================
# C 文档与版本
# ============================================================

case("CHANGELOG 删掉 v4.209.2 条目",
     "CHANGELOG.md", "## v4.209.2 — 2026-10-03", "## v4.209.x — 2026-10-03",
     ["C1"])
# 只红 C3（一致性）；C4 判的是 config 本身是不是 v4.209.2，config 没动 → 不红。
# 第一版把 C4 也写进期望，又是我预判错了。
# ⚠️ 锚点不许写死版本号（同 _perturb_208/209/2091 的教训；实测到 v4.210.0 时锚点失配、
# 报 MISS-CONTEXT）。改为读当前真实版本号再换掉，与具体版本号解耦。
_VER_NOW = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                     open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()).group(1)
case("README 版本与 config 不一致",
     "README.md", "**%s**" % _VER_NOW, "**v0.0.0**",
     ["C3"])
case("DESIGN 抹掉 v4.209.2 的修复记录",
     "DESIGN.md", "### 12.7 v4.209.2", "### 12.7 （待补）",
     ["C5"])


def is_crlf(p):
    with open(p, "rb") as f:
        return b"\r\n" in f.read(8192)


def read_raw(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def write_raw(p, t, crlf):
    d = t.replace("\r\n", "\n")
    if crlf:
        d = d.replace("\n", "\r\n")
    with open(p, "wb") as f:
        f.write(d.encode("utf-8"))


def run_suite():
    p = subprocess.run([PY, SUITE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def failed_checks(out):
    """从套件输出里解析出变红的判据名（只认名字前缀，如 A7 / B3b）。"""
    names = []
    for ln in out.split("\n"):
        s = ln.strip()
        if s.startswith("[FAIL]"):
            tail = s[7:].split("—")[0].split("  ")[0].strip()
            names.append(tail.split()[0] if tail else "")
    return [n for n in names if n]


def main():
    # 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检。
    # 本脚本的变异/还原都在下面的 try/finally 里，而 Python 默认的 SIGTERM 处理器
    # 直接终止进程（不抛异常、不走 finally）→ 被超时强杀时被测源码会留在变异态。
    # 本脚本还有「新建整份文件」的 case（case_newfile），残留时更隐蔽。
    sys.path.insert(0, ROOT)
    import _perturb_guard as _guard  # noqa: E402

    _guard.arm()

    hit, misses = 0, []
    for kind, name, fname, a, b, expect in CASES:
        path = os.path.join(ROOT, fname)
        created = False
        orig = None
        crlf = False
        try:
            if kind == "newfile":
                if os.path.exists(path):
                    print(f"[SKIP] {name} —— {fname} 已存在，不覆盖")
                    continue
                with open(path, "w", encoding="utf-8") as f:
                    f.write(a)
                created = True
            else:
                if not os.path.exists(path):
                    print(f"[SKIP] {name} —— {fname} 不存在")
                    continue
                crlf = is_crlf(path)
                orig = read_raw(path)
                if a not in orig:
                    print(f"[MISS-CONTEXT] {name} —— {fname} 里找不到待扰动片段")
                    misses.append((name, "扰动片段没匹配上，判据没被真正测到"))
                    continue
                write_raw(path, orig.replace(a, b, 1), crlf)

            rc, out = run_suite()
            reds = failed_checks(out)
            if rc == 0 or not reds:
                why = ("判据全绿（改坏了没测出来）" if rc == 0
                       else f"红了但没解析到 FAIL 名（崩了≠生效）：{out[-160:]}")
                print(f"[未命中] {name}\n         {why}")
                misses.append((name, why))
                continue
            # 真比对期望：红错地方同样算未命中
            missing_expect = [e for e in expect if e not in reds]
            if missing_expect:
                why = f"红了但没红在期望的 {missing_expect} 上（实际红：{reds[:6]}）"
                print(f"[未命中] {name}\n         {why}")
                misses.append((name, why))
                continue
            hit += 1
            print(f"[命中] {name}\n         红在 {reds[:6]}")
        finally:
            if created:
                if os.path.exists(path):
                    os.remove(path)
            elif orig is not None:
                write_raw(path, orig, crlf)

    print("\n" + "=" * 60)
    print(f"扰动 {hit}/{len(CASES)} 命中（含期望比对）")
    if misses:
        print("\n未命中：")
        for n, d in misses:
            print(f"  - {n}：{d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
