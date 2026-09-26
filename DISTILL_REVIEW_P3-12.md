# DISTILL_REVIEW_P3-12 — except 卫生（GPT 架构审查「真蒸馏」第五组）

- 轮次：第五组（P3-12，P3 最低档，可维护性/健壮性）
- 源：v4.152.3 GPT 架构审查报告 → 真蒸馏核验
- 日期：2026-09-17
- 结论：**P3-12 经核验基本已满足，针对性最小侵入改进 5 处 + 加回归守卫，不盲改 80+ 模块**

## 一、核验方法（真蒸馏，不盲信报告）
用 `_audit_except_hygiene.py`（纯 AST 扫描，**剔除**测试/dist/各类探针/verify/smoke/diag/build）
对 **82 个核心应用模块** 做实测，量出异常处理真实分布。

## 二、实测数据
| 指标 | 数量 | 结论 |
|---|---|---|
| 裸 `except:`（吞 KeyboardInterrupt/SystemExit，最危险） | **0** | ✅ 已干净 |
| 纯吞错（只 `pass` / 只日志无 raise） | 524（pass_only 349 + log_only 175） | 绝大多数为良性 |
| 其中「关键边界函数内 pass_only」 | 133 | 逐条核验后绝大多数是正确行为 |

## 三、关键边界 133 处 pass_only 的定性
逐条核验，认定 **绝大多数本就该静默**（不是掩盖错误）：
- Qt 信号 `disconnect`（已断开再 disconnect 会抛，必须 swallow）
- UI 控件 setup（`setEnabled`/`selectRow`/`addTab` 等 best-effort）
- best-effort 清理（`proc.kill`/`shutil.rmtree`/`os.remove`/`os.makedirs(exist_ok=True)`）
- 后台落盘 fallback（`os.replace`，已有「磁盘失败不影响本轮对话」注释）
- best-effort 重连（`request_stop`/`mark_done`/`clear_source_ctx`）

→ **不改动这 130+ 处**（盲目加日志 = 噪声 churn，违背「最小侵入」）。

## 四、针对性修复（仅 5 处真问题：静默落盘用户数据/授权态且零日志）
失败 = 静默丢数据、用户无感知。改法统一为 `except Exception as e: log.warning(...)`——
**只让失败可观测，不改变控制流**（仍 swallow，不引入新失败路径）：

| 模块 | 函数 | 落盘内容 |
|---|---|---|
| `context_manager.py` | `_save_summaries` / `_save_key_info` | 对话摘要 / 密钥信息 |
| `legion.py` | `_trust_save` | 军团信任/授权态 |
| `config.py` | `load_config`（迁移落盘） | 配置迁移写回 |
| `agent.py` | `run`（任务完成标记） | 任务完成态标记 |
| `trace_log.py` | `prune` | 任务轨迹裁剪落盘 |

注：`context_manager`/`trace_log` 原无 logger，新增自包含 `log = logging.getLogger(__name__)`（**不 import 项目模块，避免循环依赖**）；`config`/`agent`/`legion` 复用既有模块级 `log`。

## 五、回归守卫
新增 `_verify_except_hygiene.py`（纯 AST、沙箱可跑）：
- 硬规则①：核心模块 **零裸 `except:`**（锁定最危险轴）
- 硬规则②：锁定本轮修复的 4 个具名持久化函数（`_save_summaries`/`_save_key_info`/`_trust_save`/`prune`）不得退回只-pass
- 观测项（不 fail）：其余持久化函数内只-pass（多为 load 默认返回 / 刻意 fallback）仅报告供趋势观测
- 已注册 `_run_v4152_regress.py`（L1）

## 六、验证
- `py_compile` 全过（config/agent/legion/context_manager/trace_log/_run_v4152_regress/_verify_except_hygiene/_audit_except_hygiene）
- `_verify_except_hygiene.py` → `RESULT: PASS`（裸 except 0 + 锁定持久化函数无只-pass）
- 产物级进包核对见 build 后 `_verify_exe_version.py`（v4.153.4 / 2026-09-17）

## 七、与 GPT 报告 12 条的总账
四组 + 本组已全收官：
① v4.153.0 P1-01/P1-02/P2-07（真 bug）② v4.153.1 P2-03/P2-06
③ v4.153.2 P2-08/P2-09/P3-10 + 技能沉淀 ④ v4.153.3 P2-04/P3-11
⑤ v4.153.4 P3-12 except 卫生
P2-05 误报已证伪不修；P3-12 经核验主体已满足，仅做上述最小改进。
