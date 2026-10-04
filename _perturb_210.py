# -*- coding: utf-8 -*-
"""v4.210.0（BUG 审核 P2 补完）判据扰动验证 —— 改坏必须红，且**红在指定的那几条上**。

用法：python _perturb_210.py

沿用 _perturb_2092.py 的两条硬规矩：
1. **真比对期望**（L199）：`expect ⊆ 实际红名`。只判"红了没"会放过"红错地方"，
   而红错地方 = 诊断信息是错的，跟没红一样危险。
2. **锚点必须唯一**（L200）：函数体末行往往和别处一模一样，动手前先 grep 数一遍。
   本文件里每个锚点都在写的时候确认过只出现一次。
3. 保留行尾（L186/L187）；崩了红 ≠ 判据生效（L191），必须解析出 FAIL 名。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_p2_210.py"

CASES = []


def case(name, fname, old, new, expect):
    CASES.append(("replace", name, fname, old, new, expect))


def case_re(name, fname, pattern, new, expect):
    """同 case()，但 `old` 是**正则**。

    为什么必须有这条：锚点若写死版本号（如 `**v4.210.0**`），版本一 bump 就
    MISS-CONTEXT 成**哑弹** —— 判据没被真正测到，却照报「全绿」。实测本文件里
    README 那条从 v4.211.0 起就一直是哑的（E7 白守了两个版本）。凡是会随发布
    漂移的锚点，一律走这条。
    """
    CASES.append(("resub", name, fname, pattern, new, expect))


# ============================================================
# P2-2 欢迎页分叉（A 组）
# ============================================================

# BUG 原状：过滤空分支又回去说「还没有对话」
case("BUG 原状：过滤空分支改回说「还没有对话」",
     "ui.py",
     '"这里是最近对话",',
     '"还没有对话",',
     ["A3", "A4", "A6", "A7", "A10"])

# 分叉方向反了 —— 有会话的人反而看到"还没有对话"，比不分叉更糟。
# 只查 "_has_any_session 在不在" 是抓不到的，靠 A10 的方向锁。
case("分叉方向写反（if not _has_any_session）",
     "ui.py",
     "if _has_any_session:",
     "if not _has_any_session:",
     ["A10"])

# 给过滤空分支加回「新建对话」按钮（他已经在会话里了，是噪音）
case("过滤空分支被加回 action 按钮",
     "ui.py",
     'hint="你正在的这个会话不重复列出。点上方「查看全部」管理全部会话。",',
     'hint="你正在的这个会话不重复列出。点上方「查看全部」管理全部会话。",\n'
     '                action="新建对话", on_action=self._new_session,',
     ["A6"])

# 点击处理被改成空实现（有 mousePressEvent 那行，但不干事）—— A8c 守这个
case("「查看全部」的点击被改成空实现",
     "ui.py",
     "view_all.mousePressEvent = lambda e: self._open_session_manager()",
     "view_all.mousePressEvent = lambda e: None",
     ["A8c"])

# 手型光标被去掉（可点但没有任何视觉暗示）
case("「查看全部」丢了手型光标",
     "ui.py",
     "view_all.setCursor(Qt.PointingHandCursor)",
     "",
     ["A8b"])

# ============================================================
# P2-3 军团 0 波（B 组）
# ============================================================

# 空态文案被改/删掉 —— B2~B5 都依赖定位到那个调用块
case("0 波空态的文案被改掉",
     "legion_ui.py",
     '"还没有波次",',
     '"暂无波次",',
     ["B2", "B3", "B4"])
# 注：B5 守的是**位置**（循环之后、addStretch 之前），文案改了位置没变 →
# 它**不该**红。第一版把 B5 写进期望，是我期望写错了（L199：先判断谁错了）。

# 给 0 波空态加个 action 按钮（顶部已有真按钮，重复入口）
case("0 波空态被加了 action 按钮",
     "legion_ui.py",
     'hint="点上方「+ 添加波次」开始组队。",',
     'hint="点上方「+ 添加波次」开始组队。",\n'
     '                action="添加波次", on_action=self._add_wave,',
     ["B4"])

# 「+ 添加波次」的绑定被删 —— 指引还在，但点了没反应
case("「+ 添加波次」按钮的绑定被删（变装饰）",
     "legion_ui.py",
     "b_wave.clicked.connect(self._add_wave)",
     "b_wave.setEnabled(True)",
     ["B6"])

# ============================================================
# P2-4 动态文案压缩（C 组）
# ============================================================

# 上限被放开 —— 长文本又会顶出状态栏
case("压缩上限被放到 200（长文本不再压）",
     "director_panel.py",
     "_STATUS_MAX = 36",
     "_STATUS_MAX = 200",
     ["C2", "C7"])

# 某一处退回裸 f-string（最典型的"改了又改回去"）
case("导出失败那处退回裸 f-string",
     "director_panel.py",
     '_set_status(app, f"导出失败：{_status_dyn(e)}", err=True)',
     '_set_status(app, f"导出失败：{e}", err=True)',
     ["C8"])

# _set_status 被改成自动压缩 —— 会连写死的 50 条文案一起压（C10 守这个边界）
case("_set_status 被改成自动压缩（会误伤写死文案）",
     "director_panel.py",
     "app.director_status.setText(text)",
     "app.director_status.setText(_status_dyn(text))",
     ["C10"])

# ============================================================
# P2-1 分类登记表（D 组）
# ============================================================

# 整条登记被删 —— 条目数掉下来（D2 是条数下限，删条目不该无声无息）
case("登记表被删掉一条（条目数掉到 8）",
     "tests/test_receipt_copy_209.py",
     '    ("legion_ui.py", "没有 checkpoint", "改",\n'
     '     "存档是用户能补的 → 待办语义；原标题还是名词短语，一并改成完整句"),\n',
     "",
     ["D2"])

# 把"留"误标成"改" —— 那条的关键串其实还在源码里，D3 必须抓出来
case("把「留」误标成「改」（旧文案其实还在）",
     "tests/test_receipt_copy_209.py",
     '("ui.py", "没有可用麦克风", "留",',
     '("ui.py", "没有可用麦克风", "改",',
     ["D3"])

# ============================================================
# 文档与版本（E 组）
# ============================================================

case("DESIGN 抹掉 §12.8 标题",
     "DESIGN.md",
     "### 12.8 v4.210.0：BUG 审核的 P2-2 / P2-3",
     "### 12.8 （待补）",
     ["E1"])

case("CHANGELOG 抹掉 v4.210.0 条目",
     "CHANGELOG.md",
     "## v4.210.0 — 2026-10-03",
     "## v4.209.x — 2026-10-03",
     ["E5"])

# 锚点必须**版本无关**：原写法 `"**v4.210.0**"` 在 bump 到 v4.211.x 后就
# MISS-CONTEXT 成哑弹（E7「README 与 config 版本一致」实际没被扰动测过）。
case_re("README 版本与 config 不一致",
        "README.md",
        r"当前版本 \*\*v[\d.]+\*\*",
        "当前版本 **v4.209.1**",
        ["E7"])


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
    """从套件输出里解析出变红的判据名（只认名字前缀，如 A8c / B6）。"""
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
    sys.path.insert(0, ROOT)
    import _perturb_guard as _guard  # noqa: E402

    _guard.arm()

    hit, misses = 0, []
    for kind, name, fname, a, b, expect in CASES:
        path = os.path.join(ROOT, fname)
        orig = None
        crlf = False
        try:
            if not os.path.exists(path):
                print(f"[SKIP] {name} —— {fname} 不存在")
                continue
            crlf = is_crlf(path)
            orig = read_raw(path)
            if kind == "resub":
                mutated, n = re.subn(a, b, orig, count=1)
                if n == 0:
                    print(f"[MISS-CONTEXT] {name} —— {fname} 里正则 {a!r} 没匹配上")
                    misses.append((name, "扰动片段没匹配上，判据没被真正测到"))
                    continue
            else:
                if a not in orig:
                    print(f"[MISS-CONTEXT] {name} —— {fname} 里找不到待扰动片段")
                    misses.append((name, "扰动片段没匹配上，判据没被真正测到"))
                    continue
                mutated = orig.replace(a, b, 1)
            write_raw(path, mutated, crlf)

            rc, out = run_suite()
            reds = failed_checks(out)
            if rc == 0 or not reds:
                why = ("判据全绿（改坏了没测出来）" if rc == 0
                       else f"红了但没解析到 FAIL 名（崩了≠生效）：{out[-160:]}")
                print(f"[未命中] {name}\n         {why}")
                misses.append((name, why))
                continue
            missing_expect = [e for e in expect if e not in reds]
            if missing_expect:
                why = f"红了但没红在期望的 {missing_expect} 上（实际红：{reds[:6]}）"
                print(f"[未命中] {name}\n         {why}")
                misses.append((name, why))
                continue
            hit += 1
            print(f"[命中] {name}\n         红在 {reds[:6]}")
        finally:
            if orig is not None:
                write_raw(path, orig, crlf)

    print("\n" + "=" * 60)
    print(f"扰动 {hit}/{len(CASES)} 命中（含期望比对）")
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d" % (hit, len(misses)))
    if misses:
        print("\n未命中：")
        for n, d in misses:
            print("  -", n, "→", d)
        sys.exit(1)
    print("全部命中。")


if __name__ == "__main__":
    main()
