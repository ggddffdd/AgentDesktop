# -*- coding: utf-8 -*-
"""v4.275.0 语音端点与插嘴。

行为级：
  · 合成音频上，静音不够不收口，静音达标就收口；短于最短语音的一声丢掉
  · 真实 Silero 模型：静音和纯音不切段，合成语音说完就切，而且不等到缓冲结束
  · 朗读中低于插嘴阈值、或确认窗口不够，不停；确认之后丢掉还没念的句子，迟到的播完回调也不补播
  · 模型文件缺失时只给一句中文提示
"""
import os
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np

import voice
import voice_turn
import voice_vad
from voice_vad import (FALLBACK_HINT, SAMPLE_RATE, WINDOW, SileroScorer,
                       StreamingSegmenter, VadConfig, try_load_vad)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, extra))


class Scripted(object):
    def __init__(self):
        self.q = []

    def push(self, n, prob):
        self.q.extend([float(prob)] * int(n))

    def __call__(self, chunk):
        if not self.q:
            return 0.0
        return self.q.pop(0)


class Player(object):
    def __init__(self):
        self.started = []
        self.halts = 0

    def play_sentence(self, text, generation):
        self.started.append((text, generation))

    def halt_playback(self):
        self.halts += 1


def _feed(machine, scorer, n, prob):
    scorer.push(n, prob)
    return machine.feed_pcm(np.zeros(WINDOW * n, dtype=np.int16))


def _kinds(events, kind):
    return [ev for ev in events if ev.get("kind") == kind]


def test_scripted_endpoint():
    print("=== 合成帧：静音达标才收口 ===")
    cfg = VadConfig(
        threshold=0.5, min_silence_ms=160, min_speech_ms=96,
        speech_pad_ms=0, barge_threshold=0.72, barge_confirm_ms=160,
        barge_onset_guard_ms=96)
    scorer = Scripted()
    seg = StreamingSegmenter(scorer, cfg)
    # 两帧不够最短语音
    scorer.push(2, 0.9)
    scorer.push(6, 0.0)
    early = seg.feed(np.zeros(WINDOW * 8, dtype=np.int16))
    check("短于最短语音不收口",
          not any(ev.kind == "utterance" for ev in early),
          "kinds=%s" % [ev.kind for ev in early])

    seg.reset()
    scorer = Scripted()
    seg.scorer = scorer
    scorer.push(3, 0.9)
    scorer.push(4, 0.0)
    mid = seg.feed(np.zeros(WINDOW * 7, dtype=np.int16))
    check("静音不够不收口",
          not any(ev.kind == "utterance" for ev in mid),
          "kinds=%s" % [ev.kind for ev in mid])
    scorer.push(1, 0.0)
    done = seg.feed(np.zeros(WINDOW, dtype=np.int16))
    utt = [ev for ev in done if ev.kind == "utterance"]
    check("静音达标就收口", len(utt) == 1, "n=%d" % len(utt))

    seg.reset()
    scorer = Scripted()
    seg.scorer = scorer
    # 中间只静两帧（不够 160ms），应当并成一句
    scorer.push(3, 0.9)
    scorer.push(2, 0.0)
    scorer.push(3, 0.9)
    scorer.push(5, 0.0)
    merged = seg.feed(np.zeros(WINDOW * 13, dtype=np.int16))
    check("短停顿并成一句",
          sum(1 for ev in merged if ev.kind == "utterance") == 1,
          "kinds=%s" % [ev.kind for ev in merged])


