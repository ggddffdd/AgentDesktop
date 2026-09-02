# 小臭玩AI

一个常驻 Windows 桌面的**本地 AI Agent 工作台**：系统托盘常驻 + 全局快捷键一键呼出 + 多步推理 Agent 循环 + 45+ 内置工具 + 多模型智能路由 + MCP 可扩展。

技术栈：**Python + PySide6 + OpenAI 兼容 API + Chroma 向量库**，配合 pyautogui / pywinauto 做系统与软件自动化。免费可跑、代码可改可练手。

> 当前版本 **v4.97**。本项目由个人桌面助手迭代而来，经脱敏后开源，供学习参考与二次开发。

---

## 核心能力

### 🧠 Agent 循环引擎
- 多步推理：LLM 自主决定调用哪条工具链，最多 8 步，边想边做。
- 权限引擎（5 档风险模式）：危险操作（写文件 / 执行命令 / 系统操控）逐个确认，安全工具自动放行，支持并发执行。
- 子代理并行（TaskGraph DAG）：`run_workflow` 可将任务拆成多节点并行跑（如多角度研究），结果归并喂回模型。

### 🔧 45+ 内置工具
- **联网**：`web_search`（多后端自动降级）、`web_fetch`（含敏感文件拦截）
- **文件/代码**：`read_file`（支持长文档分段）、`write_file`、`run_command`、`run_python`
- **知识库**：`rag_index` / `rag_search`（Chroma 本地向量库）、Obsidian 异步冷启动
- **多模态**：`image_gen` / `video_gen` / `analyze_image`（生图、生视频、识图）
- **自动化**：`schedule`、`create/list/delete_automation`（一次性/每日/每周/间隔定时任务）
- **系统操控**（14 个）：截屏、鼠标、键盘、剪贴板、窗口、进程
- **软件操控**（10 个）：应用启动/强杀/聚焦、控件树枚举、UI 自动化
- **浏览器**：`browser_open/read/click/fill`（带确认机制）
- **其他**：邮件发送、数据库增删改查、Webhook 收发、图表生成、系统信息、上下文压缩/摘要
- **记忆**：`remember` / `search_memory`（会话级与长期记忆）
- **技能**：`use_skill` / `create_skill`（技能热插拔 + 待审核队列）

### 🎛️ 多模型 + 智能路由
- 内置预设：DeepSeek、Agnes（永久免费）、硅基流动、智谱 GLM、腾讯混元、魔搭 ModelScope，可自定义任意 OpenAI 兼容端点。
- 智能路由：简单问题走免费模型，命中关键词或消息超长自动升级到复杂模型，复杂模型无 key 自动回退，不中断。

### 🧬 记忆与自进化
- 三层记忆：会话记忆 + 操作经验库（harness，支持版本回滚）+ 长任务轨迹记忆（few-shot 注入）。
- 软自进化：经验库自动沉淀 refine；模型自动创建的技能先进「待审核」目录，人工通过后才生效。

### ⚙️ 工程韧性
- 长任务断点续跑 + 心跳 + 自动重试（崩了能续，卡了会换写法重试）。
- 任务完成通知（托盘弹窗 + 语音），仅长任务/定时任务触发。
- 启动性能埋点、崩溃日志兜底、记忆文件自愈。

### 🎤 语音与数字人（进阶）
- 实时语音对话（ASR / TTS，按住说话）。
- 数字人分身面板、导演台 + 视频流水线（剧本 → 分镜 → 逐镜生成 → 合成）。

---

## 快速开始

### 1. 环境
- Windows 11
- Python 3.10+（加入 PATH）

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置 API Key
首次运行会在 `~/Documents/小臭玩AI/` 下生成 `config.json`，填入模型密钥即可。默认走 DeepSeek（`https://api.deepseek.com`），也可在界面顶部「模型」下拉里切换预设。

> 想零成本：切换到 **Agnes**（永久免费）或 **硅基流动**（注册送额度）等预设即可，无需改代码。

### 4. 运行
```bash
python main.py
```

### 5. 使用
| 操作 | 快捷键 |
|------|--------|
| 呼出 / 隐藏窗口 | `Ctrl + Alt + X` |
| 技能管理器 | `Ctrl + Alt + S` |
| 工作流模板 | `Ctrl + Alt + W` |
| 发送消息 | `Enter`（`Shift + Enter` 换行） |

- 关窗口不退出，缩回系统托盘；托盘右键 → 退出 才真正关闭。
- 对话自动保存到 `~/Documents/小臭玩AI/`，下次打开自动恢复。

---

## 目录结构

```
小臭玩AI/
├── main.py                 # 主入口（托盘/热键/网关自启/新手引导）
├── ui.py                   # 主界面（对话/流式渲染/多面板导航）
├── agent.py                # Agent 循环 + 工具调用分发 + 模型路由
├── config.py               # 配置、模型预设、工具 schema、MCP/RAG 初始化
├── tools.py                # 34 个核心工具实现与注册
├── tool_defs.py            # 工具 schema 定义
├── risk.py                 # 权限引擎（风险分级）
├── permissions.py          # 权限决策
├── system_control_tools.py # 系统操控（14 工具）
├── software_control_tools.py # 软件操控（10 工具）
├── browser_control_tools.py  # 浏览器自动化
├── automation.py           # 定时/自动化任务
├── task_graph.py           # 子代理并行 DAG
├── harness.py              # 操作经验库
├── trace_log.py            # 轨迹记忆
├── task_resume.py          # 断点续跑
├── memory_store.py         # 记忆存储与自愈
├── skill_loader.py         # 技能热插拔
├── rag.py                  # 向量知识库
├── voice.py                # 语音 ASR/TTS
├── digital_twin_panel.py   # 数字人分身
├── director_panel.py       # 导演台
├── video_pipeline.py       # 视频流水线
└── ...
```

---

## 打包成 exe（可选）

仓库提供 `小臭玩AI.spec`（PyInstaller），可打包为免装 Python 的独立程序：

```bash
pip install pyinstaller
pyinstaller 小臭玩AI.spec
```

产物在 `dist/小臭玩AI/`。首次运行会在 `~/Documents/小臭玩AI/` 生成 `config.json`，填好 key 即可用。

---

## 安全提醒

- `config.json` 含你的 API Key，**别分享、别上传公网**。
- `~/Documents/小臭玩AI/` 下有聊天记录、记忆、技能等本地数据，注意保护。
- 危险工具（执行命令、系统/软件操控、浏览器点击）默认需要确认；请勿在不受信环境开启「免确认」模式。

---

## 许可证

本项目仅供学习与个人使用。二次开发与分发请遵守相关法律法规与各模型服务商条款。
