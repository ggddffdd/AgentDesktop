# -*- coding: utf-8 -*-
"""提交前自检（pre-commit 的执行体）—— 纯标准库，零第三方依赖。

**为什么要有它**：Codex 审核第 3 项指出本项目零 lint、零 hook，所有纪律靠
「记得手跑」。具体三个洞：
  ① `tests/pep701_lint.py` 写好了却没人调用（孤儿）—— 3.12 f-string 语法
     混进仓库，等 3.10/3.11 上跑才炸；
  ② 密钥扫描只在**发布**时跑 —— 进仓那一刻没人拦；
  ③ P25 事故（扰动被打断 → 变异态源码被 commit 收进仓库）**没有任何防线**，
     只能靠 commit 前肉眼 `git diff`。
本脚本把这三件事变成「不跑就红」的机器检查。

**设计约定**：
- 判定逻辑**一律复用项目既有的单点真源**，不另写一套：
    · PEP 701 → `tests/pep701_lint.find_pep701`
    · 密钥    → `release_check._first_secret_hit`（与发布门禁同一套正则）
    · 扰动残留 → `_perturb_guard._comment_spans`（tokenize 认 COMMENT）
  两套策略必然漂移，这是项目 P15 的教训。
- 只做**快检**：扫 staged 文件 + 几项轻量静态判定，不跑全量回归
  （全量回归留给提交后/CI，pre-commit 慢到几十秒就会被 `--no-verify` 绕过）。
- 大段删除只**告警不阻断**：它是「提醒你看一眼」，不是硬规则。

用法：
    python precommit_check.py              # 自动取 staged 文件
    python precommit_check.py a.py b.py    # 指定文件（判据用）
退出码：0=通过；1=有 fail；2=用法/环境错误
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# tests/ 不在包路径上（pep701_lint 住在那里）
_TESTS = os.path.join(HERE, "tests")
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

# 判为「大段删除」的行数阈值：P25 的经验值是「一眼能看出不是改一行」的量级。
BULK_DELETE_LINES = 50


def _staged_files():
    """返回已暂存（新增/修改/重命名）且仍存在的文件相对路径。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=HERE, capture_output=True, text=True, encoding="utf-8",
            errors="replace")
    except Exception:
        return []
    if out.returncode != 0:
        return []
    files = []
    for line in out.stdout.splitlines():
        p = line.strip()
        if not p:
            continue
        fp = os.path.join(HERE, p)
        if os.path.isfile(fp):
            files.append(fp)
    return files


