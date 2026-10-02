# -*- coding: utf-8 -*-
"""扰动验证（v4.206.0 空状态判据）：故意把接线/组件改坏，看判据会不会红。

判据全绿不等于判据有效 —— 只有"改坏了就红"才说明它真的在看。
每条扰动：改源码 → 跑 tests/test_empty_state_206.py → 期望某条变红 → 恢复。

⚠️ 脚本运行期间目标文件视为锁定，不要手改（L180）。
⚠️ 一律二进制读写并保留原行尾（L186：文本模式会把整个文件改成 LF）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_empty_state_206.py")

FILES = ["ui.py", "theme_qss.py", "automation_panel.py", "empty_state.py",
         "tests/test_tokens_200.py", "tests/test_radius_norm.py"]
_backup = {}
_crlf = {}

# (名称, 文件, 原串, 新串, 期望变红的判据关键字[, 跑哪个套件])
CASES = [
    ("主文案丢掉 600 字重", "theme_qss.py",
     'return label_body(color_key="dim", weight="semibold") + "padding-top:4px;"',
     'return label_body(color_key="dim") + "padding-top:4px;"',
     ["B4"]),
    ("徽章圆角 22→20", "theme_qss.py",
     'return f"background:{THEME[bg_key]};border-radius:{size // 2}px;"',
     'return f"background:{THEME[bg_key]};border-radius:{size // 3}px;"',
     ["B3"]),
    ("行动按钮丢掉 hover 态", "theme_qss.py",
     "        f\"padding:0 16px;}}\"\n"
     "        f\"QPushButton:hover{{background:{THEME['accent_hover']};}}\"\n"
     "    )",
     "        f\"padding:0 16px;}}\"\n"
     "    )",
     ["B6 行动按钮含 #1765CC"]),
    ("徽章尺寸 44→40", "empty_state.py",
     "BADGE_SIZE = 44", "BADGE_SIZE = 40",
     ["C3", "A4"]),
    ("会话空态副文案被改写", "ui.py",
     'hint="点击下方开始你的第一条对话"', 'hint="点这里开始"',
     ["C4"]),
    ("自动化空态去掉行动按钮", "automation_panel.py",
     '            action="＋ 新建任务",\n'
     "            on_action=lambda: _open_edit(app, None),\n",
     "",
     ["D3"]),
    ("组件里内联写死 hex（不走 token）", "empty_state.py",
     "            badge.setStyleSheet(empty_badge())",
     '            badge.setStyleSheet("background:rgba(26,115,232,0.08);'
     'border-radius:22px;")',
     ["B1"]),
    ("会话空态旧内联块复活", "ui.py",
     "            # v4.206.0：抽成 empty_state 组件",
     '            _ = "background:%s;border-radius:22px;"\n'
     "            # v4.206.0：抽成 empty_state 组件" % "{THEME['card_blue_bg']}",
     ["C1"]),
    ("图标判空被删（拼错名字会留空圆点）", "empty_state.py",
     '            if pm.isNull():\n'
     '                raise ValueError(f"图标名不在 _NAV_ICONS 里: {icon!r}")\n',
     "",
     ["A10"]),
    ("技能审核空态被塞了一个按钮", "ui.py",
     '                icon="技能"))',
     '                icon="技能", action="全部通过"))',
     ["D1"]),
    # --- 以下三条守的是"本轮改过的判据基建"（登记制 / 圆角守卫搬家）---
    ("徽章圆形语义被改成 ÷3（半径守卫会红吗）", "theme_qss.py",
     'return f"background:{THEME[bg_key]};border-radius:{size // 2}px;"',
     'return f"background:{THEME[bg_key]};border-radius:{size // 3}px;"',
     ["theme_qss.empty_badge 仍是"], "test_radius_norm.py"),
    ("ui.py 里重新手写 22px 圆角（真源外泄）", "ui.py",
     "            # v4.206.0：抽成 empty_state 组件",
     '            _ = "border-radius:22px;"\n'
     "            # v4.206.0：抽成 empty_state 组件",
     ["徽章底色与圆角仍走 token 工厂"], "test_radius_norm.py"),
    ("迁移清单里塞一条不存在的差异（登记制自欺）", "tests/test_tokens_200.py",
     '            ("", \'{"background": "transparent"}\'): 1,',
     '            ("", \'{"background": "transparent"}\'): 1,\n'
     "            # 扰动：塞一条基线里根本不存在的差异\n"
     '            ("", \'{"color": "#123456"}\'): 1,',
     ["B1b[ui.py]"], "test_tokens_200.py"),
]


def read(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        raw = f.read()
    return raw.decode("utf-8").replace("\r\n", "\n")


def write(fp, s, crlf=True):
    body = s.replace("\n", "\r\n") if crlf else s
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(body.encode("utf-8"))


def is_crlf(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return b"\r\n" in f.read()


def run_test(test_name="test_empty_state_206.py"):
    r = subprocess.run([PY, os.path.join(ROOT, "tests", test_name)],
                       capture_output=True, text=True, cwd=ROOT, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    m = re.search(r"PASS=(\d+) FAIL=(\d+)", out)
    return red, (m.group(1), m.group(2)) if m else ("?", "?"), out


HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read(fp)
        _crlf[fp] = is_crlf(fp)

    for case in CASES:
        name, fp, old, new, expect = case[:5]
        suite = case[5] if len(case) > 5 else "test_empty_state_206.py"
        src = _backup[fp]
        if old not in src:
            MISS.append(f"{name}（原串没匹配上，扰动没生效）")
            print(f"  [SKIP] {name} — 原串未命中")
            continue
        write(fp, src.replace(old, new, 1), _crlf[fp])
        red, stat, out = run_test(suite)
        ok = all(any(e in r for r in red) for e in expect)
        print(f"  [{'HIT ' if ok else 'MISS'}] {name} → [{suite}] "
              f"FAIL 数 {stat[1]}，命中 {len(red)} 条"
              + ("" if ok else f"；期望 {expect}，实际 {red}"))
        (HIT if ok else MISS).append(name)
        write(fp, _backup[fp], _crlf[fp])
finally:
    for fp, s in _backup.items():
        write(fp, s, _crlf[fp])
    print("  （已恢复原文件，行尾保持原样）")

print(f"\n=== 扰动汇总：命中 {len(HIT)}/{len(CASES)} ===")
for m in MISS:
    print("   ✗", m)
sys.exit(1 if MISS else 0)
