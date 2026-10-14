# -*- coding: utf-8 -*-
"""扰动验证 v4.261.0：分镜导演提示词「方法论」不是摆设。

背景（2026-10-11）：大哥反馈导演写分镜提示词功力弱。根因是 system 只有 3 句
角色设定、没有任何方法论。修法是注入 SHOT_DIRECTOR_SYSTEM（三段式+铁律）
与 SHOT_EN_SPEC（en 规范+负面清单+few-shot）。

但「加了常量」≠「功力变强」——历史上这类加固最容易退化成两种假象：
  ① 常量写了，却没接进 prompt（人设还是裸的，功力照旧弱）；
  ② 接进去了，后人嫌 prompt 太长又把方法论删回三句。
所以本扰动逐条退化，验证判据 test_shot_prompt_method_261.py **改坏必红**：

  · V1 方法论整体退回旧裸人设      → B 组（三段式/铁律）+ D1 全红
  · V2 en 规范退回旧的一句          → C 组（负面清单）+ D3/D7 全红
  · V3 删掉 few-shot 范例           → C5 红
  · V4 反向照妖镜：把 system 引用改回内联旧句（常量仍在、但没接进 payload）
                                    → 只有 D1 红，B 组**不红**
    （这条专门证明 D 组是行为级而非恒真：光有常量不接线，B 组会假绿放过，
      正是「改了但功力还是弱」的复发路径，必须靠 D1 抓住。）

原串一律按真实字节程序化截取（grab），不手抄。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_shot_prompt_method_261.py")

VP = "video_pipeline.py"

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
                       capture_output=True, text=True, timeout=600)
    red = [l.strip() for l in p.stdout.splitlines() if "[FAIL]" in l]
    return (not red) and p.returncode == 0, red


def grab(fp, start, end=None):
    """从真实字节里按「起始标记 + 结束标记」截取连续原文。"""
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
_OLD_SYSTEM_BODY = grab(VP, "SHOT_DIRECTOR_SYSTEM = (", "\n\n# en 字段规范")
_OLD_EN_BODY = grab(VP, "SHOT_EN_SPEC = (", "\n)\n")
_OLD_FEW_SHOT_MARK = "✅ 好例子（照这个颗粒度写）：'a young woman"
_OLD_SYSTEM_REF = grab(VP,
                       '            {"role": "system", "content": (SHOT_DIRECTOR_SYSTEM',
                       '\n            {"role": "user"')

_NEW_BARE_SYSTEM = (
    'SHOT_DIRECTOR_SYSTEM = (\n'
    '    "你是专业的短视频分镜导演，也是 AI 视频生成提示词专家。"\n'
    ')'
)
# ⚠️ grab 的 end 是 "\n)\n"，返回内容**不含**收尾的 `)` ——
#    所以这里的 new 也必须以 "\n" 结尾、不补 `)`，否则变异体会多出一个 `)`
#    直接语法崩坏（判据整体起不来 → 红 0 条 → 伪装成 MISS，本次 V2 踩过）。
_NEW_BARE_EN = (
    'SHOT_EN_SPEC = (\n'
    '    "- en：英文视频生成提示词，1~2 句话，'
    '只写视觉内容（主体、动作、镜头运动、光线氛围）。\\n"\n'
)
_NEW_INLINE_SYSTEM = (
    '            {"role": "system", "content": '
    '("你是专业的短视频分镜导演，也是 AI 视频生成提示词专家。"\n'
    '                                           '
    '"把剧本拆成连续、有因果推进的完整分镜序列，全片同一主角贯穿；"\n'
    '                                           '
    '"熟练运用电影摄影语言（景别、运镜、机位）设计每一镜。")},\n'
)

CASES = [
    # V1 方法论整体退回旧裸人设 → 三段式/铁律判据全红
    ("V1 方法论整体退回旧裸人设（只有角色设定）",
     VP, _OLD_SYSTEM_BODY, _NEW_BARE_SYSTEM,
     ["B1", "B2", "B3", "B4", "B5", "B6", "D1"]),

    # V2 en 规范退回旧的一句 → 负面清单判据红，且反向照妖镜 D7 红
    ("V2 en 规范退回旧的一句（负面清单全丢）",
     VP, _OLD_EN_BODY, _NEW_BARE_EN,
     ["C1", "C2", "C3", "C4", "C6", "D3", "D7"]),

    # V3 删掉 few-shot 范例
    ("V3 删掉 few-shot 范例标记",
     VP, _OLD_FEW_SHOT_MARK, "好例子标记已移除：'a young woman",
     ["C5"]),

    # V4 反向照妖镜：常量仍在但没接进 payload → B 组假绿、只有 D1 红
    ("V4 反向照妖镜：system 引用改回内联旧句（常量未接线）",
     VP, _OLD_SYSTEM_REF, _NEW_INLINE_SYSTEM,
     ["D1"]),
]

HIT, MISS = [], []
try:
    for fp in (VP,):
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
        write_raw(fp, _backup[fp])          # 每条都从原始备份重置
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
                 "" if hit else "；期望含 %s" % expect))
        (HIT if hit else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    clean_pycache()
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
