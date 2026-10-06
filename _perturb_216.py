# -*- coding: utf-8 -*-
"""扰动验证（v4.216.0 大文件拆分判据）：故意把拆分契约改坏，看判据会不会红。

每条扰动：改源码 → 跑 tests/test_split_216.py → 期望某个判据变红 → 恢复。
判据全绿不等于判据有效 —— 只有「改坏了就红」才说明它真的在看。

⚠️ 脚本运行期间目标文件视为锁定，不要手改（会被开头那份副本整文件覆盖）。
⚠️ 新串里的 `# 扰动` 不是装饰：护栏靠它识别「被强杀后留在磁盘上的变异体」。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_split_216.py")

FILES = ["ui.py", "theme_qss.py", "agent.py", "agent_text.py", "theme_tokens.py"]
_backup = {}      # 归一化文本（做锚点替换用）
_backup_raw = {}  # 原始字节（还原用 —— theme_tokens 是混合行尾，不能经 normalize 往返）
_crlf = {}


def read(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        raw = f.read()
    return raw.decode("utf-8").replace("\r\n", "\n")


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write(fp, s):
    body = s.replace("\n", "\r\n") if _crlf[fp] else s
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(body.encode("utf-8"))


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return b"\r\n" in f.read()


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([PY, TEST], capture_output=True, text=True,
                       cwd=ROOT, timeout=300, env=env,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    m = re.search(r"PASS=(\d+) FAIL=(\d+)", out)
    return red, (m.group(1), m.group(2)) if m else ("?", "?"), out


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm()

# (名称, 文件, 原串, 新串, 期望变红的判据关键字)
CASES = [
    ("re-export 少一个名字（外部 from ui import TaskStatusStrip 会炸）",
     "ui.py",
     "    RES_PRESETS, TaskStatusStrip, ThemedDialog, ConfirmDialog, RenameDialog,",
     "    RES_PRESETS, ThemedDialog, ConfirmDialog, RenameDialog,  # 扰动：删 TaskStatusStrip",
     ["B1"]),

    ("theme_qss 的 THEME 改回从 ui 拿（循环依赖复活）",
     "theme_qss.py",
     "from theme_tokens import THEME",
     "from ui import THEME  # 扰动：循环依赖复活",
     ["C2"]),

    ("agent.py 一处接线被改坏（判据族调用断了）",
     "agent.py",
     "                        and agent_text._looks_like_promise(content)):",
     "                        and self._looks_like_promise(content)):  # 扰动",
     ["B2", "B3"]),

    ("ChatWindow 不再继承 ChatAuditMixin（审计族全断）",
     "ui.py",
     "class ChatWindow(ChatAuditMixin, QMainWindow):",
     "class ChatWindow(QMainWindow):  # 扰动",
     ["C1"]),

    ("agent_text 删掉 log 定义（except 吞 NameError 的真 BUG 复活）",
     "agent_text.py",
     'log = logging.getLogger("dsdesktop")',
     "# 扰动：log 定义被删",
     ["A3[agent_text.py]", "E2"]),

    ("theme_tokens 反向 import ui（叶子模块成环）",
     "theme_tokens.py",
     "THEME = {",
     "from ui import THEME as _SECOND_THEME  # 扰动：成环\nTHEME = {",
     ["A2"]),

    ("ui.py 塞回 500 行注释块（规模悄悄回弹）",
     "ui.py",
     "class ChatWindow(ChatAuditMixin, QMainWindow):",
     "# 扰动：回弹测试用的死注释块\n" + "# 回弹填充\n" * 500
     + "class ChatWindow(ChatAuditMixin, QMainWindow):",
     ["D1"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read(fp)
        _backup_raw[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp]
        if old not in src:
            MISS.append(f"{name}（原串没匹配上，扰动没生效）")
            print(f"  [SKIP] {name} — 原串未命中")
            continue
        write(fp, src.replace(old, new, 1))
        red, stat, out = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print(f"  [{tag}] {name} → FAIL 数 {stat[1]}，命中 {len(red)} 条"
              + ("" if ok else f"；期望 {expect}，实际 {[r[:40] for r in red[:3]]}"))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup_raw[fp])   # 立刻恢复（原始字节，混合行尾文件无损）
finally:
    for fp, blob in _backup_raw.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print(f"\n=== 扰动汇总：命中 {len(HIT)}/{len(CASES)} ===")
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
for m in MISS:
    print("   ✗", m)
sys.exit(1 if MISS else 0)
