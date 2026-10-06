# 系统控制能力层 · 盘点报告（只读）

> **v4.211.1 基线**（2026-10-04）· 任务板 #3 第一步
> 范围：`system_control` / `software_control` / `browser_control` / `run_command+run_python`
> 性质：**纯只读盘点**。未改任何代码、未执行任何真实系统操作。
> 本文件为待勾选清单的取证底稿，**未提交**，等大哥决定后再进发布列车。

---

## 〇、先纠正任务板里的一处认知

任务板原文写「Mavis = 现有 system_control / software_control / browser_control / tools.run_command+run_python 能力层」。
**核对了仓库，没有叫 Mavis 的模块**（`grep -ri mavis` 零命中）。四层控制确实存在，但落点是：

| 通道 | 文件 | 行数 | 工具数 | 声明位置 |
|---|---|---|---|---|
| 系统控制 | `system_control_tools.py` | 741 | 15 | `SYSTEM_CONTROL_TOOL_DEFS` + `SYSTEM_CONTROL_TOOL_TABLE` |
| 软件控制 | `software_control_tools.py` | 988 | 11 | `SOFTWARE_CONTROL_TOOL_DEFS` + `SOFTWARE_CONTROL_TOOL_TABLE` |
| 浏览器控制 | `browser_control_tools.py` | 856 | 4 | `BROWSER_CONTROL_TOOL_DEFS` + `BROWSER_CONTROL_TOOL_TABLE` |
| 浏览器执行器 | `browser_runner.py` | 304 | —（子进程侧） | 由 `browser_control_tools` 经 subprocess 调用 |
| 命令/代码 | `tools.py::tool_run_command` / `run_python` | — | 2 | `tool_defs.py` |
| **授权层** | `risk.py` + `permissions.py` | 413 + 261 | — | 四层共用 |

"已锁 5 个薄弱点"里的行号（`agent.py:507 / 1350 / 1726`）是 v4.179.1 时代的，**已经漂了**。下面按**符号名**重新对齐，不按行号。

---

## 一、现状盘点：健康面（先说好的，避免把成熟的东西当缺口改）

### 1.1 授权闸门是**分层**的，且分层顺序是对的

`permissions.PermissionEngine.decide()` 的闸门顺序（`permissions.py:144-261`）：

| # | 闸门 | 规则名 | 能否被 auto 模式/会话信任绕过 |
|---|---|---|---|
| 1 | 模式优先（discuss 全拒 / plan 只读） | `mode:discuss` / `mode:plan` | — |
| 2 | 外发白名单（EXTERNAL 未授权直接拦） | `external_block` | **否**（先于会话信任） |
| 2 | 路径作用域（write_file 越界拦） | `scope` | **否**（同上） |
| 2.5 | 硬确认档 `ALWAYS_CONFIRM` | `always_confirm` | **否** |
| 2.55 | 参数级高危（`command_danger_level`） | `high_risk_exec` | **否** |
| 2.58 | 任务级风险闸（critical 任务） | `task_risk_critical` | **否** |
| 2.6 | 来源闸（本轮无执行意图时的非只读） | `implicit_intent` | **否** |
| 3 | 会话信任 | `session:*` / `session` | 本体 |
| 4-7 | 外发+auto / auto_allow / auto / tier | — | 本体 |

这道顺序正是大哥定的三道边界（参数围栏 / 一键收回 / 每笔审计痕）里的**前两条**，且 v4.167.0 专门修过「会话信任跑到硬围栏前面」的漏洞。**这一层不用动。**

### 1.2 授权粒度已经绑到参数指纹

`trust_tool(name, args)` 记的是 `name#sha256(args)[:16]`（`permissions.py:95`），不是工具名。
→ 确认过 `run_command("echo ok")` 后，同会话再发 `run_command("<危险命令>")` **仍会问**。原先"一次授权无限放大"的洞已堵。

### 1.3 零漏登记（实证）

探针实测（详见 §三）：
```
声明工具总数 81；RISK_MAP 登记 81
未登记（走 classify 前缀兜底）: 0 个
```
v4.169.0 那轮手工补的 16 个漏登记工具，**没有回退**。

### 1.4 控制类工具全部落在"要人点"的档位

| 通道 | exec/manual | read/auto | write_local/semi |
|---|---|---|---|
| system_control（14） | 8 | 5 | 1 |
| software_control（11） | 7 | 4 | 0 |
| browser_control（4） | 2 | 2 | 0 |

