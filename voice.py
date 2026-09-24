# -*- coding: utf-8 -*-
"""
小臭玩AI · 实时语音模块（移植自数字分身 twin.py）
  - ASR   : 麦克风语音 -> 中文文本（硅基流动 SenseVoiceSmall，需硅基流动 key）
  - TTS   : 文本 -> 中文语音（edge-tts，免 key）
  - Record: 麦克风录音（ffmpeg dshow，免 Python 音频包 / 免 portaudio）
只依赖标准库 + edge_tts + 系统已装的 ffmpeg。与数字分身共用同一套后端，保证「一样的效果」。
"""
import os
import re
import sys
import json
import asyncio
import subprocess
# v4.125 M-14：windowed 打包下调 ffmpeg 不闪黑窗
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
import tempfile
import urllib.request
import urllib.error

# edge_tts 是重库（含 aiohttp），v4.122.1 改为延迟 import：只在 TTS 合成时才加载，
# 避免冷启动 import voice 时连坐 aiohttp/edge_tts 拖慢启动（实测 ~0.5s）。
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ffmpeg：优先用本机 winget 安装路径（动态探测，不硬编码用户名），否则退回 PATH 上的 ffmpeg
def _find_winget_ffmpeg():
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ""
    import glob as _glob
    hits = _glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages",
                                   "Gyan.FFmpeg*", "ffmpeg-*", "bin", "ffmpeg.exe"))
    return sorted(hits)[-1] if hits else ""


FFMPEG = _find_winget_ffmpeg() or "ffmpeg"


# ===================== ASR（语音识别）=====================
def transcribe(wav_path, sf):
    """语音 -> 文本。sf 为 cfg["siliconflow"]: {base_url, asr_model, api_key}。"""
    url = sf["base_url"].rstrip("/") + "/audio/transcriptions"
    boundary = "----xiaochouboundary"
    CRLF = b"\r\n"
    with open(wav_path, "rb") as f:
        wavdata = f.read()
    body = (
        b"--" + boundary.encode() + CRLF
        + b'Content-Disposition: form-data; name="model"\r\n\r\n'
        + sf["asr_model"].encode() + CRLF
        + b"--" + boundary.encode() + CRLF
        + b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        + b"Content-Type: audio/wav\r\n\r\n"
        + wavdata + CRLF
        + b"--" + boundary.encode() + b"--\r\n"
    )
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + sf["api_key"])
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8", "ignore"))
            txt = resp.get("text") or resp.get("transcript") or ""
            txt = re.sub(r"<\|[^|]*\|>", "", txt)
            txt = re.sub(r"^\[[A-Za-z]+\]\s*", "", txt)
            return txt.strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"ASR失败 {e.code}: {detail}")


# ===================== TTS（语音合成）=====================
# 半角标点 -> 空格，避免 edge-tts 逐字念出来
_TTS_HALF = {ord(c): " " for c in "\"'`*_{}[]()#+-=|^~\\<>/:@!$%&?.;,"}
_TTS_REMOVE = re.compile(
    r"(?:\*\*|__|~~|``|#+\s*)"            # markdown 标记
    r"|\[[^\]]*\]\([^)]*\)"               # 链接 [x](y)
    r"|`[^`]*`"                           # 行内代码
    r"|[【】〖〗《》〈〉「」『』“”‘’]"         # 书名/引号类括号
    r"|[\(\)（）\[\]\{\}<>]"              # 半/全角括号
    r"|[·•–—…]"                           # 装饰符号
)


