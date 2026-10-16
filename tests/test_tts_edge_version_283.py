# -*- coding: utf-8 -*-
"""v4.283.1：edge-tts 403 事故的回归判据。

事故（2026-10-16，大哥与小臭对话抓包）：
edge-tts 7.2.7 的 Sec-MS-GEC-Version（Edge 143）被微软反爬策略拒绝，
wss 握手 403 → 应用内点「语音」无声；且 403 走 tts_failure_is_quota
只记 WARNING，查 ERROR 日志查不到（排障极难）。
7.3.1 起上游跟进修复（实测合成恢复，mp3 38KB）。

判据方向（fail-closed）：
- requirements.txt 必须显式锁 edge-tts 且版本 >= 7.3.1（防漏列/防回退）；
- voice.py 合成入口与 403 识别器必须原位在位（防清理时误伤）；
- 本机/CI 环境的 edge_tts 实际版本 >= 7.3.1（防只改清单不换环境）；
- onnxruntime 已随 voice_vad 退役（v4.282.2 漏清，v4.283.1 收尾）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("PASS: %s" % name)
    else:
        _f += 1
        print("FAIL: %s | %s" % (name, detail))


# ---------- G1: requirements.txt 锁 edge-tts >= 7.3.1 ----------
req_path = os.path.join(ROOT, "requirements.txt")
req = open(req_path, encoding="utf-8").read()
m = re.search(r"^edge-tts==(\d+)\.(\d+)\.(\d+)\s*$", req, re.M)
check("G1 requirements 显式锁 edge-tts", m is not None,
      "requirements.txt 缺 edge-tts==x.y.z（voice.py TTS 主链漏列）")
if m:
    ver = tuple(int(x) for x in m.groups())
    check("G1b 锁定的版本 >= 7.3.1（403 修复线）", ver >= (7, 3, 1),
          "edge-tts==%s 低于 403 修复线" % ".".join(m.groups()))

# ---------- G2: onnxruntime 已退役（v4.282.2 漏清收尾） ----------
check("G2 onnxruntime 不在 requirements（voice_vad 已删）",
      not re.search(r"^onnxruntime", req, re.M),
      "onnxruntime 仍在 requirements，而 voice_vad 已于 v4.282.2 删除")

# ---------- G3: voice.py 合成入口在位 ----------
voice = open(os.path.join(ROOT, "voice.py"), encoding="utf-8").read()
check("G3 synthesize 入口在位", "def synthesize(" in voice, "voice.py 丢 synthesize")
check("G3b _tts_async 走 edge_tts.Communicate",
      "edge_tts.Communicate(" in voice, "_tts_async 不再走 edge_tts")
check("G3c 延迟 import 注释在位（冷启动保护）",
      "延迟 import" in voice, "voice.py 丢延迟 import 注释（冷启动回归风险）")

# ---------- G4: 403 识别器在位（排障可循，WARNING 有记录） ----------
check("G4 tts_failure_is_quota 在位",
      "def tts_failure_is_quota(" in voice, "403 识别器丢失 → 排障线索断")
try:
    import voice as _v
    check("G4b 403 异常被判为 quota 类（只记 WARNING，可预期）",
          _v.tts_failure_is_quota(
              Exception("WSServerHandshakeError 403, message='Invalid response status'")),
          "403 字符串未被识别")
    check("G4c 普通异常不被误判为 quota",
          not _v.tts_failure_is_quota(Exception("ffmpeg 退出码 1")),
          "普通异常被误判 quota → 真错被压成 WARNING")
except ImportError as e:
    check("G4 import voice", False, str(e))

# ---------- G5: 本机环境实际版本 >= 7.3.1（防只改清单不换环境） ----------
try:
    import edge_tts
    parts = []
    for x in getattr(edge_tts, "__version__", "0").split(".")[:3]:
        parts.append(int(re.sub(r"\D", "", x) or 0))
    while len(parts) < 3:
        parts.append(0)
    check("G5 本机 edge_tts 实际版本 >= 7.3.1", tuple(parts) >= (7, 3, 1),
          "edge_tts %s < 7.3.1（exe 会打进旧版，照样 403）" % edge_tts.__version__)
except ImportError:
    check("G5 本机装了 edge_tts", False, "edge_tts 未安装（requirements 漏列的后果）")

print("TTS_EDGE_VERSION PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
