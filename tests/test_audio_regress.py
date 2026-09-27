#!/usr/bin/env python3
# v4.175.0：从仓库根目录搬入 tests/ —— 统一入口以 `python tests/xxx.py` 运行，
# 此时 sys.path[0] 是 tests/，必须显式把仓库根加回来，否则 import ui 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# -*- coding: utf-8 -*-
"""导演台「成片无声」回归测试。

覆盖：
  1) 复现根因：非 utf-8 编码（模拟 exe 的 gbk locale）下 subprocess 读取线程抛
     UnicodeDecodeError，stderr 变空 → 旧写法误判"无音轨"。
  2) 新 _run_ff（bytes + 显式 utf-8）不受影响。
  3) 新 _probe_has_audio 在同样条件下仍判定为有音轨（乐观策略）。
  4) 完整 _merge 跑真实分镜 → 成片必须有声（max_volume > -60 dB）。
  5) 兜底路径：片段无音轨时能降级出片而不崩。
"""
import os, re, shutil, subprocess, tempfile, types

FF = shutil.which("ffmpeg")
import video_pipeline as vp

SHOTS = [
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124042.mp4",
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124205.mp4",
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124326.mp4",
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124446.mp4",
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124718.mp4",
    r"C:\Users\xyb\Documents\小臭玩AI\产物\视频\video_20260831124907.mp4",
]
SHOT = SHOTS[0]
FADE = "-hide_banner"
fails = []


def check(tag, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + tag + (("  " + extra) if extra else ""))
    if not cond:
        fails.append(tag)


def max_volume(path):
    r = subprocess.run([FF, "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, timeout=300)
    err = (r.stderr or b"").decode("utf-8", "replace")
    m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err)
    return float(m.group(1)) if m else None


print("=== 1) 复现根因：非 utf-8 解码导致 stderr 变空 ===")
for enc in ("gbk", "ascii"):
    try:
        r = subprocess.run([FF, "-hide_banner", "-i", SHOT],
                           capture_output=True, encoding=enc, timeout=30)
        err = r.stderr or ""
        print(f"  encoding={enc:6s} -> stderr 长度={len(err)} 含'Audio:'={'Audio:' in err}")
        if enc == "gbk" and len(err) == 0:
            print("      ^ 这就是 exe 里的真实情形：解码失败→stderr 为空→误判无音轨")
    except Exception as e:
        print(f"  encoding={enc:6s} -> 抛异常 {type(e).__name__}")

print("\n=== 2) 新 _run_ff 不受编码影响 ===")
p = vp.VideoPipeline(cfg={}, app_dir=os.getcwd(), callbacks={})
rc, out, err = p._run_ff([FF, "-hide_banner", "-i", SHOT], timeout=30)
check("_run_ff 拿到非空 stderr", len(err) > 0, f"len={len(err)}")
check("_run_ff 能从 stderr 解析出音轨", "Audio:" in err)

print("\n=== 3) 新 _probe_has_audio 判定 ===")
res = p._probe_has_audio(SHOT)
check("真实分镜判定为有音轨", res is True, f"-> {res}")

print("\n=== 4) 完整 _merge 跑真实分镜（须有声）===")
tmp = tempfile.mkdtemp(prefix="reg_")
p.project_dir = tmp
p.width, p.height = 768, 1152
p.duration = 5.0
p.transition = "black"
p.transition_dur = 0.4
p.with_dialogue = True
p.burn_subtitles = True
logs = []
p.log = logs.append
shots = [{"en": "s", "zh": f"镜{i+1}", "line": f"台词{i+1}",
          "scene": 1 + (i // 2)} for i in range(6)]
out = os.path.join(tmp, "final.mp4")
ok, detail = p._merge(list(SHOTS), shots, out, burn_subtitles=True)
check("_merge 成功", ok, detail)
if ok:
    mv = max_volume(out)
    check("成片有声音（max_volume > -60 dB）", mv is not None and mv > -60.0,
          f"max_volume={mv} dB")
    check("触发了成片音量自检日志",
          any("成片自检" in x for x in logs),
          ([x for x in logs if "成片自检" in x] or ["(无)"])[0])
shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 5) 兜底：片段无音轨时能降级出片 ===")
tmp2 = tempfile.mkdtemp(prefix="reg2_")
# 造一个纯视频无音轨的片段
silent = os.path.join(tmp2, "silent.mp4")
subprocess.run([FF, "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=704x1280:d=3:r=24",
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "28",
                "-an", silent], capture_output=True, timeout=120)
print("  silent.mp4 存在:", os.path.exists(silent),
      " 有音轨:", p._probe_has_audio(silent))
p2 = vp.VideoPipeline(cfg={}, app_dir=os.getcwd(), callbacks={})
p2.project_dir = tmp2
p2.width, p2.height = 768, 1152
p2.duration = 3.0
p2.transition = "none"
p2.with_dialogue = False
p2.burn_subtitles = False
logs2 = []
p2.log = logs2.append
shots2 = [{"en": "s", "zh": "静音镜", "line": "", "scene": 1}]
out2 = os.path.join(tmp2, "final2.mp4")
ok2, detail2 = p2._merge([silent], shots2, out2, burn_subtitles=False)
check("无音轨片段也能成功出片（降级为静音轨）", ok2, detail2)
shutil.rmtree(tmp2, ignore_errors=True)

print("\n" + ("REGRESS_OK 全部通过" if not fails else f"REGRESS_FAIL: {fails}"))
