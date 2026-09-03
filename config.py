# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 配置模块"""

import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
import time
import memory_store

# ---------- APP_DIR ----------
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- 产物目录（统一落点，便于「打开产物文件夹」） ----------
# 所有生成类产物（图片/截图/视频）统一写到用户文档下的「产物」目录，
# 而非程序目录（dist）深处，避免用户难找。UI 端用 os.path.join(APP_DIR, rel)
# + abspath 解析 rel（含 .. 上溯）打开，路径仍正确，聊天显示不受影响。
PRODUCTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "产物")

# ---------- 版本 ----------
APP_VERSION = "v4.108"
APP_BUILD_DATE = "2026-09-03"
# v4.102（2026-08-22）图像输入链路：DeepSeek 通道模型换 deepseek-v4-flash-vision-exp，
# ui.py 支持视觉模型保留 image_url、普通对话/Agent 带图路由视觉模型。
# v4.101（2026-08-21）停止按钮 + 断点续传：普通 Agent 任务停止→检查点 paused→「▶ 继续上次任务」
# + 编排取消保留检查点可续跑（task_resume.mark_paused / _resume_agent_task / _scan_agent_resume）。
# v4.85（2026-08-17）集成版：生视频分辨率选择器（8 预设，实测 Agnes 透传任意 WxH 至 4K）
# + 数字人分身面板（digital_twin_panel，本人形象库+口播+首帧锁定）
# + 导演台面板（director_panel + video_pipeline 内核：LLM 剧本/分镜→逐镜生成→尾帧接力→ffmpeg 合成）。

# 更新检查源（留空=本地构建，无自动更新通道；联系构建者重打包新版即可）
UPDATE_CHECK_URL = ""