**没有一个控制类工具走 auto 直通。** 键鼠、进程、窗口、点击填表全部 `manual`。

---

## 二、缺口清单（7 条，按建议处理顺序）

### G1 ★★★ 主对话 Agent 路径**没有审计落盘** —— 违宪法第二章「每笔留痕」

| | |
|---|---|
| **实证** | `permissions.py` 里 `'jsonl' in src == False`、无 `open(` 写盘；`legion_tool_audit.jsonl` 仅由 `legion_permissions.py:66` 写 |
| **现状** | **军团（legion）路径**每笔非只读决策落 `legion_tool_audit.jsonl`（带线程锁 `_AUDIT_LOCK`、`_digest()` 摘要、`_redact()` 脱敏）；**主对话路径**（`agent.py` + `permissions.py`）决策**只在内存**，`Decision` 返回完就没了 |
| **后果** | 用户亲手确认过的 `run_command` / `process_kill` / `mouse_click` / `app_launch`，事后**无从抽查**。宪法第二章要求「每笔自动放行留审计痕（可抽查）」—— **在军团侧成立，在主对话侧不成立** |
| **注意** | `ui.py` 里那一堆 `_audit_*` 是**回复审计**（引用/附件/断言回验），与**工具执行审计**是两件事，别搞混 |
| **代价** | 小。抄 `legion_permissions` 的落盘范式（锁 + digest + redact）抽成公共 helper，在 `permissions.decide()` 返回前落一笔 |
| **授权铁律** | 不涉及真实系统操作（只增加记录，不改变是否执行），但**改的是授权层本体**，仍建议走发布列车 |
| **可写判据** | ✅ 判据：decide() 后审计文件行数 +1；扰动：删掉落盘那行 → 判据必红 |

### G2 ★★★ `system_control` 14 个工具**零可中断**（0/14 实证）

| | |
|---|---|
| **实证** | 按函数签名实测：`system_control` 工具 14 个，**可中断 0 个**；`software_control` 工具 11 个，**可中断 11 个** |
| **根因** | `system_control_tools.py` 的函数签名清一色 `(cfg, app_dir, args)`，没有 `progress/stop_event/should_stop`；而 `tools.py:192` 的 `_w` 包装器**只给声明了对应参数的 handler 透传**停止信号 → system_control 天然收不到 |
| **后果** | 点了「停止」后，键鼠模拟 / 窗口枚举 / 进程操作**照跑完**。任务板 #23「stop 信号是否到达管线最底层」在主对话系统控制侧**未闭环** |
| **代价** | 小且机械。14 个函数加参数 + 入口 `_aborted()` 检查，与 software_control 同款 |
| **授权铁律** | 不涉及（只增加"更早停下"的能力） |
| **可写判据** | ✅ 照抄 `test_software_control_2b.py` 的「入口即停」写法，14 个工具逐个验；扰动：删掉某个 `_aborted` 检查 → 必红 |

### G3 ★★ 软件控制**误点静默**：`_find_control` 第 4 级兜底会拿无关控件顶替

| | |
|---|---|
| **位置** | `software_control_tools.py:437`「4) 仅按 control_type 找第一个」 |
| **链路** | `_find_control` 找控件的四级降级：① 精确 `auto_id` → ② 精确 `name` → ③ 模糊 `title_re` → ④ **只给 `control_type`，返回第一个同类控件** |
| **后果** | 模型传了 `control_type` 但 `target` 名字对不上时，第 4 级会返回**第一个同类控件**（很可能完全不是目标）。`tool_app_click` 随后照点，并返回「已点击 '<实际控件文本>'」= **成功**。模型看到"成功"就往下走 → **误点但不报错** |
| **代价** | 小。第 4 级要么删掉，要么改为「返回候选清单让模型重挑」而不是直接选一个 |
| **授权铁律** | 不涉及（改的是"找不到时怎么办"） |
| **可写判据** | ✅ 判据：mock 一个"只有同类控件、无目标名"的窗口 → 断言返回**失败+候选清单**而非成功；扰动：恢复第 4 级直接返回 → 必红 |

### G4 ★★ `process_kill` **无系统关键进程保护**，且会杀**所有**同名进程

