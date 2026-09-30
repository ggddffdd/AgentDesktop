# 全项目架构级审查报告 — v4.186.0（2026-09-30）

> 只读审查，全程未改一行源码。六路并行 + AST except 卫生量化。
> 每条发现均带 `文件:行号` + 代码片段证据；「核验方式」区分**已读源码/已跑探针**与**静态推断**。

## 0. 审查范围与方法

| 路 | 模块 | 方式 |
|---|---|---|
| A | agent.py / tools.py / software_control_tools.py / tool_defs.py | 全量通读 + 5 个只读探针 |
| B | permissions.py / risk.py / intent_guard.py / route_judge.py | 全文 + 12 条真实语句探针 |
| C | ui.py（9930-10358 `_agent_call` 等区间） | 定点精读 + intent_guard 本地实测 |
| D | legion.py / legion_worker.py / legion_ui.py / legion_chat.py / legion_status_widget.py | grep + 定点精读 + 自带测试 27/27 |
| E | video_pipeline.py / director_panel.py / digital_twin_panel.py / memory_store.py / voice.py | 全文通读 + 边界契约抽检 |
| F | main.py / config.py / build_safe.py / release_check.py / tests/run_all.py | 全文通读 + 实跑验证 |

**except 卫生量化**（`_audit_except_hygiene.py`，纯 AST，utf-8-sig）：
裸 `except:` = **0**（最危险轴干净）；pass_only = 634（ui.py 123 / director_panel 49 / video_pipeline 30 / legion 29）；
log_only = 363；parse_err = 1（`_dev_history/_set_brave_key.py` 历史坏脚本，非生产，忽略）。
注：备份目录 `backup_20260926_001/` 未排除，统计含其 90+16 处，生产面实际更低。

**统计：P0 × 1 / P1 × 11 / P2 × 17 / P3 × 10**

---

## 1. P0（安全缺口，最高优先）

### P0-1 `run_python` 缺 ③-A 系统级毁灭硬拦截——会话信任后零确认执行 format/shutdown/rm -rf
`tools.py:1557`（`_dangerous_command_check` 定义）→ 唯一调用点 `tools.py:1698`（仅 `tool_run_command` 内）；
`tools.py:1952-2043`（`tool_run_python` 全函数无 deny 调用）

```python
# tools.py:1697-1700（仅 run_command 有）
_deny = _dangerous_command_check(command)
if _deny:
    return _deny
```

**触发链**：用户点过一次「本次会话全部信任」→ 模型对同一意图选用 `run_python`（代码任务天然更易选 python）→
`decide()` 走会话信任分支 `allowed=True, needs_user=False`（permissions.py:208-209）→ `subprocess.Popen` 直接执行
`os.system('format d:')`——**零确认、零 deny**。对照：同样信任下 `run_command("format d:")` 被 1698 硬拦。
即"即便确认被绕过也拦得住"的底层死墙在 Python 路径整体缺失，而 run_python 能力 superset 于 run_command。
**核验**：grep `_dangerous_command_check` 全仓仅 1 处调用；探针 `command_danger_level("format d:") → ''`；
探针 `decide('run_python', {code: format d:}) → tier:manual`（连高危标题都不显示）。

---

## 2. P1（功能/数据风险）

> ✅ **修复状态（2026-09-30，P0+全部 P1 已闭环）**：P0-1 与 P1-1～P1-11 共 12 项
> 已全部修复并验证。分 5 批落地（安全核心 → 数据防丢 → ui 决策链 → 健壮性 → 后置质疑句），
> 每批自带探针/回归；全量回归 55 套件 PASS=2141 FAIL=0。
> 连带修复：① `test_audio_regress` 夹具失效（硬编码个人产物视频早已被清理，
> 套件静默失败数周）→ 改为 ffmpeg 自生成夹具 + 失败退出码非零；
> ② `test_intent_routing` 写死 `if _guard_block:` 断言过时 → 同步 P1-3 新条件；
> ③ `test_director_manifest` 假片段 8 字节被 P1-9 体积守卫正确拒绝 → 夹具补足体积。
> 新增测试：test_p0p1_runcmd_fix_186 / test_dataloss_fix_186 / test_uidecision_fix_186 /
> test_robust_fix_186 / test_postref_fix_186（+此前的 test_no_stall_186）。

