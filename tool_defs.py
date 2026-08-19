# tool_defs.py
# 工具定义集中模块：从 config.py 抽离（重构建议①，v4.79）。
# 仅影响「工具注册」，不改调用链路；新增/修改工具定义直接编辑本文件。
import system_control_tools
import software_control_tools
import browser_control_tools
import skill_installer_tools

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "搜索互联网获取**实时资料**（最新榜单/新闻/股价/天气/事件/某平台实时数据等）。"
                "仅当用户明确说「查最新/搜实时/看榜单/爬数据/最新新闻」时调用。"
                "**选题/盘点/列方向/给建议 类需求禁止调此工具**——直接用你的训练知识出文本，"
                "系统提示中的【爆款选题与盘点模板】里有完整方法论。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取指定网页的纯文本内容，用于阅读长文或具体页面。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "完整 URL"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内文件内容（路径相对于程序所在目录）。用户在聊天里通过附件发来的文件位于 incoming/ 子目录，例如 incoming/报告.docx；Office 文档(docx/xlsx/pptx)与 PDF 会自动抽取真实文本返回。长文件默认只返回前 8000 字符并提示总长度，可用 offset 参数续读后续内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，如 incoming/报告.docx 或 notes/todo.txt"},
                    "offset": {"type": "integer", "description": "起始字符偏移（默认 0）。读长文件时用上次返回提示里的 offset 值继续读后续内容"},
                    "limit": {"type": "integer", "description": "本次最多读取的字符数（默认 8000）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "向工作区内写入文本文件（写入前会请求用户确认）。路径相对于程序所在目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，如 notes/todo.txt"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在 Windows 上通过 PowerShell 执行命令（如 Get-ChildItem -Recurse 列文件、运行脚本）。高风险，执行前会请求用户确认。只跑无害命令。注意：这是 PowerShell 不是 cmd——列文件用 Get-ChildItem（不认 dir），错误重定向用 2>$null（不是 2>nul），文本搜索用 Select-String（不认 findstr），否则命令会报错。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 PowerShell 命令，如 Get-ChildItem -Recurse | Select-String '关键词'"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "用真实 Python 解释器执行代码（代码解释器），可 import 任意已安装库（pptx/pandas/os/...），返回标准输出与错误。适合做PPT、数据处理、绘图、跑脚本等复杂任务。生成的文件默认落在工作区 ~/AgentDesktop/workspace（也可在代码里用绝对路径保存到桌面）。高风险，执行前会请求确认（自动模式下不确认）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"}
                },
                "required": ["code"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_gen",
            "description": "根据文字描述生成图片，返回工作区内的图片文件路径（图片会直接显示在对话里）。适合画图、配图、海报草图、插画等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图片描述，越具体越好，如'水墨风格的山间日出，远山云雾缭绕，飞鸟掠过'"}
                },
                "required": ["prompt"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule",
            "description": "设置定时提醒，到点后弹窗提醒用户。可传 delay_seconds（多少秒后）或 at_time（'HH:MM' 或 'YYYY-MM-DD HH:MM'）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "提醒内容"},
                    "delay_seconds": {"type": "integer", "description": "多少秒后提醒（与 at_time 二选一）"},
                    "at_time": {"type": "string", "description": "指定时间，格式 'HH:MM' 或 'YYYY-MM-DD HH:MM'"}
                },
                "required": ["message"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_index",
            "description": "将本地文件或目录索引到知识库中，支持 txt/md/py/pdf/docx 格式。传入文件路径或目录路径，目录会递归索引。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要索引的文件路径或目录路径"}
                },
                "required": ["path"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "在本地知识库中搜索相关内容。传入查询关键词，返回最相关的文档片段及其来源文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"},
                    "top_k": {"type": "integer", "description": "返回结果数量，默认5"}
                },
                "required": ["query"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "加载指定技能的专家指令，将返回的技能提示词注入当前对话上下文。可用技能列表见系统提示中的【可用技能】清单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "要加载的技能名称，须与【可用技能】清单中的 name 精确匹配"}
                },
                "required": ["skill_name"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": "分析本地图片内容（识图/OCR/视觉问答）。传入本地图片的绝对路径和问题，调用 Agnes 多模态模型理解图片并返回结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "本地图片的绝对路径"},
                    "prompt": {"type": "string", "description": "对图片的问题或指令，如「这张图里有什么」「提取图中的文字」"}
                },
                "required": ["path"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "video_gen",
            "description": "用 Agnes 生成短视频（文生视频/图生视频），不经过本地网关。Agnes 视频模型支持【内置中文口播】：把台词填到 dialogue 参数，即可生成带真人中文语音+对口型的视频（无需后期配音）。传入画面描述到 prompt，需要人物说话/口播/带货时务必填 dialogue；可选 duration(秒)/aspect(横版/竖版)/image(图生视频源图)。【重要流程】只需调用本工具一次：工具内部会自动完成『提交任务→轮询至完成→下载到工作区』全流程，不要把它拆成『先提交』『再轮询』多步，也不要臆测 Agnes 不能出声——口播靠 dialogue 参数，纯画面无声则是不填 dialogue 导致。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "视频画面描述，建议含主体、动作、镜头、光线、风格"},
                    "dialogue": {"type": "string", "description": "口播台词（中文）。填写后 Agnes 会用中文合成语音并对口型，视频自带人声；不填则纯画面无声。做口播/带货/人物说话类视频必填。若用户没给台词，请先自行写好中文台词再填这里。"},
                    "duration": {"type": "number", "description": "视频时长（秒），4-16，默认约12秒"},
                    "aspect": {"type": "string", "description": "画幅：portrait 竖版(768x1152) 或 landscape 横版(1152x768)，默认竖版（抖音/视频号/小红书等竖屏平台请保持竖版）"},
                    "image": {"type": "string", "description": "图生视频源图：可传图片URL，也可传本地图片路径（如 incoming/xxx.png）或 base64 data URI（data:image/png;base64,XXXX），工具会自动读取并转换，无需图床。用户附带了图片时直接传其路径/图片即可。留空则文生视频。"}
                },
                "required": ["prompt"]
            },
        },
    },
]  # 闭合基础 TOOL_DEFS 列表

