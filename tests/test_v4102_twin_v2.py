# v4.175.0：从仓库根目录搬入 tests/ —— 统一入口以 `python tests/xxx.py` 运行，
# 此时 sys.path[0] 是 tests/，必须显式把仓库根加回来，否则 import ui 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""离线单测：数字人模块 v2（反僵尸微动作 / 长口播分段 / AI 标识 / VLM 质检）。

不依赖网络，不依赖真实 ffmpeg 调用（除字体探测这种只读检查）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

_PASS = []
_FAIL = []


def check(name, cond, extra=""):
    if cond:
        _PASS.append(name)
        print(f"✅ {name} {extra}")
    else:
        _FAIL.append(name)
        print(f"❌ {name} {extra}")


# ---------------- import ----------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import digital_twin_panel as dtp
    import vision_qc as vq
except Exception as e:
    print(f"❌ import 失败：{e}")
    sys.exit(1)

print("=" * 60)
print("1) 长口播分段 split_dialogue")
print("=" * 60)

# 1) 短台词不分段
short = "大家好，我是小臭。今天聊点有意思的。"
segs = dtp.split_dialogue(short)
check("short_no_split", len(segs) == 1, f"segs={len(segs)}")
check("short_text_intact", segs and segs[0][0].replace(" ", "") ==
      short.replace(" ", ""), str(segs[:1]))

# 2) 长台词必须分段，且每段字数不超过单段容量
long_text = (
    "大家好我是小臭今天想跟大家聊一个特别有意思的话题。" * 4
)  # 约 96 字
segs = dtp.split_dialogue(long_text, max_sec=12)
max_chars = int(12 * dtp.CHARS_PER_SEC)
check("long_splits", len(segs) >= 2, f"segs={len(segs)}")
check("long_each_within_cap",
      all(len(t) <= max_chars for t, _s in segs),
      str([len(t) for t, _s in segs]))

# 3) 秒数必须钳制在 API 合法区间 4~12
check("secs_clamped",
      all(4 <= s <= 12 for _t, s in segs),
      str([s for _t, s in segs]))

# 4) 拼接回去不能丢字（允许标点/空白差异）
rejoined = "".join(t for t, _s in segs)
check("no_text_loss", rejoined == long_text,
      f"{len(rejoined)} vs {len(long_text)}")

# 5) 不在句子中间腰斩：段尾应该是句末标点（或原文结尾）
for i, (t, _s) in enumerate(segs):
    is_last = (i == len(segs) - 1)
    ok = t.rstrip()[-1] in dtp._SENT_END or is_last
    check(f"seg{i}_ends_at_sentence", ok, repr(t[-12:]))

# 6) 超长单句（无标点）必须硬切，不能超限
no_punct = "啊" * 200
segs2 = dtp.split_dialogue(no_punct, max_sec=12)
check("no_punct_hard_split", len(segs2) >= 4, f"segs={len(segs2)}")
check("no_punct_within_cap",
      all(len(t) <= max_chars for t, _s in segs2),
      str([len(t) for t, _s in segs2]))
check("no_punct_no_loss", "".join(t for t, _s in segs2) == no_punct)

# 7) 碎片尾巴要并进上一段（避免 4 秒碎片）
tail_text = "这是一段足够长的话可以切成一整段。" * 3 + "嗯。"
segs3 = dtp.split_dialogue(tail_text, max_sec=12)
if len(segs3) >= 2:
    check("tail_not_fragment", len(segs3[-1][0]) >= 10,
          f"last_len={len(segs3[-1][0])}")
else:
    check("tail_not_fragment", True, "单段，无需合并")

# 8) 空输入
check("empty_input", dtp.split_dialogue("") == [])
check("none_input", dtp.split_dialogue(None) == [])

print()
print("=" * 60)
print("2) 反僵尸微动作 prompt")
print("=" * 60)

p = dtp._build_twin_prompt("", "大家好。", keep_bg=True)
check("has_micro_motion", "MICRO-MOTION" in p)
check("has_mannequin_warning", "FROZEN MANNEQUIN" in p.upper())
check("micro_mentions_blink", "blinks" in p)
check("micro_mentions_breathe", "breathes" in p)
check("micro_mentions_silent", "silent" in p.lower())

# 旧的「把人冻住」表述必须消失
check("no_frozen_shoulders",
      "shoulders stay in the exact same position" not in p,
      "旧表述已移除")

# 原有锁定仍在
check("still_has_face_lock", "CRITICAL FACE LOCK" in p)
check("still_has_bg_lock", "BACKGROUND LOCK" in p)
check("still_has_camera_lock", "CAMERA LOCK" in p)
# 相机仍然锁死（只放开人的微动作）
check("camera_still_static", "STATIC camera" in p)
check("camera_no_move", "NO camera movement" in p)

# 声音锁定常量
check("voice_lock_exists", bool(dtp.VOICE_LOCK))
check("voice_lock_same_voice", "SAME adult male voice" in dtp.VOICE_LOCK)
check("voice_lock_no_bgm",
      "do not add background music" in dtp.VOICE_LOCK.lower())

print()
print("=" * 60)
print("3) VLM 质检判定 parse_verdict")
print("=" * 60)

ok, note = vq.parse_verdict("VERDICT: PASS\nISSUES: none")
check("verdict_pass", ok is True, repr(note[:20]))

ok, note = vq.parse_verdict("VERDICT: FAIL\nISSUES: different person")
check("verdict_fail", ok is False)