| # | 标题 | 位置 | 一句话 |
|---|---|---|---|
| P1-1 | decide() 非 dict args 裸抛逃出 run() | permissions.py:184；agent.py:1706/2059 无 try | 模型发 `arguments:"[1,2]"` → AttributeError → `done.emit()` 不触发，任务静默中断，靠 120s 看门狗恢复 |
| P1-2 | intent_guard 后置质疑句漏拦 | intent_guard.py:378/128/145 | 「生成视频这件事你怎么看」三层全放行 → 模型可能真去生视频（探针实锤） |
| P1-3 | force_tool 被 `_guard_block` 静默反盖 | ui.py:10045 vs agent.py:1275 | 「自检一下生成图片的功能」强制 sys_info 被 guard 反手盖成 `tool_choice="none"`，且不像其他分支记日志 |
| P1-4 | nudge 文本触发付费升舱 | ui.py:9725-9731（无 `_internal` 跳过） | 新注入的 nudge 含「写文件」→ `_needs_tool_intent=True` → 每次 nudge 重试 = 一次付费调用 + 注定被禁的工具轮 |
| P1-5 | `_internal` 键仍回传 API | ui.py:10092 vs 9973-9976（声明剥离后又注入） | 对严格校验未知字段的通道可能 400（设计违反，offline 无法实证各通道） |
| P1-6 | legion_chat 读史失败即清零+回写覆盖 | legion_chat.py:641/612/537 | 读失败（Defender 瞬时锁）≠ 内容损坏，但同一 except 清空 history → 攒几条自动回写空 → 对话记录永久丢失 |
| P1-7 | memory_store 读败返回空串→append 清空全部记忆 | memory_store.py:146/793 | 有 snapshot 兜底可回滚但**用户侧零感知**，函数还返回「已写入」 |
| P1-8 | 数字人面板无停止入口 | digital_twin_panel.py:993/1262 | `cancel()` 死代码（零调用）且 `tool_video_gen` 未传 cancel_token，长任务只能烧额度等超时 |
| P1-9 | 视频产物只验存在不验大小 | video_pipeline.py:3300 / tools.py:2773 / core_agnes.py:490 | 0 字节/半截 mp4 通过 `isfile` 校验 → manifest done → 进 merge → 当成品交付 |
| P1-10 | build_safe `_sync_to_dist()` 返回值丢弃 | build_safe.py:358 | exe 被占用 robocopy rc≥8 时仍打印 `BUILD_EXIT=0`（v4.186 构建 rc=3 未触发，机制隐患在） |
| P1-11 | 无单实例锁 + run_all 假绿两口子 | main.py:368；tests/run_all.py:34/63/130 | 双开抢端口共写 config；套件文件丢失静默不收集+退出码 0；rc=0 但 0 断言的套件显示 `[OK] PASS=0` |

（P2/P3 完整 27 条见下）

---

## 3. P2 完整清单（17 条）

| # | 标题 | 位置 |
|---|---|---|
| P2-1 | `_taskkill_ok` 中文 Windows 假阴性（输出「成功」不含 SUCCESS） | software_control_tools.py:45（**注意：这是今天 ②-B 修复引入的反向坑，杀掉却回报"未找到"**） |
| P2-2 | 4MB 以下中等输出无截断标记（`_trunc` 仅 >4MB 才 True） | tools.py:1793/2025 |
| P2-3 | `_aborted` 异常→fail-open 继续（与主链 fail-closed 哲学相背）；长操作中途不响应停止 | software_control_tools.py:23-32 |
| P2-4 | WMI 兜底 PowerShell 字符串拼接注入面（有确认框兜底） | software_control_tools.py:592 |
| P2-5 | `_wrap` 单表 signature 异常连坐四张扩展工具表全部注册失败 | tools.py:168-196 |
| P2-6 | 导演台缺镜合成提示弱（对照数字人面板有明示） | director_panel.py:3077 |
| P2-7 | RES_PRESETS 四处重复定义 + 8 个选项实际无效（2.5-flash 固定 720P）+ 注释矛盾 | ui.py:4358 等 4 处 |
| P2-8 | 「预期有声却无声」成片只警告不拦截 | video_pipeline.py:3948 |
| P2-9 | PM 节点异常→默认 PASS→advisory 直接放行 | legion_worker.py:998 / legion.py:7251 |
| P2-10 | worker 线程直改 UI 共享 project dict 无锁 | legion_worker.py:305/319 |
| P2-11 | 授权弹窗结果投递整段 try/except pass（用户点的决定可能丢失，worker 白等 600s） | legion_ui.py:3590 |
| P2-12 | 续跑指纹只覆盖 name/tools/model（角色技能/prompt 改动不识别） | legion.py:339 |
| P2-13 | `_CKPT_LOCK` 定义未使用（僵尸 worker 与新 worker 同写 checkpoint） | legion.py:332 |
| P2-14 | crash logger 装得太晚（import PySide6 崩溃零日志，窗口版双击"毫无反应"） | main.py:48/308 |
| P2-15 | 密钥扫描三缺口：无引号 shell 风格、短 key、.log/.db 不在后缀 | release_check.py:140 |
| P2-16 | 退出路径：Obsidian QThread 不 join（阻塞退出 15s）、gateway terminate 不 wait | main.py:379/730 |
| P2-17 | README 版本正则放过两段号（v4.186 不匹配→warn SKIP 不 fail）；exe 侧取值字典序最大 | release_check.py:216/249 |

