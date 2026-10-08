# -*- coding: utf-8 -*-
"""零依赖 git hook 安装器 —— 没装 pre-commit 框架时的等效方案。

**为什么需要它**：`.pre-commit-config.yaml` 要先生装 `pre-commit`（联网 pip）。
本项目的检查器 `precommit_check.py` 是纯标准库自研的，本就不需要框架；
这个安装器把同一个检查器直接挂到 `.git/hooks/pre-commit` 上，
**不联网、不装包、立刻生效**。

两种装法效果一致，选其一即可：
    python -m pip install pre-commit && pre-commit install   # 标准方案
    python install_hooks.py                                  # 零依赖方案

**安全约定**：
- 若目标 hook 已存在（比如别的工具装的），**先备份再追加调用**，不覆盖。
  本机 `.git/hooks/` 里已有 Qoder 装的 post-commit/post-checkout，
  直接覆盖会弄坏别人的东西。
- hook 脚本里对检查器用「存在才跑」，检查器被删了不会让提交卡死。
"""
import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_NAME = "pre-commit"
MARKER = "# BEGIN 小臭玩AI pre-commit"


def _git_dir():
    try:
        out = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=HERE,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
    except Exception:
        return None
    if out.returncode != 0:
        return None
    p = out.stdout.strip()
    return p if os.path.isabs(p) else os.path.join(HERE, p)


def _hook_body():
    # git for Windows 自带 sh，钩子按 POSIX sh 写即可跨 Win/macOS/Linux。
    return """{m}
# 提交前自检：PEP 701 / 密钥 / 扰动残留 / 大段删除（详见 precommit_check.py）
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -f "$repo_root/precommit_check.py" ]; then
  python "$repo_root/precommit_check.py" || exit 1
fi
# END 小臭玩AI pre-commit
""".format(m=MARKER)


def install():
    gd = _git_dir()
    if not gd:
        print("✗ 不在 git 仓库里（或 git 不可用），跳过安装")
        return 1
    hooks = os.path.join(gd, "hooks")
    os.makedirs(hooks, exist_ok=True)
    target = os.path.join(hooks, HOOK_NAME)

    existing = ""
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            existing = f.read()
        if MARKER in existing:
            print("✓ 已安装过（%s），无需重复安装" % HOOK_NAME)
            return 0
        # 已有别人的 hook：备份后追加，绝不覆盖
        bak = target + ".bak_xiaochou"
        n = 1
        while os.path.exists(bak):
            bak = "%s.bak_xiaochou%d" % (target, n)
            n += 1
        with open(bak, "w", encoding="utf-8") as f:
            f.write(existing)
        print("⚠ 目标 hook 已存在，原内容已备份 → %s" % os.path.basename(bak))
        body = existing.rstrip("\n") + "\n\n" + _hook_body()
    else:
        body = "#!/bin/sh\n" + _hook_body()

    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    try:
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP
                 | stat.S_IXOTH)
    except Exception:
        pass
    print("✓ 已安装 %s → %s" % (HOOK_NAME, target))
    print("  检查器：precommit_check.py（纯标准库，零依赖）")
    return 0


def main():
    print("=" * 62)
    print("小臭玩AI git hook 安装器（零依赖方案）")
    print("=" * 62)
    rc = install()
    if rc == 0:
        print("\n验证：git commit 时应看到「提交前自检」输出；"
              "临时跳过用 git commit --no-verify。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
