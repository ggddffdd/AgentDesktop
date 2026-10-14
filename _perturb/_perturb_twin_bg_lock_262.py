# -*- coding: utf-8 -*-
"""扰动验证 v4.262.0：数字人「背景变一小会」的修复不是摆设。

背景（2026-10-12）：大哥反馈数字人（本人形象口播）视频**背景会改变一小会**。
取证结论——根因不在提示词（PORTRAIT_PROMPT 系列本就写了背景不变），而在两处：

  ① 生成模式：`ref_mode=True`（默认）传 `images=[参考图]` 走 **reference 弱参考**。
     内核三模式互斥且多图优先（video-agent/core/agnes.py:291-301），
     只要传 images 就永远是 reference、first_frame 被忽略 → 模型段内自由重诠释背景。
     修法：不传 images，改 `first_frame=参考图`（+ dual_frame 时 last_frame 同图）
     → 走 keyframe **首帧强锁**。
  ② 质检漏网：`REVIEW_QUESTION_IDENTITY` 问项第 4 条**确实让模型查 BACKGROUND**，
     但裁决规则只写 "FAIL if identity changed, OR face/anatomy broken" —— 背景漂了
     不判 FAIL → 过检、不触发重生成。修法：把 background 纳入 FAIL（并新增
     keep_bg=False 的 NOBG 版，换背景时不许判背景，防误判）。

但这两处修复最容易退化成三种假象：
  ① 又传回 images（"我有参考图啊"）→ reference 弱参考，背景照旧漂；
  ② 质检 FAIL 规则被"精简"回只有 identity/face → 漂移再次漏网；
  ③ 指纹丢掉模式版本号 → 断点续跑复用旧 reference 段，混成片。

本扰动逐条退化，验证判据 tests/test_twin_seg_resilience.py **改坏必红**：

  · V1 串行锁定分支退回 images=[参考图]（reference 弱参考） → N1 红
  · V2 并发锁定分支退回 images=[参考图]                     → N1 + Q3 红
  · V3 质检 FAIL 规则退回"只判身份/畸变"（删背景条）        → T1 + T2 红
  · V4 质检丢掉 keep_bg 分支（恒用锁背景版）                → T5 红
  · V5 指纹丢掉生成模式版本 g                               → S3 红

原串一律按真实字节程序化截取（grab），不手抄。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_twin_seg_resilience.py")

DT = "digital_twin_panel.py"
VQ = "vision_qc.py"

_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def clean_pycache():
    for dirpath, dirnames, _files in os.walk(ROOT):
        if "_dev_history" in dirpath or "dist" in dirpath or "backup" in dirpath:
            continue
        for d in list(dirnames):
            if d == "__pycache__":
                import shutil
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run_test():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=900)
    # 该判据的失败行形如 "❌ N1 每段都以参考图为首帧 ..."
    red = [l.strip() for l in p.stdout.splitlines() if "❌" in l]
    return (not red) and p.returncode == 0, red


def grab(fp, start, end=None):
    """从真实字节里按「起始标记 + 结束标记」截取连续原文（不含 end 标记本身）。"""
    src = read_raw(fp).decode("utf-8").replace("\r\n", "\n")
    i = src.find(start)
    if i < 0:
        raise SystemExit("原串起始标记未命中：%s / %r" % (fp, start[:60]))
    if end is None:
        return src[i:]
    j = src.find(end, i + len(start))
    if j < 0:
        raise SystemExit("原串结束标记未命中：%s / %r" % (fp, end[:60]))
    return src[i:j]


# ---- 原串（全部从真实字节截取）----
# ① 串行锁定分支：images=None + first/last_frame 同图
_LOCK_BRANCH = grab(DT,
                    "                images = None\n                first_frame = portrait_uri",
                    "\n            else:")
# ② 并发锁定分支：关键字传 first_frame / last_frame
_PAR_CALL = grab(DT,
                 "                i, prompt, sec, first_frame=portrait_uri,",
                 "\n            return i, text, seg_file, seg_path, note")
# ③ 质检 FAIL 规则（含 background 条）
# ⚠️ end 必须**包含**字符串收尾的 `"""`，否则 old 会把 `"""` 一起吃掉 →
#    替换后字符串不闭合 → 变异体语法崩坏 → 判据整体起不来 → 红 0 条伪装成 MISS
#    （v4.261.0 的 V2 踩过同一个坑）。
_FAIL_RULE = grab(VQ,
                  "Replace PASS with FAIL if the identity changed, OR if the face/anatomy is broken,",
                  '\n"""\n\n# v4.262.0：keep_bg=False')