# ---------- 日志 ----------
logging.basicConfig(
    filename=os.path.join(APP_DIR, "debug.log"),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("dsdesktop")

# ---------- 配置 ----------
# v4.79 起：config.json 迁到用户文档目录，与 sessions/记忆等数据同处。
# 原因：存程序目录(dist)时，每次重打包整个目录被移走，用户 API key/模型设置全丢。
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI")
LEGACY_CONFIG_PATH = os.path.join(APP_DIR, "config.json")   # 旧位置（仅迁移用）
CONFIG_PATH = os.path.join(USER_DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "hotkey": "ctrl+shift+d",
    "system_prompt": (
        "你是「小臭玩AI」—— 大哥(xyb)的 Windows 本地桌面工作台。\n\n"
        "## 强制路由表 — 命中以下关键词必须调用对应工具，禁止只给文字回答\n"
        "| 场景/关键词 | 必须调用 | 说明 |\n"
        "|------------|---------|------|\n"
        "| 查/搜/最新/新闻/天气/股价/汇率/事件 | web_search | 联网搜索 |\n"
        "| 打开链接/网页内容/URL/抓取 | web_fetch | 抓取网页正文 |\n"
        "| 写/保存/创建 文件/笔记 | write_file | 写入工作区 |\n"
        "| 读/查看 文件/代码/笔记 | read_file | 读取工作区文件 |\n"
        "| 运行命令/执行命令/列文件/跑脚本 | run_command | 工作区内执行 |\n"
        "| 计算/分析/数据处理/爬取/写Python | run_python | 执行 Python 代码 |\n"
        "| 生成图/画图/配图/海报/插画/再画一张 | image_gen | Agnes 生图 |\n"
        "| 生成视频/做视频 | video_gen | Agnes 生视频 |\n"
        "| 定时任务/自动化任务/每天/每周/每日/定期/每天早上/每晚 | create_automation | 建自动化任务 |\n"
        "| 提醒/闹钟/N分钟后/N小时后/倒计时 | schedule | 一次性提醒 |\n"
        "| 截图/截屏 | screenshot | 屏幕截图 |\n"
        "| 知识库/Obsidian/我的笔记/规划/项目 | rag_search | 本地知识库搜索 |\n"
        "| 记笔记/待办/备忘/素材 | db_insert/db_query | SQLite 数据库 |\n"
        "| 图表/柱状图/折线图/饼图/散点图 | chart_gen | 数据可视化 |\n"
        "| 日志/报错/错误排查 | log_query | 查询运行日志 |\n"
        "| 清空回收站/锁屏/关机/打开设置/系统操作 | system_run | system_* 桌面系统控制 |\n"
        "| 打开文件/打开应用/控制软件/输入文字/点按钮 | software_run | software_* 软件控制 |\n"
        "| 公众号/写文章/写稿子/续写 | use_skill(公众号文章) | 写作技能 |\n"
        "| 技能/装技能/搜索技能 | skill_search/skill_install | 技能管理 |\n"
        "| 浏览器打开/网页点击/填表 | browser_open/browser_click | 浏览器操作 |\n"
        "| 偏好/记住/长期/约定 | remember | 写入长期记忆 |\n\n"
        "## 全部能力速查\n"
        "联网搜索(web_search) | 网页抓取(web_fetch) | 文件读写(read_file/write_file) | "
        "命令(run_command) | Python(run_python) | 生图(image_gen) | 生视频(video_gen) | "
        "定时(schedule) | 自动化任务(create_automation/list_automation/delete_automation) | 截图(screenshot) | 知识库RAG(rag_index/rag_search，Obsidian Vault: C:\\Users\\xyb\\Documents\\_mybase) | "
        "数据库(db_insert/db_query/db_update/db_delete：notes/todos/assets) | 图表(chart_gen) | "
        "日志(log_query) | 上下文(context_compress/context_summary) | "
        "Webhook(webhook_start/events/stop，端口9000) | 技能(use_skill/skill_search/skill_install) | "
        "ASR(SenseVoiceSmall) | TTS(edge-tts) | 浏览器(browser_open/click/fill/read) | "
        "系统控制(system_*：剪贴板/窗口/进程/输入) | 软件控制(software_*：pywinauto) | 长期记忆(remember)\n\n"
        "## 模型\n"
        "默认 Agnes（agnes-2.5-flash，永久免费）；DeepSeek 为大哥付费主力通道。\n\n"
        "## 硬约束\n"
        "1. 命中路由表关键词→直接调工具，禁止纯文字回答\n"
        "2. 时间/事实性问题→调 web_search，禁止凭模型知识猜测\n"
        "3. 工具结果为准，严禁编造不存在的数据\n"
        "4. 能推断参数不追问，用合理默认值\n"
        "5. 成功报产物路径，失败报真实错误，禁止谎称已完成\n"
        "6. 「再来/重新/重做/regenerate」→重新调工具，禁止复用历史结果\n"
        "7. 产物路径统一到 ~/Documents/小臭玩AI/ 对应子目录\n"
        "8. **禁止承诺式循环**：禁止连续多轮只输出「我现在开始做/马上做/下一步执行」之类的承诺而不真去调工具。每一步要么调工具、要么给出最终成品，否则算任务失败。\n"
        "9. **用户意图优先**：用户原话意图明确时，禁止跳到不相关技能/工具（如「清空回收站」→调 system_clean_recycle_bin，不许跑去调 ppt-generator）。\n"
        "10. **选题/盘点/列方向 类需求优先用训练知识直接出文本**（见下方【爆款选题与盘点模板】），"
        "仅在用户明确说「去搜/查最新/爬数据/看实时榜单」时才调 web_search。"
        "「搜索」一词在该语境下指的是「检索联网最新数据」，不是「列选题方向」——"
        "不要把「给我列几个方向」误解成「去搜实时榜单」。\n"
    ),
    "max_history": 30,
    "search_enabled": True,
    "search_provider": "auto",
    "search_top_k": 5,
    # 付费搜索兜底：填入 SerpAPI key 后，内容平台类查询自动走 SerpAPI（稳定、抗反爬），
    # 免费引擎（搜狗/Bing）仅作无 key 时的兜底。留空则纯免费。
    "search_api_key": "",
    "agent_skip_confirm": False,
    "onboarded": False,       # v4.79：新手引导是否已看过（看过则不再弹）
    "image_gen_provider": "agnes",
    "image_gen_model": "agnes-image-2.5-flash",
    "image_gen_size": "1920x1080",
    "sd_webui_url": "http://127.0.0.1:7860",
    "gateway_url": "http://127.0.0.1:8000",
    "gateway_autostart": True,   # v4.79：识图后端(free-api-gateway)随 APP 自动拉起
    "gateway_dir": r"C:\Users\xyb\WorkBuddy\2026-07-06-23-07-12\free-api-gateway",  # 网关项目目录（含 run_gateway.bat / app/main.py）
    "harness_notes_path": os.path.join(USER_DATA_DIR, "harness_notes.json"),  # v4.80：可自我 refine+版本回滚的操作经验库（借鉴 Prime-Agent Continual Harness）
    "task_resume_dir": os.path.join(USER_DATA_DIR, "task_resume"),  # v4.81：长任务断点续跑/心跳检查点目录（借鉴 Prime-Agent daemon 续跑）
    "orch_auto_resume": True,  # v4.82：长任务断点自动续跑（崩溃/强杀后重开 APP 自动从断点继续，无需手动点「继续」）
    "task_trace_dir": os.path.join(USER_DATA_DIR, "task_traces"),  # v4.83(D)：长任务成功轨迹记忆目录（借鉴 Prime-Agent Continual Harness 之轨迹记忆）
    "orch_trace_enabled": True,  # v4.83(D)：新鲜启动编排时检索相似成功轨迹作 few-shot 注入
    "orch_trace_auto_refine": True,  # v4.84(热修15·A)：达阈值后保守自动提炼经验库笔记（默认开，知识层软自进化闭环）
    "orch_trace_max": 200,  # v4.83(D)：轨迹文件最大条数（超出裁最旧）
    "skills_pending_dir": os.path.join(USER_DATA_DIR, "skills_pending"),  # v4.84(热修15·B)：模型自动创建的技能先落此「待审核」目录，审核通过才进 skills/
    "_mig_v484_self_evolve": False,  # v4.84 迁移标记：一次性把存量配置的 orch_trace_auto_refine 翻成 True
    "mcp_servers": [
        {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem",
                     "C:/Users/xyb/WorkBuddy/2026-07-11-22-26-49/deepseek-desktop",
                     "C:/Users/xyb/Documents/小臭玩AI"],
            "enabled": True
        }
    ],
    "model_profiles": {
        "DeepSeek 官方": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash-vision-exp", "api_key": ""},
        "硅基流动": {"base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3", "api_key": ""},
        "智谱 GLM": {"base_url": "https://open.bigmodel.cn/api/ai/v1", "model": "glm-4-flash", "api_key": ""},
        "腾讯混元": {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "model": "hunyuan-lite", "api_key": ""},
        "免费网关 free-api-gw": {"base_url": "http://127.0.0.1:8000/v1", "model": "zhipu", "api_key": ""},
        "魔搭 ModelScope": {"base_url": "https://api.modelscope.cn/v1", "model": "qwen2.5-7b-instruct", "api_key": ""},
        "Agnes": {"base_url": "https://apihub.agnes-ai.cn/v1", "model": "agnes-2.5-flash", "api_key": ""},
    },
    # v4.109：模型下拉选择。""=Auto 智能路由（默认行为，等同 v4.108）；
    # "__main__"=锁定主模型；其余=锁定 model_profiles 里的档位名（如 "DeepSeek 官方"）。
    "model_lock": "",
    "model_routing": {
        "enabled": True,                 # 模型智能路由开关
        "complex_model": "DeepSeek 官方",  # 复杂任务升级到的 profile（必须已在 model_profiles 且填了 api_key）
        "length_threshold": 1500,         # 消息总长度超过此值 → 判为复杂任务
        "complex_hint": ["代码", "编程", "分析", "报告", "深度", "推理", "终审", "架构", "设计", "重构", "review", "写代码"],
    },
    "rag_data_dir": "",     # RAG 索引数据目录，默认 {APP_DIR}/rag_data
    "rag_enabled": True,
    "embedding_api_key": "",  # embedding API key，留空自动从 model_profiles 中找 SiliconFlow 的
    "embedding_base_url": "https://api.siliconflow.cn/v1",  # OpenAI 兼容 embedding API 地址
    "embedding_model": "BAAI/bge-large-zh-v1.5",  # embedding 模型名
    "vision_model": "OpenGVLab/InternVL2-8B",  # 图片/视频帧理解的视觉模型（硅基流动 VLM，免费）
    "obsidian_vault_path": "",  # Obsidian 仓库路径，留空自动检测
    "obsidian_enabled": True,   # Obsidian 集成总开关（false 完全跳过，加速启动）
    "skills_dir": "",       # 动态技能目录，默认 {APP_DIR}/skills
    # 剪贴板自动监听（模块1）
    "clipboard_enabled": True,
    "clipboard_interval": 2,
    "clipboard_auto_fetch": True,
    "clipboard_auto_format": True,
    "clipboard_notification": "tray",
    # 上下文窗口智能管理（模块4）
    "context_enabled": True,
    "context_max_window": 20,
    "context_compress_threshold": 10,
    # SQLite 数据库操作（模块5）：落用户目录，无需额外配置
    # Webhook / 事件驱动（模块6）
    "webhook_enabled": False,      # 默认关闭，避免未经意开启端口；可用 webhook_start 工具或设为 true 自动启动
    "webhook_port": 9000,
    # v4.108 M-28：默认回环绑定 + 共享 token（启动时若为空会自动生成持久化）。
    # 原先 0.0.0.0 裸奔，局域网任何人可 POST /api/trigger 伪造事件。
    "webhook_host": "127.0.0.1",
    "webhook_token": "",
    # 技能管理器（模块7）：已启用技能清单
    "enabled_skills": [],
    # v4.111 工具开关：白名单。空 = 全部启用（向后兼容，行为零变化）。
    # 实测 68 个工具定义共 26,699 字符，占每次 API 输入 token 约 65%；
    # 其中 30 个从未被用过却占 42.9% 体积。关掉不用的 = 最省的一刀，且随时能开回来。
    # 生成清单：python tool_budget.py --suggest
    "enabled_tools": [],
    # v4.76：OS 级自动备份（Windows 任务计划程序）
    "autobackup_freq": "",        # ""=关闭 / "daily" / "weekly"
    "autobackup_time": "03:00",   # HH:MM
    # v4.76：更新检查源（留空=本地构建；填入可访问的 version.json URL 即启用在线检查）
    "update_check_url": "",
}

