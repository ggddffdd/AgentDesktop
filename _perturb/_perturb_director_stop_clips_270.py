# -*- coding: utf-8 -*-
"""扰动：导演台「停止说真话」和「全部重生成不丢旧片段」。

改坏下面任意一处，tests/test_director_stop_clips_270.py 必须翻红。
SKIP / 全绿都算哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_director_stop_clips_270.py")
VP = "video_pipeline.py"
PANEL = "director_panel.py"

_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


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


def grab(fp, start, end):
    src = read_raw(fp).decode("utf-8").replace("\r\n", "\n")
    i = src.find(start)
    if i < 0:
        raise SystemExit("原串起始未命中：%s / %r" % (fp, start[:80]))
    j = src.find(end, i + len(start))
    if j < 0:
        raise SystemExit("原串结束未命中：%s / %r" % (fp, end[:80]))
    return src[i:j]


_OLD_ALIGN = grab(VP, "        self.begin_job(\"generate_all_clips\")\n",
                  "        if self._is_cancelled():")
_OLD_STORY = grab(VP, "        text = self._gen_story(",
                  "        self.story = text\n")
_OLD_ARMED = grab(
    VP,
    "            if (isinstance(armed, dict) and armed.get(\"consume\")\n",
    "                armed[\"consume\"] = False\n")
_OLD_REGEN = grab(PANEL, "    for _c in state:\n",
                  "    try:\n        _render_clips(app)\n")
_OLD_DONE = grab(PANEL, "def _on_clips_done(app, ok, total, msg):\n",
                 "    paths = list(")

CASES = [
    ("V1 整批开跑就清空 clip_paths",
     VP, _OLD_ALIGN,
     "        self.begin_job(\"generate_all_clips\")\n"
     "        self.clip_paths = [None] * len(self.shots)  # 扰动：开跑就清空旧片段\n",
     ["第 1 镜失败仍指向旧片段"]),
    ("V2 停止后仍把剧本写入",
     VP, _OLD_STORY,
     "        text = self._gen_story(self.topic, self.n, self.with_dialogue, feedback=feedback)\n"
     "        # 扰动：停止后仍落剧本\n",
     ["停止后 gen_story 不覆盖旧剧本"]),
    ("V3 已武装的停止被 begin_job 换发清掉",
     VP, _OLD_ARMED,
     "            if False:  # 扰动：已武装的停止也换发新令牌\n",
     ["工作线程里的 begin_job 不再把刚点的停止清掉"]),
    ("V4 全部重新生成先抹掉卡片路径",
     PANEL, _OLD_REGEN,
     "    for _c in state:\n"
     "        _c[\"status\"] = \"queued\"\n"
     "        _c[\"path\"] = None  # 扰动：开跑就丢掉旧片段\n"
     "        _c[\"error\"] = \"\"\n",
     ["开跑时卡片路径还在"]),
    ("V5 停止后仍走逐镜完成",
     PANEL, _OLD_DONE,
     "def _on_clips_done(app, ok, total, msg):\n"
     "    if False:  # 扰动：停止后仍报完成\n"
     "        _on_director_stopped(app, \"clips\")\n"
     "        return\n",
     ["停止后不显示逐镜生成完成"]),
]

HIT, MISS = [], []
try:
    for fp in (VP, PANEL):
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    print("基线自检（未变异时必须全绿）：")
    ok, red = run_test()
    if not ok:
        print("  [基线红] %s" % "; ".join(red[:8]))
        sys.exit(2)
    print("  基线全绿\n")

    _norm = {fp: _backup[fp].decode("utf-8").replace("\r\n", "\n") for fp in _backup}
    bad = [n for n, fp, old, _new, _e in CASES if old not in _norm[fp]]
    if bad:
        print("  [中止] 原串未命中：")
        for n in bad:
            print("    - " + n)
        sys.exit(2)
    print("  原串预检：%d/%d 命中\n" % (len(CASES), len(CASES)))

    for name, fp, old, new, expect in CASES:
        write_raw(fp, _backup[fp])
        clean_pycache()
        src = _norm[fp]
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] %s" % name)
            continue
        out = src.replace(old, new, 1)
        blob = out.replace("\n", "\r\n") if _crlf[fp] else out
        write_raw(fp, blob.encode("utf-8"))
        _is_red, red = run_test()
        joined = "\n".join(red)
        hit = (not _is_red) and all(e in joined for e in expect)
        print("  [%s] %s → 红 %d 条%s" % (
            "HIT " if hit else "MISS", name, len(red),
            "" if hit else "；期望含 %s；实际 %s" % (expect, red[:4])))
        (HIT if hit else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()
    print("  （已恢复原文件）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
