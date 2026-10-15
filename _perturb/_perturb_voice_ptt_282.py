# -*- coding: utf-8 -*-
"""v4.282.0 语音 push-to-talk 回退扰动。

改坏两处修复点，跑 tests/test_voice_ptt_282.py，确认判据必红。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。

  P1 把「按住说话」按钮重新劫持成连续对话(VAD)
  P2 把助手回复重新路由到轮次机(conv.speak)
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_voice_ptt_282.py")

F_UI = os.path.join(ROOT, "ui.py")

OLD_P1 = "        self._recording = True"
NEW_P1 = (
    "        conv = self._voice_conv()\n"
    "        if conv.vad_ready:\n"
    "            if conv.start(self._current_mic()):\n"
    '                self.talk_btn.setText("结束对话")\n'
    "                return\n"
    "        self._recording = True"
)

OLD_P2 = "        self._tts_thread = _TTSWorker(text, tts)"
NEW_P2 = (
    "        conv.speak(text, None)  # 扰动：回复走轮次机\n"
    "        self._tts_thread = _TTSWorker(text, tts)"
)

CASES = [
    ("P1 按钮重新被劫持成连续对话", F_UI, OLD_P1, NEW_P1,
     ["_start_talk 仍被 VAD 劫持成连续对话", "ui.py 仍含连续对话调用"]),
    ("P2 回复重新路由到轮次机", F_UI, OLD_P2, NEW_P2,
     ["_speak 仍把回复路由到轮次机", "ui.py 仍含连续对话调用"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_UI])
    with open(F_UI, "r", encoding="utf-8", newline="") as fh:
        orig = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            if old not in orig:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = orig.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            mutated = orig.replace(old, new, 1)
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(mutated)
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(orig)
    finally:
        with open(F_UI, "w", encoding="utf-8", newline="") as fh:
            fh.write(orig)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
