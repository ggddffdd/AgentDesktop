# -*- coding: utf-8 -*-
"""v4.267.0 结构治理扰动：改坏必红。

三条分别打在外移后的三条接线，不改判据本身：
  V1 复杂度薄包装被短路成恒 False → 真实复杂句不再升舱（B2）
  V2 工具意图拿掉非指令短路       → 讨论句又被词表当成要调工具（B5）
  V3 扰动发现改回扫仓库根目录     → _perturb/ 里的脚本从清单里消失（L1）
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _perturb_guard  # noqa: E402

UI = os.path.join(ROOT, "ui.py")
ROUTING = os.path.join(ROOT, "ui_routing.py")
RUN_ALL = os.path.join(ROOT, "tests", "run_all.py")
JUDGE = os.path.join(ROOT, "tests", "test_struct_layout_267.py")

CASES = [
    ("V1 复杂度薄包装恒 False",
     UI,
     "return ui_routing._is_complex(self.cfg, messages, routing)",
     "return False  # 扰动：复杂度判定被短路",
     ["B2"]),
    ("V2 拿掉非指令短路",
     ROUTING,
     "        if _ig.is_non_action_message(last_user):\n"
     "            log.info(\"工具意图判定：判为非指令消息（%s），不强制 required\",\n"
     "                     _ig.why_blocked(last_user))\n"
     "            return False",
     "        if False:  # 扰动：拿掉非指令短路\n"
     "            return False",
     ["B5"]),
    ("V3 扰动发现改回扫根目录",
     RUN_ALL,
     "    return sorted(p for p in PERTURB_DIR.glob(PERTURB_PREFIX + \"*.py\")\n"
     "                  if p.name not in PERTURB_EXCLUDE)",
     "    return sorted(p for p in ROOT.glob(PERTURB_PREFIX + \"*.py\")  # 扰动：改回扫根目录\n"
     "                  if p.name not in PERTURB_EXCLUDE)",
     ["L1"]),
]


def _drop_pyc():
    for base in (ROOT, os.path.join(ROOT, "tests")):
        cache = os.path.join(base, "__pycache__")
        if not os.path.isdir(cache):
            continue
        for fn in os.listdir(cache):
            if fn.split(".")[0] in ("ui", "ui_routing", "run_all"):
                try:
                    os.remove(os.path.join(cache, fn))
                except OSError:
                    pass


def run_judge():
    _drop_pyc()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    r = subprocess.run([sys.executable, JUDGE], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    red = [line.strip() for line in out.splitlines() if "[FAIL]" in line]
    return red, out


def main():
    _perturb_guard.arm([UI, ROUTING, RUN_ALL])
    npass = nfail = 0
    base_red, _ = run_judge()
    if base_red:
        print("BASELINE_RED 基线本身就是红的，判据不可用：")
        for x in base_red[:8]:
            print("   ", x)
        nfail += 1
    else:
        print("BASELINE GREEN（判据当前全绿）")

    originals = {path: io.open(path, encoding="utf-8").read()
                 for path in (UI, ROUTING, RUN_ALL)}
    for label, path, old, new, want in CASES:
        src = originals[path]
        if src.count(old) != 1:
            print("SKIP  %s —— 锚点出现 %d 次" % (label, src.count(old)))
            nfail += 1
            continue
        io.open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
        try:
            red, out = run_judge()
        finally:
            io.open(path, "w", encoding="utf-8", newline="\n").write(src)
        hit = [w for w in want if any(w in x for x in red)]
        if hit:
            print("PASS  %s → 命中 %s" % (label, "、".join(hit)))
            npass += 1
        else:
            print("MISS  %s → 期望 %s，实际 %s" % (label, want, red[:6] or "无红行"))
            nfail += 1
            print(out[-600:])
    print("PERTURB PASS=%d FAIL=%d" % (npass, nfail))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
