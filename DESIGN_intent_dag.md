# DESIGN: 多步指令 DAG 化（意图层分解 + 前置校验前置化）

> 状态：**设计稿 v0（待评审，未实现）**
> 触发：对标 Codex 评估后提出的「多步指令 DAG 化」建议项（用户 2026-10-09 要求先出设计稿）
> 纪律约束：judge-first / 零哑弹扰动 / fail-open / 只加不减 / 不新增模型轮次 / 人盯人授权

---

## 0. 背景与痛点（实测，非臆测）

- `intent.classify` 对「做个昆明旅游的视频」已正确路由 `force=video_gen`（实测）。
- 但多意图句「生成视频并存到 D 盘」：`_route_force_tool` 只强制造**一个**工具（video_gen），
  `write_file` 这个依赖项**根本不进计划** → 模型可能整段漏掉落盘 →
  这正是 `agent_loop.py` L13-16 自承的缺口
  （"模型先生成、收尾时才发现没存盘 —— 这正是 v4.225 收尾闸门要补一轮才能救回来的场景，
  **但那时用户已经等完了全程**"）。
- PLAN 阶段已存在（`agent_loop.py` L140-213）：
  - `should_plan(required_tools, step=1)`：门槛 `PLAN_MIN_REQUIREMENTS = 2`，且**只数用户字面点名的工具**
    （L148-149 明说：用 `intent.requested_tools` 关键词命中会"为不存在的产物编计划"）。
  - `build_plan(goal, required_tools)`：只扁平列 `必须完成：真实调用工具 X`，**无依赖边**。
  - `plan_instruction(plan)`：注入"请在心里按依赖排好顺序直接开工" ——
    **依赖边缺失 = 真缺口**：模型连"有哪些依赖"都不知道，更别说不跳过。
- `task_state` 已具备记账能力：`artifacts=[(path, exists, tool)]`、`record_tool`、
  `missing_tools()`、`missing_artifacts()`、`is_complete()`。
  但校验发生在 VERIFY 末段（SUMMARIZE 前），属于**事后救回**，不是事中拦截。

**结论**：问题不在"有没有计划框架"，而在 (a) 计划缺依赖边、(b) 校验太晚。
本设计只补这两处，不动已验证的 `_route_force_tool`。

---

## 1. 设计目标 / 非目标

**目标**
1. 把"一句话抽 N 动作 + 依赖"结构化为 `ActionDAG`，注入 PLAN 阶段（带依赖边，不再靠模型自己悟）。
2. 把"生视频→存盘"类前置缺口从"事后救回"提前到"事中校验"（每步末拦，而非收尾才拦）。

**非目标（明确不做什么）**
- ❌ 不新增模型轮次（守 `agent_loop.py` 设计纪律第 1 条：PLAN 不是新模型轮次）。
- ❌ 不改 `_route_force_tool` 的确定性单工具守卫（它解决 sys_info 死循环，必须快且确定性）。
- ❌ 不做画像注入（那是第 1 项，且已论证弊大于利，本设计与画像解耦）。
- ❌ 不做硬调度器：不替代模型执行顺序，只做"带依赖边的不可跳过清单 + 校验兜底"。

---

## 2. 模块边界（关键：不碰 `_route_force_tool`）

```
用户句 → intent.classify()           # 已有，产 Intent(force_tool / requested_tools / needs_clarification)
        → [澄清闸门 v4.239.0]         # 命中(无动词多候选)→ 反问，不分解
        → decompose_intent(text,intent) # 【新增】纯函数，跑在 classify 之后、PLAN 之前
        → ActionDAG | None
                    ↓
        agent_loop.should_plan / build_plan / plan_instruction  # 接入点（L153-213）
                    ↓
        task_state.precond_check(dag) # 【新增】每步末前置校验，前置化
```

- `_route_force_tool`：保持 step1 单工具强制，**一字不改**。
- `decompose_intent`：新增纯函数（建议放 `intent.py` 或独立 `intent_dag.py`），无 IO、可单测。
- 接入点：`agent_loop.py` 的 PLAN 三函数 + `task_state` 前置校验。

---

## 3. 数据结构

```python
@dataclass
class ActionNode:
    id: str                 # "n1"
    tool: str               # 候选工具名（video_gen / write_file / web_search ...）
    verb: str               # 原句动词（生成 / 存 / 搜 ...）
    object: str             # 宾语（视频 / D盘 ...）
    deps: tuple            # 依赖的 node id 列表
    precond_tool: str = ""  # 前置必须已调用的工具（如 write_file 依赖 video_gen）
    done: bool = False

@dataclass
class ActionDAG:
    nodes: tuple           # (ActionNode, ...)
    ordered: tuple        # 拓扑序的 node id 列表
    # 纯数据，无 IO，可 json 序列化便于判据断言
```

---

## 4. 分解规则（`decompose_intent`）

**触发信号**（需 `intent.kind == "action"` 且命中其一）：
- (a) 显式连接词：`并/并且/然后/再/接着/先…后…/以及/顺带/另外` + 多个动作动词；
- (b) 生成动词 + 落盘动词共现：`生成/做/剪/画` + `存/保存/导出/写到/下载/落盘/存档`；
- (c) `intent.requested_tools` 含 ≥2 工具 **且** 文本有清晰动词结构
  （区别于澄清闸门的"无动词多候选"——见 §5）。

**依赖推断（小规则表，可单测）**：
| 主节点 | 条件 | 追加节点 | 依赖 |
|---|---|---|---|
| video_gen / image_gen | 文本有落盘动词或路径词（D盘/桌面/下载/导出） | write_file | deps=[生成节点]，precond_tool=生成节点 |
| web_search | 有"整理/存档/存/落盘" | write_file | deps=[search]，precond_tool=web_search |
| browser_open | — | 无 | 无依赖 |

