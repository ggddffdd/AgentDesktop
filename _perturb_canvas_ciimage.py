# -*- coding: utf-8 -*-
"""节点画布阶段 C（图片局部编辑·结果图）· 扰动脚本（非空转验证）。

对 image_local_edit.py / canvas_export.py 做**单点变异**，断言目标 B 判据
由绿变红（证明判据确实钉住了真实行为）；末尾**反向基线**确认不改动时全绿。

运行：python _perturb_canvas_ciimage.py
依赖：系统 Python（带 numpy + Pillow），与判据套件同环境。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
TEST = os.path.join("tests", "test_canvas_ciimage.py")

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()


def run(cx_check=None):
    env = dict(os.environ)
    if cx_check:
        env["CX_CHECK"] = cx_check
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    return p.stdout + p.stderr


def token_failed(out, token):
    for line in out.splitlines():
        if line.startswith("[FAIL]") and line.split("]", 1)[1].strip().startswith(token):
            return True
    return False


def all_green(out):
    return "RESULT: ALL GREEN" in out


def apply_mutation(rel, old, new):
    path = os.path.join(ROOT, rel)
    s = open(path, encoding="utf-8").read()
    if old not in s:
        raise AssertionError("锚点未命中: %s" % rel)
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
    return s  # 原内容，用于还原


# (描述, 文件, 原片段, 变异片段, CX_CHECK, 期望翻红的判据 token)
MUTATIONS = [
    ("PG1 brightness 不乘以 factor",
     "image_local_edit.py",
     '        f = float(p.get("factor", 1.2))\n        return np.clip(arr * f, 0, 1)',
     '        f = float(p.get("factor", 1.2))\n        return arr  # 扰动：不提亮',
     "B1", "B1"),

    ("PG2 region rect 掩码恒为全图",
     "image_local_edit.py",
     '        m = np.zeros((H, W), dtype=bool)\n        if y1 > y0 and x1 > x0:\n            m[y0:y1, x0:x1] = True\n        return m',
     '        m = np.ones((H, W), dtype=bool)\n        return m  # 扰动：恒全图',
     "B1", "B1"),

    ("PG3 export 不应用 local_edits",
     "canvas_export.py",
     '    arr = _load_arr(src_path)\n    res = il.apply_local_edits(arr, edits, inpaint_fn=inpaint_fn)\n    _save_arr(res, out_path)',
     '    arr = _load_arr(src_path)\n    res = arr  # 扰动：不应用编辑\n    _save_arr(res, out_path)',
     "B4b", "B4b"),

    ("PG4 inpaint 无 fn 时不抛错",
     "image_local_edit.py",
     '            if inpaint_fn is None:\n                raise UnsupportedEditMode(\n                    "inpaint 需外部模型，未提供 inpaint_fn（阶段 C 未接真执行器）")\n            out = inpaint_fn(out, region, e.get("instruction", ""),\n                             e.get("params") or {})\n            continue',
     '            if inpaint_fn is None:\n                out = out  # 扰动：不抛、不应用\n                continue\n            out = inpaint_fn(out, region, e.get("instruction", ""),\n                             e.get("params") or {})\n            continue',
     "B6", "B6"),

    ("PG5 grayscale 操作失效",
     "image_local_edit.py",
     '    if op == "grayscale":\n        gray = arr.mean(axis=2, keepdims=True)\n        return np.repeat(gray, 3, axis=2)',
     '    if op == "grayscale":\n        return arr  # 扰动：不灰度',
     "B9", "B9"),

    ("PG6 region=None 掩码恒为空（不改）",
     "image_local_edit.py",
     '    if not region:\n        return np.ones((H, W), dtype=bool)',
     '    if not region:\n        return np.zeros((H, W), dtype=bool)  # 扰动：整图不编辑',
     "B8", "B8"),
]


def main():
    print("===== 阶段 C 扰动：非空转验证 =====")
    total = len(MUTATIONS)
    hit = 0
    for desc, rel, old, new, cx, tok in MUTATIONS:
        # 1) 变异前先确认该判据本来是绿的（基线）
        base = run(cx)
        assert not token_failed(base, tok), "基线 %s 竟已红，判据本身不稳" % tok
        # 2) 单点变异
        backup = apply_mutation(rel, old, new)
        # 3) 变异后该判据应翻红
        out = run(cx)
        ok_red = token_failed(out, tok)
        # 4) 立即还原
        open(os.path.join(ROOT, rel), "w", encoding="utf-8").write(backup)
        # 5) 还原后该判据应回绿
        back = run(cx)
        ok_back = not token_failed(back, tok)
        status = "PASS" if (ok_red and ok_back) else "FAIL"
        if ok_red and ok_back:
            hit += 1
        print("[%s] %s :: 变异后%s=%s 还原后回绿=%s"
              % (status, desc, tok, ok_red, ok_back))

    # 反向基线：不改动任何文件，全量判据应全绿
    full = run(None)
    base_ok = all_green(full)
    print("\n[反向基线] 不改动时全量判据: %s" % ("ALL GREEN" if base_ok else "HAS FAIL"))

    print("\n===== 横幅 =====")
    print("扰动: %d  命中红名: %d  反向基线: %s" % (total, hit, "GREEN" if base_ok else "RED"))
    print("RESULT: %s" % ("ALL GREEN" if (hit == total and base_ok) else "HAS FAIL"))
    raise SystemExit(0 if (hit == total and base_ok) else 1)


if __name__ == "__main__":
    main()