STRUCTURED_LOG_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "log_query",
            "description": "查询结构化运行日志（SQLite），可按日志级别、模块、时间范围筛选，用于排查工具执行错误与异常。",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "description": "日志级别筛选"},
                    "module": {"type": "string", "description": "模块名筛选，如 tools/ui/agent"},
                    "start_time": {"type": "string", "description": "开始时间 YYYY-MM-DD HH:MM:SS"},
                    "end_time": {"type": "string", "description": "结束时间 YYYY-MM-DD HH:MM:SS"},
                    "limit": {"type": "integer", "default": 20, "description": "返回条数"}
                }
            }
        }
    }
]

CHART_GEN_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "chart_gen",
            "description": "生成数据可视化图表（柱状图/折线图/饼图/散点图），输出 PNG 图片。用于把表格/统计数据变成直观图表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "scatter"], "description": "图表类型"},
                    "data": {"type": "object", "description": "数据对象：bar/line 用 {categories:[],values:[]}；pie 用 {labels:[],sizes:[]}；scatter 用 {x:[],y:[]}"},
                    "title": {"type": "string", "default": "图表", "description": "图表标题"},
                    "palette": {"type": "string", "default": "default", "enum": ["healing", "lotus", "landscape", "default"], "description": "配色方案"},
                    "output_path": {"type": "string", "description": "可选输出路径，默认存用户目录 charts/"}
                }
            }
        }
    }
]


# ---------- 上下文窗口智能管理工具（模块4） ----------
CONTEXT_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "context_compress",
            "description": "压缩当前积累的对话上下文：调用免费 LLM 对超窗历史生成真实中文摘要，归档关键信息（决策/待办/实体/偏好），并裁剪旧消息。长对话可定期调用以防超出上下文窗口。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "context_summary",
            "description": "查看当前压缩后的上下文：最近对话、提取的关键信息、历史摘要列表。用于回顾被压缩掉的内容。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ---------- SQLite 数据库操作工具（模块5） ----------
