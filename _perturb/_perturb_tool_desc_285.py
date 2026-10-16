# -*- coding: utf-8 -*-
"""v4.285.0 P2b 扰动：工具描述分工点名改坏必红。

P1 db_delete 描述打回「删除记录。」（长度 + 邻居点名双杀）
P2 screenshot 描述打回一句话（长度杀，核心 25 成员）
P3 mouse_move 描述点名邻居摘除（只杀 C 段点名判据）

跑 tests/test_tool_desc_285.py。原串从真实文件取且唯一。
IO 纯 text mode（tool_defs.py / system_control_tools.py 磁盘 CRLF/LF
均 round-trip 原样）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_tool_desc_285.py")
F_DEFS = os.path.join(ROOT, "tool_defs.py")
F_SYS = os.path.join(ROOT, "system_control_tools.py")

OLD_P1 = '"description": "按 record_id 从 SQLite 永久删除一条记录（不可恢复，执行前会请求用户确认）。\\n仅当用户明确说「删除/去掉/清掉某条」时使用；只是改字段值用 db_update，别删了重建。\\nrecord_id 必须来自 db_query 的查询结果；批量删逐条调并告知进度。"'
NEW_P1 = '"description": "删除记录。"'

OLD_P2 = '"description": "截取屏幕、指定区域或指定窗口的图像，返回截图保存路径。\\nregion=[x,y,w,h] 只截该矩形区域（屏幕像素坐标，左上为原点）；不传 region 且不传 window_title 时截全屏。只要某个应用窗口的内容用 app_screenshot（只截该窗口、不含桌面）；要整个屏幕或任意区域才用本工具。"'
NEW_P2 = '"description": "截取屏幕、指定区域或指定窗口的图像。返回截图保存路径。"'

OLD_P3 = '"description": "把鼠标指针移动到指定坐标（x/y 为屏幕像素坐标，左上角为原点），只移动不点击。\\n需要「移动并点击」一步完成时直接用 mouse_click 传坐标，不要先 move 再 click 浪费一轮。"'
NEW_P3 = '"description": "把鼠标指针移动到指定坐标（x/y 为屏幕像素坐标，左上角为原点），只移动不点击。"'

CASES = [
    ("P1 db_delete 描述打回一句话", F_DEFS, OLD_P1, NEW_P1,
     ["B1 目标工具描述全部达到长度下限", "C db_delete 描述点名邻居"]),
    ("P2 screenshot 描述打回一句话", F_SYS, OLD_P2, NEW_P2,
     ["B1 目标工具描述全部达到长度下限", "C screenshot 描述点名邻居"]),
    ("P3 mouse_move 邻居点名摘除", F_SYS, OLD_P3, NEW_P3,
     ["C mouse_move 描述点名邻居"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_DEFS, F_SYS])
    files = {F_DEFS: open(F_DEFS, "r").read(),
             F_SYS: open(F_SYS, "r").read()}
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            orig = files[target]
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w") as fh:
                fh.write(orig.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w") as fh:
                fh.write(orig)
    finally:
        for path, content in files.items():
            with open(path, "w") as fh:
                fh.write(content)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
