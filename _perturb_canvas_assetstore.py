# -*- coding: utf-8 -*-
"""扰动验证：证明 tests/test_canvas_assetstore.py 的静态判据「会红在该红的地方」。

与 _perturb_canvas_model.py 同一套规矩：
  1. 期望采用**包含式**：expect ⊆ actual_red，只要求「该红的红了」，不禁止连带红其它；
  2. 每条 case 真生成一份**变异后源码**落临时文件，再用环境变量指向它跑判据
     （CANVAS_PATH / CANVAS_STATIC），不在原文件上动刀；
  3. 期望为空 = 这条 case 应全绿，用来防「判据过宽、什么都判红」。

用法：python _perturb_canvas_assetstore.py
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
CANVAS = os.path.join(ROOT, "canvas_graph.py")
JUDGE = os.path.join(ROOT, "tests", "test_canvas_assetstore.py")
SRC = open(CANVAS, encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(mutated_src, tag):
    """把变异后的源码落临时文件，静态模式跑判据，返回 (红名集合, 原始输出)。"""
    fd, path = tempfile.mkstemp(prefix="canvas_mut_", suffix=".py", dir=ROOT)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(mutated_src)
    env = dict(os.environ, CANVAS_PATH=path, CANVAS_STATIC="1")
    try:
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    red = set(re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M))
    return red, out


def case(name, mutate, expect_ids):
    """mutate(src) -> 变异后源码。expect_ids：期望转红的判据名（子串匹配即可）。"""
    global PASS_N, FAIL_N
    try:
        new_src = mutate(SRC)
    except Exception as e:  # noqa: BLE001
        print("  [FAIL] %s  — 变异函数本身出错：%s: %s" % (name, type(e).__name__, e))
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    if new_src == SRC:
        print("  [FAIL] %s  — 变异没有生效（源码没变），这条 case 是假的" % name)
        FAIL_N += 1
        FAILED_CASES.append(name)
        return
    red, _out = run_judge(new_src, name)
    missing = [e for e in expect_ids if not any(e in r for r in red)]
    ok = not missing
    print("  [%s] %s   期望红 %s | 实际红 %d 项%s"
          % ("OK  " if ok else "FAIL", name, "、".join(expect_ids) or "(无)",
             len(red), ("  缺：" + "、".join(missing)) if missing else ""))
    if ok:
        PASS_N += 1
    else:
        FAIL_N += 1
        FAILED_CASES.append(name)


def sub(old, new):
    """生成「把 old 替换成 new」的变异函数（自带生效校验）。"""
    def f(src):
        if old not in src:
            raise AssertionError("锚点不在源码里：%r" % old[:60])
        return src.replace(old, new, 1)
    return f


def drop_line(anchor):
    """删掉包含 anchor 的整行。"""
    def f(src):
        if anchor not in src:
            raise AssertionError("锚点不在源码里：%r" % anchor[:60])
        lines = src.splitlines(True)
        for i, ln in enumerate(lines):
            if anchor in ln:
                del lines[i]
                return "".join(lines)
        raise AssertionError("没找到可删的行")
    return f


print("\n=== 扰动：资产库接线契约被删 → 对应静态判据必须转红 ===")

case("P01 撤掉 self._asset_store 解析（改 None）",
     sub("        self._asset_store = _resolve_store(asset_store)",
         "        self._asset_store = None  # mutated"),
     ["A1"])

case("P02 撤掉 _register_asset 对 sink 的调用",
     sub("            ok, aid = self._asset_store(name, ref.kind, path,",
         "            ok, aid = (False, \"\")  # mutated"),
     ["A2"])

case("P03 删掉 asset_id 回填",
     sub("            ref.asset_id = aid", "            pass  # mutated"),
     ["A3"])

case("P04 把 registered=True 改成 False（去掉成功态）",
     sub("            ref.registered = True", "            ref.registered = False"),
     ["A4"])

case("P05 撤掉 _wrap 里的 _register_asset 调用",
     sub("                    self._register_asset(a)", "                    pass  # mutated"),
     ["A5"])

case("P06 撤掉 stub 产出的 path",
     sub('                path=_stub_stage_path(out_kind, self.id, "out"),',
         '                path="",  # mutated'),
     ["A6"])

case("P07 默认 sink 不返回 _MemStore",
     sub("        return _MemStore()", "        return None  # mutated"),
     ["A7"])

case("P08 撤掉真实库接线（_as.register_asset）",
     sub("        return _as.register_asset(name, kind, path, tags=tags,",
         "        return None  # mutated"),
     ["A8"])

case("P09 删掉 AssetRef.registered 字段",
     drop_line("registered: bool = False"),
     ["A9"])

# ---- 反向：没坏就不许红（防判据过宽）----
print("\n=== 反向：原样通过时不许有任何红项 ===")
_red0, _out0 = run_judge(SRC, "baseline")
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % sorted(_red0))
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    print("  [OK  ] 未变异源码全绿（%d 项静态判据）"
          % len(re.findall(r"\[OK  \] A", _out0)))
    PASS_N += 1

print("\nPASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_CANVAS_ASSETSTORE_OK ===")