DATABASE_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "查询 SQLite 数据库记录。表与字段：\n- notes(笔记)：id/title*/content/tags/created_at/updated_at\n- todos(待办)：id/title*/description/status(pending|in_progress|completed|cancelled)/priority(low|medium|high|urgent)/due_date/created_at/completed_at\n- assets(素材)：id/name*/type(image|video|audio|document|other|link)/file_path/url/tags/description/created_at\n（* = 必填）",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["notes", "todos", "assets"], "description": "表名"},
                    "where": {"type": "object", "description": "查询条件，键名必须用上表列出的字段。如 {\"status\":\"pending\"} 或 {\"title\":\"电费\"}"},
                    "limit": {"type": "integer", "default": 50, "description": "返回条数上限"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_insert",
            "description": "插入一条记录到 SQLite。字段名严格按表填，* = 必填：\n- notes: {\"title\":\"必填\", \"content\":\"正文可选\", \"tags\":\"标签可选\"}\n- todos: {\"title\":\"必填\", \"description\":\"描述可选(不是 content！)\", \"status\":\"pending 默认\", \"priority\":\"medium 默认\", \"due_date\":\"YYYY-MM-DD 可选\"}\n- assets: {\"name\":\"必填(标题/名字)\", \"type\":\"image|video|audio|document|other|link\", \"url\":\"可选\", \"file_path\":\"可选\", \"description\":\"可选\"}\n⚠️ todos 没有 content 列（用 description），assets 没有 title 列（用 name）。缺必填字段会返回友好错误。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["notes", "todos", "assets"], "description": "表名"},
                    "data": {"type": "object", "description": "字段值对象，键名见上方三表字段说明"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_update",
            "description": "更新现有记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["notes", "todos", "assets"], "description": "表名"},
                    "record_id": {"type": "integer", "description": "记录 ID"},
                    "data": {"type": "object", "description": "要更新的数据对象"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "db_delete",
            "description": "删除记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "enum": ["notes", "todos", "assets"], "description": "表名"},
                    "record_id": {"type": "integer", "description": "记录 ID"}
                }
            }
        }
    },
]


# ---------- Webhook / 事件驱动工具（模块6） ----------
WEBHOOK_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "webhook_start",
            "description": "启动内置 Webhook HTTP 服务器，监听外部事件（GitHub webhook / 自定义 / 跨应用触发器）。默认端口 9000。",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9000, "description": "监听端口"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "webhook_stop",
            "description": "停止内置 Webhook HTTP 服务器。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "webhook_events",
            "description": "查看最近收到的 Webhook 事件列表（含类型与负载）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20, "description": "返回条数"}
                }
            }
        }
    },
]


TOOL_DEFS = TOOL_DEFS + system_control_tools.SYSTEM_CONTROL_TOOL_DEFS + software_control_tools.SOFTWARE_CONTROL_TOOL_DEFS + browser_control_tools.BROWSER_CONTROL_TOOL_DEFS + skill_installer_tools.SKILL_INSTALLER_TOOL_DEFS + STRUCTURED_LOG_TOOL_DEFS + CHART_GEN_TOOL_DEFS + CONTEXT_TOOL_DEFS + DATABASE_TOOL_DEFS + WEBHOOK_TOOL_DEFS


# ---------- 跨对话长期记忆工具 ----------
REMEMBER_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "将用户的稳定长期信息写入跨对话记忆库，以便未来新对话自动沿用。"
                "适用于：用户身份/称呼、稳定偏好与禁忌、与你的约定、关键项目状态、长期目标。"
                "不要记录一次性任务细节或临时对话内容；若同类信息已记录过则不要重复写入。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "一条值得长期记住的事实，简洁陈述，例如「用户抖音笔名屋檐下的一缕灰」「用户厌恶拉踩竞品」「DeepSeek 是用户付费订阅的主力通道」",
                    }
                },
                "required": ["fact"],
            },
        },
    }
]
TOOL_DEFS = TOOL_DEFS + REMEMBER_TOOL_DEFS

# v4.59 记忆搜索工具
SEARCH_MEMORY_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "全文搜索长期记忆库，查找已记录的用户偏好、约定、历史信息。"
                "当需要确认用户之前说过什么、有什么偏好或禁忌时使用。"
                "示例：搜索用户笔名、平台策略、技术栈偏好等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如「笔名」「抖音」「小红书」",
                    }
                },
                "required": ["query"],
            },
        },
    }
]
TOOL_DEFS = TOOL_DEFS + SEARCH_MEMORY_TOOL_DEFS