| | |
|---|---|
| **实证** | `grep -n "explorer\|lsass\|csrss\|winlogon\|services\.exe"` 在 `system_control_tools.py` / `software_control_tools.py` = **零命中** |
| **实现** | `tool_process_kill`（`system_control_tools.py:682`）：`flag = "/PID" if name.isdigit() else "/IM"` → `taskkill /IM <name>` **杀掉所有同名进程**；子进程不跟随（无 `/T`），成孤儿 |
| **为何纵深防御没兜住** | 系统级毁灭硬拦 `_DANGEROUS_CMD_PATTERNS`（`tools.py:1597`）只对 `run_command` / `run_python` 的**命令行文本**生效，**管不到 `process_kill` 这个结构化工具**（它的 args 是 `{"name": ...}`，不是一段命令） |
| **后果** | `process_kill("explorer.exe")` 桌面壳重启；`process_kill("lsass.exe")` 更严重。确认框走的是泛化的「确认桌面/系统控制」分支，只显示原始 JSON |
| **代价** | 小-中。加一层关键进程名黑名单（explorer/lsass/csrss/winlogon/services/smss/csrss/wininit…）→ 命中即**直接 deny**（不是"需确认"，与 ③-A 同档）；`/IM` 路径补返"将影响 N 个进程"的计数 |
| **授权铁律** | **涉及**。这是真实系统操作能力的**收紧**（更严），不是放宽；但动的是系统控制语义，建议单独一批做 |
| **可写判据** | ✅ 判据：`process_kill("lsass.exe")` 返回 deny 且**不 spawn 子进程**；扰动：删黑名单 → 必红 |
| **处置状态** | ✅ **全部落地**（第一步 v4.211.6 / 第二步 v4.211.7）：① `/IM` 路径补「同名进程共 N 个」影响面计数（只读 helper `_count_processes`）；② **关键进程名黑名单 → 硬拒绝**：9 个名单、命中即拒绝且**零 spawn**、归一化覆盖带不带 `.exe`/大小写、PID 路径反查镜像名也过；三个入口一起堵（`process_kill` / `app_kill` / `_dangerous_command_check` 的 taskkill·Stop-Process 文本模式）。判据 `tests/test_critical_proc_deny.py`(45) + 扰动 `_perturb_critical_proc_deny.py`(7)。 |

### G5 ★★ 判据覆盖**倒挂**：最危险的一层判据最薄

| 模块 | 行数 | 工具数 | 引用它的判据套件 |
|---|---|---|---|
| `system_control_tools.py` | 741 | 15 | **1 个**（`test_system_control_a/b` + `test_clean_recycle_bin_217`） |
| `browser_runner.py` | 304 | — | **0 个** ← 裸奔 |
| `software_control_tools.py` | 988 | 11 | 1 个（`test_software_control_2b.py` + `test_app_close_218.py`，只覆盖截断/中断/启动健壮性） |
| `browser_control_tools.py` | 856 | 4 | 2 个（但都是 profile 路径 / workspace 路由，**非**点击填表逻辑） |
| `skill_installer_tools.py` | 491 | — | 1 个 |
| **授权层** `permissions.py` / `risk.py` | 674 | — | **7 / 9 个**（很厚） |

**读法**：闸门（授权层）判据很厚，但闸门后面那只手（键鼠/进程/点击）几乎没判据。
一旦 G2/G3/G4 那种"改坏了没人知道"的改动进来，没有任何套件会翻红。

| **代价** | 中。至少给 `system_control_tools` 建一套（可全部无头跑：入口即停 / 参数校验 / deny 分支 / 缺依赖降级） |
| **可写判据** | ✅ 本身就是判据工程 |

### G6 ★ 工具声明表 vs 风险登记表的**全等判据缺失**（"零漏登记"是靠人守的）

| | |
|---|---|
| **实证** | `grep -rn "TOOL_DEFS" tests/*.py` = **零命中**；`risk.validate_policy()`（`risk.py:370`）**只遍历 `RISK_MAP` 自己的键**，查不出「声明了工具却漏登记」 |
| **结构守卫** | `test_risk_policy_single_table.py` 守的是**单表设计**（`_policy` 唯一入口 / A5 ALWAYS_CONFIRM 由表推导 / D3 档位只能更严 / D2.x 动态 `__getattr__`）—— 设计守卫很全，**唯独缺"完整性"这一条** |
| **后果** | 新增一个控制工具忘了进 `RISK_MAP` → 走 `classify()` 前缀兜底 / 最终落 EXTERNAL → **可能被静默拦掉**（正是 v4.169.0 手工修过的那类 bug）。今天恰好是 81/81 对得上，但**没有任何判据会阻止它明天变成 81/82** |
| **代价** | 极小。一条判据：`set(TOOL_DEFS 名) == set(RISK_MAP 键)` |
| **可写判据** | ✅ 立刻可写，配扰动（从 RISK_MAP 删一条 → 必红） |

