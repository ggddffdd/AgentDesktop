# -*- coding: utf-8 -*-
"""v4.275.0 语音端点与插嘴扰动。

改坏五处修复点，跑 tests/test_voice_vad_275.py，确认判据必红。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。

  P1 静音不再收口
  P2 打断后不清队列
  P3 模型加载失败不再给那句中文
  P4 朗读中不抬插嘴阈值
  P5 对话不再按句朗读
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_voice_vad_275.py")

F_VAD = os.path.join(ROOT, "voice_vad.py")
F_TURN = os.path.join(ROOT, "voice_turn.py")
F_UI = os.path.join(ROOT, "ui.py")

OLD_P1 = "        if self._silence >= self._min_silence:"
NEW_P1 = "        if False and self._silence >= self._min_silence:  # 扰动：静音不再收口"

OLD_P2 = """        dropped.extend(self.queue)
        self.queue.clear()
        self.utterance_epoch += 1"""
NEW_P2 = """        dropped.extend(self.queue)
        self.utterance_epoch += 1  # 扰动：打断后仍留着队列"""

OLD_P3 = """        log.warning("VAD 模型加载失败: %s", exc)
        return None, FALLBACK_HINT"""
NEW_P3 = """        log.warning("VAD 模型加载失败: %s", exc)
        return None, ""  # 扰动：失败不再提示"""

OLD_P4 = "        thr = self._barge_threshold"
NEW_P4 = "        thr = self._threshold  # 扰动：播放中不抬阈值"

OLD_P5 = """            conv.speak(text, getattr(self, "_inflight_voice_epoch", None))
            return"""
NEW_P5 = """            pass  # 扰动：对话不再按句朗读
            return"""

CASES = [
    ("P1 静音不再收口", F_VAD, OLD_P1, NEW_P1, ["静音达标就收口"]),
    ("P2 打断后仍留着队列", F_TURN, OLD_P2, NEW_P2, ["打断后队列被清空"]),
    ("P3 失败不再提示", F_VAD, OLD_P3, NEW_P3, ["失败提示是一句中文"]),
    ("P4 播放中不抬阈值", F_VAD, OLD_P4, NEW_P4, ["朗读中低于插嘴阈值不算插嘴"]),
    ("P5 对话不再按句朗读", F_UI, OLD_P5, NEW_P5, ["界面在语音对话里按句朗读"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_VAD, F_TURN, F_UI])
    orig = {}
    for f in (F_VAD, F_TURN, F_UI):
        with open(f, "r", encoding="utf-8", newline="") as fh:
            orig[f] = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = src.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    finally:
        for f, s in orig.items():
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(s)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
