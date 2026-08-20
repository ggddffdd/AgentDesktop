AgentDesktop_README_v3.md
AgentDesktop
一个常驻 Windows 桌面的本地 AI Agent 工作台

托盘常驻 · 全局快捷键呼出 · 多步推理 Agent 循环 · 61 个内置工具

多模型智能路由 · 4 档风险权限引擎 · TaskGraph 并行子代理 · MCP 可扩展

Python + PySide6 · 25,000+ 行 · 无需付费云服务，填好 API Key 即可运行

为什么是 AgentDesktop？
大多数 AI 助手停留在「对话框」，AgentDesktop 让 AI 真正操作你的电脑：搜资料、读写文件、跑代码、控制鼠标键盘、操作软件窗口、定时自动化——每一步都在权限引擎的管控之下，危险操作逐个确认，安全操作自动放行。

它不是演示 Demo，而是一个作者本人每天都在用的桌面助手——自学 AI 4 个月、边踩坑边开发的第 1 个月作品，几十次迭代打磨 + 脱敏后开源。

三个差异化设计
1. 权限引擎（不是简单的开关）

每个工具按副作用归入 4 档风险：READ（只读）→ WRITE_LOCAL（本地写入）→ EXEC（执行/控制）→ EXTERNAL（外部操作）。5 种执行模式自由组合：

模式	行为
discuss	仅讨论，不执行任何操作
plan	只做只读调研，不实际执行
interactive	默认，危险操作逐个弹窗确认
auto	全部直接执行（谨慎使用）
custom	仅白名单内免确认
外加路径作用域（本地写入必须落在允许目录内）、会话信任（勾选后本会话免打扰）、对外动作白名单（EXTERNAL 类工具必须显式授权）三层安全边界。

2. TaskGraph 子代理并行

复杂任务自动拆成任务图：TaskCreate 建节点 → addBlockedBy 定依赖 → 引擎自动推进就绪节点（ThreadPoolExecutor 并行）。例如「多角度研究」工作流：3 个研究员子代理各自独立搜索，并行执行完毕后归并结果喂回主模型。

3. 全本地记忆系统

三层记忆：会话上下文压缩 + 长期记忆（对话后自动提取沉淀，topic 去重覆盖）+ 操作经验库（harness）。数据全部落在本地 ~/Documents/AgentDesktop/，不出你的机器。

核心能力一览
🧠 Agent 循环引擎
多步推理：LLM 自主决定工具链，最多 8 步，边想边做
断点续跑：长任务中断后可从轨迹记忆恢复
轨迹日志：每一步决策可追溯
🔧 61 个内置工具
类别	数量	代表工具
联网	2	web_search（多后端自动降级）、web_fetch（敏感文件拦截）
文件/代码	4	read_file（长文档分段）、write_file、run_command、run_python
知识库	2	rag_index / rag_search（Chroma 本地向量库）
多模态	3	image_gen / video_gen / analyze_image
自动化	4	schedule、create/list/delete_automation
系统操控	14	截屏、鼠标、键盘、剪贴板、窗口、进程
软件操控	10	应用启动/强杀/聚焦、控件树枚举、UI 自动化
浏览器	4	browser_open/read/click/fill（带确认机制）
其他	18	邮件、数据库 CRUD、Webhook、图表、记忆、技能…
🤖 多模型智能路由
不同任务自动路由到不同模型：日常对话走便宜模型，复杂推理/多模态走旗舰模型，一个配置文件管多个 provider（OpenAI 兼容协议）。

🧩 扩展机制
技能热插拔：create_skill 动态生成技能，use_skill 直接调用，skills/ 目录放 Markdown 即可
MCP 接入：标准 Model Context Protocol 客户端，接第三方工具服务
待审核技能队列：AI 自己写的技能先进队列，人工确认后才生效
🎛️ 多面板工作台
聊天面板之外还有：导演台（任务编排）、数字人分身（语音交互 + ASR/TTS）、视频流水线面板。

快速开始
环境要求