def _staged_numstat():
    """返回 [(增, 删, 路径)]，用于大段删除告警。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--numstat"],
            cwd=HERE, capture_output=True, text=True, encoding="utf-8",
            errors="replace")
    except Exception:
        return []
    rows = []
    if out.returncode != 0:
        return rows
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            add = int(parts[0]) if parts[0].strip().isdigit() else -1
            dele = int(parts[1]) if parts[1].strip().isdigit() else -1
        except ValueError:
            continue
        rows.append((add, dele, parts[2].strip()))
    return rows


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _check_pep701(paths, fail):
    """PEP 701（3.12+ f-string）检测 —— 复用孤儿模块，不另写扫描器。"""
    try:
        from pep701_lint import find_pep701
    except Exception as e:  # 模块丢了要让钩子红，不是静默放行
        fail.append("PEP 701 检查不可用（pep701_lint 导入失败：%s）" % e)
        return
    for fp in paths:
        if not fp.endswith(".py"):
            continue
        try:
            src = _read_bytes(fp).decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        for ln, why in find_pep701(src):
            fail.append("PEP 701 语法（3.10/3.11 会 SyntaxError）"
                        " %s:%d —— %s" % (_rel(fp), ln, why))


def _check_secrets(paths, fail):
    """密钥扫描 —— 用发布门禁同一套判定，避免两套正则漂移。"""
    try:
        from release_check import _first_secret_hit
    except Exception as e:
        fail.append("密钥检查不可用（release_check 导入失败：%s）" % e)
        return
    for fp in paths:
        try:
            data = _read_bytes(fp)
        except OSError:
            continue
        hit = _first_secret_hit(data)
        if hit:
            fail.append("疑似密钥进仓 %s —— 命中规则「%s」" % (_rel(fp), hit[0]))


def _check_leftover(paths, fail):
    """扰动残留 —— 变异标记识别复用护栏的 tokenize 判定（P4）。

    只认「代码行末尾的注释」：说明文档里的标记字样不算残留，
    否则一个假阳性就能让整条防线瘫痪（护栏实测踩过）。
    """
    try:
        from _perturb_guard import (_comment_spans, _LEFT_MARKERS,
                                    _SKIP_PREFIX, find_leftovers)
    except Exception as e:
        fail.append("扰动残留检查不可用（_perturb_guard 导入失败：%s）" % e)
        return

    # ① 仓库内的文件：一律走护栏的 find_leftovers()，不自己扫。
    #    护栏会跳过 `_perturb_*.py`（68 个，它们**本来就带** `# 扰动` 标记，
    #    那是变异的定义而非残留）。自己扫会把合法文件全报成残留 —— 一个
    #    假阳性就能让整条防线被 `--no-verify` 习惯性绕过（护栏实测踩过同类）。
    inside = [p for p in paths if os.path.abspath(p).startswith(HERE)]
    if inside:
        try:
            rel_set = {os.path.relpath(os.path.abspath(p), HERE)
                       for p in inside}
            for rel, ln, txt in find_leftovers():
                if rel in rel_set:
                    fail.append("疑似扰动残留（未还原的变异） %s:%d —— %s"
                                % (rel, ln, txt[:80]))
        except Exception as e:
            fail.append("扰动残留检查异常：%s" % e)

    # ② 仓库外的文件（判据喂的临时样本）：本地近似，同样排除 `_perturb_` 前缀
    for fp in paths:
        if os.path.abspath(fp).startswith(HERE):
            continue
        if not fp.endswith(".py"):
            continue
        if os.path.basename(fp).startswith(_SKIP_PREFIX):
            continue
        spans = _comment_spans(fp)
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        if spans is None:
            # 词法分析失败（源码被改到语法不通）：按行近似，跳过整行注释
            for i, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if any(m in line for m in _LEFT_MARKERS):
                    fail.append("疑似扰动残留（未还原的变异） %s:%d —— %s"
                                % (_rel(fp), i, s[:80]))
            continue
        for row, (col, text) in spans.items():
            if not any(m in text for m in _LEFT_MARKERS):
                continue
            line = lines[row - 1] if 0 < row <= len(lines) else ""
            # 注释前必须有真实代码 —— 整行注释是说明文字，不是变异残留
            if line[:col].strip():
                fail.append("疑似扰动残留（未还原的变异） %s:%d —— %s"
                            % (_rel(fp), row, line.strip()[:80]))


def _check_bulk_delete(warn):
    """大段删除告警（warn 级）：P25 要求 commit 前重点看大段删除。

    整段删除型变异**不带 `# 扰动` 标记**（标记只加在短路型变异上），
    所以残留扫描抓不到 —— 只能靠这条告警提醒人看一眼。
    """
    for add, dele, path in _staged_numstat():
        if dele >= BULK_DELETE_LINES:
            warn.append("大段删除（%d 行）请人眼复核：%s（增删 %d/%d）"
                        % (dele, path, add, dele))


def _rel(fp):
    try:
        return os.path.relpath(fp, HERE)
    except ValueError:
        return fp


def run_checks(paths, repo_root=None, staged_diff=None):
    """跑全部检查。返回 {"fail": [...], "warn": [...]}。

    paths 为空表示「没有要检查的文件」（例如只删了文件）→ 只跑不依赖文件的项。
    """
    paths = [p for p in (paths or []) if os.path.isfile(p)]
    fail, warn = [], []
    _check_pep701(paths, fail)
    _check_secrets(paths, fail)
    _check_leftover(paths, fail)
    _check_bulk_delete(warn)
    return {"fail": fail, "warn": warn, "checked": len(paths)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    paths = [os.path.abspath(a) for a in argv if not a.startswith("-")]
    if not paths:
        paths = _staged_files()

    res = run_checks(paths, repo_root=HERE)
    fail, warn = res["fail"], res["warn"]

    print("=" * 62)
    print("提交前自检（pre-commit）  已检查 %d 个文件" % res["checked"])
    print("=" * 62)
    for w in warn:
        print("  [WARN] %s" % w)
    for f in fail:
        print("  [FAIL] %s" % f)
    if not fail and not warn:
        print("  [OK  ] 全部通过（PEP 701 / 密钥 / 扰动残留 / 大段删除）")

    print("-" * 62)
    print("PRECOMMIT PASS=%d FAIL=%d WARN=%d"
          % (1 if not fail else 0, len(fail), len(warn)))
    if fail:
        print("\n提交被拦下。若确属误报，用 git commit --no-verify 绕过，"
              "但请先确认上面每一条都不是真密钥/真残留。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
