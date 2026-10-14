# -*- coding: utf-8 -*-
"""扰动：导演台界面编辑、开拍前选择、尾帧留座、提示词自检。

改坏下面任意一处，tests/test_director_desk_270.py 必须翻红。
SKIP / 全绿都算哑弹。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_director_desk_270.py")
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


CASES = [
    ("V1 超过 60 字又当原稿",
     VP,
     "    if kind == \"brief\":\n"
     "        return {\"topic\": body, \"passthrough\": None, \"needs_confirm\": False}\n",
     "    if kind == \"brief\" or len((topic_raw or \"\").strip()) >= 60:  # 扰动：字数又当原稿\n"
     "        return {\"topic\": \"本人形象口播（原稿直通）\", \"passthrough\": (topic_raw or \"\").strip(),\n"
     "                \"needs_confirm\": False}\n",
     ["长说明不当原稿"]),
    ("V2 运镜栏不进提示词",
     VP,
     "    if cam.lower() in en.lower():\n        return en\n",
     "    if True:  # 扰动：运镜栏不进提示词\n        return en\n",
     ["改过的运镜替换旧镜头词"]),
    ("V3 同场不给尾帧留座",
     VP,
     "        reserve_tail = (\n"
     "            not self.portrait_mode\n"
     "            and self._tail_required(i)\n"
     "            and bool(prev_tail)\n"
     "            and os.path.isfile(prev_tail)\n"
     "        )\n",
     "        reserve_tail = False  # 扰动：不给尾帧留座\n",
     ["同场尾帧没有被道具挤掉"]),
    ("V4 理解失败直接开写",
     PANEL,
     "        _offer_understand_fallback(app, topic_raw, on_ok)\n        return False\n",
     "        on_ok()  # 扰动：理解失败直接开写\n        return False\n",
     ["理解失败不直接开写"]),
    ("V5 关键帧又拼回 cinematic",
     VP,
     "            f\"High detail, consistent lighting and visual \"\n",
     "            f\"High detail, cinematic composition, consistent lighting and visual \"  # 扰动\n",
     ["关键帧不再拼 cinematic"]),
    ("V6 智能剧本又看不见用户镜数",
     VP,
     "        return (f\"用户指定了 {int(n)} 镜（每镜约 {duration} 秒，总长约 {total} 秒）。\"\n",
     "        return (f\"镜头数量自行决定，通常 6~18 镜。\"  # 扰动：不写用户镜数\n",
     ["智能剧本看见用户镜数"]),
    ("V7 提交时不带方法论",
     VP,
     "            if not self.portrait_mode and SHOT_SUBMIT_RULES not in prompt:\n",
     "            if False:  # 扰动：提交时不带方法论\n",
     ["视频提示词带上提交规则"]),
    ("V8 续跑不存未采用的稿",
     PANEL,
     "    draft = {}\n    ed = getattr(app, \"director_story_edit\", None)\n",
     "    return {}  # 扰动：不存未采用的稿\n    ed = getattr(app, \"director_story_edit\", None)\n",
     ["续跑记下未采用的剧本"]),
    ("V9 续跑总是打开智能分镜",
     PANEL,
     "                \"smart\": (bool(app.director_smart.isChecked())\n"
     "                          if getattr(app, \"director_smart\", None) is not None\n"
     "                          else bool(getattr(p, \"smart\", True))),\n",
     "                \"smart\": True,  # 扰动：续跑总是打开智能分镜\n",
     ["续跑记下智能分镜是关的"]),
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