def test_barge():
    print("=== 插嘴：抬阈值、确认窗口、丢掉未念的句子 ===")
    cfg = VadConfig(
        threshold=0.5, min_silence_ms=160, min_speech_ms=96,
        speech_pad_ms=0, barge_threshold=0.72, barge_confirm_ms=160,
        barge_onset_guard_ms=96)
    scorer = Scripted()
    player = Player()
    machine = voice_turn.VoiceTurnMachine(StreamingSegmenter(scorer, cfg), player)
    machine.start()
    token = machine.issue_token()
    machine.enqueue(["甲。", "乙。", "丙。"], token)
    check("先只念第一句",
          [t for t, _g in player.started] == ["甲。"],
          str(player.started))
    gen1 = player.started[-1][1]
    machine.playback_finished(gen1)
    check("播完接着念第二句",
          player.started[-1][0] == "乙。", str(player.started))
    gen2 = player.started[-1][1]

    ev = _feed(machine, scorer, 3, 0.99)
    check("刚开口的保护窗不算插嘴", not _kinds(ev, "barge_in"))
    ev = _feed(machine, scorer, 8, 0.60)
    check("朗读中低于插嘴阈值不算插嘴", not _kinds(ev, "barge_in"),
          "halts=%d" % player.halts)
    ev = _feed(machine, scorer, 4, 0.95)
    check("确认窗口不够不停", not _kinds(ev, "barge_in"))
    ev = _feed(machine, scorer, 2, 0.10)
    check("中途掉下去要重新确认", not _kinds(ev, "barge_in"))
    ev = _feed(machine, scorer, 5, 0.95)
    barges = _kinds(ev, "barge_in")
    check("连续确认后打断", len(barges) == 1, str(barges))
    dropped = barges[0]["dropped"] if barges else []
    check("打断名单含正在念的和还没念的",
          "乙。" in dropped and "丙。" in dropped, str(dropped))
    check("打断后队列被清空", machine.queue == [], str(machine.queue))
    machine.playback_finished(gen2)
    machine.playback_finished(machine.generation)
    spoken = [t for t, _g in player.started]
    check("打断后不再播放丙", "丙。" not in spoken, str(spoken))
    machine.enqueue(["甲。", "乙。", "丙。"], token)
    spoken = [t for t, _g in player.started]
    check("旧一轮的回复不会再念", spoken.count("丙。") == 0 and spoken.count("甲。") == 1,
          str(spoken))
    token2 = machine.issue_token()
    machine.enqueue(["新的一句"], token2)
    check("新一轮可以接着念", player.started[-1][0] == "新的一句", str(player.started))


def test_split_and_config():
    print("=== 分句与配置 ===")
    check("按句号切开",
          voice_turn.split_sentences("你好。今天怎么样？") == ["你好。", "今天怎么样？"])
    check("没有标点就是一句", voice_turn.split_sentences("没有标点") == ["没有标点"])
    check("空文本没有句子", voice_turn.split_sentences("  ") == [])
    blank = VadConfig.from_cfg({})
    check("缺省静音 600ms", blank.min_silence_ms == 600)
    check("缺省插嘴阈值 0.72", abs(blank.barge_threshold - 0.72) < 1e-6)
    tuned = VadConfig.from_cfg({"voice": {
        "vad_min_silence_ms": 800, "vad_threshold": "nope"}})
    check("配置能改静音时长", tuned.min_silence_ms == 800)
    check("坏阈值退回 0.5", abs(tuned.threshold - 0.5) < 1e-6)
    import config
    live = VadConfig.from_cfg(config.DEFAULT_CONFIG)
    check("默认配置带上端点参数", live.min_speech_ms == 250 and live.barge_confirm_ms == 320)


def test_resample_pull():
    print("=== 采集帧可以边录边取 ===")
    data = np.zeros(4800, dtype=np.int16)
    out = voice.resample_int16(data, 48000, 16000)
    check("48000 重采样到 16000", len(out) == 1600, "len=%d" % len(out))
    rec = voice.Recorder()
    rec._stream = object()
    rec._cap_rate = 16000
    rec._frames = [np.arange(1600, dtype=np.int16).reshape(-1, 1)]
    got = rec.pull_new()
    again = rec.pull_new()
    check("pull_new 取出后清空", got is not None and len(got) == 1600 and again.size == 0,
          "len=%s" % (None if got is None else len(got)))
    rec._cap_rate = 48000
    rec._frames = [np.zeros((4800, 1), dtype=np.int16)]
    got = rec.pull_new()
    check("pull_new 按目标采样率交出去", got is not None and len(got) == 1600,
          "len=%s" % (None if got is None else len(got)))
    rec.close()
    check("关掉之后不再吐帧", rec.pull_new() is None)


def _load_speech():
    path = os.path.join(ROOT, "tests", "fixtures", "vad_espeak_16k.wav")
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).copy()
    return rate, ch, pcm


