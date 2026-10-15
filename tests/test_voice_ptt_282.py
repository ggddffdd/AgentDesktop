# -*- coding: utf-8 -*-
"""v4.282.0 语音回退 push-to-talk 验收判据（judge-first）。

根因（用户体感「升级语音后整的不能用 + 小臭没语音回复」）
------------------------------------------------
v4.275.0 引入「连续对话(VAD)」：端点模型加载后把「按住说话」按钮劫持成
连续监听模式。两个问题：
  1) 连续监听对噪声敏感，经常误触发/不触发 → 「整的不能用」；
  2) 助手回读声被当成插嘴，`utterance_epoch` 自增使回复 token 失配，
     被 voice_turn.enqueue 整段丢弃 → 「小臭没语音回复」。

v4.282.0 回退为纯 push-to-talk：
  - 「按住说话」按钮永远 = 按下录音、松开识别发送，不再被 VAD 劫持；
  - 助手回复一律走直接 TTS（_TTSWorker → play_audio），不经过轮次机。

静态护栏（fail-closed）：谁把「连续对话」劫持加回 ui.py，判据立刻红。
本套件不 import ui（不需要 Qt），只读源码文本做结构性断言。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

UI = os.path.join(ROOT, "ui.py")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s%s" % (name, ("  —— " + detail) if detail else ""))


def _read():
    with open(UI, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _method_body(src, name):
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("    def %s(" % name):
            start = i
            break
    if start is None:
        return None
    body = []
    for ln in lines[start + 1:]:
        if ln.startswith("    def ") or ln.startswith("class "):
            break
        body.append(ln)
    return "\n".join(body)


def main():
    src = _read()

    # ===== 负向护栏：连续对话劫持不得回潮 =====
    check("G1 ui.py 不再导入 VoiceConversation（不接连续对话）",
          "from voice_session import VoiceConversation" not in src,
          "ui.py 仍接连续对话(VoiceConversation 导入)")

    _conv_calls = ["conv.start(", "conv.vad_ready", "conv.speak(",
                   "conv.active", "conv.shutdown(", "conv.prepare("]
    bad = [c for c in _conv_calls if c in src]
    check("G2 ui.py 不含连续对话调用(conv.start/vad_ready/speak/active/shutdown/prepare)",
          not bad, "ui.py 仍含连续对话调用(%s)" % " / ".join(bad))

    start_body = _method_body(src, "_start_talk") or ""
    check("G3 _start_talk 未被 VAD 劫持成连续对话",
          "conv.start(" not in start_body and "conv.vad_ready" not in start_body,
          "_start_talk 仍被 VAD 劫持成连续对话")

    speak_body = _method_body(src, "_speak") or ""
    check("G4 _speak 不把回复路由到轮次机(conv.speak)",
          "conv.speak(" not in speak_body,
          "_speak 仍把回复路由到轮次机(conv.speak)")

    check("G5 连续对话接线已移除(def _voice_conv / def _on_voice_utterance 不在)",
          "def _voice_conv" not in src and "def _on_voice_utterance" not in src,
          "连续对话接线未移除(def _voice_conv/_on_voice_utterance 仍在)")

    # ===== 正向护栏：push-to-talk + 直接 TTS 真实在位（防止「只删坏代码没接好」）=====
    check("P1 _start_talk 接 push-to-talk 录音(self.recorder.start)",
          "self.recorder.start(" in start_body,
          "_start_talk 未接 push-to-talk 录音")
    check("P2 _stop_talk 松手即送识别(self._submit_asr)",
          "self._submit_asr(" in (_method_body(src, "_stop_talk") or ""),
          "_stop_talk 松手未送识别")
    check("P3 _speak 走直接 TTS(_TTSWorker)",
          "_TTSWorker" in speak_body,
          "_speak 未走直接 TTS(_TTSWorker)")

    print("VOICE_PTT PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
