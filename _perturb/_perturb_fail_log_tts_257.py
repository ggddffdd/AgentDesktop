# -*- coding: utf-8 -*-
"""v4.257.0 扰动脚本：失败日志真落库（P0-5）+ TTS 失败不再二次抛错（P0-6）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_fail_log_tts_257.py，
确认判据必红（改坏必红、零哑弹）。SKIP/MISS = 哑弹，判据白写。

V1  _log 不接 exc_info     → 退回 TypeError，工具失败日志重新全丢（病根复活）
V2  error 不传 exc_info    → 签名接住了但不往下传（假修：接得住却不生效）
V3  同族只修 error         → warning/critical 重新踩同一个坑（F1-6 专盯）
V4  异常信息不并入         → 接住了也记了，但只剩一句「执行异常」（无信息增量）
V5  模块级 logger 撤掉     → TTS 失败路径 NameError 复活（P0-6 病根）
V6  sig_error 撤掉         → 不再二次抛错，但用户端仍然静默无声（换个方式丢信息）
V7  成功路径被误伤         → 加了错误通道却把正常 emit 搞坏（反向：不回归）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
SL = os.path.join(ROOT, "structured_logger.py")
UW = os.path.join(ROOT, "ui_workers.py")
JUDGE = os.path.join(ROOT, "tests", "test_fail_log_tts_257.py")

CASES = [
    # ---- P0-5 ----
    ("V1 _log 不接 exc_info", SL,
     "    def _log(self, level, message, module=None, extra=None, session_id=None,\n"
     "             exc_info=False):",
     "    def _log(self, level, message, module=None, extra=None, session_id=None):  # 扰动：不接"),
    ("V2 error 不往下传 exc_info", SL,
     "        self._log(\"ERROR\", message, module, extra, session_id, exc_info)",
     "        self._log(\"ERROR\", message, module, extra, session_id)  # 扰动：不传"),
    ("V3 同族只修 error", SL,
     "        self._log(\"WARNING\", message, module, extra, session_id, exc_info)",
     "        self._log(\"WARNING\", message, module, extra, session_id)  # 扰动：warning 漏修"),
    ("V4 异常信息不并入", SL,
     "        if exc_info:\n"
     "            message, extra = self._append_exc(message, exc_info, extra)",
     "        if False:  # 扰动：接住但不并入\n"
     "            message, extra = self._append_exc(message, exc_info, extra)"),
    # ---- P0-6 ----
    ("V5 模块级 logger 撤掉", UW,
     "log = logging.getLogger(\"dsdesktop.workers\")",
     "log = None  # 扰动：撤掉模块级 logger"),
    ("V6 sig_error 撤掉", UW,
     "    sig_error = Signal(str)\n"
     "\n"
     "    def __init__(self, text, tts):\n"
     "        super().__init__()\n"
     "        self.text = text\n"
     "        self.tts = tts\n"
     "\n"
     "    def run(self):\n"
     "        try:\n"
     "            mp3 = voice_mod.synthesize(self.text, self.tts)",
     "    sig_mp3_marker = Signal(str)  # 扰动：sig_error 撤掉\n"
     "\n"
     "    def __init__(self, text, tts):\n"
     "        super().__init__()\n"
     "        self.text = text\n"
     "        self.tts = tts\n"
     "\n"
     "    def run(self):\n"
     "        try:\n"
     "            mp3 = voice_mod.synthesize(self.text, self.tts)"),
    ("V7 成功路径被误伤", UW,
     "            mp3 = voice_mod.synthesize(self.text, self.tts)\n"
     "            self.sig_mp3.emit(mp3)",
     "            mp3 = voice_mod.synthesize(self.text, self.tts)\n"
     "            self.sig_mp3.emit(mp3)\n"
     "            raise RuntimeError('扰动：成功路径被误伤')"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [SL, UW]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            assert cnt == 1, "old 串在 %s 出现 %d 次，非唯一" % (fp, cnt)
            mutated = src.replace(old, new, 1)
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(mutated)
            try:
                rc, out = run_judge()
                failed = (rc != 0) and ("FAIL" in out)
            except Exception as e:  # noqa: BLE001
                failed, out = False, "judge 崩了: %r" % e
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
