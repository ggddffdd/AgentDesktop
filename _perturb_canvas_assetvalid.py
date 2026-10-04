# -*- coding: utf-8 -*-
"""画布 · 统一资产校验 / 导入加固 / 语法兼容 判据扰动（证明判据非空转）。

逐个把 canvas_graph.py / canvas_export.py / executors.py / tests/pep701_lint.py 里的
守卫写法打松，用 *_PATH 指向变异副本、AV_CHECK 只验目标判据，断言该判据**由绿变红**。
最后做反向基线（原文件 + 同判据应绿），确认判据本身有效、不是巧合。

用法：python _perturb_canvas_assetvalid.py
"""

import os
import sys
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRCS = {
    "cg": os.path.join(HERE, "canvas_graph.py"),
    "cx": os.path.join(HERE, "canvas_export.py"),
    "ex": os.path.join(HERE, "executors.py"),
    "pl": os.path.join(HERE, "tests", "pep701_lint.py"),
}
ENVKEY = {"cg": "CG_PATH", "cx": "CX_PATH", "ex": "EX_PATH", "pl": "PL_PATH"}
TEST = os.path.join(HERE, "tests", "test_canvas_assetvalid.py")
PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, HERE)
import _perturb_guard as _guard  # noqa: E402
_guard.arm(list(SRCS.values()))

# (变异名, 目标判据, 源键, 原串, 替换为)
MUTATIONS = [
    # ---- A 组：检测器不能变成哑弹（否则「源码零残留」是永远绿的空判据）----
    ("M1", "A1", "pl", "            if q == stack[-1]:",
     "            if False:  # 扰动：检测器变哑（同引号嵌套也不报）"),
    # ---- B 组：统一资产有效性 ----
    ("M2", "B2", "cg",
     '    if not os.path.isfile(path):\n'
     '        return ASSET_INVALID, "文件不存在: %s" % path',
     '    if not os.path.isfile(path):\n'
     '        return ASSET_REAL, ""  # 扰动：不存在的文件也算真产物'),
    ("M3", "B5", "cg", '    if getattr(ref, "placeholder", False):',
     '    if False:  # 扰动：不认占位标记（占位物会被当真实文件去校验）'),
    ("M4", "B9", "cg",
     "            if bad:\n                raise CanvasAssetError(",
     "            if False:  # 扰动：坏产物不报，节点照样 completed\n"
     "                raise CanvasAssetError("),
    ("M5", "B10", "cg",
     "        ref.validity, ref.invalid_reason = assess_asset(ref, self.asset_root)",
     "        pass  # 扰动：不做有效性判定"),
    ("M6", "B15", "cg",
     "            for a in (node.out_assets or {}).values():\n"
     "                if isinstance(a, AssetRef):\n"
     "                    a.stale = True",
     "            pass  # 扰动：不给上一轮产出打历史标"),
    # ---- C 组：导入校验 ----
    ("M7", "C2", "cx", "        if st is not None and st not in tg.VALID_STATUSES:",
     "        if False:  # 扰动：不校验 status"),
    ("M8", "C4", "cx",
     "                if isinstance(v, bool) or not isinstance(v, (int, float)):",
     "                if False:  # 扰动：不校验 pos 是否为数字"),
    ("M9", "C5", "cx",
     '            _validate_local_edits(nd["config"]["local_edits"], where, nid)',
     "            pass  # 扰动：不校验 local_edits"),
    ("M10", "C8", "cx", "        if key in seen:",
     "        if False:  # 扰动：不查重复边"),
    ("M11", "C9", "cx", "        if prev is not None:",
     "        if False:  # 扰动：单入端口也允许多连"),
    ("M12", "C11", "cx", "        if ph is not None and not isinstance(ph, bool):",
     "        if False:  # 扰动：不校验 placeholder 类型"),
    ("M13", "C12", "cx", "        if version not in SUPPORTED_VERSIONS:",
     "        if False:  # 扰动：不校验工程文件版本"),
    # ---- D 组：占位命名 ----
    ("M14", "D1", "ex",
     "            path = stage_path(asset_root, kind, node.id, p, ext=PLACEHOLDER_EXT)",
     "            path = stage_path(asset_root, kind, node.id, p)  # 扰动：占位借真媒体扩展名"),
    ("M15", "D2", "ex",
     '                      "content_type": PLACEHOLDER_CONTENT_TYPE,\n', ""),
    # ---- E 组：多入端口 ----
    ("M16", "E1", "cg",
     '                if port is not None and getattr(port, "multi", False):',
     '                if False:  # 扰动：多入也当单入（只留最后一个）'),
]