def test_silero_on_audio():
    print("=== Silero：合成语音上的端点 ===")
    model = voice_vad.default_model_path()
    size = os.path.getsize(model) if os.path.isfile(model) else 0
    check("模型文件已随仓库带上", size > 1000000, "size=%d" % size)
    vad, hint = try_load_vad(model)
    check("模型能加载", vad is not None and hint == "", hint)
    if vad is None:
        return
    rate, ch, speech = _load_speech()
    check("夹具是 16k 单声道", rate == SAMPLE_RATE and ch == 1 and len(speech) > 8000,
          "rate=%s ch=%s n=%d" % (rate, ch, len(speech)))

    def run(pcm, cfg=None):
        seg = StreamingSegmenter(SileroScorer(vad), cfg or VadConfig())
        return [ev for ev in seg.feed(pcm) if ev.kind == "utterance"]

    silence = np.zeros(SAMPLE_RATE * 2, dtype=np.int16)
    check("两秒静音不切段", len(run(silence)) == 0)
    t = np.arange(SAMPLE_RATE) / float(SAMPLE_RATE)
    tone = (np.sin(2 * np.pi * 220 * t) * 6000).astype(np.int16)
    check("纯音不切段", len(run(tone)) == 0)
    click = np.concatenate([
        speech[8000:8000 + int(0.1 * SAMPLE_RATE)],
        np.zeros(int(0.8 * SAMPLE_RATE), dtype=np.int16),
    ])
    check("短于最短语音的一声不切段", len(run(click)) == 0)
    pcm = np.concatenate([
        np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.int16),
        speech,
        np.zeros(int(1.0 * SAMPLE_RATE), dtype=np.int16),
    ])
    utt = run(pcm)
    check("合成语音切出一句", len(utt) == 1, "n=%d" % len(utt))
    if utt:
        check("说完就收口，不等缓冲播完",
              utt[0].end_sample < len(pcm) - int(0.25 * SAMPLE_RATE),
              "end=%d total=%d" % (utt[0].end_sample, len(pcm)))
        check("切出来的音频不是整段缓冲",
              0 < len(utt[0].pcm) < len(pcm),
              "pcm=%d total=%d" % (len(utt[0].pcm), len(pcm)))


def test_fallback_and_wiring():
    print("=== 加载失败与接线 ===")
    missing, hint = try_load_vad(os.path.join(ROOT, "models", "no_such_vad.onnx"))
    check("找不到模型时引擎为空", missing is None)
    check("失败提示是一句中文", hint == FALLBACK_HINT, repr(hint))
    check("提示里写明改回按住说话", "按住说话" in hint and "\n" not in hint)
    junk = os.path.join(ROOT, "models", "not_a_model.onnx")
    try:
        with open(junk, "wb") as fh:
            fh.write(b"not a model")
        _vad, hint2 = try_load_vad(junk)
    finally:
        try:
            os.remove(junk)
        except OSError:
            pass
    check("坏模型同样是这一句", hint2 == FALLBACK_HINT, repr(hint2))

    ui = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
    # 减法（v4.282.0）：自动 VAD 连续对话已从界面移除，按钮恢复纯 push-to-talk；
    # 回复走直接 TTS（_TTSWorker），不再经轮次机 conv.speak（旧模式把回读声当插嘴致整段丢弃）。
    check("界面回复合成为语音走直接 TTS（不再经轮次机）",
          "self._tts_thread = _TTSWorker(text, tts)" in ui
          and "conv.speak(text" not in ui)
    check("自动 VAD 状态/提示接线已从界面移除",
          "sig_hint.connect(self._on_vad_hint)" not in ui
          and "_on_vad_hint" not in ui)
    session = open(os.path.join(ROOT, "voice_session.py"), encoding="utf-8").read()
    check("会话加载失败会把提示送出去",
          "try_load_vad" in session and "sig_hint.emit" in session)
    spec = open(os.path.join(ROOT, "小臭玩AI.spec"), encoding="utf-8").read()
    check("spec 带上 vad 模型", "silero_vad.onnx" in spec)
    check("spec 登记 onnxruntime", "'onnxruntime'" in spec)
    check("spec 登记 voice_vad", "'voice_vad'" in spec)
    check("spec 登记 voice_session", "'voice_session'" in spec)
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    check("依赖锁了 onnxruntime", "onnxruntime==" in req)
    check("没有把 torch 加进依赖", "torch==" not in req)


def main():
    test_scripted_endpoint()
    test_barge()
    test_split_and_config()
    test_resample_pull()
    test_silero_on_audio()
    test_fallback_and_wiring()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
