# AGENTS.md

> 面向 AI agent 与贡献者的工程约定。首次在本仓库动手前请先读完。
> 深度文档：`DESIGN.md`（架构）· `CHANGELOG.md`（版本史）· `tests/README.md`（测试）· `LEGACY_SCRIPTS.md`（历史脚本）。
>
> ⚠️ 本仓库**公开**。禁止提交：真实密钥、`config.json`、本机绝对路径（如 `C:/Users/<用户名>/…`）、用户目录结构。

## 1. 仓库布局

| 类别 | 文件 / 目录 | 说明 |
|---|---|---|
| 入口 | `main.py` | |
| 配置 | `config.py` | `APP_VERSION` 是版本**唯一真源**（见 §6） |
| 核心 | `agent.py` / `ui.py` / `tools.py` | 三大件，均有体积红线 |
| Agent 侧 | `agent_*.py` `intent*.py` `task_state.py` `task_graph.py` | v4.216 从 `agent.py` 拆出 |
| UI 侧 | `ui_*.py` `theme_tokens.py` | `theme_tokens.py` 的 `THEME` 是配色唯一真源 |
| 安全 | `untrusted_boundary.py` `tool_verifiers_227.py` `risk.py` `permissions.py` | |
| 测试 | `tests/test_*.py`（131 个） | **自研 runner，不是 pytest** |
| 元测试 | 根目录 `_perturb_*.py`（64 个） | 「判据的疫苗」，见 §5 |
| 构建 / 门禁 | `build_safe.py` `release_check.py` `_scan_exe_secrets.py` | |
| 归档 | `_dev_history/` | 旧版核验脚本（`_verify_pyz_v*.py`） |

⚠️ **根目录存在 `nul` 文件**（Windows 保留设备名，历史上 `2>nul` 被误解析的产物）。
`os.walk` 之后调 `os.path.relpath` 会抛 `ValueError: path is on mount`。
遍历仓库时**先判 `nul` / `con` / `prn` / `aux` 再算路径**。

## 2. 环境确认（先做这一步）

**必须先确认解释器带 PySide6，否则会大面积假红**。
裸 `python` 曾解析到系统解释器（无 PySide6）→ 58 套件 + 87 扰动集体 `ModuleNotFoundError`，
看着像大面积真回归，实为环境错（P20）。

```bash
python -c "import PySide6, PyInstaller; print('ok')"   # 必须输出 ok
```

判读全量结果前先确认解释器：失败套件大面积 `PASS=0 FAIL=1` 且 detail 有 `No module named`
→ **先查环境，别查代码**。

Python 要求 3.10+；`requirements.txt` 全部 `==` 锁死（防构建漂移）。

## 3. 常用命令

| 目的 | 命令 |
|---|---|
| 跑全量回归 | `python tests/run_all.py` |
| 跑回归 + 元测试 | `python tests/run_all.py --with-perturb` |
| 只跑某套件 | `python tests/run_all.py --only <关键字>` |
| 打包 | `python build_safe.py` |
| 发布门禁（五道） | `python release_check.py` |
| 产物密钥扫描 | `python _scan_exe_secrets.py` |
| 提交前自检（手动跑） | `python precommit_check.py` |

**提交前自检（pre-commit）**：`git commit` 会自动跑 `precommit_check.py`，
拦四样东西 —— PEP 701（3.12 才合法的 f-string）、疑似密钥、
**扰动残留**（P25 事故的防线）、大段删除（warn，提醒人眼复核）。

装一次即可，两种装法任选：
```
python -m pip install pre-commit && pre-commit install   # 标准框架
python install_hooks.py                                   # 零依赖方案（不装包也能用）
```
`install_hooks.py` 不会覆盖已有 hook（本机 `.git/hooks/` 里有别的工具装的
post-commit），会先备份再追加。

`tests/run_all.py` 已内置 `QT_QPA_PLATFORM=offscreen`，并把测试日志改道到 `%TEMP%`，
不会污染真实 `debug.log`。

## 4. 改代码前必须知道的硬纪律

1. **凭据绝不硬编码** —— 只许环境变量或 `config.json`（已 gitignore）。
2. **行尾**：blob 全 LF、工作区混杂。改 CRLF 文件用 `newline=""`。提交前用
   `git diff --stat` 与 commit files changed 核对，确认无假 diff。
3. **改动必配判据 + 扰动**，验收标准是**哑弹清零**（不是脚本 PASS）。
4. **扰动统一走 `_perturb_guard.py`**：变异标记只能用 `# 扰动`；ROOT 一律 `__file__` 推导。
5. **改源码用「行号切片 + 断言邻近行」**，别抄原串做全文字符串替换 —— 已 4 次静默空替换（P10）。
   抄扰动原串必须**从真实字节取**（缩进差一级就 SKIP）；插段后**必须回头核原串**。
6. **撞红先怀疑判据，别改实现迁就**。判据自身 bug 占比极高：断言名不一致、取段越界、
   转发指针当终点、拿 docstring 当证据、期望串写反（P3~P8）。
7. **静态字符串判据是恒真的** —— 短路变异（`if False:`）和 `.pyc` 缓存都打不穿（P9 / P19）。
   凡是「有结构化结果就用、否则退回旧口径」的双口径结构，**必须有行为级判据 + 反向用例**。
8. **装饰性 gate 要删而不是留**；登记了却从不改变结论的条目（幽灵名）属同一类失效（P12 / P21）。
9. **非 .py 文件**一律 `open(..., newline="")` + 写完 `blob.decode("utf-8")` 自检；
   **禁用 bash heredoc 里的字节转义**（P16）。**含反引号的文本一律用编辑工具直接写，不走 bash**
   —— bash 会在 python 启动前就把反引号当命令执行，`blob.decode` 合法 ≠ 内容正确（P22）。