# 爆款选题与盘点模板（v4.56）
# 当用户要「列选题/盘点爆款方向/给建议」时优先注入这段，让 AI 用训练知识出文本，
# 避免无意义地先 web_search 拉一堆不可靠的搜索结果。
TOPIC_IDEA_TEMPLATE = """

## 爆款选题与盘点模板（v4.56 专设）

### 适用场景
用户问「列几个方向 / 写什么选题 / 盘点爆款 / 给我建议 / 想做 X 类内容 / 哪个赛道值得做」时，
**直接基于下方模板 + 你自己的训练知识** 给出 20-30 个候选选题 + 各选题钩子/封面建议，
不必先 web_search（除非用户明确说「查最新数据 / 看实时榜单 / 爬数据」）。

### 平台爆款选题角度（按平台划分，AI 自取）

**小红书**（高互动选题 5 大类）
1. 治愈/情绪：独居日常、慢生活、读书、深夜emo、陪伴感
2. 实用干货：干货清单、避坑指南、合集盘点、工具/APP 推荐
3. 视觉冲击：OOTD、家居改造、前后对比、妆容前后
4. 争议/共情：年龄焦虑、原生家庭、职场 PUA、男女差异
5. 季节/热点：节日仪式、季节限定、节气、热点借势

**抖音**（高完播选题 5 大类）
1. 反常识/反转：开头 3 秒抛出反常识结论
2. 情感共鸣：亲情/友情/爱情/励志
3. 实用技巧：1 分钟学会 X、教程合集
4. 知识科普：冷知识、趣闻、原理可视化
5. 强烈视觉：剧情反转、帅哥美女、惊险瞬间

**视频号**（中老年友好）
1. 家庭温情：子女、夫妻、父母日常
2. 养生保健：中医、食材、节气、动作
3. 爱国正能量：感动中国、军人、英雄
4. 怀旧金曲：经典老歌、画面
5. 生活窍门：厨房、清洁、小妙招

**公众号**（长文深度）
1. 观点输出：行业洞察、社会观察
2. 个人故事：成长、经历、转折
3. 干货合集：方法论、工具盘点
4. 情感共鸣：人生感悟、关系反思
5. 趋势解读：未来预测、变化分析

**知乎**（问答/长文）
1. 行业内幕：XX 行业真实情况
2. 个人经历：我是怎么 X 的
3. 反常识观点：大多数人都错了
4. 数据盘点：2024-2026 趋势
5. 方法论分享：我是如何 X 的

### 标准输出格式（用户没指定格式时默认用这个）

对每个选题，**至少给这 4 项**：
1. **选题标题**：用户一眼会点的钩子句（小红书 ≤20字、抖音 ≤30字、视频号 ≤25字、公众号 15-25字、知乎 15-30字）
2. **目标受众**：谁会看（年龄/性别/职业/痛点）
3. **核心钩子**：第一句话/前 3 秒/首图要传达什么
4. **封面/标题建议**：封面文案 + 视觉元素（色彩/构图/IP 形象）
5. **爆款因子**：为什么这条会火（情绪/实用/争议/季节性/反差）

如平台未指定，默认覆盖**小红书 + 抖音**（大哥主战场）。
如目标用户未指定，默认按「小红书泛大众 18-35 岁女性」展开。

### 注意事项
- **不要凭空编造具体数据**（如「2024 年小红书 XXX 品类增长 200%」）——除非你确定有据可查，否则用「据训练数据」「通常情况下」模糊表述
- **实时数据必须搜**（如「今天热搜」「最近 7 天榜单」），模型训练数据可能已过时
- 选题不必全展开——可先给 20-30 个标题 + 简述，让大哥挑 3-5 个再深耕
"""


