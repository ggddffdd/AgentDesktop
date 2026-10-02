# -*- coding: utf-8 -*-
"""v4.209.1 判据扰动验证 —— 改坏必须红，且红在指定的那几条上。

用法：python _perturb_2091.py
沿用 L186/L187（保留行尾）、L191（崩了红 ≠ 判据生效，必须解析 FAIL 名）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_status_len_2091.py"

CASES = []


def case(name, fname, old, new, expect):
    CASES.append((name, fname, old, new, expect))


# ---- A/B 长度：文案改长就该红 ----------------------------------------------
case("回滚文案改回带「下游」的 432px 版（贴边回来了）",
     "director_panel.py",
     '"已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成"',
     '"已回滚上游资产：下游关键帧/视频/成片已标记「基于旧资产」，建议重新生成"',
     ["A2", "B3", "B4"])

case("回滚文案继续加长（超过 460px 阈值）",
     "director_panel.py",
     '"已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成"',
     '"已回滚上游资产：下游的关键帧、关键视频、最终成片都已标记「基于旧资产」，建议你去重新生成一遍"',
     ["A2", "B2", "B3"])

case("换一条更长的文案塞进 _set_status（贴边且非登记项）",
     "director_panel.py",
     '"已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成"',
     '"已回滚上游资产：所有下游的关键帧、关键视频与最终成片均已统一标记为基于旧资产的过渡状态，建议重新生成"',
     ["A2", "B2", "B3"])

# ---- C 布局：越界开 wordWrap 就该红 ----------------------------------------
case("越界：给状态栏开了 wordWrap（v4.209.1 明确不开）",
     "director_panel.py",
     "app.director_status = QLabel(\"\")",
     "app.director_status = QLabel(\"\")\n    app.director_status.setWordWrap(True)",
     ["C1", "C4"])

case("越界：给状态栏加了限高",
     "director_panel.py",
     "app.director_status = QLabel(\"\")",
     "app.director_status = QLabel(\"\")\n    app.director_status.setMaximumHeight(32)",
     ["C2"])

case("_set_status 被改成两行渲染（同样是越界改布局）",
     "director_panel.py",
     "    app.director_status.setText(text)",
     "    app.director_status.setText(text[:20] + '\\n' + text[20:])",
     ["C3"])

# ---- B 扫描有效性：正则失效要能被发现 --------------------------------------
# 注意：只改 def 行**不会**让判据红（所有调用点仍是 `_set_status(`，
# 正则照样抓得到 —— 实测过56 条仍匹配）。所以这里要改的是**调用点**。
case("把所有调用点改名（判据 B 的正则彻底抓不到）",
     "director_panel.py",
     "_set_status(app, \"还没有工程可导出。先点「开始导演」建一个。\")",
     "_status_v2(app, \"还没有工程可导出。先点「开始导演」建一个。\")",
     ["B1", "B2", "B3"])

# 第一版用f 前缀，实测**不红**（f 在引号外，正则 `("(?:...)")` 照样匹配）。
# 改成双引号 → 单引号：Python 里等价，但正则只认双引号 → 真失效。
case("文案引号从双引号改成单引号（判据 B 的正则失配）",
     "director_panel.py",
     '_set_status(app, "已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成")',
     "_set_status(app, '已回滚上游资产：关键帧/视频/成片已标记「基于旧资产」，建议重新生成')",
     ["B1", "B2", "B3", "B4"])

# ---- 注释与文档 ------------------------------------------------------------
case("删掉改动理由注释（432px 那段）",
     "director_panel.py",
     "# v4.209.1：原文36 字 / 432px，是导演台 55 条 _set_status 里最贴边的一条",
     "# 改短了",
     ["B5"])

case("DESIGN 把波 B 改回「待排」（后人以为 wordWrap 还没做）",
     "DESIGN.md",
     "⛔ 已降级",
     "待排",
     ["D1"])

case("DESIGN 抹掉阈值来历（460px 实测那段）",
     "DESIGN.md",
     "**卡满点**（阈值来源）",
     "（阈值来源不详）",
     ["D2"])

case("CHANGELOG 删掉 v4.209.1 条目",
     "CHANGELOG.md", "## v4.209.1", "## v4.209.x",
     ["D3"])

case("README 版本与 config 不一致",
     "README.md", "**v4.209.1**", "**v4.209.0**",
     ["D5"])


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
    names = []
    for ln in out.split("\n"):
        s = ln.strip()
        if s.startswith("[FAIL]"):
            names.append(s[7:].split("— ")[0].split("  — ")[0].strip())
    return names


def main():
    hit, misses = 0, []
    for name, fname, old, new, expect in CASES:
        path = os.path.join(ROOT, fname)
        if not os.path.exists(path):
            print(f"[SKIP] {name} —— {fname} 不存在")
            continue
        crlf = is_crlf(path)
        orig = read_raw(path)
        if old not in orig:
            print(f"[MISS-CONTEXT] {name} —— {fname} 里找不到待扰动片段")
            misses.append((name, "扰动片段没匹配上，判据没被真正测到"))
            continue
        try:
            write_raw(path, orig.replace(old, new, 1), crlf)
            rc, out = run_suite()
            reds = failed_checks(out)
            if rc != 0 and reds:
                hit += 1
                print(f"[命中] {name}\n         红在 {reds[:4]}")
            else:
                why = ("判据全绿（改坏了没测出来）" if rc == 0
                       else f"红了但没解析到 FAIL 名（崩了≠生效）：{out[-160:]}")
                print(f"[未命中] {name}\n         {why}")
                misses.append((name, why))
        finally:
            write_raw(path, orig, crlf)

    print("\n" + "=" * 60)
    print(f"扰动 {hit}/{len(CASES)} 命中")
    if misses:
        print("\n未命中：")
        for n, d in misses:
            print(f"  - {n}：{d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