## 4. P3 完整清单（10 条）

`_agent_call` 429 行长函数（ui.py:9930-10358）；agent 侧三份自维护词表与 intent_guard 漂移（agent.py:592/369）；
`_user_refuses_tools` 不过滤 `_internal`（ui.py:9795）；口播「20~35字」与 12 秒可选时长自相矛盾（video_pipeline.py:3090）；
非合并类 ffmpeg 子进程不可取消 + volumedetect 300s 无取消检查（video_pipeline.py:3389/3933）；
数字人生成不登记远端 task_id（digital_twin_panel.py:1262）；APP_BUILD_DATE 写死无校验（config.py:81）；
gateway `shutil.which("python")` 打包版用户机静默失败（main.py:255）；状态条 gate 态取字典序最后一个
（legion_status_widget.py:205）；legion_status_widget 与测试未纳入 git 跟踪（发布前需确认进包）。

## 5. 已排除项（查过没问题的，防重复劳动）

- **续跑张冠李戴**：阵容指纹/任务文本不一致即 `status="refused"` + done.emit 拒绝，不是 warning 后续跑（legion_worker.py:1725）。
- **会话信任绕不过硬闸**：2.5/2.55 先于信任分支；`db_delete/skill_install/git clean -fd/send_email` 探针在信任下仍被拦。
- **确认弹窗无超时自动放行**：`_confirm_val=False` 前置，180s 超时/关窗/停止全走拒绝（fail-closed 正确）。
- **deny 不能被 allow 覆盖**：deny 在最内层执行前。
- **nudge 死循环/上下文膨胀**：`_nudge_count` 三处共用 MAX_FORCE_RETRIES=3；internal 消息不回写 session、不成气泡。
- **memory_store 并发写**：全部写函数 `@_sync` 全局 RLock；manifest/state 全原子写（tmp+replace+fsync）。
- **qt-material 换肤漂移 / reset() 陈旧引用 / @property 感知锁**：ui.py 零命中，不存在。
- **config.py 明文 key 进包**：全部默认空串，真实 key 只在用户目录 config.json。
- **备份轮转误删当前 exe**：候选项显式跳过 `小臭玩AI.exe`，无窗口。
- **legion_worker 异常后继续跑下一环**：波循环 try 2734 收口→checkpoint+结项+done，fail-closed。
- **视频主链路把失败包装成成功**：契约为「成功=元组/失败=错误串」，分取消/失败两路，4xx 不重试。
- **member 异常伪装成功**：`_wrap` re-raise + TaskGraph 标 failed + 报告显式标注。

## 6. 诚实边界

1. 全部结论 = 静态阅读 + grep + 本地纯函数探针；**未启动应用、未发起真实 API 调用**；「触发场景」为代码路径推演，P0/P1 的最小触发条件已探针证实，最大影响面需线上佐证。
2. P1-5（`_internal` 回传是否被上游 400）取决于各通道严格度，offline 无法判定。
3. 未构造真实恶意 target / 真实 IO 故障复现（逾只读边界）。
4. 六个 agent 未交叉覆盖的区域：legion_permissions.py 内部、MCP 工具执行端（tools.py:889）、video-agent/core 的 submit/wait 逐行、director_chat.py。
5. 本机无 PySide6 的 agent 运行时级 import 验证（agent 侧探针为抽取源码 exec 自测）。