# ---------- Agent 模式（v4）：工具定义 + 系统提示 ----------
AGENT_SYS_APPEND = (
    "\n## 执行风格铁律（v4.104 新增，违反即失败）\n"
    "- 能一步做完绝不两步：优先选用「一次调用就能直接达成目标」的工具，"
    "不要先探测再操作、不要「先看看再动手」、不要做多余的前置调用。\n"
    "- 只调真正必要的工具：回答能直接给的就不调工具；一个工具能拿到的结果不要拆成两个。\n"
    "- 禁止自问自答、禁止复述步骤、禁止「我先做 X 然后做 Y 然后…」式的计划播报，"
    "直接执行并汇报结果。\n"
    "- 调工具前想清楚：这一步调用后能否离目标更近？不能就不调。\n"
    "- 最终回答只给结论和必要信息（产物路径/关键数字），不要重复工具过程。\n"
    "\n## 工具调用补充规则\n"
    "【重要】你运行在 Windows 系统上（PowerShell），不是 Linux / macOS。"
    "禁止使用 cat / grep / ls / head / tail / sed / awk 等 Unix 命令，"
    "它们会报错「不是内部或外部命令」导致工具调用浪费。"
    "读取文件内容请用 read_file 工具，脚本请用 run_python，批量操作请用 PowerShell。\n"
    "run_command 在 Windows 走 PowerShell：列文件用 Get-ChildItem -Recurse，错误重定向用 2>$null"
    "（不是 2>nul），文本搜索用 Select-String（不是 findstr），否则命令会报错浪费次数。\n"
    "- 最多连续调 12 轮工具；复杂任务分批执行（每批 ≤5 项），接近上限优先落盘\n"
    "- 文件路径用相对于程序目录的相对路径（如 notes/todo.txt）\n"
    "- write_file / run_command / run_python / browser 类操作执行前弹确认框\n"
    "- image_gen 返回的图片路径直接在对话中显示\n"
    "- use_skill 需传入准确的 skill_name（见【可用技能】清单）\n"
    "- skill_install 自动拉取并安全审计（P0 危险指令拒绝安装，P1 放行并提示）\n"
    "- 工具结果超 6000 字符时会被截断，有截断时告知用户\n"
    "- 做数据分析/报告时：先用 web_search 抓 2-3 个来源的具体数据（务必带数字、年份、平台名），"
    "再用 run_python 把关键指标汇总成表格/图表，最后 write_file 落盘；禁止凭空编造数据\n"
    "- 若收到『自动续跑提示』类系统消息：说明上一轮已执行过部分工具（历史里有调用与结果），"
    "必须先从断点接着推进原始目标，禁止再说「我来搜索/我开始」之类空话，禁止重复已完成的搜索\n"
    "- 搜索内容平台（小红书/抖音/知乎等）时，web_search 已自动用高质量来源（搜狗）展开"
    "『趋势报告/用户画像/爆款策略/赛道数据』多角度搜索并聚合返回；直接基于这些真实文章/报告"
    "做数据分析（抓具体数字+年份+来源），无需自己重复搜同一平台\n"
    "- 选题/盘点/列方向 类需求：直接基于系统提示中的【爆款选题与盘点模板】"
    "用训练知识出文本，禁止把这类需求误判成「去搜实时榜单」"
)


def get_skill_scan_dirs():
    """返回所有要扫描的技能目录（绝对路径，去重、保序）。

    **单一路经策略（v4.79 技能统一）**：
    用户目录 ~/Documents/小臭玩AI/skills 排第一、作为唯一完整来源（内置技能已
    复制进用户目录），内置/打包目录降为兜底（仅补全用户目录缺失的）。
    这样重打包（safe-delete 整体搬 dist）不影响技能可见性，技能物理上只认用户目录。

    顺序：用户目录 → 内置/打包 skills（兜底）→ config.skills_dir（自定义，若有）。
    """
    dirs = []
    # 用户目录（唯一完整来源，大哥自己加技能的地方，重打包不影响）
    user_dir = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "skills")
    if user_dir:
        dirs.append(user_dir)
    # 内置/打包技能目录（兜底：仅补全用户目录没有的，防用户目录被清空）
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "skills"))  # 顶层（某些打包方式）
        dirs.append(os.path.join(os.path.dirname(sys.executable), "_internal", "skills"))  # onedir _internal
    else:
        dirs.append(os.path.join(APP_DIR, "skills"))
    # 自定义目录（config.json 的 skills_dir）
    try:
        cfg_dir = ""
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg_dir = json.load(f).get("skills_dir", "")
        if cfg_dir:
            dirs.append(cfg_dir)
    except Exception as e:
        log.warning("读取 skills_dir 配置失败: %s", e)

    # 去重、保序
    seen = set()
    result = []
    for d in dirs:
        ad = os.path.abspath(d)
        if ad not in seen:
            seen.add(ad)
            result.append(ad)
    return result


def _load_enabled_skills():
    """读取 config.json 的 enabled_skills 列表（v4.67 起用于对话侧技能过滤）。

    失败或字段缺失时返回 []，表示「未配置」→ 调用方按全启用处理（向后兼容）。
    """
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("enabled_skills", []) or []
    except Exception as e:
        log.warning("读取 enabled_skills 失败: %s", e)
    return []


def _load_enabled_tools():
    """读取 config.json 的 enabled_tools 白名单（v4.111 起用于工具注入过滤）。

    失败或字段缺失时返回 []，表示「未配置」→ 调用方按全启用处理（向后兼容）。

    规则与 enabled_skills（v4.67）完全一致：空 = 全开，非空 = 只留显式列出的。
    """
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("enabled_tools", []) or []
    except Exception as e:
        log.warning("读取 enabled_tools 失败: %s", e)
    return []


def _filter_enabled_tools(tools, cfg=None):
    """按 enabled_tools 白名单过滤工具清单。

    取值顺序：**优先用调用方传进来的 cfg**，取不到再读 config.json 文件。
    （只读文件会和调用方手上的 cfg 脱节——测试改了内存里的 cfg 却读不到，
      表现就是"配了白名单但没生效"，极难排查。）

    为什么动这里（v4.111 实测）：68 个工具定义 26,699 字符，占每次 API 输入
    token 的约 65%；Agent 路径每轮全量注入，长会话（知乎那条 323 次工具调用）
    把这项放大几百倍。**功能可以无限堆，但每轮注入量不能跟着涨**——否则
    「功能怪兽」路线会被自己的成本拖死。

    ⚠ 过滤掉的等于不存在：模型看不见就不会调。但只是不注入、不是删除，
      配置里加回来即可恢复。
    ⚠ 这里过滤后 `_build_tool_overview`（系统提示里的工具概览）也会一起瘦——
      它同样读 get_all_tools，不会漏改。
    """
    enabled = cfg.get("enabled_tools") if isinstance(cfg, dict) else None
    if enabled is None:
        enabled = _load_enabled_tools()
    if not enabled:
        return tools
    keep = set(enabled)
    out = []
    for t in tools:
        fn = (t or {}).get("function") or {}
        name = fn.get("name") or (t or {}).get("name") or ""
        if name in keep:
            out.append(t)
    # 白名单里写了不存在的工具名（拼错 / 工具已改名）→ 明确告警，别静默吞掉
    gone = keep - {((t or {}).get("function") or {}).get("name")
                   or (t or {}).get("name") or "" for t in tools}
    if gone:
        log.warning("enabled_tools 里有 %d 个名字匹配不到任何工具（拼错或已改名）：%s",
                    len(gone), sorted(gone))
    return out


