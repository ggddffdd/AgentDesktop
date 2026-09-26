# tests/ — 回归测试

一条命令跑完全部：

```bash
python tests/run_all.py
```

其他用法：

```bash
python tests/run_all.py --list          # 只看有哪些套件，不执行
python tests/run_all.py --only bridge   # 只跑文件名含 bridge 的套件
python tests/run_all.py --verbose       # 失败时打印该套件完整输出
```

退出码：`0` = 全部通过，`1` = 有失败。可直接挂在构建前或提交前检查。

## 现有套件

| 套件 | 覆盖内容 | 依赖 |
|------|----------|------|
| `test_bridge_security.py` | 浏览器桥接：认证收紧（`?token=` 必须被拒）、请求加固（负数 `Content-Length`→400 / 超限→413）、诚实投递回执（202 未确认 / 200 delivered=true / 200 delivered=false+detail） | 纯标准库 |
| `test_task_status.py` | 任务状态总线：四态流转、失败原因脱敏与截断、环形缓冲上限、多任务不串台、订阅通知、重试钩子、`track` 上下文管理器、多线程并发 | 纯标准库 |
| `test_task_status_ui.py` | 状态栏任务条：五种界面状态渲染、按钮显隐、无重试钩子时的诚实提示、清除历史、`+N` 计数 | 需 PySide6（离屏） |
| `test_task_wiring.py` | **接线契约**：逐个断言 6 个生产者都有 begin + 收口（防「任务永远卡在处理中」）、网页抓取回调的 `nonlocal` 守卫（防 `UnboundLocalError`）、无孤儿登记、界面已订阅/退订 | 纯标准库（只读源码） |
| `test_cdp_profile_path.py` | **调试浏览器 profile 路径**：必须落在 `%LOCALAPPDATA%`；`_default_cdp_profile` 不接受任何参数（调用方无法把它拽回 app_dir）；源码无 `app_dir` 兜底分支；回退路径也不指向 app_dir | 纯标准库 |
| `test_workspace_routing.py` | **运行数据归口**：用户数据类目录必须落在 `WORKSPACE_DIR`；资源类（icon / 内置 skills）仍锚 `APP_DIR`；越界写入被拒；含真实读写验证 | 纯标准库 |
| `test_legion_resume_fill.py` | **军团续跑回填**：只回填 `resume_from` 之前的历史波；未来波不污染（曾因条件写反而整段空转） | 纯标准库（只读源码） |
| `test_agent_node_failure.py` | **成员失败不得伪装成功**：模型调用异常必须抛 `AgentExecutionError`（不是 `break`）、跑满轮次无正文要标 `incomplete`、`state` 写入标记 | 纯标准库（只读源码） |
| `test_director_recover.py` | **导演台紧急收口**：回调异常后解锁运行锁、阶段转 ERROR（不覆盖 DONE）、失败回执不污染 cancelled、幂等 | 需 PySide6（离屏） |
| `test_cancel_token.py` | **取消令牌**：一次性幂等、父链继承（子取消不影响父）、阶段追踪与兜底、回调（已取消后注册立即触发 / 回调内再操作不自死锁）、并发 cancel 只有一个赢 | 纯标准库 |
| `test_legion_permissions.py` | **军团权限闸门**：成员默认只读、执行类需本波授权、写入限工作区、外发需白名单+授权双条件、危险命令底线、围栏先于信任、审计（参数摘要哈希 + 脱敏 + 并发不串行损坏） | 纯标准库 |
| `test_task_graph_cancel.py` | **任务图取消**：派发前取消则 executor 零调用、执行中取消则立刻停手、终态区分（before_start / during_model_call / after_tool_call）、已完成者保持 completed、不传令牌时行为不变 | 纯标准库 |
| `test_director_cancel_retry.py` | **导演台停止后可重试**：`cancel_job` 只终结当前一轮、`begin_job` 换发令牌即复位（核心）、外部置旧标志也能复位、后台任务收口五步齐备、回调带项目令牌校验 | 纯标准库（只读源码） |
| `regression.py` | 记忆层（钉住召回 / 关键词召回 / 冲突合并 / 中文搜索 / 备份自愈）、上下文隔离、记忆加密 | 纯标准库 |

合计 **410 项断言**，约 15 秒跑完。

> `test_task_wiring.py`、`test_legion_resume_fill.py`、`test_agent_node_failure.py`、
> `test_director_cancel_retry.py` 都含**静态契约测试**（只读源码不执行）。它的价值在于
> 挡住那类「不报错、只静默劣化」的回归 —— 例如有人重构时删掉了某个 `task_status.fail(...)`，
> 任务就会永远停在「处理中」，比没有状态条还糟。
>
> ⚠️ **一条硬边界（实测踩过）**：**互为冗余的双保险**（A 挂了还有 B 兜底）时，
> **行为测试无法证明某一层还在** —— 删掉 A，B 会兜住，所有行为断言仍全绿。
> 这类地方**必须补源码契约断言**锁住结构，否则"测过了"是假象。
> 判断法：注入失效后测试仍绿 → 那是**测试缺口**，先补测试再重验。

## 新增套件的约定

1. 文件名用 `test_*.py`，放在 `tests/` 下，会被 `run_all.py` 自动收集。
2. 必须能独立运行：`python tests/你的套件.py`，**不依赖 pytest**。
3. 输出里须含 `PASS=<n>` 和 `FAIL=<n>`（便于汇总），并以非 0 退出码表示失败。
   最简单的写法：

   ```python
   PASS = FAIL = 0
   def check(name, cond, extra=""):
       global PASS, FAIL
       if cond:
           PASS += 1; print(f"  [PASS] {name}")
       else:
           FAIL += 1; print(f"  [FAIL] {name}  {extra}")
   # … 跑完后
   print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
   sys.exit(1 if FAIL else 0)
   ```

4. **每个套件在独立子进程中运行**。多个套件会改写模块级全局
   （如 `memory_store._configure`、`browser_bridge._token`、`DELIVERY_WAIT`），
   同进程跑会互相污染，因此 `run_all.py` 一律起子进程隔离。
5. 涉及 Qt 的套件不用自己处理平台：`run_all.py` 已预设 `QT_QPA_PLATFORM=offscreen`。
6. 涉及网络端口的套件请用测试专用端口（如 19100+），**不要占用桌面端在用的 9100**。
7. 测试数据请写入临时目录（`tempfile.mkdtemp()`），**不要碰 `~/Documents/小臭玩AI/`**。

## 建议后续补齐的覆盖面

当前尚未覆盖、按优先级建议逐步补：

- 草稿保护（外部输入不得覆盖输入框草稿）
- 配置迁移与模型切换
- 文件 / 图片输入链路
- Agent 任务完成后的 UI 解锁

> 注：根目录下另有若干历史校验脚本（`test_v4102_*.py` 等），其中不少含本地绝对路径，
> 故仍由 `.gitignore` 的 `test_*.py` 规则排除、不入库。
> 如需纳入正式回归，请先去掉其中的本地路径依赖再移入本目录。
