# -*- coding: utf-8 -*-
"""扰动：v4.271 审阅剩余项。改坏下面任意一处，判据必须翻红。

SKIP / 全绿都算哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_review_followups_271.py")

_backup = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def clean_pycache():
    import shutil
    for dirpath, dirnames, _files in os.walk(ROOT):
        if "_dev_history" in dirpath or "dist" in dirpath or "backup" in dirpath:
            continue
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


sys.path.insert(0, os.path.join(ROOT, "_perturb"))
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=180)
    red = [ln.strip() for ln in (p.stdout or "").splitlines() if "[FAIL]" in ln]
    if p.returncode != 0 and not red:
        err = (p.stderr or "").strip().splitlines()
        red = err[-3:] or ["returncode=%s" % p.returncode]
    return (not red) and p.returncode == 0, red


CASES = [
    ("V1 高成本波不再拦住",
     "legion.py",
     "    if \"仍要开工\" in str(task or \"\"):\n        return False\n    return True\n",
     "    if \"仍要开工\" in str(task or \"\"):\n        return False\n    return False  # 扰动：高成本波不再拦住\n",
     ["高成本波 Critical 会拦住"]),
    ("V2 工人不再调用拦住",
     "legion_worker.py",
     "                        if _cok is False and legion.capability_gap_blocks_wave(\n"
     "                                True, True, task):\n"
     "                            self._pause_for_capability_gap(\n"
     "                                wave_no, _cgap, waves, task, gate_reports)\n"
     "                            return\n",
     "                        if False:  # 扰动：高成本波不再调用拦住\n"
     "                            pass\n",
     ["工人在高成本波调用拦住"]),
    ("V3 拒用退回裸正文",
     "legion.py",
     "        log.info(\"军团技能拒用 %s：%s\", slug, getattr(verdict, \"reason\", \"\"))\n"
     "        return \"\"\n",
     "        log.info(\"军团技能拒用 %s：%s\", slug, getattr(verdict, \"reason\", \"\"))\n"
     "        return None  # 扰动：拒用退回裸正文\n",
     ["拒用不得退回裸正文"]),
    ("V4 保存抹掉职责标签",
     "legion_ui.py",
     "            \"capability_tags\": _split_role_list(self.e_tags.text()),\n",
     "            \"capability_tags\": [],  # 扰动：保存抹掉职责标签\n",
     ["保存后职责标签还在"]),
    ("V5 口播跳过涂成完成",
     "director_panel.py",
     "    if portrait and index in (2, 4) and index < current:\n        return \"skipped\"\n",
     "    if False:  # 扰动：口播跳过涂成完成\n        return \"skipped\"\n",
     ["口播跳过人物不当完成"]),
    ("V6 导演台熔断文案退回主对话",
     "tools.py",
     "    if surface == \"director\":\n",
     "    if False:  # 扰动：导演台熔断文案退回主对话\n",
     ["导演台熔断不提 HTML"]),
    ("V7 设置页又写打开弹层",
     "ui.py",
     "        sub = QLabel(\"模型和密钥、干活方式、备份都在这一页，不用再开第二层。\")\n",
     "        sub = QLabel(\"点击按钮打开设置弹层。\")  # 扰动：设置又套一层\n",
     ["设置页不再单独弹层"]),
]


def main():
    for fp in {c[1] for c in CASES}:
        _backup[fp] = read_raw(fp)
    clean_pycache()
    ok, red = run_test()
    if not ok:
        print("PERTURB FAIL baseline red:", red[:8])
        return 1
    hit = miss = 0
    try:
        for name, fp, old, new, expects in CASES:
            blob = read_raw(fp)
            if blob.count(old.encode("utf-8")) != 1:
                print("SKIP", name, "count", blob.count(old.encode("utf-8")))
                miss += 1
                continue
            write_raw(fp, blob.replace(old.encode("utf-8"), new.encode("utf-8"), 1))
            clean_pycache()
            ok2, red2 = run_test()
            write_raw(fp, _backup[fp])
            clean_pycache()
            joined = "\n".join(red2)
            if ok2 or not all(e in joined for e in expects):
                print("MISS", name, red2[:6])
                miss += 1
            else:
                print("HIT", name)
                hit += 1
    finally:
        for fp, blob in _backup.items():
            write_raw(fp, blob)
        clean_pycache()
    print("PERTURB PASS=%d FAIL=%d" % (hit, miss))
    return 0 if miss == 0 and hit == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