def load_dynamic_skills(compact=False):
    """扫描所有技能目录，返回格式化的可用技能清单文本，注入系统提示。

    支持 .py 与 SKILL.md 两种形态，跨多目录自动合并去重。
    无可用技能时给出占位说明（含用户目录提示）；失败时返回空字符串，不影响主流程。

    v4.87 省 token：compact=True（默认用于对话系统提示）只列「name：首句摘要(≤36字)」，
    完整 description 在 use_skill 真正加载该技能时由其 SKILL.md 注入系统提示；
    compact=False（诊断/统计用）保留原完整 description。
    """
    try:
        from skill_loader import get_available_skills, normalize_skill_name
    except Exception as e:
        log.warning("导入 skill_loader 失败: %s", e)
        return ""

    dirs = get_skill_scan_dirs()
    all_skills = []
    seen = set()
    for d in dirs:
        try:
            for sk in get_available_skills(d):
                n = sk.get("name", "")
                if n and n not in seen:
                    seen.add(n)
                    all_skills.append(sk)
        except Exception as e:
            log.error("扫描技能目录失败 %s: %s", d, e)

    # v4.67：让技能管理器的「启用/禁用」在对话侧真实生效。
    # 规则：enabled_skills 为空（未配置）→ 全部启用（向后兼容）；
    #       非空 → 仅保留被显式启用的技能。
    enabled = _load_enabled_skills()
    if enabled:
        keep = {normalize_skill_name(n) for n in enabled}
        filtered = [sk for sk in all_skills
                    if normalize_skill_name(sk.get("name", "")) in keep]
        if filtered:
            all_skills = filtered

    if not all_skills:
        if compact:
            return ""
        return ("\n\n【可用技能】\n"
                "（当前无可用技能；把技能放进 技能名/SKILL.md 目录，"
                "置于 ~/Documents/小臭玩AI/skills 即可被自动识别）")

    # 「技能必须落地」硬约束——堵住「连续多轮只输出承诺不调工具」的承诺式循环（compact/完整都保留）
    HARD = [
        "## ⚠️ 技能执行硬约束（适用所有可用技能）",
        "1. 加载技能后**必须立即调用 run_python / write_file / run_command 落地**，禁止只输出大纲/计划/承诺。",
        "2. 交付物必须是**实物**（文件/写入/计算结果），不是文字描述。",
        "3. 完成后必须返回**产物绝对路径**（默认 ~/Documents/小臭玩AI/ 对应子目录）。",
        "4. 不允许「我先思考一下」「下一步再调工具」之类的纯文字回应——每一步必须产生可验证的副产物。",
        "5. 用户说「做 X」= 立即产出 X，不是「先列 X 的章节大纲」。",
    ]
    if compact:
        lines = ["\n\n【可用技能】（用 use_skill 按 name 加载，name 不含 emoji 前缀）："]
        for sk in all_skills:
            name = sk.get("name", "")
            # 只取首句并截断到 36 字——足够模型判断「这个技能适不适合用户需求」，
            # 完整 prompt 在 use_skill 真正加载该技能时注入，避免每轮全量平铺 52 个技能全文。
            desc = (sk.get("description", "") or "").split("\n")[0].strip()
            short = desc[:36]
            lines.append(f"- {name}：{short}" if short else f"- {name}")
        lines.append("")
        lines.extend(HARD)
        return "\n".join(lines)

    lines = ["\n\n【可用技能】（用 use_skill 工具按 name 加载，name 不含 emoji 前缀）："]
    for sk in all_skills:
        name = sk.get("name", "")
        desc = sk.get("description", "")
        # 注意：只列 name，不带 emoji 前缀——否则模型会照抄「📊 ppt-generator」
        # 这种带 emoji 的名字去调 use_skill，而 SKILL.md 的 name 不含 emoji → 匹配失败。
        lines.append(f"- {name}：{desc}")
    lines.append("")
    lines.extend(HARD)
    return "\n".join(lines)

from tool_defs import TOOL_DEFS  # 工具定义见 tool_defs.py（已从 config.py 抽离）

MAX_AGENT_STEPS = 20
# 续跑单轮步数上限（独立于 MAX_AGENT_STEPS，便于单独调参防空转烧 token）。
AGENT_RESUME_STEPS = 20
# Agent 单轮步数耗尽后自动续跑的轮数（每轮再给 MAX_AGENT_STEPS 步，封顶防无限循环）
# 总预算 = (1 + AGENT_RESUME_ROUNDS) * MAX_AGENT_STEPS 步。设为 0 则回到旧的硬停行为。
# v4.104.1（2026-08-31）：v4.104 曾收紧到 24 步（12/12/1），实测复杂任务不够用、
# 「任务随时断」。现放宽回总 60 步（20/20/2）。
# 断的根因交给 token 预算管（见下方 AGENT_TOKEN_BUDGET），步数只防死循环，不防花钱。
AGENT_RESUME_ROUNDS = 2
TOOL_READ_LIMIT = 8000
TOOL_RESULT_LIMIT = 6000


def get_agent_step_budget(cfg=None):
    """取当前生效的步数预算：优先 config.json（cfg），回退模块默认。

    返回 (max_steps, resume_steps, resume_rounds)。
    v4.104.1：步数此前写死在代码里，调一次要重打包 8 分钟；改为可配置后
    改 config.json 重启即生效。非法值（非正数/非数字）一律回退模块默认。
    """
    d = cfg if isinstance(cfg, dict) else {}
    out = []
    for key, default in (("agent_max_steps", MAX_AGENT_STEPS),
                         ("agent_resume_steps", AGENT_RESUME_STEPS),
                         ("agent_resume_rounds", AGENT_RESUME_ROUNDS)):
        try:
            v = int(d.get(key, default))
        except (TypeError, ValueError):
            v = default
        if v < 0:
            v = default
        out.append(v)
    return tuple(out)

# ---------- v4.102 fix12：Agent 单任务 token 预算熔断 ----------
# 背景：续跑/步数预算（AGENT_RESUME_*）只约束「轮次」，不约束「token 花销」。
# DeepSeek 付费路由一旦被大量触发（复杂任务自动升舱），单任务 token 可无上限
# 累积——这正是 Codex /goal 烧钱的同源风险。Agnes 免费主通道不烧钱，付费通道
# 必须设红线。**设为 0 即完全禁用熔断**（行为回退）。
# v4.104.1（2026-08-31）：大哥反馈「任务随时断」→ 总预算 200K → 400K。
# 同日再提：大哥要求「400000+」→ 400K → 500K，留足复杂任务余量。
# 同时付费档 150K → 0（跟随总预算）。原因：熔断取的是
#   _limit = min(总预算, 付费档预算)   # 只要任务触发过一次 DeepSeek 就生效
# 付费档 150K 会先把 limit 从 200K 拉到 150K，等于总预算形同虚设，
# 这才是「怎么老断」的真凶。改 0 后单一真相源，只调 agent_token_budget 一个数。
AGENT_TOKEN_BUDGET = 500000           # 单任务 token 硬上限（0 = 禁用熔断）
AGENT_TOKEN_WARN = 0.8                # 达预算该比例时提前告警一次
AGENT_TOKEN_BUDGET_DEEPSEEK = 0       # 付费通道单独更紧（0 = 跟随总预算）


