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
            "description": "用真实 Python 解释器执行代码（代码解释器），可 import 任意已安装库（pptx/pandas/os/...），返回标准输出与错误。适合做PPT、数据处理、绘图、跑脚本等复杂任务。生成的文件默认落在工作区 ~/小臭玩AI/workspace（也可在代码里用绝对路径保存到桌面）。高风险，执行前会请求确认（自动模式下不确认）。",
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
            "description": "根据文字描述生成图片，返回工作区内的图片文件路径（图片会直接显示在对话里）。适合画图、配图、海报草图、插画、封面图等。支持指定尺寸：传 size 可控制画幅，如 '1920x1080' 横版(16:9)、'1024x1024' 方图；不传则用设置里的默认画幅（config 默认 1920x1080 横版，会随 size 参数透传实际生效）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图片描述，越具体越好，如'水墨风格的山间日出，远山云雾缭绕，飞鸟掠过'"},
                    "size": {"type": "string", "description": "可选，图片尺寸 WxH，如 '1920x1080' 横版(16:9)、'1024x1024' 方图。不传用 config 的 image_gen_size 默认。"}
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
            "description": "用 Agnes 生成短视频（文生视频/图生视频），不经过本地网关。Agnes 视频模型支持【内置中文口播】：把台词填到 dialogue 参数，即可生成带真人中文语音+对口型的视频（无需后期配音）。传入画面描述到 prompt，需要人物说话/口播/带货时务必填 dialogue；可选 duration(秒)/aspect(横版/竖版)/image(单图)/images(多参考图,最多5张)/ref_images(同images,v4.127)。【三种图片模式，互斥】① images 传1-5张参考图 → reference 参考图模式（模型参考这些图的风格/人物/场景来生成，多图优先）；② first_frame 或 last_frame → keyframe 首尾帧模式（首帧锁定/首尾帧过渡）；③ 都不传 → text 纯文生视频。【重要流程】只需调用本工具一次：工具内部会自动完成『提交任务→轮询至完成→下载到工作区』全流程，不要把它拆成『先提交』『再轮询』多步，也不要臆测 Agnes 不能出声——口播靠 dialogue 参数，纯画面无声则是不填 dialogue 导致。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "视频画面描述，建议含主体、动作、镜头、光线、风格"},
                    "dialogue": {"type": "string", "description": "口播台词（中文）。填写后 Agnes 会用中文合成语音并对口型，视频自带人声；不填则纯画面无声。做口播/带货/人物说话类视频必填。若用户没给台词，请先自行写好中文台词再填这里。"},
                    "duration": {"type": "number", "description": "视频时长（秒），4-16，默认约12秒"},
                    "aspect": {"type": "string", "description": "画幅：portrait 竖版(768x1152) 或 landscape 横版(1152x768)，默认竖版（抖音/视频号/小红书等竖屏平台请保持竖版）。横版内容（漫剧分镜/影视画面）必须显式传 landscape，否则横版构图会被塞进竖容器。"},
                    "image": {"type": "string", "description": "图生视频源图（单张）：可传图片URL，也可传本地图片路径（如 incoming/xxx.png）或 base64 data URI（data:image/png;base64,XXXX），工具会自动读取并转换，无需图床。用户附带了图片时直接传其路径/图片即可。留空则文生视频。多图请改用 images。"},
                    "images": {"type": "array", "description": "参考图列表（reference 模式，最多 5 张，超出会自动截断）。用于让模型参考这些图的【人物形象/服装/场景/画风】来生成视频，适合：固定角色形象跨镜头一致、指定场景与美术风格、多角度同人物。每项可为图片URL、本地路径（如 incoming/xxx.png）或 data URI。传了 images 就走参考图模式，会覆盖 first_frame/last_frame。"},
                    "ref_images": {"type": "array", "description": "v4.127 多参考图（等价 images，最多 5 张，超出自动截断）。导演台逐镜装配参考图时走这个入参；与 images 同时给时以 images 为准。用于让模型参考这些图的【人物形象/服装/场景/画风】来生成视频。"},
                    "first_frame": {"type": "string", "description": "首帧图（keyframe 模式）：URL/本地路径/data URI。只传首帧=首帧锁定，模型从这张图开始演绎。"},
                    "last_frame": {"type": "string", "description": "尾帧图（keyframe 模式）：URL/本地路径/data URI。与 first_frame 同传=精确首尾帧过渡。"}
                },
                "required": ["prompt"]
            },
        },
    },
    # ---- 军团调度三件套（v4.122）：项目经理的「眼睛」----
    {
        "type": "function",
        "function": {
            "name": "legion_list_outputs",
            "description": (
                "列出本次 Agent 军团执行中各波次成员的产出清单（哪位成员、第几波、第几次尝试、"
                "字数、内容摘要）。**验收/调度前先调它**，知道有什么可读，再用 "
                "legion_get_output 读全文。仅在军团运行中可调，平时调用会提示暂无执行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "执行批次 ID，留空=当前/最近一次"},
                    "preview_chars": {"type": "integer", "description": "每位成员产出的摘要字数，默认 200"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_get_output",
            "description": (
                "读取军团某位成员（或某一波）的完整产出原文。**做验收判断必须基于这里读到的内容**，"
                "禁止凭感觉或脑补。可按成员名（如『研究员』，支持模糊匹配）或 wave 序号定位。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "成员角色名，支持模糊匹配，如『研究员』"},
                    "wave": {"type": "integer", "description": "波次序号（从 1 开始），与 role 二选一；只给 wave 则返回该波全部成员产出"},
                    "run_id": {"type": "string", "description": "执行批次 ID，留空=当前/最近一次"},
                    "max_chars": {"type": "integer", "description": "单篇最多返回字数，默认 6000"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_read_log",
            "description": (
                "读取本次军团执行的过程日志（谁在跑、跑没跑完、报没报错、验收结论）。"
                "判断『这波到底执行成功没有』时用它，比只看产出可靠。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "description": "返回最近多少行，默认 80"},
                    "run_id": {"type": "string", "description": "执行批次 ID，留空=当前/最近一次"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_get_sources",
            "description": (
                "读取成员的**抓取留痕**：本波谁搜了哪些关键词、抓了哪些网页、抓回来多少字、"
                "是不是空手而归。**验收数据类产出（研究员/竞品分析师/选品官/市场调研）时先查它再读正文**："
                "搜索词跑偏 → 正文写得再像样也是编的；一条抓取记录都没有 → 成员压根没联网就下了结论，"
                "直接判定 FAIL。仅在军团运行中可调。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "wave": {"type": "integer", "description": "波次序号（从 1 开始），留空=全批次"},
                    "limit": {"type": "integer", "description": "最多列出多少条，默认 30"},
                    "run_id": {"type": "string", "description": "执行批次 ID，留空=当前/最近一次"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_find_asset",
            "description": (
                "查**资产库**：找以前跑军团/导演台留下的存货（三视图、关键帧、成片、剧本、数据等）。"
                "同一题材/同一角色别重造 —— 开工前先查库，命中就直接沿用或改稿，省一轮生成。"
                "不带 query 时返回全库清单概览。仅在有存货时有意义，库空会直接告诉你。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "搜索关键词（题材/项目名/资产名）；留空=看全库清单"},
                    "kind": {"type": "string",
                             "description": "资产类型（如 image / video / script），留空=不限"},
                    "top": {"type": "integer", "description": "最多返回几件，默认 10"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_report_issue",
            "description": (
                "v4.131-F 成员上报通道：执行中发现**上游数据不可信 / 缺依赖 / 指令自相矛盾**时上报。"
                "例：『研究员给的市场规模 3 亿在留痕里查不到出处』『让我按第 2 波成稿写，但那份产出不存在』。"
                "上报后项目经理验收本波时**必须逐条回应**，你先继续做自己能确认的部分，不要停着等，"
                "也不要硬编一个数字交差。仅在军团运行中可调。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "上报内容：说清你质疑什么、依据是什么（必填）"},
                    "kind": {"type": "string",
                             "description": "类型：数据存疑 / 缺依赖 / 指令矛盾 / 工具不可用 / 范围过大 / 其他"},
                    "upstream": {"type": "string",
                                 "description": "指名上游：哪个角色或哪份产出不可信（例：研究员·第1波·市场规模表）"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "legion_board",
            "description": (
                "读取项目的**共享任务板**：每个节点（成员 / 验收节点）当前状态、最近事件流水。"
                "任务板挂在**项目**上而非某个角色身上，换成员或换项目经理都不丢，"
                "所以『谁干到哪了』要以任务板为准。调度前先查它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "项目 ID（必填）"},
                    "events": {"type": "integer", "description": "返回最近多少条事件，默认 30"},
                },
                "required": ["project_id"],
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
                "【重要触发约束】仅当用户显式要求记住时才调用，例如他说『记住这个』『记下来』"
                "『存入记忆』『别忘了』『帮我记住』。不要因为用户在闲聊中随口提到个人信息、"
                "心得、辛苦、偏好就主动调用——这类信息由后台自动记忆流程处理，你无需手动写。"
                "违反此约束会在纯聊天时打断对话、反复刷屏，是明确错误行为。"
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
                "把一套可复用的流程固化成技能文件(SKILL.md)。"
                "**只在用户明确要求时才调用**（例如「把这个流程存成技能」「以后都这么做」）——"
                "不要因为「我刚做完一个复杂任务」就自动创建：那会在用户不知情的情况下"
                "不断积累沉淀物，而技能是会被后续加载并影响行为的指令文件。"
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

# ---------- v4.106 对话框导演工具（Agent 指挥导演台 · 最小闭环） ----------
DIRECTOR_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "director_status",
            "description": "查询导演台当前视频项目状态：进行到第几步、每个分镜的中文描述/关键帧/片段状态、角色列表（含序号）。用户在对话里提到「分镜」「关键帧」「三视图」「成片」等导演台相关操作时，先调本工具掌握现状再动手。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_clip",
            "description": "按修改意见重生成导演台项目的某一个分镜视频片段（等价于导演台「修改这镜」按钮）。idx 从 1 数（导演台界面显示的镜号）。用户说「把第2镜改成…」「第3镜重新生成，xxx」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer", "description": "分镜号，从 1 开始"},
                    "note": {"type": "string", "description": "修改意见（中文，如：主体换成小孩、镜头拉远、画面调亮）。留空=按原提示词直接重生成"},
                    "replace": {"type": "boolean", "description": "内容审核被拦（content_policy_violation/400）时置 true：note 会整段替换该镜英文提示词而不是追加"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 1500"},
                },
                "required": ["idx"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_keyframe",
            "description": "按修改意见只重生成导演台项目某一个分镜的关键帧图片（其他镜与场景图不动）。用户说「把第3镜的关键帧改成夜晚」时用。改完后该镜片段如需同步更新，再调 director_revise_clip。要全部关键帧一起重跑时，idx 填字符串 \"all\"（会重生成每一镜，耗时与费用都高，用户没明确要求整批就别用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "idx": {"description": "分镜号（整数，从 1 开始）；只有用户明确要求「全部/整批重生成关键帧」时才填字符串 \"all\"。不填会报错，不会默认整批。"},
                    "note": {"type": "string", "description": "修改意见（中文，如：改成夜晚、人物表情更惊讶）。留空=按原提示词直接重生成"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，单镜默认 600，整批默认 1800"},
                },
                "required": ["idx"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_character",
            "description": "按修改意见只重生成导演台项目某一个角色的三视图，并同步刷新角色锁定描述（后续分镜自动跟新形象一致）。用户说「把主角换成短发」时用。idx 是角色序号（先调 director_status 查看）。要全部角色一起重跑时 idx 填字符串 \"all\"（费用高，用户没明确要求就别用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "idx": {"description": "角色序号（整数，从 1 开始，见 director_status 的 characters 列表）；只有用户明确要求「全部角色重生成」时才填字符串 \"all\"。不填会报错，不会默认整批。"},
                    "note": {"type": "string", "description": "外观修改意见（中文，如：换成红衣服、短发）。留空=按原描述直接重生成"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，单个默认 600，整批默认 1800"},
                },
                "required": ["idx"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_story",
            "description": "按修改意见重写导演台项目的剧本（等价于导演台「✎ 重写剧本」按钮）。纯文本产出、不调生成接口，没有费用风险。用户说「重写剧本：xxx」「剧本再紧凑一点」「开头改成倒叙」时用。note 留空=按原样重试。",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "修改意见（中文，原样传用户的话，不要缩写成空话）。留空=原样重试"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 600"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_shots",
            "description": "按修改意见重排/重拆分镜（等价于导演台「✎ 重排分镜」按钮）。纯文本产出、不调生成接口，没有费用风险。用户说「重排分镜：xxx」「分镜太碎了，合并一下」「加一镜特写」时用。注意：重排分镜后，已生成的关键帧与片段会和新的分镜对不上，这是预期行为，要在回复里提醒用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "修改意见（中文，原样传用户的话）。留空=原样重试"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 900"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_confirm",
            "description": "采用当前步骤的产物并推进到下一步（等价于点导演台「✓ 采用XX → 下一步」按钮）。用户说「采用」「确定」「没问题，继续」「下一步」时用。只作用于当前停留的那一步；已经推进过的步骤会拒绝重复采用。涉及关键帧/视频生成的推进仍会先弹 Prompt 预审窗，由用户自己确认后才烧钱。",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer", "description": "要采用的步骤号，一般不用填（自动取当前停留步骤）。填了就必须与当前步一致，否则拒绝。"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 1800（推进可能触发整批生成）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_merge",
            "description": "把导演台项目已生成的分镜片段合成为成片（等价于导演台「合成成片」按钮）。需至少一个片段已生成；用户说「合成」「出成片」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 1500"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_gen_clues",
            "description": "跨镜一致性：从剧本抽取并生成「关键道具 / 场景资产」参考图（同一把剑、同一块招牌这类跨镜复用的东西），之后每镜都会带上它们，避免道具造型漂移。人物的一致性由三视图负责，本工具只管道具与陈设。用户说「道具怎么不一样了」「把道具锁一下」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "额外风格/外观意见（中文，可选）"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 1200"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_revise_clue",
            "description": "按修改意见只重生成某一件道具/场景资产的参考图（其他件不动）。idx 从 1 数，先调 director_status 看 clues 列表。用户说「那把剑改成木头的」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer", "description": "道具/资产序号，从 1 开始（见 director_status 的 clues 列表）"},
                    "note": {"type": "string", "description": "修改意见（中文，如：改成木柄、加一道裂纹）。留空=按原描述重生成"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 600"},
                },
                "required": ["idx"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "director_rollback",
            "description": "把某一镜的片段/关键帧，或某个角色、某件道具，回滚到上一个版本（改崩了想退回上一版时用）。kind 选 clip=视频片段 / keyframe=关键帧 / character=角色三视图 / clue=道具资产；idx 从 1 数；version 默认 -1（上一版），-2 是更早一版。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "clip | keyframe | character | clue"},
                    "idx": {"type": "integer", "description": "序号，从 1 开始（镜号 / 角色号 / 道具号，见 director_status）"},
                    "version": {"type": "integer", "description": "版本号：默认 -1=上一版，-2=更早一版；也可填正数表示绝对第几版"},
                    "timeout": {"type": "integer", "description": "等待完成秒数，默认 60"},
                },
                "required": ["kind", "idx"],
            },
        },
    },
]
TOOL_DEFS = TOOL_DEFS + DIRECTOR_TOOL_DEFS
