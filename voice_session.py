# -*- coding: utf-8 -*-
"""语音对话会话：麦克风帧 → 端点 → 识别用 wav；回复按句朗读，可被插嘴打断。

Qt 只留在这一层。状态机在 voice_turn，端点在 voice_vad，测试不经过这里。
"""
import logging
import os
import tempfile
import wave

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

from voice_turn import VoiceTurnMachine, split_sentences
from voice_vad import (FALLBACK_HINT, SileroScorer, StreamingSegmenter,
                       VadConfig, try_load_vad)

log = logging.getLogger("dsdesktop.voice")


class _Port(object):
    """让状态机调用会话上的播放方法，避免和 QObject.stop 撞名。"""

    def __init__(self, session):
        self.session = session

    def play_sentence(self, text, generation):
        self.session.play_sentence(text, generation)

    def halt_playback(self):
        self.session.halt_playback()


class VoiceConversation(QObject):
    sig_utterance = Signal(str)
    sig_status = Signal(str)
    sig_hint = Signal(str)

    def __init__(self, recorder, audio_player, cfg, parent=None,
                 hold_thread=None, on_tts_error=None, speech_enabled=None):
        super().__init__(parent)
        self.recorder = recorder
        self.audio_player = audio_player
        self.cfg = cfg or {}
        self._hold_thread = hold_thread
        self._on_tts_error = on_tts_error
        self._speech_enabled = speech_enabled
        self.active = False
        self.vad_ready = False
        self._hinted = False
        self._mic_hinted = False
        self._machine = None
        self._port = _Port(self)
        self._play_gen = None
        self._suppress_end = False
        self._wav_seq = 0
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._poll)
        try:
            self.audio_player.mediaStatusChanged.connect(self._on_media)
        except Exception:
            pass

    def prepare(self):
        if self._machine is not None or self._hinted:
            return self.vad_ready
        vad, hint = try_load_vad()
        if vad is None:
            self.vad_ready = False
            self._hinted = True
            self.sig_hint.emit(hint or FALLBACK_HINT)
            return False
        seg = StreamingSegmenter(SileroScorer(vad), VadConfig.from_cfg(self.cfg))
        self._machine = VoiceTurnMachine(seg, self._port)
        self.vad_ready = True
        return True

    def start(self, mic):
        if not self.vad_ready or self._machine is None:
            return False
        try:
            opened = self.recorder.start(mic=mic)
        except Exception as exc:
            log.warning("语音对话打开麦克风失败: %s", exc)
            opened = None
        if not opened or getattr(self.recorder, "_stream", None) is None:
            try:
                self.recorder.close()
            except Exception:
                pass
            if not self._mic_hinted:
                self._mic_hinted = True
                self.sig_hint.emit("麦克风没法边听边判断，已改回按住说话。")
            return False
        self._machine.start()
        self.active = True
        self._timer.start()
        self.sig_status.emit("语音对话中，说完会自动发送")
        return True

    def shutdown(self):
        self._timer.stop()
        self.active = False
        self._suppress_end = True
        if self._machine is not None:
            self._machine.halt()
        try:
            self.recorder.close()
        except Exception:
            pass

    def issue_token(self):
        if self._machine is None:
            return 0
        return self._machine.issue_token()

    def speak(self, text, token):
        if not self.active or self._machine is None:
            return False
        self._machine.enqueue(split_sentences(text), token)
        return True

    def play_sentence(self, sentence, generation):
        if self._speech_enabled is not None and not self._speech_enabled():
            if self._machine is not None:
                self._machine.drop_unsaid()
            return
        self._suppress_end = False
        self._play_gen = generation
        tts = dict(self.cfg.get("tts") or {})
        tts.setdefault("voice", "zh-CN-XiaoxiaoNeural")
        tts.setdefault("rate", "+0%")
        tts.setdefault("volume", "+0%")
        from ui_workers import _TTSWorker
        worker = _TTSWorker(sentence, tts)
        if self._hold_thread is not None:
            self._hold_thread(worker)
        worker.sig_mp3.connect(lambda path, g=generation: self._play_mp3(path, g))
        worker.sig_error.connect(lambda msg, g=generation: self._tts_failed(msg, g))
        worker.start()

    def halt_playback(self):
        self._suppress_end = True
        try:
            self.audio_player.stop()
        except Exception:
            pass

    def _play_mp3(self, path, generation):
        if self._machine is None or generation != self._machine.generation:
            return
        if not self.active:
            return
        if not path or not os.path.isfile(path):
            self._machine.playback_finished(generation)
            return
        self._suppress_end = False
        self._play_gen = generation
        self.audio_player.setSource(QUrl.fromLocalFile(path))
        self.audio_player.play()

    def _tts_failed(self, msg, generation):
        if self._on_tts_error is not None:
            try:
                self._on_tts_error(msg)
            except Exception:
                log.warning("朗读失败回调异常", exc_info=True)
        if self._machine is None or generation != self._machine.generation:
            return
        if self._speech_enabled is not None and not self._speech_enabled():
            self._machine.drop_unsaid()
            return
        self._machine.playback_finished(generation)

    def _on_media(self, status):
        if self._suppress_end:
            return
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        gen = self._play_gen
        if gen is None or self._machine is None:
            return
        if gen != self._machine.generation:
            return
        self._play_gen = None
        self._machine.playback_finished(gen)

    def _poll(self):
        if not self.active or self._machine is None:
            return
        try:
            pcm = self.recorder.pull_new()
        except Exception as exc:
            log.warning("读取麦克风失败: %s", exc)
            self._fail_runtime()
            return
        if pcm is None or len(pcm) == 0:
            return
        try:
            events = self._machine.feed_pcm(pcm)
        except Exception as exc:
            log.warning("端点检测失败: %s", exc)
            self._fail_runtime()
            return
        for ev in events:
            kind = ev.get("kind")
            if kind == "speech_start":
                self.sig_status.emit("正在听…")
            elif kind == "barge_in":
                self.sig_status.emit("已打断，正在听你说")
            elif kind == "utterance":
                path = self._write_wav(ev.get("pcm"))
                if path:
                    self.sig_status.emit("语音识别中…")
                    self.sig_utterance.emit(path)

    def _fail_runtime(self):
        self.vad_ready = False
        self.shutdown()
        if not self._hinted:
            self._hinted = True
            self.sig_hint.emit(FALLBACK_HINT)

    def _write_wav(self, pcm):
        if pcm is None or len(pcm) == 0:
            return ""
        self._wav_seq += 1
        path = os.path.join(
            tempfile.gettempdir(), "xiaochou_vad_%s.wav" % self._wav_seq)
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm.tobytes())
        except Exception as exc:
            log.warning("写端点 wav 失败: %s", exc)
            return ""
        return path