def get_agent_token_budget(cfg=None):
    """取当前生效的 token 预算配置：优先 config.json（cfg），回退模块默认。

    返回 (budget, warn_ratio, deepseek_budget)。
    budget=0 表示禁用熔断；deepseek_budget=0 表示付费通道跟随总预算。
    """
    d = cfg if isinstance(cfg, dict) else {}
    try:
        budget = int(d.get("agent_token_budget", AGENT_TOKEN_BUDGET))
    except (TypeError, ValueError):
        budget = AGENT_TOKEN_BUDGET
    try:
        warn = float(d.get("agent_token_warn", AGENT_TOKEN_WARN))
    except (TypeError, ValueError):
        warn = AGENT_TOKEN_WARN
    try:
        ds = int(d.get("agent_token_budget_deepseek",
                       AGENT_TOKEN_BUDGET_DEEPSEEK))
    except (TypeError, ValueError):
        ds = AGENT_TOKEN_BUDGET_DEEPSEEK
    if budget < 0:
        budget = 0
    if not 0 < warn <= 1:
        warn = AGENT_TOKEN_WARN
    return budget, warn, ds


# ---------- 技能库（v4.5，DEFAULT_SKILLS 仅作工具栏技能兜底常量，不再落盘 skills.json）----------
DEFAULT_SKILLS = {
    "skills": [
        {"id": "xiaohongshu", "name": "小红书文案", "emoji": "📕",
         "category": "内容创作", "desc": "生成吸睛的小红书图文文案",
         "prompt": "你是小红书爆款文案写手。\n输出结构：吸睛标题（带emoji）→ 分段正文（短句+空行）→ 相关话题标签。\n语气亲切有共鸣，多用口语化表达。\n只讲场景适配，不点名拉踩任何工具或平台。"},
        {"id": "gzh", "name": "公众号文章", "emoji": "📝",
         "category": "内容创作", "desc": "生活感悟/观影/读书笔记类公众号文章",
         "prompt": "你是公众号主笔，写生活感悟、观影感悟、读书笔记类内容。\n排版清爽（小标题+短段落+金句加粗），语气温和真诚。\n注意：不出现具体城市、行业、职业身份（电力相关内容是禁区，绝不提及）。"},
        {"id": "novel", "name": "小说续写", "emoji": "📖",
         "category": "内容创作", "desc": "第一人称小说续写，每500字一个钩子",
         "prompt": "你是小说创作搭档。\n用第一人称「我」续写，每约500字设置一个钩子保持悬念，延续用户给定的人设与文风。\n先理解已有剧情再动笔，不擅自推翻设定。"},
        {"id": "shortvideo", "name": "短视频脚本", "emoji": "🎬",
         "category": "内容创作", "desc": "抖音/视频号竖屏短视频脚本",
         "prompt": "你是短视频脚本编剧。\n输出竖屏(9:16)脚本：0-2秒强钩子 → 分镜（画面/台词/时长标注）→ 结尾引导互动。\n适配抖音/视频号风格，节奏快、信息密度高。"},
        {"id": "translate", "name": "翻译", "emoji": "🌐",
         "category": "效率办公", "desc": "中英互译，保留语气与格式",
         "prompt": "你是专业翻译。\n默认中英互译，保留原文语气、专有名词与格式；可按要求切换风格（直译/意译/本地化）。\n只输出译文与必要说明。"},
        {"id": "summarize", "name": "总结提炼", "emoji": "📋",
         "category": "效率办公", "desc": "把长文/会议/资料压缩为要点",
         "prompt": "你是信息提炼专家。\n把长文、会议或资料压缩为结构化要点（分点+关键词加粗+一句结论），保留关键数据与来源。\n忠于原意，不编造。"},
        {"id": "rewrite", "name": "改写润色", "emoji": "✨",
         "category": "效率办公", "desc": "不改原意，提升表达与可读性",
         "prompt": "你是文字润色师。\n在不改变原意前提下提升表达与可读性；可指定目标风格（正式/口语/活泼/精简）。\n主要给出润色后全文，必要时简短指出优化处。"},
        {"id": "weekly", "name": "周报/纪要", "emoji": "🗓️",
         "category": "效率办公", "desc": "生成周报或会议纪要模板",
         "prompt": "你是职场文档助手。\n根据零散事项生成周报（本周完成/进行中/下周计划）或会议纪要（议题-结论-待办+负责人+时限），格式清晰可直接用。"},
        {"id": "officecli", "name": "Office 文档处理", "emoji": "📎",
         "category": "效率办公", "desc": "用 Python/LibreOffice 生成编辑转换 Word/Excel/PPT",
         "prompt": "你是 Office 文档处理助手。\n用 run_python 生成/读取/编辑 Office 文档：Word 用 python-docx、Excel 用 openpyxl、PPT 用 python-pptx（本机缺库时先 `pip install python-docx openpyxl python-pptx` 再跑）。\n也支持用 LibreOffice 无界面命令行 `soffice --headless --convert-to` 做格式互转（docx↔pdf、xlsx↔csv 等）。\n操作前说明计划，生成文件放到工作区相对路径（如 docs/report.docx）并告知完整路径；大批量或覆盖已有文件先确认。"},
        {"id": "pycode", "name": "代码解释", "emoji": "🐍",
         "category": "技术自动化", "desc": "写/解释/调试 Python（可实际运行验证）",
         "prompt": "你是 Python 工程师。\n写、解释、调试 Python 代码；需要验证时主动用 run_python 工具实际运行并据输出修正。\n代码力求简洁可运行。"},
        {"id": "data", "name": "数据处理", "emoji": "📊",
         "category": "技术自动化", "desc": "用 Python 处理表格/JSON/文本",
         "prompt": "你是数据处理助手。\n用 Python 处理表格（JSON/CSV/Excel）与文本：清洗、统计、转换、可视化前处理。\n优先用 run_python 实际跑出结果再说明。"},
        {"id": "batchfile", "name": "批量文件整理", "emoji": "🗂️",
         "category": "技术自动化", "desc": "在工作区批量重命名/分类/移动",
         "prompt": "你是文件整理助手。\n用 run_command / run_python 在工作区内批量重命名、分类、移动文件。\n操作前先说明计划，危险批量动作走确认。"},
        {"id": "cmd", "name": "命令助手", "emoji": "⌨️",
         "category": "技术自动化", "desc": "自然语言转安全 shell 命令并执行",
         "prompt": "你是命令行助手。\n把自然语言转成安全的 shell 命令并在工作区执行（run_command），解释每条命令作用。\n只做无害操作，破坏性命令先确认。"},
    ]
}


