AgentDesktop
<div align="center">
一个常驻 Windows 桌面的本地 AI Agent 工作台

托盘常驻 · 全局快捷键呼出 · 多步推理 Agent 循环 · 61 个内置工具

多模型智能路由 · 4 档风险权限引擎 · TaskGraph 并行子代理 · MCP 可扩展

Python + PySide6 · 25,000+ 行 · 无需付费云服务，填好 API Key 即可运行

</div>
为什么是 AgentDesktop？
大多数 AI 助手停留在「对话框」，AgentDesktop 让 AI 真正操作你的电脑：搜资料、读写文件、跑代码、控制鼠标键盘、操作软件窗口、定时自动化——每一步都在权限引擎的管控之下，危险操作逐个确认，安全操作自动放行。

它不是演示 Demo，而是一个作者本人每天都在用的桌面助手——而且是个意外长成的庞然大物：最初只是想给 DeepSeek 做个简单的个人工作台（当时官方还没出），结果需求越提越多，脑洞越开越大……

作者不会写代码，靠提出想法、把关验收，指挥多个大模型协作编码：1 个多月、几十次迭代，从聊天窗长出了权限引擎、任务图和记忆系统，累计 2.5 万行，脱敏后开源。

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
Windows 10/11（系统操控依赖 Windows API）
Python 3.10+
任一 OpenAI 兼容 API Key（OpenAI / DeepSeek / 智谱 / 本地 Ollama 均可）
安装
git clone https://github.com/ggddffdd/AgentDesktop.git
cd AgentDesktop
pip install -r requirements.txt
python main.py
 
首次运行会在 ~/Documents/AgentDesktop/ 生成 config.json，填入你的 API Key 和 base_url 即可。

最小配置示例
{
  "api_key": "sk-xxx",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat"
}
 
打包成 exe（可选）
pip install pyinstaller
pyinstaller AgentDesktop.spec
 
产物在 dist/AgentDesktop/，免装 Python 独立运行。

架构
<div align="center"> <img src="https://sfile.chatglm.cn/workspace/image/57/57d529429a.png" alt="AgentDesktop 整体架构：用户交互层 → Agent 核心 → 多模型路由 → 权限引擎 → 工具执行层 → 本地数据层" width="860"> </div>
关键数据流：用户指令 → AgentWorker（QThread）→ LLM 推理 → 权限引擎 decide() → 工具执行（可并行）→ 结果回流 → 循环直到任务完成。UI 与引擎通过 Qt Signal 解耦，确认弹窗走 confirm_action 信号回调。

<details> <summary>模块清单（点击展开）</summary>
main.py                    入口
├── agent.py               Agent 循环引擎（QThread 多线程）
│   ├── tools.py           核心工具（联网/文件/知识库/多模态/记忆 33 工具）
│   ├── system_control_tools.py    系统操控（14 工具）
│   ├── software_control_tools.py  软件操控（10 工具）
│   └── browser_control_tools.py   浏览器自动化（4 工具）
├── risk.py                风险分类（4 档，权限引擎的唯一事实来源）
├── permissions.py         权限引擎（5 模式 + 路径作用域 + 会话信任）
├── task_graph.py          任务图引擎（DAG + 线程池并行）
├── memory_store.py        记忆存储（长期记忆 + 自愈）
├── harness.py             操作经验库
├── trace_log.py           轨迹记忆
├── task_resume.py         断点续跑
├── skill_loader.py        技能热插拔
├── rag.py                 向量知识库（Chroma）
├── automation.py          定时/自动化任务
├── voice.py               语音 ASR/TTS
├── mcp_client.py          MCP 协议客户端
├── ui.py                  主界面（多面板工作台）
├── digital_twin_panel.py  数字人分身面板
├── director_panel.py      导演台面板
└── video_pipeline.py      视频流水线
 
</details>
适用场景
📚 学习 Agent 工程：完整的多步推理循环、权限设计、记忆系统实现，代码可读可改
🛠️ 二次开发自己的桌面助手：模块化设计，换 LLM / 加工具 / 改 UI 都有清晰切入点
💻 日常生产力：多角度调研、文档处理、定时自动化、批量软件操作
不适用场景（诚实说明）
❌ macOS / Linux（系统操控层依赖 Windows API，跨平台需自行适配）
❌ 需要企业级多用户/审计合规的场景（这是个人工具，不是平台）
安全提醒
config.json 含你的 API Key，别分享、别上传公网
~/Documents/AgentDesktop/ 下有聊天记录、记忆、技能等本地数据，注意保护
危险工具（执行命令、系统/软件操控、浏览器点击）默认需要确认；请勿在不受信环境开启 auto 模式
EXTERNAL 类工具（发邮件、webhook）默认不放行，需在配置里显式加入 external_allow 白名单
许可证
本项目仅供学习与个人使用。二次开发与分发请遵守相关法律法规与各模型服务商条款。

<div align="center">
如果这个项目对你有帮助，欢迎 Star 支持 ⭐

</div>
