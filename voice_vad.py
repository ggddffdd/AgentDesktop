# -*- coding: utf-8 -*-
"""语音端点：Silero VAD（onnxruntime + silero_vad.onnx），不依赖 torch。

模型随仓库放在 models/silero_vad.onnx（上游 MIT，见 models/NOTICE.txt）。
打包时由 小臭玩AI.spec 的 datas 带进 exe，离线可用。

加载失败时 try_load_vad 返回空引擎和一句中文提示，调用方改回按住说话。
"""
import logging
import os
import sys

log = logging.getLogger("dsdesktop.voice")

SAMPLE_RATE = 16000
WINDOW = 512          # 16kHz 下 32ms，Silero 规定的帧长
CONTEXT = 64

# 模型或运行时不可用时，界面只显示这一句。
FALLBACK_HINT = "语音端点检测暂时不可用，已改回按住说话。"


class VadConfig(object):
    """端点与插嘴的可调参数。界面配置见 config.DEFAULT_CONFIG['voice']。"""

    def __init__(self, threshold=0.5, min_silence_ms=600, min_speech_ms=250,
                 speech_pad_ms=200, barge_threshold=0.72, barge_confirm_ms=320,
                 barge_onset_guard_ms=180):
        self.threshold = float(threshold)
        self.min_silence_ms = int(min_silence_ms)
        self.min_speech_ms = int(min_speech_ms)
        self.speech_pad_ms = int(speech_pad_ms)
        self.barge_threshold = float(barge_threshold)
        self.barge_confirm_ms = int(barge_confirm_ms)
        self.barge_onset_guard_ms = int(barge_onset_guard_ms)

    @classmethod
    def from_cfg(cls, cfg):
        voice = {}
        if isinstance(cfg, dict):
            raw = cfg.get("voice") or {}
            if isinstance(raw, dict):
                voice = raw

        def num(key, default, lo, hi, cast):
            try:
                value = cast(voice.get(key, default))
            except (TypeError, ValueError):
                return default
            if value < lo or value > hi:
                return default
            return value

        return cls(
            threshold=num("vad_threshold", 0.5, 0.05, 0.95, float),
            min_silence_ms=num("vad_min_silence_ms", 600, 100, 3000, int),
            min_speech_ms=num("vad_min_speech_ms", 250, 80, 2000, int),
            speech_pad_ms=num("vad_speech_pad_ms", 200, 0, 1000, int),
            barge_threshold=num("vad_barge_threshold", 0.72, 0.05, 0.99, float),
            barge_confirm_ms=num("vad_barge_confirm_ms", 320, 80, 2000, int),
            barge_onset_guard_ms=num("vad_barge_onset_guard_ms", 180, 0, 1000, int),
        )


class VadEvent(object):
    def __init__(self, kind, pcm=None, end_sample=0):
        self.kind = kind
        self.pcm = pcm
        self.end_sample = int(end_sample)


def default_model_path():
    """开发态在仓库 models/；冻结态在 _MEIPASS 或 exe 旁的 _internal。"""
    rel = os.path.join("models", "silero_vad.onnx")
    here = os.path.dirname(os.path.abspath(__file__))
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return os.path.join(here, rel)
    bases = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        bases.append(meipass)
    exe_dir = os.path.dirname(getattr(sys, "executable", "") or "")
    if exe_dir:
        bases.append(exe_dir)
        bases.append(os.path.join(exe_dir, "_internal"))
    for base in bases:
        cand = os.path.join(base, rel)
        if os.path.isfile(cand):
            return cand
    if bases:
        return os.path.join(bases[0], rel)
    return os.path.join(here, rel)