def load_skills():
    """技能条统一来源（v4.79+ 单轨）：扫描用户目录 SKILL.md，仅取标记 toolbar 的技能。

    返回兼容字段列表（id/name/emoji/category/desc/description/prompt），供 ui.py 技能条
    分组展示、tooltip、点击回调使用。use_skill 对话侧另由 load_dynamic_skills() 扫描
    全部 SKILL.md（不过滤 toolbar），两套来源在此清晰分离。
    兜底：当扫描不到任何工具栏技能时，回退 DEFAULT_SKILLS（原 skills.json 14 个常量），
    保证技能条不为空（向后兼容，不再落盘 skills.json）。
    """
    try:
        from skill_loader import scan_skills
    except Exception as e:
        log.warning("导入 skill_loader 失败: %s", e)
        return list(DEFAULT_SKILLS["skills"])

    dirs = get_skill_scan_dirs()
    result = []
    seen = set()
    for d in dirs:
        try:
            for sk in scan_skills(d):
                n = sk.name
                if not n or n in seen:
                    continue
                if not getattr(sk, "toolbar", False):
                    continue
                seen.add(n)
                body = sk.body if getattr(sk, "body", "") else sk.prompt
                result.append({
                    "id": n,
                    "name": n,
                    "emoji": sk.emoji or "🛠️",
                    "category": sk.category or "其他",
                    "desc": sk.description or "",
                    "description": sk.description or "",
                    "prompt": body or sk.prompt or "",
                })
        except Exception as e:
            log.error("扫描技能目录失败 %s: %s", d, e)

    if not result:
        # 兜底：用户目录无工具栏技能时回退原 14 个（常量，不落盘）
        return list(DEFAULT_SKILLS["skills"])
    return result


def load_config():
    """Read config; auto-create with defaults on first run.

    v4.79：若新位置（~/Documents/小臭玩AI/config.json）不存在而旧位置
    （程序目录/config.json）存在，则迁移旧配置到新位置，保住用户 API 设置。
    """
    if not os.path.exists(CONFIG_PATH):
        # ---- 旧位置迁移（一次性）----
        if os.path.exists(LEGACY_CONFIG_PATH) and LEGACY_CONFIG_PATH != CONFIG_PATH:
            try:
                with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as f_old:
                    cfg_data = json.load(f_old)
                os.makedirs(USER_DATA_DIR, exist_ok=True)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f_new:
                    json.dump(cfg_data, f_new, ensure_ascii=False, indent=2)
                log.info("已从旧位置迁移配置到 %s", CONFIG_PATH)
                for k, v in DEFAULT_CONFIG.items():
                    cfg_data.setdefault(k, v)
                return cfg_data
            except Exception as e:
                log.warning("迁移旧配置失败，按首次运行处理: %s", e)
        # Frozen EXE: config.json is in _internal/, copy to EXE dir
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                src = os.path.join(meipass, "config.json")
                if os.path.exists(src):
                    try:
                        with open(src, "r", encoding="utf-8") as f_src:
                            cfg_data = json.load(f_src)
                        with open(CONFIG_PATH, "w", encoding="utf-8") as f_dst:
                            json.dump(cfg_data, f_dst, ensure_ascii=False, indent=2)
                        log.info("Copied config.json from _internal to %s", CONFIG_PATH)
                        for k, v in DEFAULT_CONFIG.items():
                            cfg_data.setdefault(k, v)
                        return cfg_data
                    except Exception as e:
                        log.warning("Failed to copy config.json: %s", e)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        log.info("Created default config.json")
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        log.error("Failed to read config.json: %s", e)
        cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)

    # v4.84 迁移：自进化默认开启（轨迹自动提炼）。仅对仍处旧默认 False 的存量配置一次性打开，
    # 之后再尊重用户手动开关（标记置位后不再翻回）。
    if not cfg.get("_mig_v484_self_evolve", False):
        if cfg.get("orch_trace_auto_refine", False) is False:
            cfg["orch_trace_auto_refine"] = True
        cfg["_mig_v484_self_evolve"] = True
        try:
            save_config(cfg)
        except Exception:
            pass

    return cfg


def save_config(cfg):
    """将配置字典写回 config.json（自动创建父目录）。

    技能管理器启用/禁用技能后调用，确保状态重启不丢失。
    v4.108 M-24：改为 tmp + os.replace 原子写——原 open("w") 写入途中崩溃会留下
    截断的损坏配置，下次启动加载失败直接丢全部设置。
    """
    import tempfile
    try:
        parent = os.path.dirname(CONFIG_PATH)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
        log.info("配置已保存到 %s", CONFIG_PATH)
    except Exception as e:
        log.error("保存 config.json 失败: %s", e)
        raise

# 模块级默认：保证 get_all_tools / shutdown_mcp 在 init_mcp_clients 之前调用也不 NameError
mcp_clients = []

def init_mcp_clients(cfg):
    """遍历 cfg["mcp_servers"]，为每个 enabled: true 的服务器创建 McpClient 并启动。
    收集所有 MCP 工具定义存入每个 client.tools。
    """
    from mcp_client import McpClient
    global mcp_clients
    mcp_clients = []
    servers = cfg.get("mcp_servers", [])
    if not servers:
        return

    for srv in servers:
        if not srv.get("enabled", False):
            continue
        name = srv.get("name", "unknown")
        command = srv.get("command", "")
        if not command:
            log.warning("MCP 服务器 [%s] 缺少 command，跳过", name)
            continue
        client = McpClient(
            name=name,
            command=command,
            args=srv.get("args", []),
            env=srv.get("env"),
            cwd=srv.get("cwd"),
        )
        if client.start():
            mcp_clients.append(client)
            log.info("MCP [%s] 初始化成功，提供 %d 个工具", name, len(client.tools))
        else:
            log.warning("MCP [%s] 初始化失败，跳过", name)


