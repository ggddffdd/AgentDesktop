# -*- coding: utf-8 -*-
"""语音对话的轮次状态机。

不碰麦克风、不碰播放器实现。播放器只要两个方法：
  play_sentence(text, generation)
  halt_playback()

朗读过程中用户开口：立刻 halt，丢掉还没念的句子，并且旧 generation
的「这句播完了」一律忽略，不会把剩下的补完。
"""
import re


def split_sentences(text):
    """按中文句号、问号、叹号和换行切开。没有标点就整段一句。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])|\n+", text)
    out = []
    for part in parts:
        piece = part.strip()
        if piece:
            out.append(piece)
    return out


class VoiceTurnMachine(object):
    def __init__(self, segmenter, player):
        self.segmenter = segmenter
        self.player = player
        self.generation = 0
        self.utterance_epoch = 0
        self.queue = []
        self._current = None
        self.phase = "idle"   # idle / listening / capturing / awaiting / speaking

    def start(self):
        self.phase = "listening"
        self.segmenter.reset()

    def halt(self):
        """用户关掉语音对话。未念完的句子作废。"""
        self.queue.clear()
        self._current = None
        self.generation += 1
        self.utterance_epoch += 1
        self.phase = "idle"
        self.segmenter.set_playback(False)
        self.player.halt_playback()

    def issue_token(self):
        """界面收下这一句、准备去识别时调用。返回值要原样交回 enqueue。"""
        self.utterance_epoch += 1
        return self.utterance_epoch

    def feed_pcm(self, pcm):
        out = []
        for ev in self.segmenter.feed(pcm):
            if ev.kind == "barge_in":
                out.append(self._on_barge())
            elif ev.kind == "speech_start":
                self.phase = "capturing"
                out.append({"kind": "speech_start"})
            elif ev.kind == "utterance":
                self.phase = "awaiting"
                out.append({
                    "kind": "utterance",
                    "pcm": ev.pcm,
                    "end_sample": ev.end_sample,
                })
        return out

    def enqueue(self, sentences, token):
        """把回复排进去。token 对不上当前轮就整段丢掉，不播。"""
        if token is None:
            token = self.utterance_epoch
        cleaned = []
        for sentence in sentences or []:
            if isinstance(sentence, str) and sentence.strip():
                cleaned.append(sentence.strip())
        if token != self.utterance_epoch:
            return [{"kind": "stale_reply", "dropped": cleaned}]
        if self.phase == "speaking":
            self.generation += 1
            self._current = None
            self.player.halt_playback()
        self.queue = list(cleaned)
        self._pump()
        return [{"kind": "enqueued", "n": len(cleaned)}]

    def playback_finished(self, generation):
        """自然播完一句。打断之后的迟到回调进不来。"""
        if generation != self.generation or self.phase != "speaking":
            return [{"kind": "stale"}]
        self._current = None
        self._pump()
        return [{"kind": "advanced"}]

    def drop_unsaid(self):
        """朗读失败时丢掉还没开口的句子，不当成用户插嘴。"""
        self.queue.clear()
        self._current = None
        self.generation += 1
        self.phase = "listening"
        self.segmenter.set_playback(False)
        self.player.halt_playback()

    def _pump(self):
        if not self.queue:
            self._current = None
            self.phase = "listening"
            self.segmenter.set_playback(False)
            return
        sentence = self.queue.pop(0)
        self._current = sentence
        self.generation += 1
        self.phase = "speaking"
        self.segmenter.set_playback(True)
        self.player.play_sentence(sentence, self.generation)

    def _on_barge(self):
        dropped = []
        if self._current:
            dropped.append(self._current)
            self._current = None
        dropped.extend(self.queue)
        self.queue.clear()
        self.utterance_epoch += 1
        self.generation += 1
        self.phase = "capturing"
        # 端点器已经在录新的一句，这里不要 set_playback(False)，那会把刚录的清掉。
        self.player.halt_playback()
        return {"kind": "barge_in", "dropped": dropped}
