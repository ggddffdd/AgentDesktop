# -*- coding: utf-8 -*-
"""扰动验证（v4.205.0 接线判据）：故意把接线改坏，看判据会不会红。

判据全绿不等于判据有效 —— 只有"改坏了就红"才说明它真的在看。
每条扰动：改 ui.py → 跑 tests/test_toast_wiring_205.py → 期望某个判据变红 → 恢复。

⚠️ 脚本运行期间目标文件视为锁定，不要手改（L180：会被开头那份副本整文件覆盖）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_toast_wiring_205.py")

FILES = ["ui.py", "ui_widgets.py", "diagnostic_export.py"]
_backup = {}

# (名称, 文件, 原串, 新串, 期望变红的判据关键字)
CASES = [
    ("host 改回 chat_col（切页就隐形）", "ui.py",
     "register_toast_host(self.main_stack", "register_toast_host(self.chat_col",
     ["C1", "C2"]),
    ("不调 _mount_toast（永不注册）", "ui.py",
     "        self._mount_toast()", "        pass  # 扰动",
     ["C6"]),
    ("保存成功 kind 改 info", "ui.py",
     'toast("API Key 已保存", kind="success")',
     'toast("API Key 已保存", kind="info")',
     ["D1"]),
    ("复制成功改回 status_label（老写法复活）", "ui.py",
     '            toast("配对码已复制到剪贴板", kind="success")',
     '            self.status_label.setText("✅ 配对码已复制到剪贴板")',
     ["D4", "E1"]),
    # v4.216.0：_brief_err 随对话框族搬到 ui_widgets.py（判据走 ui re-export 仍绿，
    # 锚点必须跟着搬 —— 否则 [SKIP] 计 MISS）
    ("_brief_err 不截断", "ui_widgets.py",
     "return s if len(s) <= limit else s[: limit - 1] + \"…\"",
     "return s",
     ["D8a"]),
    ("删会话去掉确认框（不可逆操作裸奔）", "ui.py",
     "        if box.exec() == QMessageBox.Yes:\n"
     "            self._close_session(sid)\n"
     "            # v4.205.0：删除结果是一次性确认（确认框本身已经打断过一次了）→ toast。\n"
     "            toast(f\"已删除「{title}」\", kind=\"success\")",
     "        if True:\n"
     "            self._close_session(sid)\n"
     "            toast(f\"已删除「{title}」\", kind=\"success\")",
     ["F1"]),
    ("诊断包导出改走 toast（路径抄不走）", "diagnostic_export.py",
     '        QMessageBox.information(parent, "诊断包已导出",\n'
     '                                f"已保存到：\\n{path}\\n\\n把此文件发给开发者即可。")',
     '        toast("诊断包已导出", kind="success", detail=path)  # 扰动',
     ["F4", "F4b"]),
    ("端到端：保存后不弹（回归到无声）", "ui.py",
     '        if key:\n            toast("API Key 已保存", kind="success")',
     "        if key:\n            pass",
     ["D1", "D9"]),
]


def read(fp):
    """按二进制读 + 手动解码：项目是 CRLF，用文本模式读会把 \\r\\n 统一成 \\n，
    写回时就悄悄把整个文件改成 LF（L186 —— 一次扰动改掉 12704 行的行尾）。"""
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


def run_test():
    r = subprocess.run([PY, TEST], capture_output=True, text=True,
                       cwd=ROOT, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    m = re.search(r"PASS=(\d+) FAIL=(\d+)", out)
    return red, (m.group(1), m.group(2)) if m else ("?", "?"), out


# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检。
# 本脚本在顶层一次跑完「改源码 → 跑判据 → 还原」，只靠下面这个 try/finally 兜底；
# 而 Python 默认的 SIGTERM 处理器直接终止进程（不抛异常、不走 finally），
# 被超时强杀时 ui.py / diagnostic_export.py 会留在变异态（详见 _perturb_guard.py）。
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm()

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read(fp)
    _crlf = {fp: is_crlf(fp) for fp in FILES}

    for name, fp, old, new, expect in CASES:
        src = _backup[fp]
        if old not in src:
            MISS.append(f"{name}（原串没匹配上，扰动没生效）")
            print(f"  [SKIP] {name} — 原串未命中")
            continue
        write(fp, src.replace(old, new, 1), _crlf[fp])
        red, stat, out = run_test()
        # 期望的关键字必须都在变红列表里
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print(f"  [{tag}] {name} → FAIL 数 {stat[1]}，命中 {len(red)} 条"
              + ("" if ok else f"；期望 {expect}，实际 {red}"))
        (HIT if ok else MISS).append(name)
        write(fp, _backup[fp], _crlf[fp])   # 立刻恢复，别让扰动叠加
finally:
    for fp, s in _backup.items():
        write(fp, s, _crlf[fp])
    print("  （已恢复原文件，行尾保持原样）")

print(f"\n=== 扰动汇总：命中 {len(HIT)}/{len(CASES)} ===")
# 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
for m in MISS:
    print("   ✗", m)
sys.exit(1 if MISS else 0)