def get_all_tools(cfg):
    """返回 TOOL_DEFS + 所有已连接 MCP 服务器的工具定义合并列表。

    调用前需确保 init_mcp_clients 已完成。
    """
    tools = list(TOOL_DEFS)
    # v4.31 一致性校验 + v4.92 风险登记自检（防漏注册 / 防漏登记）
    try:
        from tools import TOOL_REGISTRY as _REG
        from risk import RISK_MAP as _RM, classify as _clf, RiskClass as _RC
        _def_names = {t.get("function", {}).get("name") for t in TOOL_DEFS}
        _missing = _def_names - set(_REG.keys())
        if _missing:
            log.warning("工具一致性: TOOL_DEFS 有定义但 registry 未注册: %s", _missing)
        # 风险登记自检：未在 risk.RISK_MAP 显式登记 且 兜底为 EXTERNAL 的工具会被权限引擎拦截。
        # （正是 create_skill / create_automation 曾踩的坑：新工具忘登记 → classify 兜底 EXTERNAL → 拦截）
        _unreg = [n for n in _REG if n not in _RM and _clf(n) == _RC.EXTERNAL]
        if _unreg:
            log.error("风险登记自检: 以下工具未在 risk.py 登记风险等级(会被当外部危险操作拦截): %s", sorted(_unreg))
    except Exception:
        pass
    for client in mcp_clients:
        tools.extend(client.tools)
    # v4.111 工具白名单过滤（空=全开，行为零变化）。放在 MCP 合并**之后**，
    # 这样 MCP 工具也一起受控——它们同样按字符算钱。
    return _filter_enabled_tools(tools, cfg)


def shutdown_mcp():
    """关闭所有 MCP 客户端进程"""
    global mcp_clients
    for client in mcp_clients:
        try:
            client.stop()
        except Exception as e:
            log.error("关闭 MCP [%s] 异常: %s", client.name, e)
    mcp_clients = []


# ---------- RAG 知识库 ----------
rag_store = None


def init_rag(cfg):
    """初始化 RAG 知识库。

    仅创建对象，不在此处下载 embedding 模型——避免启动时同步访问 HuggingFace
    卡住界面（网络不通时会阻塞数分钟）。模型在首次检索/索引时懒加载，
    若失败会自动降级为不可用，不影响主程序启动。
    """
    global rag_store
    if not cfg.get("rag_enabled", True):
        return
    rag_data_dir = cfg.get("rag_data_dir", "")
    if not rag_data_dir:
        rag_data_dir = os.path.join(APP_DIR, "rag_data")
        cfg["rag_data_dir"] = rag_data_dir
    try:
        from rag import RAGStore
        rag_store = RAGStore(rag_data_dir, cfg=cfg)
        # 注意：不在此处调用 rag_store.init()，避免启动时同步下载 hf 模型卡住界面
        log.info("RAG 知识库对象已创建（懒加载，首次使用时再初始化）: %s", rag_data_dir)
    except Exception as e:
        log.error("RAG 初始化失败: %s", e)
        rag_store = None


# ---------- Obsidian 集成 ----------
def detect_obsidian_vaults():
    """自动检测 Obsidian 仓库路径"""
    vaults = []
    appdata = os.environ.get('APPDATA', '')
    obsidian_json = os.path.join(appdata, 'Obsidian', 'obsidian.json')
    if os.path.exists(obsidian_json):
        try:
            with open(obsidian_json, 'r', encoding='utf-8') as f:
                data = json.loads(f.read())
            for vault_id, vault_info in data.get('vaults', {}).items():
                vault_path = vault_info.get('path', '')
                if vault_path and os.path.isdir(vault_path):
                    vaults.append(vault_path)
        except Exception as e:
            log.warning("检测 Obsidian 仓库失败: %s", e)
    return vaults


def init_obsidian(cfg, store, timeout=15.0):
    """初始化 Obsidian 集成：检测仓库路径，索引 markdown 文件到 RAG。

    三件套（v4.78 性能优化）：
    - 异步：由调用方在后台线程驱动，不阻塞冷启动；
    - 超时：单次启动累计索引超 timeout 秒即提前收工，绝不拖垮体验；
    - 可跳过：obsidian_enabled=false 时直接返回跳过。
    """
    if store is None:
        return "RAG 未初始化"
    if not cfg.get("obsidian_enabled", True):
        return "Obsidian 已禁用（obsidian_enabled=false），跳过"

    vault_path = cfg.get("obsidian_vault_path", "")
    if not vault_path:
        vaults = detect_obsidian_vaults()
        if vaults:
            vault_path = vaults[0]  # 默认第一个
            cfg["obsidian_vault_path"] = vault_path

    if not vault_path:
        return "未找到 Obsidian 仓库，请在 config.json 中设置 obsidian_vault_path"

    vault = Path(vault_path)
    if not vault.exists():
        return f"Obsidian 仓库路径不存在: {vault_path}"

    results = []
    count = 0
    start = time.perf_counter()
    try:
        for f in vault.rglob("*.md"):
            # 超时护栏：单次启动索引累计超阈值即提前收工
            if timeout and (time.perf_counter() - start) > timeout:
                return f"已索引 Obsidian 仓库（超时 {int(timeout)}s 提前结束）: {count} 个文件"
            # 跳过 .obsidian 和 .trash 目录
            if '.obsidian' in f.parts or '.trash' in f.parts:
                continue
            try:
                result = store.index_file(str(f))
            except Exception as e:
                log.warning("索引失败(已跳过) %s: %s", f, e)
                continue
            if result:
                count += 1
                results.append(result)
    except Exception as e:
        log.warning("Obsidian 索引遍历异常（已安全中止）: %s", e)

    return f"已索引 Obsidian 仓库 ({vault_path}): {count} 个文件"


# ---------- 图标 ----------
ICON_PATH = os.path.join(APP_DIR, "icon.ico")


def get_app_icon():
    """加载 icon.ico（XC 字母标），文件不存在则回退到内置 DS 图标。

    onedir 打包后 icon.ico 实际落在 _internal/ 下（sys._MEIPASS），
    故需同时搜索 exe 目录与 _internal 候选路径。
    """
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
    from PySide6.QtCore import Qt
    import sys
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates = []
    if meipass:
        candidates.append(os.path.join(meipass, "icon.ico"))
    candidates.append(os.path.join(exe_dir, "icon.ico"))
    candidates.append(os.path.join(exe_dir, "_internal", "icon.ico"))
    candidates.append(ICON_PATH)
    for c in candidates:
        if os.path.exists(c):
            return QIcon(c)
    pix = QPixmap(64, 64)
    pix.fill(QColor("#4f46e5"))
    p = QPainter(pix)
    p.setPen(QColor("white"))
    p.setFont(QFont("Arial", 24, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "DS")
    p.end()
    return QIcon(pix)