# ④ 质检的 keep_bg 三元分支
_QC_BRANCH = ("    q = REVIEW_QUESTION_IDENTITY if keep_bg "
              "else REVIEW_QUESTION_IDENTITY_NOBG")
# ⑤ 指纹里的生成模式版本字段
_FP_VER = '         "g": 2},'

# ---- 退化体 ----
_NEW_REF_IMAGES_BRANCH = (
    "                images = [portrait_uri]\n"
    "                first_frame = None\n"
    "                last_frame = None"
)
_NEW_PAR_IMAGES = (
    "                i, prompt, sec, None, None, tmp_dir, ff, "
    "images=[portrait_uri], on_submit=_on_sub)"
)
_NEW_WEAK_FAIL_RULE = (
    "Replace PASS with FAIL if the identity changed, "
    "OR if the face/anatomy is broken."
)
_NEW_NO_BRANCH = "    q = REVIEW_QUESTION_IDENTITY"
_NEW_NO_VER = "        },"

CASES = [
    # V1 串行锁定分支退回 reference 弱参考 → N1 红
    #    （N1 断言"每段都以参考图为首帧、且不传 images"，两条同时失守）
    ("V1 串行锁定分支退回 images=[参考图]（reference 弱参考）",
     DT, _LOCK_BRANCH, _NEW_REF_IMAGES_BRANCH, ["N1"]),

    # V2 并发锁定分支退回 reference → O6（并发行为级）+ Q3（源码契约）红
    #    注：不能指望 N1 —— N 组走 workers=1 的**串行**路径，测不到 _gen_parallel。
    ("V2 并发锁定分支退回 images=[参考图]",
     DT, _PAR_CALL, _NEW_PAR_IMAGES, ["O6", "Q3"]),

    # V3 质检 FAIL 规则退回"只判身份/畸变" → T1（FAIL 含 background）+ T2 红
    ("V3 质检 FAIL 规则退回只判身份/畸变（背景漂移再次漏网）",
     VQ, _FAIL_RULE, _NEW_WEAK_FAIL_RULE, ["T1", "T2"]),

    # V4 质检丢掉 keep_bg 分支 → T5 红（换背景时会把好段误判 FAIL）
    ("V4 质检丢掉 keep_bg 分支（恒用锁背景版问题）",
     VQ, _QC_BRANCH, _NEW_NO_BRANCH, ["T5"]),

    # V5 指纹丢掉生成模式版本 → S3 红（旧 reference 段被续跑复用、混成片）
    ("V5 指纹丢掉生成模式版本 g",
     DT, _FP_VER, _NEW_NO_VER, ["S3"]),
]

HIT, MISS = [], []
try:
    for fp in (DT, VQ):
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    print("基线自检（未变异时必须全绿，否则判据本身坏了）：")
    ok, red = run_test()
    if not ok:
        print("  [基线红] %s" % "; ".join(red[:6]))
        print("  基线不绿就停止扰动 —— 否则「命中」无意义。")
        sys.exit(2)
    print("  基线全绿 ✔\n")

    _norm = {fp: _backup[fp].decode("utf-8").replace("\r\n", "\n")
             for fp in _backup}
    _unmatched = [n for n, fp, old, _new, _e in CASES if old not in _norm[fp]]
    if _unmatched:
        print("  [中止] 以下用例原串未命中真实字节（防静默失效）：")
        for n in _unmatched:
            print("    - " + n)
        sys.exit(2)
    print("  原串预检：%d/%d 全部命中真实字节 ✔\n" % (len(CASES), len(CASES)))

    for name, fp, old, new, expect in CASES:
        for f in (DT, VQ):
            write_raw(f, _backup[f])          # 每条都从原始备份重置
        clean_pycache()
        src = _norm[fp]
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] %s —— 原串未命中" % name)
            continue
        out = src.replace(old, new, 1)
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))

        _is_red, red = run_test()
        joined = "\n".join(red)
        hit = all(e in joined for e in expect)
        print("  [%s] %s → 红 %d 条%s"
              % ("HIT " if hit else "MISS", name, len(red),
                 ("｜" + "；".join(r[:70] for r in red[:3])) if red else ""))
        if hit:
            HIT.append(name)
        else:
            MISS.append("%s（期望命中 %s，实际红 %d 条）"
                        % (name, "/".join(expect), len(red)))
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()

print()
print("=" * 64)
print("扰动汇总：PASS=%d FAIL=%d（单位=用例）" % (len(HIT), len(MISS)))
for m in MISS:
    print("  ✗ " + m)
print("=" * 64)
if MISS:
    print("PERTURB FAIL=%d" % len(MISS))
    sys.exit(1)
print("PERTURB PASS=%d FAIL=0" % len(HIT))