ok, note = vq.parse_verdict("")
check("verdict_empty_passes", ok is True, "空=质检不可用=放行")

ok, note = vq.parse_verdict(None)
check("verdict_none_passes", ok is True)

ok, note = vq.parse_verdict("verdict: fail")
check("verdict_case_insensitive", ok is False)

# 质检不可用时（无 key）必须放行而不是卡死
ok, note = vq.review_identity({"model_profiles": {}}, "/no/such/a.png", "/no/such/b.png")
check("review_identity_no_key_passes", ok is True, "无 key 放行")

ok, note = vq.review_keyframe({"model_profiles": {}}, "/no/such.png", "a shot")
check("review_keyframe_missing_file_passes", ok is True)

print()
print("=" * 60)
print("4) ffmpeg 辅助（只读/失败路径）")
print("=" * 60)

font = dtp._cn_font()
check("cn_font_found", bool(font) and os.path.isfile(font or ""), str(font))

ff = dtp._ffmpeg()
if ff:
    check("ffmpeg_found", True, str(ff))
else:
    # v4.239.1（CI 红 → 修）：ffmpeg 是环境依赖项，缺失时只 SKIP 本项；
    # 后续 burn/extract 失败路径断言不依赖 ffmpeg 可照跑，画幅裁剪块本就有 if dtp._ffmpeg() 守卫。
    print("  [SKIP] ffmpeg_found —— 本机无 ffmpeg，环境依赖项跳过（不冒充通过）")

# 对不存在的文件，烧标识要返回 False 而不是抛异常
tmp = tempfile.mkdtemp(prefix="twin_t_")
ok = dtp.burn_ai_mark(os.path.join(tmp, "nope.mp4"),
                      os.path.join(tmp, "out.mp4"))
check("burn_missing_src_returns_false", ok is False)

ok = dtp.extract_last_frame(os.path.join(tmp, "nope.mp4"),
                            os.path.join(tmp, "t.png"))
check("extract_missing_returns_false", ok is False)

# 画幅解析
check("parse_res_ok", dtp.parse_resolution("768x1152") == (768, 1152))
check("parse_res_bad", dtp.parse_resolution("bad") == (0, 0))
check("parse_res_none", dtp.parse_resolution(None) == (0, 0))

# 参考图居中裁剪（Agnes 跟随首帧比例，这一步必需）
_src = os.path.join(tmp, "wide.jpg")
_dst = os.path.join(tmp, "tall.png")
if dtp._ffmpeg():
    subprocess.run([dtp._ffmpeg(), "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=1408x768:d=1", "-frames:v", "1", _src],
                   capture_output=True)
    got = dtp.fit_image_to_aspect(_src, _dst, 768, 1152)
    check("aspect_fit_returns_path", bool(got) and os.path.isfile(_dst))
    if os.path.isfile(_dst):
        _probe = shutil.which("ffprobe") or "ffprobe"
        r = subprocess.run([_probe,
                            "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=s=x:p=0", _dst],
                           capture_output=True, text=True)
        check("aspect_fit_size", r.stdout.strip() == "768x1152",
              f"实际 {r.stdout.strip()}")
    # 失败路径
    check("aspect_fit_missing_src_returns_none",
          dtp.fit_image_to_aspect(os.path.join(tmp, "nope.jpg"),
                                  os.path.join(tmp, "o.png"), 768, 1152) is None)

print()
print("=" * 60)
print("5) 本人照片目录：必须落用户目录，且迁移旧照片")
print("=" * 60)

import shutil as _sh  # noqa: E402

_fake_app = tempfile.mkdtemp(prefix="fake_app_")
os.makedirs(os.path.join(_fake_app, "avatars"))
# 一张"真照片"（20KB）和一张"程序内置小图标"（100B）
_big = os.path.join(_fake_app, "avatars", "my_photo.jpg")
with open(_big, "wb") as f:
    f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 20000)
_small = os.path.join(_fake_app, "avatars", "avatar_user.png")
with open(_small, "wb") as f:
    f.write(b"\x89PNG\r\n" + b"\x00" * 100)

_fake_user = tempfile.mkdtemp(prefix="fake_user_")
_old_appdir = dtp.APP_DIR
_old_udd = dtp._user_data_dir
try:
    dtp.APP_DIR = _fake_app
    dtp._user_data_dir = lambda: _fake_user
    d = dtp._avatar_dir()
    check("avatar_dir_under_userdata",
          os.path.abspath(d).startswith(os.path.abspath(_fake_user)), d)
    check("avatar_dir_not_in_appdir",
          os.path.abspath(d) != os.path.abspath(os.path.join(_fake_app, "avatars")))
    files = [f for f in os.listdir(d) if f.lower().endswith(dtp.AVATAR_EXTS)]
    check("photo_migrated", "my_photo.jpg" in files, str(files))
    check("tiny_icon_not_migrated", "avatar_user.png" not in files, str(files))
finally:
    dtp.APP_DIR = _old_appdir
    dtp._user_data_dir = _old_udd

# 真实环境下拍一张：目录确实指向 Documents
_real = dtp._avatar_dir()
check("real_avatar_dir_in_documents", "小臭玩AI" in _real, _real)

print()
print("=" * 60)
print(f"结果：{len(_PASS)}/{len(_PASS) + len(_FAIL)} 通过")
if _FAIL:
    print("失败项：", _FAIL)
    sys.exit(1)
print("ALL_TWIN_V2_OK")