10. **不要手工写临时脚本改文件**（还原顺序易错）—— 全交给扰动脚本（P13）。
11. **规模红线**：`ui.py < 10000` 行、`agent.py < 2400` 行。撞红线**处理方式是外移，不是抬阈值**。
    抬阈值等于把红线挪到自己脚下。
12. **扰动批次后必须重跑全量回归，再 commit**（P25）。扰动靠「改坏源码 → 期望判据翻红 → 还原」工作，
    进程被打断时 `finally` / `atexit` 不执行，源码会**留在变异态**；而「整段删掉判定体」这类变异
    **不带 `# 扰动` 标记**，残留扫描扫不出来，照常 commit 就把变异永久收进仓库
    （v4.234.0 就是这样把 v4.233 断点 E 的判定体删掉的，直到 v4.235.0 跑全量回归才发现）。
    commit 前另需 `git diff` 眼看一遍，**重点看有大段删除的文件**。
13. **外移代码要搬三处，不能只搬本体**（P26）：判据的取材路径、扰动脚本的取材路径、
    进包核验的钉子。只改判据不改扰动 → 变异 old 串失配、变异没生效，
    但判据因为「看错了文件」照样红 → 扰动报 HIT，**假命中比哑弹更危险**。
    改漂移判据时顺手加一条接线断言（如「主类继承该 mixin」），
    否则只改路径等于把判据改松了。外移后还要复核 `小臭玩AI.spec` 的 hiddenimports。

## 5. 什么是「做完了」

一次改动算完成，必须依次满足：

1. **judge-first（先红后绿）**：先写判据并确认它在改动前**真的会红**，再改实现让它转绿。
   没见过红的判据等于没有判据。
2. **扰动零哑弹**：写 `_perturb_*.py` 改坏源码 → 判据必须翻红 → 还原。
   输出 `PERTURB PASS=<命中> FAIL=<哑弹>`，**FAIL 必须为 0**。SKIP / MISS 就是哑弹。
3. **全量回归**：`tests/run_all.py` 全绿（含 `test_frozen_smoke` 版本一致性）。
4. **发布门禁**：`release_check.py` 五道（语法 / 回归 / 密钥安全 / 版本一致 / 打包卫生）
   + `_scan_exe_secrets.py` 0 泄露。
5. **本地 commit**（**默认不 push**，等人发话）。

`git push` 报 `remote: Internal Server Error` 时**先重试，别动本地**（P23：实证是 GitHub 侧
仓库级写通道临时故障，换 token / 拆提交 / `--force` 全是错方向）。起「间隔 150s 重试至多 4 次」
后台循环即可。**成败只认 sha 比对**（`git ls-remote` vs `git rev-parse HEAD`），
不看 push 自己的输出。

## 6. 版本单一真源

`config.py` 的 `APP_VERSION` 是**唯一真源**。所有模块级 `VERSION = "..."` 必须与之相等 ——
`release_check.py` 会逐一核对，不一致即 fail。

历史漂移（已修）：`agent_task_mixin` / `intent` / `task_state` 曾停在 `v4.225.0`，
`intent_guard` 曾停在 `v4.168.0`。**bump 版本时必须同步全部模块级 `VERSION`**，否则门禁拦下。

## 7. Review guidelines

- 任何新工具必须登记风险等级并能被 `tool_contract` 枚举到；登记了却不被消耗 = 幽灵名，判红。
- 权限相关改动：**禁止再造第二个策略**。`_permission_gate` 的按风险分类口径必须唯一；
  任何 `allowed=True` 的私有兜底决策都会短路整个闸门（P15）。
- 三个重叠的权限入口（`system_control_tools.py` / `software_control_tools.py` /
  `tools._dangerous_command_check`）**堵一处等于没堵**。
- 不可信内容（工具产出、技能提示词）包装前必须中和伪造闭合标签。
- 改动 `task_graph.run` 的 `continue` / `break` 分支时，`all_done` 终态集合必须含 `failed`，
  否则整图死循环（P24）。
- 判据 detail 里出现**两边都是 0**（如 `okc=0 仍用其他=0`）→ 是扫错了不是没接线，先怀疑扫描范围。

## 8. 踩坑索引

| 编号 | 一句话 |
|---|---|
| P1 / P20 | 沙箱删除配额与裸 python → **环境性假红**，重跑即绿，先查环境 |
| P2 | PYZ 核验：入口 `main` 在 PKG 不在 PYZ；解 PYZ 必传 `start_offset`；变量名 / 属性名不在 `co_consts` |
| P3~P8 | 判据自身的坑（断言名不一致 / 转发指针 / 取段越界 / docstring 当证据 / 期望串写反） |
| P9 / P19 | 静态串判据恒真；`__pycache__` 让「变异等于没发生」 |
| P10 / P13 / P16 / P22 | 改文件的坑：静默空替换 / 手工脚本还原错 / heredoc 转义 / bash 吃反引号 |
| P12 / P21 | 装饰性 gate 与幽灵名 |
| P15 | `allowed=True` 兜底短路整个权限闸门 |
| P17 / P18 | 边界中和正则要按真实字符集卡；注释剔除按列挖不整行删 |
| P23 | push 500 先重试，别动本地 |
| P24 | `continue` 分支必须让 `all_done` 收敛（含 `failed`） |
| P25 | 被打断的扰动会把**变异态源码**留在工作区并被 commit 收走 —— 整段删除型变异不留 `# 扰动` 标记，扫不出来 |
| P26 | 外移代码后，**判据取材路径 + 扰动取材路径 + 进包核验钉子**三处要一起搬；只改判据不改扰动会造出「假命中」 |
