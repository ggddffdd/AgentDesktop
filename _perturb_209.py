# -*- coding: utf-8 -*-
"""v4.209.0 波A 判据扰动验证 —— 改坏必须红，且红在指定的那几条上。

用法：python _perturb_209.py
沿用 L186/L187（二进制读写 + 保留行尾）、L191（崩了红 ≠ 判据生效，必须解析 FAIL 名）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_receipt_copy_209.py"

CASES = []


def case(name, fname, old, new, expect):
    CASES.append((name, fname, old, new, expect))


# ---- A 导演台：文案与 err 语义 ---------------------------------------------
case("导演台文案退回旧版（破折号）",
     "director_panel.py",
     '"还没有素材。先点「开始导演」，跑起来后这里就有东西。"',
     '"还没有素材——点「开始导演」跑起来后这里才有东西。"',
     ["A2", "D1"])

case("4处误用 err=True 复活（红色又回来了）",
     "director_panel.py",
     '_set_status(app, "还没有工程可导出。先点「开始导演」建一个。")',
     '_set_status(app, "还没有工程可导出。先点「开始导演」建一个。", err=True)',
     ["A6", "A8b"])

case("回滚失败的 err=True 被删（真失败也不红了）",
     "director_panel.py",
     '_set_status(app, msg or "回滚失败：还没有历史版本可回滚。", err=True)',
     '_set_status(app, msg or "回滚失败：还没有历史版本可回滚。")',
     ["A7", "A8b"])

case("纯陈述文案没补下一步（退回12 字版）",
     "director_panel.py",
     '"还没有工程可导出。先点「开始导演」建一个。"',
     '"还没有工程可导出。"',
     ["A3", "A6", "A8"])

case("句式不统一（改回「没有X」）",
     "director_panel.py",
     '"还没有历史版本。改一次（✎改 / ↻重生成）就有了。"',
     '"没有历史版本。改一次（✎改 / ↻重生成）就有了。"',
     ["A5", "A8", "D3"])

# ---- B 军团 ----------------------------------------------------------------
case("emoji 复活（团队库那处）",
     "legion_ui.py",
     '"还没有选中团队。先去「团队库」选一个。"',
     '"还没有选中团队。先去「\U0001F9E9 团队库」选一个。"',
     ["B3", "D2"])

case("2199 那处漏网的破折号复活",
     "legion_ui.py",
     'f"团队「{p.get(\'name\')}」还没有成员。"',
     'f"团队「{p.get(\'name\')}」还没有成员 —— "',
     ["D1"])

case("军团弹窗标题退回「团队是空的」",
     "legion_ui.py",
     'QMessageBox.information(self, "还没有成员",',
     'QMessageBox.information(self, "团队是空的",',
     ["B1"])

case("授权记录回执被擅改（v4.208 定的口径：不动）",
     "legion_ui.py",
     "还没有任何授权记录。跑一次带验收的军团后这里就有。",
     "授权记录为空。",
     ["B6"])

# ---- C 技能市场 ------------------------------------------------------------
case("技能市场范本句式被改回「没有X」",
     "skill_market_ui.py",
     '"该技能还没有可用的安装链接。',
     '"该技能没有可用的安装链接。',
     ["C1"])

case("技能市场范本的两条替代路径被砍掉一条",
     "skill_market_ui.py",
     "请用其仓库链接安装，或从「从链接安装」粘贴。",
     "请自己想办法安装。",
     ["C2", "C3"])

# ---- D 布局不许动 ----------------------------------------------------------
case("波A 越界：顺手给状态栏开了 wordWrap",
     "director_panel.py",
     "app.director_status = QLabel(\"\")",
     "app.director_status = QLabel(\"\")\n    app.director_status.setWordWrap(True)",
     ["D4"])

case("_set_status 的 err 语义被改（红/灰不分了）",
     "director_panel.py",
     'color = THEME["accent"] if not err else THEME["danger_text"]',
     'color = THEME["accent"]',
     ["D5", "A8b"])

# ---- E 跨轮守护：回执不许被接成空态卡 --------------------------------------
case("把「还没有素材」回执接成空态卡",
     "director_panel.py",
     '_set_status(app, "还没有素材。先点「开始导演」，跑起来后这里就有东西。")',
     '_set_status(app, "还没有素材")\n'
     '    _es = empty_state("还没有素材", icon="文档")',
     ["A2", "E1", "E2"])

case("空状态白名单被塞进未登记项",
     "legion_ui.py",
     '                    "本波还没有成员",',
     '                    "还没有在编成员",',
     ["E1"])

# ---- F 版本与文档 ----------------------------------------------------------
# ⚠️ 锚点不许写死版本号（同 _perturb_208 的教训）：写「当时的当前版本」跨版本必然失配，
# 实测到 v4.210.0 时本 case 报 MISS-CONTEXT、判据从未被真正测到。改为跟随当前值。
_VER_NOW = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                     open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()).group(1)
case("版本号没升（版本一致性判据应红）",
     "config.py", 'APP_VERSION = "%s"' % _VER_NOW, 'APP_VERSION = "v0.0.0"',
     ["F4"])

# 锚点要用**整段**（判据第二版改成查 §13.2 段内多要素），
# 只抹一句的话段内其他要素还在，判据不红。
case("DESIGN 抹掉 err 语义那条口径的关键句",
     "DESIGN.md",
     "会把字染成 `danger_text` 红色",
     "会变色",
     ["F2"])


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
    # 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检。
    # 本脚本的变异/还原都在下面的 try/finally 里，而 Python 默认的 SIGTERM 处理器
    # 直接终止进程（不抛异常、不走 finally）→ 被超时强杀时被测源码会留在变异态。
    sys.path.insert(0, ROOT)
    import _perturb_guard as _guard  # noqa: E402

    _guard.arm()

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