# 反向基线：这些判据在**原文件**上应保持绿（exit=0）
BASELINE = ("A1", "B2", "B9", "B10", "C2", "C9", "D1", "E1")


def run_with(check_name, variant=None, env_extra=None):
    """AV_CHECK 只验目标判据；variant=(源键, 变异副本路径) 注入给套件。"""
    env = dict(os.environ)
    env["AV_CHECK"] = check_name
    env.update(env_extra or {})
    if variant:
        key, path = variant
        env[ENVKEY[key]] = path
    p = subprocess.run([PY, TEST], env=env, capture_output=True, text=True)
    return p.returncode


def _write_tmp(text, suffix=".py"):
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=suffix,
                                     dir=tempfile.gettempdir(),
                                     delete=False, encoding="utf-8")
    tf.write(text)
    tf.close()
    return tf.name


def main():
    bases = {}
    for k, p in SRCS.items():
        with open(p, "r", encoding="utf-8") as f:
            bases[k] = f.read()

    passed = 0
    total = 0
    for mname, target, key, old, new in MUTATIONS:
        total += 1
        mut = bases[key].replace(old, new, 1)
        if mut == bases[key]:
            print(f"  [SKIP] {mname} -> {target}: 未命中锚点 {old[:46]!r}")
            continue
        tf = _write_tmp(mut)
        try:
            rc = run_with(target, variant=(key, tf))
        finally:
            try:
                os.remove(tf)
            except OSError:
                pass
        ok = rc != 0
        print(f"  [{'OK ' if ok else 'FAIL'}] {mname} -> {target}: 变异后 exit={rc} (期望 !=0)")
        passed += 1 if ok else 0

    # ---- A5：扫描根指向「含违规写法的目录」→ 应翻红 ----
    # 不去改真源文件（那会污染其它套件），改用一个假源码目录作为扫描根。
    total += 1
    fake = tempfile.mkdtemp(prefix="perturb_pep701_")
    try:
        with open(os.path.join(fake, "violating.py"), "w", encoding="utf-8") as f:
            f.write("x = f'a {d['k']}'\n")       # 外层单引号 + 内层单引号（3.12-only）
        rc = run_with("A5", env_extra={"PEP701_ROOT": fake})
        ok = rc != 0
        print(f"  [{'OK ' if ok else 'FAIL'}] M17 -> A5: 扫描根含违规源码时 exit={rc} "
              f"(期望 !=0)")
        passed += 1 if ok else 0
    finally:
        shutil.rmtree(fake, ignore_errors=True)

    # ---- 反向基线：原文件 + 同判据应绿 ----
    base_bad = []
    for target in BASELINE:
        total += 1
        rc = run_with(target)
        if rc == 0:
            passed += 1
        else:
            base_bad.append("%s(exit=%s)" % (target, rc))
    base_ok = not base_bad
    print(f"  [{'OK ' if base_ok else 'FAIL'}] 反向基线：原文件下 "
          f"{'/'.join(BASELINE)} 应均 exit=0"
          + ("" if base_ok else f" —— 异常项 {base_bad}"))

    print("\n" + "=" * 56)
    print(f"  资产校验扰动：{passed}/{total} 命中翻红；反向基线 {'OK' if base_ok else 'FAIL'}")
    print("=" * 56)
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    print("PERTURB PASS=%d FAIL=%d"
          % (passed, total - passed + (0 if base_ok else 1)))
    sys.exit(0 if (passed == total and base_ok) else 1)


if __name__ == "__main__":
    main()
