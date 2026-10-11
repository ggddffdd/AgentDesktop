# -*- coding: utf-8 -*-
r"""v4.259.0 扰动脚本：军团装技能判据对齐真正加载器（P1-8）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_skill_install_gate_259.py，
确认判据必红（改坏必红、零哑弹）。SKIP/MISS = 哑弹，判据白写。

V1  判据恒放行（return True）      → 回到修复前「校验永远放行」，空白技能照装（G1/G3/G5）
V2  调用点退回 `if not info`       → 效果同 V1，但改的是**接线**不是判据（防只改一半）
V3  判据恒拒收（return False）     → 过度拒收，正常技能装不了（G4 误伤）
V4  回滚不删盘                     → 只返回失败不 remove，空壳继续占位挡重试（G1-2/G2）
V5  判据改用本模块宽松版           → ★同族根因：校验器又用自己的宽松版（G1/G5）
V6  fail-open 反转（异常时 False） → 加载器抽风就装不了技能（G7）
V7  slug 传空                      → 无 frontmatter 纯正文技能被误拒（G4 误伤）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
LG = os.path.join(ROOT, "legion.py")
JUDGE = os.path.join(ROOT, "tests", "test_skill_install_gate_259.py")

# _skill_loadable 的判据体（唯一，用于整块替换）
BODY = (
    "    try:\n"
    "        from skill_loader import _parse_skill_md as _loader_parse\n"
    "    except Exception:\n"
    "        return True\n"
    "    try:\n"
    "        return _loader_parse(md_path, slug) is not None\n"
    "    except Exception:\n"
    "        return True"
)

CASES = [
    ("V1 判据恒放行", LG, BODY,
     "    return True  # 扰动：判据恒放行（回到修复前）"),

    ("V2 调用点退回只判 info", LG,
     "    if not info or not _skill_loadable(dst, target_slug):",
     "    if not info:  # 扰动：接线退回，判据白写"),

    ("V3 判据恒拒收", LG, BODY,
     "    return False  # 扰动：判据恒拒收"),

    ("V4 回滚不删盘", LG,
     "        try:\n"
     "            os.remove(dst)\n"
     "            os.rmdir(dst_dir)\n"
     "        except Exception:\n"
     "            pass\n"
     "        return False, (\"装是装进去了，但这份 SKILL.md 加载不出来\"",
     "        return False, (\"扰动：不删盘，空壳继续占位\"\n"
     "                       \"（内容为空或无法解析）\""),

    ("V5 判据改用本模块宽松版", LG, BODY,
     "    try:\n"
     "        _loader_parse = _parse_skill_md  # 扰动：又用自己的宽松版（同族根因）\n"
     "    except Exception:\n"
     "        return True\n"
     "    try:\n"
     "        return _loader_parse(md_path) is not None\n"
     "    except Exception:\n"
     "        return True"),

    ("V6 fail-open 反转", LG,
     "        return _loader_parse(md_path, slug) is not None\n"
     "    except Exception:\n"
     "        return True",
     "        return _loader_parse(md_path, slug) is not None\n"
     "    except Exception:\n"
     "        return False  # 扰动：加载器抽风就装不了技能"),

    ("V7 slug 传空", LG,
     "    if not info or not _skill_loadable(dst, target_slug):",
     "    # 扰动：slug 传空，靠 slug 兜底 name 的技能会被误拒\n"
     "    if not info or not _skill_loadable(dst, \"\"):"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [LG]
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
            if cnt != 1:
                misses.append(name)
                print("SKIP %s: old 串出现 %d 次，非唯一（无法安全替换）" % (name, cnt))
                continue
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new, 1))
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