### G7 ☆ 已知取舍（**不建议回退**，仅登记备查）

- `browser_open` / `browser_read` 被**刻意降为 READ（免确认自动执行）**（`risk.py:133-139`，v4.80）。
  当时是为了破「每次确认 → 原样重试 → 去重护栏拦截」的死循环。
  代价是：**`browser_read` 能自行打开任意 URL 且不问**。
  这是有意识的取舍，**不建议动**；但值得作为"对外可见行为"在大哥这儿备一笔。
- `system_control` 的 `screenshot` 走 `_run_on_gui_thread(app, fn, timeout=15)`（`system_control_tools.py:361`），
  GUI 线程 15s 无响应会返回「本次截图放弃」—— **这一条是有超时保护的**，不在 G2 范围内。

---

## 三、取证方式（可复现）

探针脚本：`%TEMP%\_probe_ctl_audit.py`（**仓库外**，未入库），只读，不上手系统：

```
① 停止信号接收能力 —— inspect.signature 逐函数实测
  [缺口] system_control     工具 14 个，可中断  0 个  ← 差 14 个
  [OK  ] software_control   工具 11 个，可中断 11 个

② 工具声明表 vs 风险登记表
  声明工具总数 81；RISK_MAP 登记 81；未登记 0 个

③ 授权层落盘（审计痕）
  permissions.py 含 'jsonl': False / 含 open( 写盘: False
  legion_permissions.py 含 'legion_tool_audit.jsonl': True

④ 判据套件覆盖
  system_control_tools    0 个套件 ← 裸奔
  browser_runner          0 个套件 ← 裸奔
  software_control_tools  1 个套件
  browser_control_tools   2 个套件
  skill_installer_tools   1 个套件
```

其余证据均来自**源码直读**（`risk.py` 全文 / `permissions.py` 全文 / 三张 TOOL_TABLE / `_find_control` /
`tool_process_kill` / `_DANGEROUS_CMD_PATTERNS` / `_run_on_gui_thread`），行号已在各条中标注。

---

## 四、待勾选：建议分四批

> 授权铁律适用说明：G1/G2/G3/G6 是**能力或记录**层面的改动（不动系统语义）；
> G4 动的是**系统控制语义**（收紧），单独一批；G5 是判据工程。

| 批 | 内容 | 条数 | 为何这样分 |
|---|---|---|---|
| **A** | G6（全等判据）+ G2（14 个工具补可中断）+ G3（删/改第 4 级兜底） | 3 | 都是"小、机械、零风险"，**一批做完**，当天可出判据+扰动 |
| **B** | G5（给 `system_control_tools` 建判据套件） | 1 | 判据工程，量最大；A 批的能力改动正好当它的被测对象 |
| **C** | G1（主对话审计落盘） | 1 | 改授权层本体，建议单独一批，配"审计文件行数 +1"判据 |
| **D** | G4（关键进程保护） | 1 | ✅ **已完成**（v4.211.6 影响面计数 + v4.211.7 黑名单硬拒绝）。授权铁律已履行：两步都是**收紧**方向，第二步单独成批并经大哥放行 |

**大哥只需回答：A / B / C / D 里开哪几个，或者直接说「按 A→B→C→D 顺序全做」。**

---

## 五、明确没做的事（避免越界）

- ❌ 没有执行任何真实系统操作（鼠标/键盘/杀进程/起进程一律没碰）
- ❌ 没有改任何源码、没有建判据、没有跑扰动
- ❌ 没有提交本文件（`SYSTEM_CONTROL_INVENTORY_REPORT.md` 目前是**未跟踪状态**）
- ❌ 没有动 `browser_open/read` 的 READ 降级（G7 判定为有意取舍，仅登记）
- ❌ 没有按任务板旧行号去改 agent.py（行号已漂，按符号名重新对齐后才敢下结论）
