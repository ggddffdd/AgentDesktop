# -*- coding: utf-8 -*-
"""v4.236.0 运行数据落点扰动：反向照妖镜（改坏必红）。

三个 case 各自打在修复的一个不同侧面，且都只变异**源码**（不改判据本身）：

  V1 落点回退（ui_widgets.py）：粘贴图片改回 `Path(APP_DIR) / "temp" / "images"`
     → 判据 B「活跃源码无 APP_DIR 下写运行数据」+「ui_widgets 已不含 APP_DIR」翻红
  V2 归口 helper 失效（config.py）：ensure_workspace 落回 APP_DIR
     → 判据 A「粘贴图片落点不在 APP_DIR 内」翻红
       （行为级：真去算路径再判断是否落在 APP_DIR 里，不靠字符串匹配）
  V3 换一个文件再犯（diagnostic_export.py）：debug.log 读回 APP_DIR
     → 判据 B 翻红，证明静态扫描**会泛化**，不是只认 ui_widgets 那一处

为什么 V3 必须存在：只钉「出事的那一处」的判据，本质是拿案发现场当判据，
别处再犯照样绿（v4.164.0 迁移就漏了 ui_widgets 这一处，同一个坑）。

⚠️ V2 会让判据真的去 makedirs(APP_DIR/temp/images)；源码模式下 APP_DIR = 仓库根，
   会在工作树里留下空目录。脚本按「本轮新建才回收」清理，绝不删既有目录。

沿用已验证的「直接变异 + _perturb_guard 还原」模式。
原串均从真实字节取（脚本内 assert 唯一）。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
JUDGE = os.path.join(ROOT, "tests", "test_runtime_dir_hygiene_236.py")

F_UIW = os.path.join(ROOT, "ui_widgets.py")
F_CFG = os.path.join(ROOT, "config.py")
F_DIA = os.path.join(ROOT, "diagnostic_export.py")

# ---- V1：粘贴图片落点退回 APP_DIR ----
OLD_V1 = '                tmp_dir = Path(ensure_workspace("temp", "images"))'
NEW_V1 = '                tmp_dir = Path(APP_DIR) / "temp" / "images"'

# ---- V2：归口 helper 落回 APP_DIR（行为级回归）----
OLD_V2 = '''def ensure_workspace(*parts):
    """拼一个工作区下的路径，并确保该目录存在。"""
    p = os.path.join(WORKSPACE_DIR, *parts)'''
NEW_V2 = '''def ensure_workspace(*parts):
    """拼一个工作区下的路径，并确保该目录存在。"""
    p = os.path.join(APP_DIR, *parts)'''

# ---- V3：另一个文件再犯（验证静态扫描会泛化）----
OLD_V3 = '        log_path = _cfg.app_log_path()'
NEW_V3 = '        log_path = os.path.join(_cfg.APP_DIR, "debug.log")'

CASES = [
    ("V1 粘贴图片落点退回 APP_DIR（本体回归）", F_UIW, OLD_V1, NEW_V1,
     ["活跃源码无 APP_DIR 下写运行数据的写法",
      "ui_widgets.py 代码里已不含 APP_DIR"]),
    ("V2 ensure_workspace 落回 APP_DIR（行为级回归）", F_CFG, OLD_V2, NEW_V2,
     ["粘贴图片落点不在 APP_DIR 内"]),
    ("V3 diagnostic_export 读回 APP_DIR（验证扫描泛化）", F_DIA, OLD_V3, NEW_V3,
     ["活跃源码无 APP_DIR 下写运行数据的写法"]),
]

# V2 变异期间判据会真去建 APP_DIR/temp/images（源码模式=仓库根），回收用
_TEMP_IN_ROOT = os.path.join(ROOT, "temp")


def _reap_if_created(pre_existed):
    """只回收本轮新建的目录；既有目录一律不动（不越权删用户的东西）。"""
    if not pre_existed and os.path.isdir(_TEMP_IN_ROOT):
        shutil.rmtree(_TEMP_IN_ROOT, ignore_errors=True)


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_UIW, F_CFG, F_DIA])  # 三重还原 + 残留预检
    orig = {}
    for f in (F_UIW, F_CFG, F_DIA):
        with open(f, "r", encoding="utf-8") as fh:
            orig[f] = fh.read()

    pre = os.path.isdir(_TEMP_IN_ROOT)
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            assert src.count(old) == 1, \
                "old 串出现 %d 次，非唯一：%s" % (src.count(old), name)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            _reap_if_created(pre)
            # 判据应翻红：退出码非 0，且命中的是预期的那条断言
            failed = (rc != 0) and any(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src)
    finally:
        for f, s in orig.items():
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(s)  # 最终兜底还原
        _reap_if_created(pre)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