def sanitize_tts_text(text):
    """清掉 TTS 会当成字面念出来的符号（括号/列表符/markdown/半角标点）。"""
    t = text.translate(_TTS_HALF)
    t = _TTS_REMOVE.sub(' ', t)
    t = t.replace('、', '，').replace('/', '，').replace('\\', '，')
    t = re.sub(r'(?m)^\s*[-*•]\s+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _split_text(text, limit=800):
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for seg in re.split(r'(?<=[。！？.!?])', text):
        if len(buf) + len(seg) > limit and buf:
            chunks.append(buf)
            buf = seg
        else:
            buf += seg
    if buf:
        chunks.append(buf)
    return chunks or [text]


async def _tts_async(text, voice, out_path, rate, volume):
    import edge_tts  # 延迟 import：见模块顶部注释，TTS 合成时才加载
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await comm.save(out_path)


def _concat_audio(files, out_path):
    list_path = out_path + ".list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for fp in files:
            f.write(f"file '{fp}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    try:
        os.remove(list_path)
    except Exception:
        pass


def synthesize(text, tts, out_path=None):
    """文本 -> mp3 路径。tts 为 cfg["tts"]: {voice, rate, volume}。"""
    out_path = out_path or os.path.join(tempfile.gettempdir(), "xiaochou_tts.mp3")
    clean = sanitize_tts_text(text)
    text = clean if clean.strip() else text  # 清洗后空了就退回原文，避免无声
    chunks = _split_text(text)
    if len(chunks) == 1:
        asyncio.run(_tts_async(chunks[0], tts["voice"], out_path,
                               tts.get("rate", "+0%"), tts.get("volume", "+0%")))
        return out_path
    tmp = []
    for i, c in enumerate(chunks):
        tp = os.path.join(tempfile.gettempdir(), f"xiaochou_tts_{i}.mp3")
        asyncio.run(_tts_async(c, tts["voice"], tp,
                               tts.get("rate", "+0%"), tts.get("volume", "+0%")))
        tmp.append(tp)
    _concat_audio(tmp, out_path)
    for tp in tmp:
        if os.path.exists(tp):
            try:
                os.remove(tp)
            except Exception:
                pass
    return out_path


# ===================== 麦克风 / 录音 =====================
KNOWN_MIC = None

# sounddevice（WASAPI/CoreAudio，系统原生）优先，ffmpeg dshow 仅作回落。
# 这是数字分身 twin.py 验证可用的方案：不依赖 ffmpeg 子进程抢麦克风，
# 冻结 exe 里也不会出现「没有权限 / 调不动麦克风」的问题。
# —— 与分身一致的关键：录音后端用 sounddevice，而不是裸 ffmpeg dshow。

def _sounddevice_library_path():
    """冻结 exe（onedir）里 portaudio DLL 被我们手动放进 sounddevice_data/，
    需通过该环境变量告诉 sounddevice 去哪找。开发态（系统 Python）无需设置。
    onedir 下 datas 可能落在根目录或 _internal 下，两种都试。"""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        dll = "libportaudio64bit.dll"
        base = os.path.dirname(sys.executable)
        cands = [
            os.path.join(base, "_internal", "sounddevice_data", dll),
            os.path.join(base, "sounddevice_data", dll),
        ]
        if hasattr(sys, "_MEIPASS"):
            cands.append(os.path.join(sys._MEIPASS, "sounddevice_data", dll))
        for c in cands:
            if os.path.exists(c):
                return c
    return None


# ============ sounddevice / numpy 惰性加载（v4.152：启动提速）============
# 原先这里是在**模块级**直接 `import sounddevice` + `import numpy`，
# 而 main → ui → voice 是启动必经链 —— 实测冷启动为它俩付了 **1877 ms**
# （numpy 1695 + sounddevice 134 + asyncio 43），占整个启动 import 时长的 64%。
# 但它们只在「检测麦克风 / 录音」时才真正需要 → 改为**首次使用时才 import**。
#
# 对外接口不变：外部读 `voice.HAVE_SD` / `voice._sd` / `voice._np` 仍照旧可用
# （走下面的模块级 __getattr__，会自动触发加载）；模块内部统一改用 `_have_sd()`。
_sd = None
_np = None
_HAVE_SD = None          # None=尚未探测；True/False=探测结果（只真正尝试一次）


def _load_sd():
    """首次需要时加载 sounddevice + numpy，返回是否可用（只真正尝试一次）。"""
    global _sd, _np, _HAVE_SD
    if _HAVE_SD is not None:
        return _HAVE_SD
    try:
        _lib = _sounddevice_library_path()
        if _lib:
            os.environ["SOUNDDEVICE_LIBRARY_PATH"] = _lib
        import sounddevice as _s
        import numpy as _n
        _sd, _np, _HAVE_SD = _s, _n, True
    except Exception:
        _sd, _np, _HAVE_SD = None, None, False
    return _HAVE_SD


def _have_sd():
    """sounddevice 是否可用（惰性探测：首次调用才会真正加载库）。"""
    return _load_sd()


def __getattr__(name):
    """让**外部**沿用 `voice.HAVE_SD` / `voice._sd` / `voice._np` 的读取方式。

    PEP 562 模块级 __getattr__：只在属性查不到时触发。
    本模块**内部**读同名全局变量不会走这里，所以模块内一律用 `_have_sd()` 显式触发。
    """
    if name == "HAVE_SD":
        return _have_sd()
    if name in ("_sd", "_np"):
        _load_sd()
        return globals()[name]
    raise AttributeError("module %r has no attribute %r" % (__name__, name))

import re as _re
import wave as _wave


def _sd_mics():
    """用 sounddevice 列出所有输入设备名。"""
    if not _load_sd():          # v4.152：惰性加载后才可用，否则 _sd 仍为 None
        return []
    try:
        devs = _sd.query_devices()
        return [d["name"] for d in devs if d.get("max_input_channels", 0) > 0]
    except Exception:
        return []


def detect_mics(diag=None):
    """返回所有可用录音设备名（sounddevice 优先，ffmpeg dshow 回落）。
    diag: 可选 dict，回填诊断信息（ffmpeg 路径/返回码/输出长度/异常/用的后端）。"""
    info = {"ffmpeg": FFMPEG, "ffmpeg_exists": os.path.exists(FFMPEG),
            "backend": "sounddevice" if _have_sd() else "ffmpeg-dshow",
            "returncode": None, "stderr_len": 0, "stdout_len": 0,
            "raw_head": "", "exception": None}
    if diag is not None:
        diag.update(info)
    try:
        # 优先 sounddevice：直接读 WASAPI 输入设备，无需 ffmpeg 子进程，最稳。
        if _have_sd():
            names = _sd_mics()
            if names:
                info["backend"] = "sounddevice"
                if diag is not None:
                    diag.update(info)
                return names
        # 回落 ffmpeg dshow（兼容旧机器 / 无 sounddevice 时）
        out = subprocess.run(
            [FFMPEG, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
            creationflags=_NO_WINDOW,
        )
        info["returncode"] = out.returncode
        info["stderr_len"] = len(out.stderr or b"")
        info["stdout_len"] = len(out.stdout or b"")
        info["raw_head"] = ((out.stderr or b"") + (out.stdout or b""))[:600].decode("utf-8", "ignore")
        info["backend"] = "ffmpeg-dshow"
        if diag is not None:
            diag.update(info)
        # 字节捕获 + 手动 utf-8 解码(errors=ignore)：绕开冻结窗口程序无控制台时的 locale 编码坑。
        raw = (out.stderr or b"") + b"\n" + (out.stdout or b"")
        txt = raw.decode("utf-8", "ignore")
        names = []
        in_audio = False
        for line in txt.splitlines():
            low = line.lower()
            if "directshow" in low or "[dshow" in low:
                if "audio devices" in low:
                    in_audio = True
                elif "video devices" in low:
                    in_audio = False
            if in_audio and "alternative name" not in low:
                m = _re.search(r'"([^"]+)"', line)
                if m:
                    names.append(m.group(1))
                    continue
            m = _re.search(r'"([^"]+)"\s*\(audio\)', line)
            if m:
                names.append(m.group(1))
        seen, dedup = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                dedup.append(n)
        return dedup
    except Exception as e:
        info["exception"] = f"{type(e).__name__}: {e}"
        if diag is not None:
            diag.update(info)
        return []


def detect_mic():
    global KNOWN_MIC
    if KNOWN_MIC:
        return KNOWN_MIC
    names = detect_mics()
    KNOWN_MIC = names[0] if names else "默认麦克风"
    return KNOWN_MIC


class Recorder:
    """录音器：sounddevice 优先（WASAPI，原生、稳定），ffmpeg dshow 回落。
    start() 返回输出 wav 路径（录音中先写临时文件，stop() 收尾）。
    与数字分身 twin.Recorder 同一套实现。"""

    def __init__(self, rate=16000, channels=1):
        self.rate = rate
        self.channels = channels
        self.error = None
        self._frames = []
        self._stream = None
        self._proc = None
        self._raw_path = None
        self._out_wav = None
        self._cap_rate = rate          # 实际采集采样率（WASAPI 常只支持 48000）
        # v4.152：backend 改为**惰性 property**（见下）。
        # 此前这里是 `self.backend = "sounddevice" if _have_sd() else ...` —— 直接
        # 调 _have_sd() 会在**构造 Recorder 时**就加载 sounddevice+numpy（实测 ~200ms）。
        # 而 ChatWindow.__init__ 启动路径上就 new 了一个 Recorder，等于把上一轮
        # 「numpy/sounddevice 惰性加载」的收益在启动路径上原样吐了回去。
        self._backend = None

    @property
    def backend(self):
        """录音后端名（"sounddevice" / "ffmpeg-dshow"）。

        首次读取时才探测 —— 探测会加载 sounddevice+numpy（数百 ms），
        绝不能在构造 Recorder 时付出。构造只做纯赋值。
        """
        if self._backend is None:
            self._backend = "sounddevice" if _have_sd() else "ffmpeg-dshow"
        return self._backend

    def _sd_device(self, mic):
        if not mic or mic in ("默认麦克风", "未检测到麦克风"):
            return None
        try:
            # 找 WASAPI 后端索引：优先用原生 WASAPI（低延迟、与数字分身一致）
            wasapi = None
            try:
                for i, a in enumerate(_sd.query_hostapis()):
                    if "wasapi" in a.get("name", "").lower():
                        wasapi = i
                        break
            except Exception:
                pass
            devs = _sd.query_devices()
            matches = [d for d in devs
                       if d.get("max_input_channels", 0) > 0
                       and mic.lower() in d["name"].lower()]
            if not matches:
                return None
            # 优先同名设备里的 WASAPI 变体；否则取第一个匹配。
            # 返回整数索引（而非设备名）：同名设备跨多个 host API 时，
            # 传名字给 InputStream 会抛 "Multiple input devices found"，传索引则无歧义。
            if wasapi is not None:
                for d in matches:
                    if d.get("hostapi") == wasapi:
                        return int(d["index"])
            return int(matches[0]["index"])
        except Exception:
            return None

    def start(self, mic=None, out_wav=None):
        self.error = None
        self._out_wav = out_wav or os.path.join(tempfile.gettempdir(), "xiaochou_rec.wav")
        if _have_sd():
            try:
                self._frames = []
                dev = self._sd_device(mic)
                # WASAPI 设备常只支持固定采样率（如 48000），强制 16000 会 -9997。
                # 按设备原生采样率采集，stop() 再重采样到目标 16000。
                cap_rate = self.rate
                if dev is not None:
                    try:
                        di = _sd.query_devices(dev)
                        ds = di.get("default_samplerate")
                        if ds:
                            cap_rate = int(round(ds))
                    except Exception:
                        pass
                self._cap_rate = cap_rate
                self._stream = _sd.InputStream(
                    samplerate=cap_rate, channels=self.channels,
                    dtype="int16", device=dev, callback=self._cb)
                self._stream.start()
                return self._out_wav
            except Exception as e:
                self.error = f"sounddevice 启动失败: {e}，尝试 ffmpeg"
        return self._start_ffmpeg(mic or detect_mic(), self._out_wav)

    def _cb(self, indata, frames, time_info, status):
        self._frames.append(indata.copy())

    def _start_ffmpeg(self, mic, out_wav):
        self._out_wav = out_wav
        self._raw_path = os.path.join(tempfile.gettempdir(), "xiaochou_rec.raw")
        try:
            self._proc = subprocess.Popen(
                [FFMPEG, "-y", "-f", "dshow", "-i", f"audio={mic}",
                 "-ac", str(self.channels), "-ar", str(self.rate),
                 "-f", "s16le", self._raw_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW)
            return self._raw_path
        except Exception as e:
            self.error = f"ffmpeg 启动失败: {e}"
            return None

    def stop(self, out_wav=None):
        out_wav = out_wav or self._out_wav or os.path.join(tempfile.gettempdir(), "xiaochou_rec.wav")
        if _have_sd() and self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            if self._frames:
                try:
                    data = _np.concatenate(self._frames, axis=0)
                    if self._cap_rate != self.rate:
                        # 采集率与目标率不一致：先落临时 wav，再 ffmpeg 重采样到 16000。
                        tmp = out_wav + ".cap.wav"
                        with _wave.open(tmp, "wb") as wf:
                            wf.setnchannels(self.channels)
                            wf.setsampwidth(2)
                            wf.setframerate(self._cap_rate)
                            wf.writeframes(data.tobytes())
                        subprocess.run(
                            [FFMPEG, "-y", "-i", tmp, "-ar", str(self.rate),
                             "-ac", str(self.channels), out_wav],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                            creationflags=_NO_WINDOW)
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                        return out_wav
                    with _wave.open(out_wav, "wb") as wf:
                        wf.setnchannels(self.channels)
                        wf.setsampwidth(2)
                        wf.setframerate(self.rate)
                        wf.writeframes(data.tobytes())
                    return out_wav
                except Exception as e:
                    self.error = f"写 wav 失败: {e}"
                    return None
            self.error = self.error or "没录到声音（麦克风未输入或录音太短）"
            return None
        # ffmpeg 回落
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
        raw = self._raw_path
        if raw and os.path.exists(raw) and os.path.getsize(raw) > 44:
            try:
                subprocess.run(
                    [FFMPEG, "-y", "-f", "s16le", "-ar", str(self.rate),
                     "-ac", str(self.channels), "-i", raw, out_wav],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                    creationflags=_NO_WINDOW)
                os.remove(raw)
                return out_wav
            except Exception as e:
                self.error = f"录音转换失败: {e}"
                return None
        self.error = self.error or "没录到声音（麦克风设备不可用或未授权）"
        return None