# v4.60 工作流工具
WORKFLOW_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "run_workflow",
            "description": (
                "启动多Agent协作工作流，用专门的研究员+写手分工完成任务。"
                "适用于：研究并撰写报告、多角度搜索聚合等复杂任务。"
                "type 可选 'research_write'（研究+写作）或 'multi_search'（多引擎搜索）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "工作流类型：research_write / multi_search",
                        "enum": ["research_write", "multi_search"],
                    },
                    "task": {
                        "type": "string",
                        "description": "任务描述，如「搜索2026年AI趋势并写成报告」",
                    },
                },
                "required": ["type", "task"],
            },
        },
    }
]
TOOL_DEFS = TOOL_DEFS + WORKFLOW_TOOL_DEFS

# v4.60 自省工具
SYS_INFO_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "sys_info",
            "description": (
                "获取系统运行时真实状态：技能数、数据库表、配置路径、模型等。"
                "做自检/能力盘点/查配置时**必须调用此工具**，不要凭空猜测。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]
TOOL_DEFS = TOOL_DEFS + SYS_INFO_TOOL_DEFS

# v4.60 自动技能创建
CREATE_SKILL_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "将当前完成的复杂多步任务提炼为可复用的技能文件(SKILL.md)。"
                "当你成功完成了一个需要5步以上的复杂任务后，总结执行流程并保存为技能，"
                "下次遇到类似任务时可直接复用，大幅提效。"
                "注意：技能会先进入「待审核」队列，需用户在「技能审核」中点击通过后才正式生效，"
                "不会立即自动加载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名(英文ID)，如 'weekly-report'"},
                    "description": {"type": "string", "description": "一句话描述这个技能做什么"},
                    "prompt": {"type": "string", "description": "完整的执行步骤/提示词，包含工具调用流程"},
                    "emoji": {"type": "string", "description": "图标 emoji，如 📊"},
                    "category": {"type": "string", "description": "分类，如 效率办公/内容创作/技术自动化"},
                },
                "required": ["name", "description", "prompt"],
            },
        },
    }
]
TOOL_DEFS = TOOL_DEFS + CREATE_SKILL_TOOL_DEFS

SEND_EMAIL_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "SMTP 发送邮件。需在 config.json 配 smtp_host/smtp_user/smtp_pass（QQ邮箱用授权码）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "收件人邮箱"},
                    "subject": {"type": "string", "description": "邮件主题"},
                    "body": {"type": "string", "description": "邮件正文（支持 HTML）"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    }
]
TOOL_DEFS = TOOL_DEFS + SEND_EMAIL_TOOL_DEFS


AUTOMATION_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "create_automation",
            "description": "创建自动化任务（定时提醒或定时执行任务）。用户说「每天X点做Y」「每周X提醒我」「X分钟后做Y」等需求时用这个，而不是注册系统计划任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称，如「每日AI新闻抓取」"},
                    "action": {"type": "string", "description": "动作类型：'remind'=到点弹窗提醒，'run'=到点把 message 作为指令交给 Agent 执行。默认 run"},
                    "message": {"type": "string", "description": "提醒内容（action=remind）或执行指令（action=run）"},
                    "schedule_type": {"type": "string", "description": "调度方式：'once'一次性 / 'daily'每天 / 'weekly'每周 / 'interval'间隔重复。默认 daily"},
                    "at_time": {"type": "string", "description": "触发时间 'HH:MM'，如 '09:00'"},
                    "at_date": {"type": "string", "description": "一次性任务用，日期 'YYYY-MM-DD'"},
                    "weekday": {"type": "string", "description": "每周任务用，'一'~'日' 或 0(周一)~6(周日)"},
                    "interval_minutes": {"type": "integer", "description": "间隔重复任务的分钟数，如 30 表示每 30 分钟"},
                },
                "required": ["name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_automation",
            "description": "列出所有自动化任务及其状态（启用/停用、调度方式）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_automation",
            "description": "删除一个自动化任务。传 id 或 name 均可（先 list_automation 查）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 id"},
                    "name": {"type": "string", "description": "任务名称"},
                },
                "required": [],
            },
        },
    },
]
TOOL_DEFS = TOOL_DEFS + AUTOMATION_TOOL_DEFS
