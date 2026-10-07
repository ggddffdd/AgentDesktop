# -*- coding: utf-8 -*-
"""扰动验证 v4.229.0：生视频面板「提示词内带台词」→ 真配音（音画同出）。

手法：备份原字节 → 逐条退化 tools.py 的台词抽取 / ui.py 的接线 →
跑 test_video_dialogue_229.py → 期望对应判据翻红 → 恢复。

验收 = **哑弹清零**。本轮另设反向照妖镜：
  · V2 删中文引号规则 → A 组「引号整句」必须红（证明 A 组不是恒真）
  · V4 删运镜术语护栏 → B 组「被引号包住的运镜」必须红（证明反例在约束实现）
  · V6 把画面描述退回原始 prompt → C2 必须红（证明「摘走台词」确有判据钉着）

⚠️ 本轮吸取 228 的教训：原串一律按**真实字节程序化截取**，不手抄。
   手抄含反斜杠/引号的原串必被转义吃掉（228 踩过），手抄两段拼接还会漏 \n。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_video_dialogue_229.py")

TS = "tools.py"
UI = "ui.py"

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
    for dirpath, dirnames, _files in os.walk(ROOT):
        if "_dev_history" in dirpath or "dist" in dirpath or "backup" in dirpath:
            continue
        for d in list(dirnames):
            if d == "__pycache__":
                import shutil
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=600)
    red = [l.strip() for l in p.stdout.splitlines() if "[FAIL]" in l]
    return (not red) and p.returncode == 0, red


def grab(fp, start, end=None):
    """从真实字节里按「起始标记 + 结束标记」截取连续原文。"""
    src = read_raw(fp).decode("utf-8").replace("\r\n", "\n")
    i = src.find(start)
    if i < 0:
        raise SystemExit("原串起始标记未命中：%s / %r" % (fp, start[:60]))
    if end is None:
        return src[i:]
    j = src.find(end, i + len(start))
    if j < 0:
        raise SystemExit("原串结束标记未命中：%s / %r" % (fp, end[:60]))
    return src[i:j]


# ---- 原串（全部 compute from real bytes）----
# ⚠️ grab 必须传 end：不传会截到文件末尾，等于把后半截源码整段删掉
#    （本次踩到：V1 少了 end 标记 → 变异体语法崩坏 → 判据整体起不来 → 误报 MISS）
_OLD_EARLY_RETURN = grab(TS,
                         '    if not prompt or not str(prompt).strip():\n        return "", None\n',
                         "    scene_lines, dlg_lines")
_OLD_QUOTED_IF = "if m and not any(w in line for w in _DLG_SCENE_WORDS):"
_OLD_BRACKET_PART = "\\s*[】）)\\]]?\\s*[:：]?\\s*(.+?)\\s*$"
_OLD_DIALOGUE_KW = grab(UI, "            first_frame=first, last_frame=last, images=refs,\n            dialogue=dialogue)")
_OLD_SUBMIT = "tools_mod.tool_video_gen, self.cfg, APP_DIR, submit_prompt,"
_OLD_FALLBACK = grab(UI, "        if dialogue and not scene:", "        submit_prompt = scene or prompt")
_OLD_HINT_TAIL = '            "或用中文引号包住整句）")'

CASES = [
    # ---- V1 照妖镜：抽台词整体失效（一律当没有台词）→ A 组 + C1 红 ----
    ("V1 照妖镜：抽台词整体失效（退回「无台词」）",
     TS, _OLD_EARLY_RETURN,
     "    if True:\n        return str(prompt).strip(), None  # 扰动：一律当没有台词\n",
     ["A 台词冒号", "C1"]),

    # ---- V2 反向照妖镜：删掉「中文引号整句」规则 → A 组引号用例红 ----
    ("V2 删掉中文引号整句规则",
     TS, _OLD_QUOTED_IF,
     "if False:  # 扰动：不再认引号整句",
     ["A 中文引号整句"]),

    # ---- V3 括号型标记回退成「必须有冒号」→ 【口播】/（台词）/[对白] 红 ----
    ("V3 括号型标记【台词】回退失效",
     TS, _OLD_BRACKET_PART,
     "\\s*[:：]\\s*(.+?)\\s*$  # 扰动：只认冒号",
     ["A 【口播】", "A （台词）", "A 对白方括号"]),

    # ---- V4 反向照妖镜：删掉运镜术语护栏 → 画面描述会被当台词念出来 ----
    ("V4 删掉运镜术语护栏（画面被误当台词）",
     TS, _OLD_QUOTED_IF,
     "if m:  # 扰动：不再排除运镜术语",
     ["B 被引号包住的运镜", "C7"]),

    # ---- V5 接线：不再把 dialogue 传给内核 → C1 红（回到 bug 本身）----
    ("V5 接线：不传 dialogue= 给内核",
     UI, _OLD_DIALOGUE_KW,
     "            first_frame=first, last_frame=last, images=refs)  # 扰动：不传台词",
     ["C1"]),

    # ---- V6 接线：画面描述退回原始 prompt（台词仍混在画面里）→ C2/C6 红 ----
    ("V6 接线：画面描述退回原始 prompt（台词没被摘走）",
     UI, _OLD_SUBMIT,
     "tools_mod.tool_video_gen, self.cfg, APP_DIR, prompt,  # 扰动：台词混在画面里",
     ["C2", "C6"]),

    # ---- V7 只有台词时不补中性画面 → C6/C6b 红 ----
    ("V7 只有台词时不补中性画面描述（空 prompt）",
     UI, _OLD_FALLBACK,
     "",
     ["C6"]),

    # ---- V8 UI 误导文案回归（又去指向不存在的框）→ D2 红 ----
    ("V8 UI 文案倒退回指向不存在的台词框",
     UI, _OLD_HINT_TAIL,
     '            "或用中文引号包住整句）")\n'
     '        self.video_prompt.setPlaceholderText('
     '"描述视频画面与镜头…（口播台词请填下方「台词/口播」框，不要写这里）")  # 扰动',
     ["D2"]),
]

HIT, MISS = [], []
try:
    for fp in (TS, UI):
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    print("基线自检（未变异时必须全绿，否则判据本身坏了）：")
    ok, red = run_test()
    if not ok:
        print("  [基线红] %s" % "; ".join(red[:6]))
        print("  基线不绿就停止扰动 —— 否则「命中」无意义。")
        sys.exit(2)
    print("  基线全绿 ✔\n")

    _norm = {fp: _backup[fp].decode("utf-8").replace("\r\n", "\n")
             for fp in _backup}
    _unmatched = [n for n, fp, old, _new, _e in CASES if old not in _norm[fp]]
    if _unmatched:
        print("  [中止] 以下用例原串未命中真实字节（防静默失效）：")
        for n in _unmatched:
            print("    - " + n)
        sys.exit(2)
    print("  原串预检：%d/%d 全部命中真实字节 ✔\n" % (len(CASES), len(CASES)))

    for name, fp, old, new, expect in CASES:
        write_raw(fp, _backup[fp])          # 每条都从原始备份重置
        clean_pycache()
        src = _norm[fp]
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] %s —— 原串未命中" % name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))

        _is_red, red = run_test()
        joined = "\n".join(red)
        hit = all(e in joined for e in expect)
        print("  [%s] %s → 红 %d 条%s"
              % ("HIT " if hit else "MISS", name, len(red),
                 "" if hit else "；期望含 %s" % expect))
        (HIT if hit else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