**fail-open**：抛异常 / 节点数 < 2 → 返回 `None`（退回当前行为，不分解、不阻断）。

---

## 5. 与澄清闸门（v4.239.0）的冲突裁决

| 场景 | 澄清闸门 | DAG 分解 | 处置 |
|---|---|---|---|
| 「视频和图片都处理一下」 | 命中（force=None, req≥2, 无动词） | 不触发 | **澄清优先 → 反问** |
| 「生成视频并存到 D 盘」 | 不命中（force=video_gen≠None） | 命中（清晰多动作） | **分解** |
| 「做个昆明旅游的视频」 | 不命中（单动作） | 不触发（节点<2） | 走正常单工具强制 |

**裁决原则：澄清优先于分解。** 判定顺序：先跑澄清闸门，命中即反问、不分解；
未命中才跑 `decompose_intent`。理由：有歧义时"问"比"猜"安全，与 v4.239.0 纪律一致。
两者正交（澄清要求 force=None，分解要求 force≠None 且有动词结构），不会同时真命中。

---

## 6. PLAN 阶段接入（改 `build_plan` / `plan_instruction`，不新增模型轮次）

- `should_plan`：门槛从"`required_tools>=2`"扩展为"`required_tools>=2` **或** `dag is not None`"。
  dag 非空即 ≥2 动作，即使字面只点名 1 个工具（如"生成视频并存盘"只点名 video_gen，
  但落盘动词隐含 write_file）。
- `build_plan`：dag 非空时按 `ordered` 输出**带依赖边**的清单：
  ```
  ① video_gen（生成视频）
     ↓ 依赖①产物路径
  ② write_file（存到 D 盘）
  ```
  而非现在扁平的"必须完成：真实调用工具 X"。
- `plan_instruction`：保留"按需排序、别复述、做不完明说"，**升级一条**：
  "涉及『生成→落盘』的步骤，生成后必须确实调用落盘工具，不得跳步。"
  （把 L208 的"按依赖排序"升级为"带依赖边的不可跳过清单"）。

---

## 7. 前置校验前置化（task_state，从"事后"到"事中"）

**现状**：VERIFY 在 SUMMARIZE 前跑，但模型收尾后才校验 → 用户等完全程。

**改法**：在 `agent.py` step 循环里，当 `force_required` 进入"无更多工具可调用 / 模型产出纯文本收尾"前，
跑一次 `task_state.precond_check(dag)`：
- 对每个 dag 节点，`precond_tool` 映射为 task_state 事实
  （生成节点是否 `record_tool` 过 video_gen + 产物路径存在；write_file 是否被调用过）；
- 若依赖未满足（video_gen 没产出 / write_file 没调）→ **不打 summary**，
  注入一条 nudge："检测到『生成视频』已完成但『存盘』未执行，请先调用 write_file 保存到 D 盘"，
  再给模型一轮（复用 v4.225 收尾闸门逻辑，只是把"一轮救回"提前到"每步末"）；
- fail-open：校验抛异常 → 当作满足，照常 summary（绝不阻断主循环）。

---

## 8. judge-first 判据 & 零哑弹扰动（落地纪律）

**判据 `tests/test_intent_dag_239.py`（judge-first：先红后绿）**
- D1 正例：「生成视频并存到 D 盘」→ dag 2 节点、write_file.deps 含 video_gen、precond_tool 非空；
- D2 单动作：「做个昆明旅游的视频」→ `None`（不分解）；
- D3 歧义：「视频和图片都处理一下」→ `None`（交给澄清闸门，不分解）；
- D4 fail-open：「」/ `None` / 乱码 → `None`；
- D5 依赖表：「搜一下并整理成文档存起来」→ web_search + write_file（deps=search）；
- D6（相B）`plan_instruction(build_plan(dag))` 输出含依赖边标记（①→②）。

**扰动 `_perturb_intent_dag_239.py`**
- P1 删依赖推断（write_file 不再被追加）→ D1 翻红；
- P2 触发门槛改成恒 False（永不分步）→ D1/D5 翻红；
- P3 澄清优先改成 DAG 优先（歧义句被分解）→ D3 翻红。
- 目标 `PERTURB PASS=3 FAIL=0`（零哑弹）。

---

## 9. 分相（每相独立 judge-first + 扰动 + 回归）

- **相A**：`decompose_intent` 纯函数 + 判据 D1-D5 + 扰动 P1-P3（**不接线**，零风险，可独立验收）。
- **相B**：接入 PLAN（`should_plan`/`build_plan`/`plan_instruction`），判据扩 D6。
- **相C**：`task_state.precond_check` + `agent.py` step 循环接前置校验，判据验"漏落盘被拦下并 nudge 一轮"。

每相：VERSION bump + CHANGELOG + `tests/.suite_manifest.txt` 登记 + 全量回归 + pre-commit + 重打包（frozen_smoke 对齐）+ push（人盯人授权）。

---

## 10. 风险 / 注意

- 不新增模型轮次（守 `agent_loop.py` 设计纪律第 1 条）。
- 不取代 `_route_force_tool`；它仍是 step1 单工具强制。
- 分解只基于**字面动词/连接词/落盘动词**，不引入画像（与第 1 项彻底解耦）。
- DAG 是"注入清单 + 校验"，非硬调度器；执行顺序仍模型驱动、VERIFY/前置校验兜底。
- 与澄清闸门正交，澄清优先（安全第一）。
- `should_plan` 的"≥2 字面点名"门槛文档明言是为避免"为不存在产物编计划"，
  本设计用"落盘动词隐含 write_file"扩展触发，但 **write_file 节点只在文本确有落盘动词时才追加**，
  不会凭空编造依赖。
