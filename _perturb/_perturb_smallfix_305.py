# -*- coding: utf-8 -*-
"""v4.305.0 三项小改的扰动：真实改坏源码，目标判据必须翻红。

九组扰动覆盖：
  F-04 skill_enabled_decision 决策 + config 接线；
  F-05 artifact_snapshot_root + tools 三处快照根接线；
  F-08 count_event_filter_defs + ui 合并定义。

注意换行混合：config.py/smallfix_guard_305.py 是 LF，tools.py/ui.py 是 CRLF。
io.open(newline="") 保留原换行，锚点按各文件 NL 拼接。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard  # noqa: E402

SUITE = os.path.join(ROOT, "tests", "test_smallfix_305.py")
CR = "\r\n"
LF = "\n"

# (file, label, anchor_lines(list of str, 用 \n 表示逻辑行), replacement, want, newline)
CASES = [
    # ---- F-04 纯函数 ----
    ("smallfix_guard_305.py", "F-04 全禁用判定失效",
     ["    if disabled_all:", "        return False"],
     "    if False:  # 扰动：全禁用失效" + LF + "        return False", "A1", LF),
    ("smallfix_guard_305.py", "F-04 白名单比对失效（恒放行）",
     ["    keep = {normalize(x) for x in enabled}", "    return n in keep"],
     "    keep = {normalize(x) for x in enabled}" + LF + "    return True  # 扰动：恒放行", "A8", LF),
    # ---- F-04 接线 ----
    ("config.py", "F-04 config 接线断开（回到吞错 strip）",
     ["    from smallfix_guard_305 import skill_enabled_decision",
      "    return skill_enabled_decision(",
      "        name, _load_enabled_skills(), skills_disabled_all(), _norm)"],
     "    return (name or '').strip()  # 扰动：接线断开回退 strip", "B2", LF),
    # ---- F-05 纯函数 ----
    ("smallfix_guard_305.py", "F-05 快照根返回 script_dir（相对路径产物漏报）",
     ["    return run_cwd"],
     "    return script_dir  # 扰动：错误地以脚本目录为快照根", "A9", LF),
    # ---- F-05 接线 ----
    ("tools.py", "F-05 before 快照回退到 ws",
     ["    before = snapshot_workspace(snap_root)"],
     "    before = snapshot_workspace(ws)  # 扰动：回退错误快照根", "B10", CR),
    ("tools.py", "F-05 deliverable 拼接回退到 ws",
     ["        full = os.path.join(snap_root, nf)"],
     "        full = os.path.join(ws, nf)  # 扰动：拼接回退", "B12", CR),
    # ---- F-08 纯函数 ----
    ("smallfix_guard_305.py", "F-08 计数恒为 0（漏检重复定义）",
     ["            return sum(",
      "                1 for item in node.body",
      "                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))",
      "                and item.name == \"eventFilter\"",
      "            )"],
     "            return 0  # 扰动：恒 0 漏检", "A12", LF),
    # ---- F-08 接线 ----
    ("ui.py", "F-08 合并定义里删掉焦点联动（回到死代码）",
     ["        if getattr(self, \"_input_frame\", None) is not None and obj is getattr(self, \"input_box\", None):",
      "            if event.type() == QEvent.FocusIn:"],
     "        if False and getattr(self, \"_input_frame\", None) is not None and obj is getattr(self, \"input_box\", None):" + CR
     + "            if event.type() == QEvent.FocusIn:", "B18", CR),
    ("tools.py", "F-05 snap_root 接线顺序颠倒（依赖未定义→运行崩溃）",
     ["    run_cwd = WORKSPACE_DIR" + CR
      + "    # v4.305.0 F-05：产物快照根必须与执行 cwd 同根（纯判据 artifact_snapshot_root）。" + CR
      + "    # 旧代码脚本落 ws、cwd=WORKSPACE_DIR、快照却用 ws，相对路径产物落在 cwd 抓不到。" + CR
      + "    from smallfix_guard_305 import artifact_snapshot_root" + CR
      + "    snap_root = artifact_snapshot_root(run_cwd, ws)"],
     "    from smallfix_guard_305 import artifact_snapshot_root" + CR
     + "    snap_root = artifact_snapshot_root(WORKSPACE_DIR, ws)" + CR
     + "    run_cwd = WORKSPACE_DIR", "B13b", CR),
    ("ui.py", "F-08 再插一个 eventFilter（重复定义回归）",
     ["    def eventFilter(self, obj, event):",
      "        \"\"\"v4.305.0 F-08：唯一的事件过滤器"],
     "    def eventFilter(self, obj, event):" + CR
     + "        \"\"\"扰动：重复定义\"\"\"" + CR
     + "        return super().eventFilter(obj, event)" + CR + CR
     + "    def eventFilter(self, obj, event):" + CR
     + "        \"\"\"v4.305.0 F-08：唯一的事件过滤器", "B14", CR),
]


def build(old_lines, new, nl):
    return nl.join(old_lines), new


def run_suite():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, SUITE], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    files = sorted({os.path.join(ROOT, rel) for rel, *_ in CASES})
    _perturb_guard.arm(files)
    originals = {p: io.open(p, encoding="utf-8", newline="").read() for p in files}
    baseline = run_suite()
    if "FAIL=0" not in baseline:
        print("BASELINE RED：套件本身未全绿")
        print(baseline[-1500:])
        print("PERTURB PASS=0 FAIL=%d" % len(CASES))
        return 1
    npass = nfail = 0
    for rel, label, old_lines, new, want, nl in CASES:
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8", newline="").read()
        old, new = build(old_lines, new, nl)
        if src.count(old) != 1:
            print("MISS %s：锚点数量=%d" % (label, src.count(old)))
            nfail += 1
            continue
        try:
            io.open(path, "w", encoding="utf-8", newline="").write(
                src.replace(old, new, 1))
            out = run_suite()
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(
                originals[path])
        red = [line for line in out.splitlines() if "[FAIL]" in line]
        if "PASS=" in out and any(want in line for line in red):
            print("PASS %s → %s" % (label, want))
            npass += 1
        else:
            print("MISS %s：期望 %s，实际 %s"
                  % (label, want, red[:3] or out[-200:]))
            nfail += 1
    for path, original in originals.items():
        if io.open(path, encoding="utf-8", newline="").read() != original:
            print("FAIL 源码未恢复：%s" % os.path.basename(path))
            nfail += 1
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
