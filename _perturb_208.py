# -*- coding: utf-8 -*-
"""v4.208.0 判据扰动验证 —— 改坏必须红，且红在指定的那几条上。

用法：python _perturb_208.py

沿用 L186/L187：二进制读写 + 保留原行尾。
沿用 L191：**崩了红 ≠ 判据生效** —— 必须解析出 [FAIL] 条目名才算命中。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\xyb\AppData\Local\Programs\Python\Python312\python.exe"
SUITE = "tests/test_empty_state_208.py"

CASES = []


def case(name, fname, old, new, expect):
    CASES.append((name, fname, old, new, expect))


def is_crlf(p):
    with open(p, "rb") as f:
        return b"\r\n" in f.read(4096)


def read_raw(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8")


def write_raw(p, t, crlf):
    d = t.replace("\r\n", "\n")
    if crlf:
        d = d.replace("\n", "\r\n")
    with open(p, "wb") as f:
        f.write(d.encode("utf-8"))


# ---- 接线层 -----------------------------------------------------------------
# 1) 旧写法复活
case("旧括号注记复活",
     "legion_ui.py",
     'bl.addWidget(empty_state(',
     'bl.addWidget(QLabel("（本波还没有成员，点下面「+ 添加成员」）") or empty_state(',
     ["A2", "A3", "A4", "A5", "A10", "A11", "A12", "A14", "A15", "B3", "E1", "E4b"])

# 2) 误用 compact 形态（窄容器才用，GroupBox 够宽）
case("波次空态误用 compact 形态",
     "legion_ui.py", 'icon="军团"))', 'icon="军团", compact=True))',
     ["B2", "B1"])

# 3) 给了行动按钮 —— 下方已有「+ 添加成员」，这是重复入口
case("给空态加了行动按钮（与真按钮重复）",
     "legion_ui.py",
     'hint="点下面「+ 添加成员」给他派活。",',
     'hint="点下面「+ 添加成员」给他派活。", action="+ 添加成员", on_action=lambda: None,',
     ["A9", "A13", "A13c"])

# 4) 图标名写错会静默降级 —— 换成不存在的名字，判据应红在 A12
case("图标名写错（静默降级成纯文字）",
     "legion_ui.py", 'icon="军团"))', 'icon="不存在的图标"))',
     ["A8", "A12"])

# 5) 副文案指向不存在的按钮（v4.207 那个错误的翻版）
case("副文案指引不存在的按钮",
     "legion_ui.py", '"点下面「+ 添加成员」给他派活。"',
     '"点上方「+ 新建」给他派活。"',
     ["A7"])

# ---- 不接守护 ---------------------------------------------------------------
# 6) 把 #7b 授权记录接成组件
case("把授权记录回执接成空态卡",
     "legion_ui.py",
     '"还没有任何授权记录。跑一次带验收的军团后这里就有。")',
     '"")\n            _w = empty_state("还没有任何授权记录", icon="清单")',
     ["C1", "C2", "C3", "C4", "E1", "E4b4"])

# 7) 删掉波次框下方真实的「+ 添加成员」按钮（判据 A13c 守的就是这个）
case("真按钮「+ 添加成员」被删（空态引导失效）",
     "legion_ui.py", 'b_add = QPushButton("+ 添加成员")',
     'b_add = QPushButton("添加")',
     ["A13c"])

# 8) 导演台把QTextEdit 换成组件
case("导演台把提示词框换成空态组件",
     "director_panel.py",
     'te.setPlainText(prompt or "暂无，这镜可能还没生成 / 或被重置")',
     'te.setPlainText("")',
     ["D1", "D2"])

# 9) 导演台旧文案（带括号）复活
case("导演台旧文案复活",
     "director_panel.py", '"暂无，这镜可能还没生成 / 或被重置"',
     '"（暂无，这镜可能还没生成 / 或被重置）"',
     ["D1", "D2"])

# 10) 组件化扩散到第 4 个文件
# 锚点必须是**行首顶格**的 import ——第一版用"import"做锚，
    # 命中的却是文件头注释/docstring 里出现的 "import" 字样，
    # 插进去的 import 落在注释里，判据的正则（^\s*from）当然匹配不到。
case("组件化扩散到第 4 个文件（legion_chat）",
     "legion_chat.py", "\nimport os", "\nfrom empty_state import empty_state\nimport os",
     ["E2"])

# 11) 凭空多接一处未登记的调用点
case("凭空多接一处未登记的空态",
     "legion_ui.py", 'bl.addWidget(empty_state(',
     'bl.addWidget(empty_state("多出来的一处", icon="文档")) or empty_state(',
     ["A5", "E1", "E4b"])

# 12) 把已登记的合法空态换成未登记文案（分类判据 E4b 守的正是这个）
case("空态文案换成未登记的（分类判据要红）",
     "legion_ui.py", '"本波还没有成员"', '"尚未添加任何角色"',
     ["A6", "E4b"])

# 13) 删掉代码注释里的理由
case("删掉「为什么不重复给按钮」那句理由",
     "legion_ui.py",
     "# 再给一个就是重复入口。",
     "# （理由待补）",
     ["B4"])

case("删掉「为什么不接 #2 那种」那句对比理由",
     "legion_ui.py",
     "#2 那个QTextEdit 框本身是内容容器",
     "# （对比说明待补）",
     ["B4"])

# 14) 版本号没升
#   ⚠️ 锚点**不许写死版本号**：本 case 原本写的是「当时的当前版本」v4.208.0，
#   跨版本后 config.py 里再没有那个串 → 锚点失配 → 静默退化成哑弹
#   （实测 2026-10-03：版本走到 v4.210.0 时报 MISS-CONTEXT，判据一次都没被真正测到）。
#   改为「读当前真实版本号 → 换成一个明显不是它的值」，与版本号解耦、永久有效。
_VER_NOW = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                     open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()).group(1)
case("config.APP_VERSION 被改回旧版本（版本一致性判据应红）",
     "config.py", 'APP_VERSION = "%s"' % _VER_NOW, 'APP_VERSION = "v0.0.0"',
     ["F5"])

# 15) DESIGN 把 5 类口径改回 4 类
case("DESIGN 清点口径退回 4 类",
     "DESIGN.md",
     "实际是**五种**",
     "实际是四种",
     ["F3"])


def run_suite():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([PY, SUITE], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
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
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d" % (hit, len(misses)))
    if misses:
        print("\n未命中：")
        for n, d in misses:
            print(f"  - {n}：{d}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