class SileroVad(object):
    """一帧 512 样本进，一个 0~1 的语音概率出。内部带着 RNN state。"""

    def __init__(self, model_path):
        import numpy as np
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self._np = np
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def prob(self, chunk):
        np = self._np
        x = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != WINDOW:
            raise ValueError("vad window must be %d samples" % WINDOW)
        inp = np.concatenate([self._context, x], axis=1)
        out, state = self._sess.run(None, {
            "input": inp,
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        })
        self._state = state
        self._context = inp[:, -CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


class SileroScorer(object):
    """给 StreamingSegmenter 用的可调用打分器。"""

    def __init__(self, vad):
        self.vad = vad

    def __call__(self, chunk):
        return self.vad.prob(chunk)

    def reset(self):
        self.vad.reset()


def try_load_vad(model_path=None):
    """返回 (引擎或 None, 提示)。提示非空时调用方应改回按住说话。"""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception as exc:
        log.warning("VAD 依赖不可用: %s", exc)
        return None, FALLBACK_HINT
    path = model_path or default_model_path()
    try:
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(path or "silero_vad.onnx")
        vad = SileroVad(path)
        import numpy as np
        vad.prob(np.zeros(WINDOW, dtype=np.float32))
        vad.reset()
        return vad, ""
    except Exception as exc:
        log.warning("VAD 模型加载失败: %s", exc)
        return None, FALLBACK_HINT


def _ms_to_samples(ms, sr):
    return int(round(float(ms) * sr / 1000.0))


class StreamingSegmenter(object):
    """把 16kHz 单声道流切成一句一句。

    普通聆听：概率过 threshold 持续 min_speech_ms 才算开口，
    再连续静音 min_silence_ms 才收口。
    朗读中（set_playback(True)）：阈值抬到 barge_threshold，
    开头 barge_onset_guard_ms 不判，并且要连续 barge_confirm_ms 才算用户插嘴。
    """

    def __init__(self, scorer, cfg=None):
        self.scorer = scorer
        self.cfg = cfg or VadConfig()
        self.sr = SAMPLE_RATE
        self.window = WINDOW
        self._min_speech = _ms_to_samples(self.cfg.min_speech_ms, self.sr)
        self._min_silence = _ms_to_samples(self.cfg.min_silence_ms, self.sr)
        self._barge_confirm = _ms_to_samples(self.cfg.barge_confirm_ms, self.sr)
        self._barge_guard = _ms_to_samples(self.cfg.barge_onset_guard_ms, self.sr)
        self._pad_windows = int(self.cfg.speech_pad_ms * self.sr / 1000.0 / self.window)
        self._threshold = float(self.cfg.threshold)
        self._barge_threshold = float(self.cfg.barge_threshold)
        self._neg = max(self._threshold - 0.15, 0.01)
        self._pending = None
        self.reset()

    def reset(self):
        import numpy as np
        self._pending = np.zeros(0, dtype=np.float32)
        self._playback = False
        self._playback_age = 0
        self._triggered = False
        self._provisional = 0
        self._silence = 0
        self._utt = []
        self._pre = []
        self._sample_pos = 0
        reset = getattr(self.scorer, "reset", None)
        if reset:
            reset()

    def set_playback(self, on):
        """朗读开始时抬高判定；关掉时保留正在录的那句。"""
        on = bool(on)
        self._playback = on
        self._playback_age = 0
        if on:
            self._triggered = False
            self._provisional = 0
            self._silence = 0
            self._utt = []
            reset = getattr(self.scorer, "reset", None)
            if reset:
                reset()

    def feed(self, pcm):
        import numpy as np
        arr = np.asarray(pcm)
        if arr.size == 0:
            return []
        if arr.ndim > 1:
            arr = arr[:, 0]
        if np.issubdtype(arr.dtype, np.integer):
            arr = arr.astype(np.float32) / 32768.0
        else:
            arr = arr.astype(np.float32)
        if self._pending is None or len(self._pending) == 0:
            self._pending = arr
        else:
            self._pending = np.concatenate([self._pending, arr])
        events = []
        while len(self._pending) >= self.window:
            chunk = self._pending[:self.window]
            self._pending = self._pending[self.window:]
            events.extend(self._on_window(chunk))
        return events

    def _push_pre(self, chunk):
        if self._pad_windows <= 0:
            return
        self._pre.append(chunk)
        if len(self._pre) > self._pad_windows:
            self._pre = self._pre[-self._pad_windows:]

    def _pcm_from_utt(self):
        import numpy as np
        if not self._utt:
            return np.zeros(0, dtype=np.int16)
        audio = np.concatenate(self._utt)
        return np.clip(np.rint(audio * 32768.0), -32768, 32767).astype(np.int16)

    def _on_window(self, chunk):
        prob = float(self.scorer(chunk))
        self._sample_pos += self.window
        if self._playback:
            return self._on_playback_window(chunk, prob)
        return self._on_listen_window(chunk, prob)

    def _on_playback_window(self, chunk, prob):
        start_age = self._playback_age
        self._playback_age += self.window
        if start_age < self._barge_guard:
            return []
        # 朗读中必须连续高于插嘴阈值，一声回声不够。
        thr = self._barge_threshold
        if prob >= thr:
            if self._provisional == 0:
                self._utt = []
            self._provisional += self.window
            self._utt.append(chunk)
            if self._provisional >= self._barge_confirm:
                self._playback = False
                self._triggered = True
                self._silence = 0
                self._provisional = 0
                return [VadEvent("barge_in")]
            return []
        self._provisional = 0
        self._utt = []
        return []

    def _on_listen_window(self, chunk, prob):
        if not self._triggered:
            if prob >= self._threshold:
                if self._provisional == 0:
                    self._utt = list(self._pre)
                self._provisional += self.window
                self._utt.append(chunk)
                if self._provisional >= self._min_speech:
                    self._triggered = True
                    self._silence = 0
                    return [VadEvent("speech_start")]
                return []
            self._provisional = 0
            self._utt = []
            self._push_pre(chunk)
            return []
        if prob >= self._neg:
            self._silence = 0
            self._utt.append(chunk)
            return []
        self._silence += self.window
        self._utt.append(chunk)
        if self._silence >= self._min_silence:
            pcm = self._pcm_from_utt()
            end_sample = self._sample_pos
            self._triggered = False
            self._provisional = 0
            self._silence = 0
            self._utt = []
            self._pre = []
            if pcm is None or len(pcm) == 0:
                return []
            return [VadEvent("utterance", pcm=pcm, end_sample=end_sample)]
        return []
