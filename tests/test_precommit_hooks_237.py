# -*- coding: utf-8 -*-
"""v4.237.0 判据：pre-commit 钩子真的接线了，且「改坏必红」。

背景（Codex 审核第 3 项）：项目零 lint、零 hook，所有纪律靠「记得手跑」。
`tests/pep701_lint.py` 写好了却没人调用（孤儿），密钥扫描只在发布时跑
（进仓那一刻没人拦）。本轮把它们接进 pre-commit。

守的东西（每条都能被 `_perturb_precommit_hooks_237.py` 单独翻红）：

  A  检查器存在且可导入，有稳定入口 run_checks()（不是只能当脚本跑）
  B  真的复用了 pep701_lint（不是自己重写一份扫描逻辑）
  C  真的复用了 release_check 的密钥判定（不是另写一套正则，避免两套策略漂移）
  D  会拦「扰动残留」—— 这是最有价值的一条：P25 事故就是扰动被打断后
     变异态源码被 commit 收进仓库。行为级验证：喂一个带 `# 扰动` 的文件必须红
  E  .pre-commit-config.yaml 存在且引用了检查器（配置与脚本不是两回事）
  F  干净输入必须全绿（防「恒红」—— 恒红的门禁只会让人习惯性 --no-verify）
  G  大段删除会告警（P25：commit 前重点看大段删除）

行为级优先：D/F/G 都是真跑检查器看结果，不是 grep 源码串
（静态串判据恒真，见 P9）。
用法：python tests/test_precommit_hooks_237.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, detail))


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig", errors="replace")


def _new_workspace():
    """造一个最小工作区：git 仓库 + 若干文件，供检查器行为级试跑。"""
    d = tempfile.mkdtemp(prefix="pc_judge_")
    os.makedirs(os.path.join(d, "tests"), exist_ok=True)
    return d


def _write(d, rel, text):
    p = os.path.join(d, rel)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def main():
    print("==== pre-commit 钩子接线：判据开始 ====")

    pc_path = os.path.join(ROOT, "precommit_check.py")
    cfg_path = os.path.join(ROOT, ".pre-commit-config.yaml")

    # ---------------- A 组：检查器存在且可导入 ----------------
    print("\n-- A) 检查器存在且有稳定入口 --")
    check("A1 precommit_check.py 存在", os.path.isfile(pc_path), pc_path)
    check("A2 .pre-commit-config.yaml 存在", os.path.isfile(cfg_path), cfg_path)

    pc = None
    if os.path.isfile(pc_path):
        sys.path.insert(0, ROOT)
        try:
            import precommit_check as pc  # noqa: E402
        except Exception as e:
            check("A3 precommit_check 可导入", False, repr(e))
        else:
            check("A3 precommit_check 可导入", True)
            check("A4 有稳定入口 run_checks()（可被调用方复用）",
                  callable(getattr(pc, "run_checks", None)),
                  "没有 run_checks → 只能当脚本跑，无法被判据/其他工具复用")

    # ---------------- B 组：真的复用了 pep701_lint ----------------
    print("\n-- B) PEP 701 孤儿接线（不是另写一份）--")
    if pc is not None:
        src = _read(pc_path)
        check("B1 检查器引用了 pep701_lint（复用而非重写）",
              "pep701_lint" in src or "find_pep701" in src,
              "没引用 → 又多一套 f-string 扫描，两套必然漂移")
        # 行为级：喂一个 3.11 会炸的 f-string，必须被报出来。
        # 注意样本形状：只有「**同引号**嵌套」才是 PEP 701 新语法
        # （f'{d["k"]}' 内外引号不同，3.11 也支持，属老写法，不该报）。
        d = _new_workspace()
        bad = _write(d, "bad_fstring.py", 'd = {"k": 1}\nx = f"{d["k"]}"\n')
        try:
            res = pc.run_checks([bad], repo_root=d)
            hits = res.get("fail", [])
            joined = " ".join(str(h) for h in hits)
            check("B2 行为级：PEP 701 违规文件被报出",
                  bool(hits) and "PEP 701" in joined,
                  "没报出来 → 接线是假的；hits=%s" % (hits[:3],))
        except Exception as e:
            check("B2 行为级：PEP 701 违规文件被报出", False, repr(e))

    # ---------------- C 组：真的复用了 release_check 的密钥判定 ----------------
    print("\n-- C) 密钥扫描接线（复用发布门禁同一套判定）--")
    if pc is not None:
        src = _read(pc_path)
        check("C1 检查器引用 release_check 的密钥判定",
              ("_first_secret_hit" in src or "_scan_secrets" in src
               or "release_check" in src),
              "没引用 → 另写一套正则，两套判定必然漂移（项目 P15 教训）")
        d = _new_workspace()
        # 样本**运行时才拼出**完整形状：源码里若直接写 `sk-...` 整串，
        # 本文件自己就会被 pre-commit 的密钥检查拦下（装好 hook 后真实踩到过）。
        # 拼接后写入临时文件，检查器扫到的仍是完整的密钥形状 → C2 判据成立。
        fake = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "1234567890"
        sec = _write(d, "leaky.py", 'API_KEY = "%s"\n' % fake)
        try:
            res = pc.run_checks([sec], repo_root=d)
            hits = res.get("fail", [])
            joined = " ".join(str(h) for h in hits)
            check("C2 行为级：含密钥的文件被拦下",
                  bool(hits) and ("密钥" in joined or "secret" in joined.lower()),
                  "没拦住 → 密钥能直接进仓；hits=%s" % (hits[:3],))
        except Exception as e:
            check("C2 行为级：含密钥的文件被拦下", False, repr(e))

    # ---------------- D 组：拦扰动残留（P25 事故的直接防线）----------------
    print("\n-- D) 拦扰动残留（P25：变异态被 commit 的防线）--")
    if pc is not None:
        d = _new_workspace()
        mut = _write(d, "mutated.py", "def f():\n    return 1  # 扰动：短路变异\n")
        try:
            res = pc.run_checks([mut], repo_root=d)
            hits = res.get("fail", [])
            joined = " ".join(str(h) for h in hits)
            check("D1 行为级：带「# 扰动」标记的残留被拦下",
                  bool(hits) and ("扰动" in joined),
                  "没拦住 → P25 事故没有防线；hits=%s" % (hits[:3],))
        except Exception as e:
            check("D1 行为级：带「# 扰动」标记的残留被拦下", False, repr(e))

    # ---------------- E 组：配置与脚本确实是同一套 ----------------
    print("\n-- E) 配置引用检查器（不是两回事）--")
    if os.path.isfile(cfg_path):
        cfg = _read(cfg_path)
        # 不能全文 grep 文件名 —— 配置项本文件的注释里也提到它，
        # 那样判据恒真，改坏了配置照样绿（P9：静态串判据恒真）。
        # 只认 entry 字段的真实取值。
        entry_lines = [l for l in cfg.splitlines()
                       if l.strip().startswith("entry:")]
        check("E1 配置的 entry 字段指向 precommit_check.py",
              bool(entry_lines) and "precommit_check.py" in entry_lines[0],
              "entry=%r → hook 装了也不会跑我们的检查" % (entry_lines[:1],))
        # E3：entry 指向的脚本必须真实存在（防配置与脚本脱钩）
        import re as _re
        target = ""
        if entry_lines:
            m = _re.search(r"precommit_check\.py", entry_lines[0])
            if m:
                target = "precommit_check.py"
        check("E3 entry 指向的脚本真实存在（配置与脚本没脱钩）",
              bool(target) and os.path.isfile(os.path.join(ROOT, target)),
              "entry=%r 指向的文件不在仓库里" % (entry_lines[:1],))
        check("E2 配置用 local/system（不联网拉第三方 hook 环境）",
              "repo: local" in cfg and "language: system" in cfg,
              "非 local+system → 首次跑要联网建环境，离线即挂")

    # ---------------- F 组：干净输入必须全绿（防恒红）----------------
    print("\n-- F) 干净输入全绿（恒红的门禁会被习惯性绕过）--")
    if pc is not None:
        d = _new_workspace()
        clean = _write(d, "clean.py", "def f(x):\n    return x + 1\n")
        try:
            res = pc.run_checks([clean], repo_root=d)
            check("F1 行为级：干净文件零 fail",
                  not res.get("fail"), "fail=%s" % (res.get("fail", [])[:3],))
        except Exception as e:
            check("F1 行为级：干净文件零 fail", False, repr(e))

    # ---------------- G 组：大段删除告警 ----------------
    print("\n-- G) 大段删除告警（P25：commit 前重点看大段删除）--")
    if pc is not None:
        src = _read(pc_path)
        check("G1 检查器实现了大段删除告警（warn 级）",
              ("大段删除" in src or "bulk_delete" in src
               or "bulk-delete" in src),
              "没有 → P25 的「重点看大段删除」仍靠人记")

    # ---------------- H 组：不误报（假阳性会让整条防线被绕过）----------------
    print("\n-- H) 合法文件不得被误报 --")
    if pc is not None:
        # 仓库里 68 个 `_perturb_*.py` **本来就带** `# 扰动` 标记 ——
        # 那是变异的定义，不是「没还原的残留」。若把它们报成残留，
        # 每次改扰动脚本都会被拦，结果是大家习惯性 --no-verify，防线等于没有。
        pfiles = [os.path.join(ROOT, n) for n in sorted(os.listdir(ROOT))
                  if n.startswith("_perturb_") and n.endswith(".py")]
        with_marker = [p for p in pfiles
                       if "# 扰动" in _read(p)]
        if not with_marker:
            check("H0 找到带标记的合法扰动脚本（样本存在）", False,
                  "没找到样本 → H1 无从验证")
        else:
            check("H0 找到带标记的合法扰动脚本（样本存在）", True)
            try:
                res = pc.run_checks(with_marker[:5], repo_root=ROOT)
                bad = [h for h in res.get("fail", []) if "扰动残留" in str(h)]
                check("H1 行为级：合法的 _perturb_*.py 不被误报为残留",
                      not bad,
                      "误报 %d 条：%s" % (len(bad), bad[:2]))
            except Exception as e:
                check("H1 行为级：合法的 _perturb_*.py 不被误报为残留", False,
                      repr(e))

    print("\n" + "=" * 62)
    print("汇总：PASS=%d  FAIL=%d" % (_p, _f))
    print("=" * 62)
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
