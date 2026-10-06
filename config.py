# -*- coding: utf-8 -*-
"""DeepSeek 桌面助手 — 配置模块"""

import sys
import os
import copy  # 审计修复 F1：DEFAULT_CONFIG 含嵌套可变对象，浅拷贝会污染进程级默认值
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
import time
import memory_store

# ---------- APP_DIR ----------
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- 工作区目录（运行时数据的统一落点）----------
# ⚠️ 必须定义在 APP_DIR 之后、且早于下方的日志初始化（app_log_path 会用到它）。
#
# v4.164.0：把「用户数据类」运行时目录与 Agent 的工作目录统一收到用户数据目录下，
# **不再落 APP_DIR（= exe 所在目录 = dist）**。
#
# 为什么必须改：APP_DIR 就是 exe 所在目录，而它**同时是分发源**。运行数据落在那儿
# 会被连带打包分发 —— 实测曾把 1.2GB 浏览器 profile（含 51 条已存登录）连同
# Cookies/Local State 一起分发出去（见 v4.163.1）。dist 顶层还堆过
# incoming/output/outputs/notes/multi_platform/pages/temp/log/rag_data/orchestrate/
# director_session.json + debug.log，合计十几 MB。
#
# 归口规则（重要，别搞混）：
#   · 用户数据类 → WORKSPACE_DIR：incoming、output(s)、notes、pages、temp、
#     multi_platform、rag_data、orchestrate、avatars、log、director_session.json、
#     debug.log，以及 Agent 执行命令 / 读写文件的 cwd 基准。
#   · 资源类 → 仍用 APP_DIR：icon.ico、内置 skills 兜底、core 包、浏览器扩展。
#   · 产物类 → PRODUCTS_DIR（本来就是用户目录，未变）。
#
# 默认与 USER_DATA_DIR 同值（~\\Documents\\小臭玩AI）；可用 XC_WORKSPACE_DIR 改道。
WORKSPACE_DIR = os.path.expanduser(
    os.environ.get("XC_WORKSPACE_DIR")
    or os.path.join("~", "Documents", "小臭玩AI"))


def workspace_path(*parts):
    """拼一个工作区下的路径（并确保目录存在由调用方决定）。"""
    return os.path.join(WORKSPACE_DIR, *parts)


def ensure_workspace(*parts):
    """拼一个工作区下的路径，并确保该目录存在。"""
    p = os.path.join(WORKSPACE_DIR, *parts)
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p


def is_under_app_dir(path):
    """判断某路径是否落在 APP_DIR（= exe 目录 = dist = 分发源）之内。

    用于把历史遗留在 dist 里的运行数据做一次性迁移改写。
    """
    try:
        if not path:
            return False
        return os.path.abspath(str(path)).startswith(os.path.abspath(APP_DIR) + os.sep)
    except Exception:
        return False

# ---------- 产物目录（统一落点，便于「打开产物文件夹」） ----------
# 所有生成类产物（图片/截图/视频）统一写到用户文档下的「产物」目录，
# 而非程序目录（dist）深处，避免用户难找。UI 端用 os.path.join(APP_DIR, rel)
# + abspath 解析 rel（含 .. 上溯）打开，路径仍正确，聊天显示不受影响。
PRODUCTS_DIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI", "产物")

# ---------- 版本 ----------
APP_VERSION = "v4.218.0"
APP_BUILD_DATE = "2026-10-06"
# v4.164.0（2026-09-27）**运行数据归口：彻底治「dist 既是运行目录又是分发源」**：
#   新增 config.WORKSPACE_DIR（默认 ~/Documents/小臭玩AI，与 USER_DATA_DIR 同值，
#   可用 XC_WORKSPACE_DIR 改道），把所有「用户数据类」落点从 APP_DIR（= exe 目录 =
#   dist）统一迁到工作区：
#     · Agent 执行命令 / run_python 的 cwd（tool_run_command 原为 cwd=app_dir）
#     · 文件工具 tool_read_file / tool_write_file 的**相对路径基准**（原以 app_dir 为
#       基准），并把白名单扩为「工作区 + APP_DIR」双根（兼容历史绝对路径）
#     · incoming / orchestrate / rag_data / temp / log / avatars /
#       director_session.json / debug.log（app_log_path 默认落点）
#   资源类（icon.ico / 内置 skills 兜底 / core 包 / 浏览器扩展）**仍锚 APP_DIR**，
#   否则打包后技能与图标会找不到。产物类仍走 PRODUCTS_DIR（本就是用户目录）。
#   配套：桌面快捷方式工作目录由 dist 改为 ~/Documents/小臭玩AI；dist 顶层已清空。
#   测试：tests/test_workspace_routing.py（26 断言）钉住「工作区不在 APP_DIR 内」
#   「相对路径以工作区为基准」「越界写入被拒」「资源类仍锚 APP_DIR」。
# v4.163.1（2026-09-26）**修隐私泄露：调试浏览器 profile 不再落在 exe 所在目录**：
#   `_ensure_cdp` 原用 `app_dir/cdp_edge_profile` 作调试浏览器 profile，而 app_dir
#   就是 exe 所在目录（dist），它**同时是分发源** —— 实测该 profile 长到 1.2GB /
#   7558 文件，含 Login Data（51 条已存登录）、Cookies（396 条）、Local State，
#   缓存里另有明文 API key。**打包分发 = 分发登录态**。
#   修法：抽出 `_default_cdp_profile()`，固定 `%LOCALAPPDATA%\小臭玩AI\cdp_edge_profile`，
#   不接任何参数、任何分支都不回落 app_dir（代码对齐 config 里 ② 那条注释的原意，
#   该注释早前只写了约定、实现没跟上）。现有 profile 已完整迁移到新位置。
#   测试：tests/test_cdp_profile_path.py 钉住「不接受参数 / 无 app_dir 兜底」不变量。
# v4.163.0（2026-09-26）**可观测性：任务状态条（新增 task_status.py + 状态栏 UI 接入）**：
#   把「模型调用 / 网页抓取 / 文件解析 / 视频生成 / 图片生成 / 语音识别 / Agent 整轮」
#   统一成用户看得懂的状态：已接收 → 处理中 →（完成 | 失败原因 + 可重试）。
#   此前这些状态只落在日志里，用户感知是「没反应」，排查成本高。
#   · 新增 task_status.py（纯标准库、线程安全）：四态 + 环形缓冲 + 变更订阅 +
#     重试钩子；失败原因落库前统一脱敏（data URI / 长 base64 / URL 内 key / 明文 sk-）。
#   · 新增 ui.py 的 TaskStatusStrip：嵌入既有状态栏，跨线程用 Qt queued 信号渲染，
#     失败可「重试」（无钩子时诚实提示，不假装成功）、可「清除」。
#   · 覆盖 6 个生产者：model / browser / image / video / speech / agent。
# v4.161.0（2026-09-18）**路由强制调工具误触止血（只改 agent.py 路由判据，无功能删减）**：
#   修复 _route_force_tool() 把「夸赞/评价句式」误判为生成指令、step1 强制注入工具导致白跑的 BUG：
#     ① 新增 0.1) 评价句式前置短路：动词紧贴「的/得」后接褒义评价（『封面做的漂亮』『图画得太好了』
#        『文章写得很棒』）属动补结构而非祈使指令，return None。去程度副词限制（允许 0~1 个）+ 补全褒义词集，
#        且要求动词紧贴『的/得』，正常祈使句（『做一张好看的图』动词后无紧贴『的/得』）不受影响。
#     ② agnes 分支收紧：原先 "agnes" in t 且无图意图时裸兜底 video_gen，导致纯夸赞（『Agnes 中文能力
#        颠覆认知』）白跑 video_gen 7 分半；现要求命中 VIDEO_OBJ 意图才路由 video_gen，否则 return None。
#   影响面：该强制通道对所有模型都跑（非仅思考模型），误触影响比原方案文档写的更大，本次一并覆盖。
#   校验：agent.py py_compile 通过；正则命中/不命中回归脚本覆盖 NEG 组（期望 None）与 POS 组（期望非 None）。
# v4.160.0（2026-09-18）**浏览器扩展 L1 加强（只改扩展 + 桥接 + 注入，无功能删减）**：
#   落实《浏览器扩展加强方案》L1 补漏档全 8 项，解决「只有推没有拉 / 抓完等回车 / 两个按钮功能重合 /
#   正文只取单节点丢兄弟块 / /pair 后端闲置」五类根因。改动面：
#     ① 扩展 content.js：区分 mode(article/selection)；extractArticle 回溯内容容器合并同层兄弟块（根治长页抓不全）；
#        新增 toMarkdown() 把正文序列化为带标题/列表/引用/代码的 Markdown；附带 siteName/capturedAt/wordCount 元信息。
#     ② 扩展 background.js：payload 透传 mode/autosend/markdown/meta；新增右键菜单（正文/选中）+ 全局快捷键
#        （Ctrl+Shift+S 正文、Ctrl+Shift+X 选中，manifest commands）；contextMenus 权限。
#     ③ 扩展 popup.js + popup.html：两个按钮真正区分（article/selection）；新增「自动发送」勾选框与「一键配对/重置」按钮（接 /pair）。
#     ④ browser_bridge.py /page：新增清洗 mode/autosend/markdown/meta 字段并透传；旧版扩展不带时走默认，向后兼容。
#     ⑤ main.py _inject：autosend=true 直接调 window.send()（等价于按回车，上一条处理中则跳过、内容保留）；
#        正文优先用 markdown，块头注入「来源/字数/抓取于」元信息。
#   校验：5 个 JS 全部 node --check 通过；browser_bridge.py / main.py py_compile 通过；三处扩展副本 md5 一致。
#   注：扩展侧改动重载扩展即生效；Python 侧（自动发送/元信息注入）需本版重建 exe 才生效。
# v4.159.5（2026-09-18）**用户自检收口——治本堵死 v4.159.4 残留 6 危险侧 + 3 误杀真指令（不照抄 in-memory 开集补丁）**：
#   大哥转发小臭自检报告：基线 v4.159.4 有 54 条用例 11 条失败（危险侧 6 + 误杀 5）；小臭自测 v4.159.5 用
#   types.MethodType 内存绑定 54/54 全过，但自曝三雷：① 54/54 里 4 条（需要什么条件/有啥技巧/有哪些坑/走哪个接口）
#   是靠补词表过的，是第五次开集坑；② 补丁仅在内存绑定验证、未写进源码文件（落盘状态不明）；③ 用例表自己标错 2 条。
#   经实证探针抽取真实源码核对：小臭雷②坐实（源码/exe 全是旧 v4.159.4）；小臭雷③部分坐实——
#   「分别拍两个版本/差别做两个版本」确为标错（无视频/图片宾语，本就合法返 None），但雷③所暗指的「别出心裁做个视频已返
#   video_gen」是错的：实证显示 v4.159.4 它返回 None（被后向 tail 的『出』命中『别出心裁』），属真误杀，必须修。
#   治本（严守方法论定式：绝不补短语，改用闭集/结构判据）：
#     ① 危险侧 6（做视频需要什么条件/有啥技巧/有哪些坑/走哪个接口/要花多少钱/要多久）→ 根因 _is_question 缺疑问代词
#        → 补闭集疑问代词（什么/啥/哪些/哪个/多少/多久）结构性识别，一次全堵（W1-W6）。
#     ② 误杀真 3（特别想做/特别出彩/别出心裁做个视频）→ 根因裸『别』后向判据把『特/分/差 + 别 + 做/出』当否定：
#        · 前向复合词闭集豁免（特/分/差/辨/识/类/个/送/级/性/告/离/区/辞/样/致 前一字时跳过，可穷举）；
#        · 后向窗口收紧到紧贴后一字 + 动词集剔除『出』，避开『别出心裁』（X1-X3）。
#   单测 _route_force_tool_test.py 补 W1-W6（危险侧 None）+ X1-X3（误杀真 video_gen）；进包核对适配。
# v4.159.4（2026-09-18）**路由判据三轮重构——堵死 v4.159.3 对抗测试打穿的 3 危险侧 + 6 安全侧误杀**：
#   用户复测 v4.159.3（元话语动词辖制生成短语的位置判据）后，对抗面又打穿：
#     · 危险侧 3（该 None 却 video_gen）：生成视频是什么原因 / 我生成了一份报告，视频先不做了 / 昨天生成了很多图片，视频我再想想；
#     · 安全侧误杀 6（真指令被裸『别』误杀）：给我做个视频顺便辨别一下真假 / 生成个视频顺便识别下素材 / 做个视频类别要区分清楚 /
#       做个视频个别镜头要慢 / 做个视频然后送别一下老同事 / 别别扭扭地帮我做个视频。
#   根因（三轮同一坑）：① 裸『别』用【前向白名单】排除复合词（只排 特/分/告/离/区/性），漏排 辨别/识别/类别/个别/送别/别扭
#     —— 前向排除是开集，永远补不完（第三次验证同一坑）；② _neg_hit 缺一般否定短语（先不做了/再想想/以后再说/算了/不做），
#     且 window=30 跨分句，否定够不到生成短语；③ _ref_by_position 只查【前置】，元话语词后置时不认。
#   治本（方法论定式：凡『排除类』判断一律改【后向/位置判据】或【一票否决表】，不再用『枚举复合词』）：
#     ① 裸『别』改【后向词法判据】：不看前一个字，只看后 1-3 字是否跟生成动词/语气助词（闭集可枚举）；实测修好 5 条误杀；
#     ② _neg_hit 补一般否定短语（不做/不弄/不搞/不生成/不剪/不拍/不画/不做了/先不做了/再想想/以后再说/算了/先不做）；
#     ③ _ref_by_position 补【反向位置判据】：元话语词在生成短语【之后】且其后无 _CLAUSE_SEP → 也判引用语境（修『生成视频是什么原因』）。
#   实测：危险侧 3→0，误杀 6→1（仅『特别想做个视频』仍被后向 tail『别想』含『做』误杀，安全侧漏报代价可控，按用户策略 (b) 接受不补）。
#   单测 _route_force_tool_test.py 补 S1-S10/T1-T7/U1-U2/V1-V3 用例；进包核对适配。
# v4.159.3（2026-09-18）**P2 位置判据收口——彻底堵死 v4.159.2 残留的裸「分析下生成视频」误报**：
#   v4.159.2 仅靠前置词表 _REF_KW（这件事/你说的/是BUG…）识别引用语境，但「分析下生成视频」
#   无引用标记、生成短语（生成+视频）又真实共现 → 仍被路由 video_gen → 思考模型注入伪 user 指令
#   → 用户只是想【分析】生成视频这件事，反而被强制生成。根因＝引用语境靠「词表枚举」永远补不完。
#   治本（位置判据，逐步退役 _DISCUSS_KW 词表）：新增 _ref_by_position()——
#     若元话语动词（分析下/聊聊/解释/复盘/怎么看/为什么/评价/诊断…）出现在生成短语【之前】且同分句
#     （前置无独立分句分隔符 逗号/然后/顺便/再/并…）→ 生成短语是被分析的对象而非指令 → 返回 None。
#   配套：_gen_intent 拆出 _gen_intent_span（返回宾语位置）供位置判据复用；_route_force_tool 路由重排
#     否定 → 引用词表 → 状态 → 【位置引用判据】→ 生成意图 → 弱讨论；_detect_action_intent 同步加位置否决。
#   关键防回归：① 不收裸「分析/聊/谈」单字（误中『大数据分析』『刚才聊的』『话题』）；② 含分句分隔符
#     则不触发（『生成个视频，顺便分析下这个题材』O4 仍返 video_gen；『分析完数据再生成个视频』仍返 video_gen）。
#   实测新用例全过：R1 分析下生成视频→None / R2 分析下做一个视频→None / R3 复盘这次生成视频的过程→None
#     / R4 分析下怎么生成视频→None（疑问）/ R5 分析完数据再生成个视频→video_gen（分隔符）/ R6 生成视频后复盘→video_gen。
#   单测 _route_force_tool_test.py 44→50 项 ALL_PASS（新增 R1-R6 + 结构断言）；进包核对适配。
# v4.159.2（2026-09-18）**路由拒判补丁——对抗测试打穿 v4.159.1 的 7 危险侧误报 + 1 安全侧漏报**：
#   用户对抗测试专挑「豁免词挪后」副作用打，暴露根因：否定侧空白 + 豁免挪过头 + 状态词缺漏 + 动词缺漏。
#   危害链：force_tool 非空 → ui.py 向思考模型注入 role:"user" 的「必须真实调用 video_gen」，
#   用户喊停/质疑时反而被强制生成（危害远大于漏报），故本版以「否定一票否决 + 引用语境一票否决」前置拦截。
#   改动点（agent.py）：
#     ① 新增 _neg_hit() 否定一票否决（别/不用/不要/取消/停止/先别/别急…），短语集合 + 裸『别』字位置扫描，
#        规避『区别/特别/分别/告别/性别』复合词误伤（单字『别』直接子串会误杀）；置于生成意图判断【之前】。
#     ② 新增 _REF_KW 引用/质疑标记（这件事/你说的/你刚才说的/什么时候/让你/是BUG/讨论/评价），
#        命中即一票否决，同样前置；解决「分析下生成视频这件事」「你刚才说的生成个视频，是BUG」
#        「我什么时候让你生成视频了」三类被『生成视频』共现误触的用例。
#     ③ _DISCUSS_KW 拆两级：讨论/评价 上提为 _REF_KW（前置否决）；仅留 分析/解释/聊聊 等弱讨论词
#        在生成意图之后兜底，降低误伤真指令概率。
#     ④ _STATUS_KW 补 完了吧/生成完了/跑完了/完了没（修「视频生成完了吧」被当指令）。
#     ⑤ _GEN_VERBS 补 来一段/来一期（修「来一段口播视频」漏报）。
#     ⑥ _route_force_tool 重排：否定 → 引用 → 状态 → 生成意图 → 弱讨论；_detect_action_intent 同步加否定/引用否决。
#   实测 8 用例全修：7 危险侧（别生成/不用生成/取消生成/分析下…这件事/什么时候让你/你说的…是BUG/生成完了吧）
#   均 return None（不再强制生成）；1 安全侧（来一段口播视频）return video_gen（不再漏报）。
#   唯一残留风险（P2）：裸「分析下生成视频」（无引用标记、仅弱讨论词在生成意图之后）仍可能误触 video_gen，
#   属 P2 位置判据范畴，不阻塞发布。单测 _route_force_tool_test.py 补 P/Q 用例；进包核对适配。
# v4.159.1（2026-09-18）**路由判据边界补丁——堵死 v4.159.0 实测残留 5 洞**：
#   用户实测 v4.159.0 原三洞已全堵死（伪指令注入质变成因根治），但收尾没做边界自测，
#   抓出 5 处保守侧漏报/误杀（代价=该调时不调，不会失控）：
#     ① 动词表补「剪/合成/合并/拼/接/出/出片」（剪一个视频、合成一个视频 原漏报）
#     ② 宾语表补「成品/成片/作品/口播」（出个成品、做个成片、来一条口播 原漏报）
#     ③ _is_question 移除「吧」（「帮我做个视频吧」被误当疑问而漏报；仅「吧？」仍算疑问）
#     ④ 讨论豁免(_DISCUSS_KW)检查挪到 _gen_intent 之后——先判生成意图，命中直接返回工具，
#        不让「分析/解释」压制真指令（「生成个视频，顺便分析下」原被豁免词吃掉）
#     ⑤ _verb_near 窗口 16→30（「基于…生成…能发抖音的视频」动词离宾语远原够不着）
#   单测 35/35 PASS；唯一残留风险：「分析一下我做的视频」类「X做的视频」相对小句仍可能误触
#   video_gen（豁免挪后+无『视频的』修饰跳过），属可接受边角，不阻塞发布。
# v4.159.0（2026-09-18）**强制路由判据重建——彻底堵死 v4.158 三洞**：
#   用户实测 v4.158 的子串白名单仍不彻底，根子是「子串命中 = 下达指令」判据本身：
#     · 误报：「把上次做视频的稿子找出来」「这条短视频的标题怎么起」都含 做视频/短视频，
#       被路由 video_gen；讨论豁免词救不了（句里没有 聊聊/分析）。
#     · 漏报：「帮我做个视频」返回 None——关键词是 做视频，而 做个视频 不构成子串，真指令反漏。
#     · 白名单枚举：_DISCUSS_KW 只有 36 词，没列进的讨论句式一律漏网，永远补不完。
#   根治：废除 _VIDEO_GEN_KW/_IMAGE_GEN_KW 子串白名单，改为「组合式生成意图判据」：
#     _gen_intent = 生成类动词(_GEN_VERBS) + 媒体宾语(_VIDEO_OBJ/_IMAGE_OBJ) 共现
#       + 宾语为实头（非『X的宾语』修饰语，跳过后接『的』的出现）
#       + 非疑问句（_is_question）+ 非平台名（视频号 占位排除）。
#     _phrase_hit 仅补「画一张/拍一张」这类动词+量词无显式宾语的明确祈使短语。
#   覆盖 _route_force_tool（当前句 / agnes 分支 / prev_text 兜底）与 _detect_action_intent 全部引用点。
#   验证：_route_force_tool_test.py 三洞用例 + 回归全绿；py_compile 三文件通过；进包核对 7/7 PASS。
#   取代 v4.158.0。
# v4.155.0（2026-09-17）**小臭自检三 BUG 闭环 + 技能存储防丢**：
#   ① 方案一（错误日志落库）：tools.py 两处异常分支补 get_logger().error(exc_info=True)
#     + 入参限长截断(_trunc_args)，错误从此进 agent_logs 表（level=ERROR）。
#   ② 方案二（视频轮询超时失控）：video-agent/core/agnes.py 的 wait_video 补齐 timeout=timeout，
#     超时报错带 task_id；tools.py 新增 VIDEO_POLL_TIMEOUT（默认 240s，XIAOCHOU_VIDEO_TIMEOUT 可覆盖）
#     替换原写死 120（原本只管 submit，轮询仍落 1800 默认）；240 < Agent 回合上限 445 留余量。
#   ③ 方案三（路由误触发）：config.py 强制路由表加「先判指令再判关键词·纯评价严禁触发生成工具」条款；
#     ui.py 新增 _looks_like_praise() 纯评价豁免，接入 _needs_tool_intent。
#   ④ 技能存储防丢：skill_review 的 active/pending 目录加「绝不落在打包程序目录(dist)」重定向；
#     新增 seed_builtin_skills() 首次运行把内置技能从 dist 同步进用户目录，重打包不丢自建/内置技能。
#   来源：用户反馈——看到面板里「时长」下拉最高只有 12 秒，误以为「整条视频最多
#     12 秒、只能 1 镜」，于是觉得「长口播做不了」。
#   真相：该下拉是**每段**时长（Agnes 单次生成硬上限 12 秒）；面板早已对长口播
#     自动分段 + 尾帧接力 + 拼接，总时长不受 12 秒限制（实测 151 字 → 5 段 / ~33 秒）。
#   纯 UI/文案纠偏（不改生成逻辑）：
#     - 「时长」→「每段时长」，加 tooltip 说明「这是每段、不是整条；长口播自动多段拼接」。
#     - 口播框下方新增**实时分段预览**（口播/每段时长任一变化即刷新）：
#       「长口播 → 自动切成 N 段，逐段生成后拼接 · 预计总时长 ≈ M 秒（整条不受 12 秒限制）」；
#       单段时显示「共 1 段 · 约 M 秒」。
#     - 新增 _update_twin_seg_preview(app)（getattr 防御，可单测），复用 split_dialogue。
#   验证：digital_twin_panel py_compile 通过；split_dialogue 纯逻辑实测 151 字 → 5 段 / 33 秒。
# v4.154.0（2026-09-17）**导演台「AI 智能分镜」开关（分镜数 + 每镜时长双解放）**：
#   来源：用户反馈——导演 Agent 只会按下拉菜单的「分镜数」机械拆分，不会按剧本智能定镜数；
#     且「每镜秒数」是全局同一值锁死每一镜，AI 无法给不同镜不同节奏。
#   设计：新增「🤖 AI 智能分镜」勾选框（默认勾选），一个开关同时解放两个维度：
#     - 分镜数：智能模式让 AI 按剧本情节起伏/场景转换自主定镜数（提示词 6~18，丰富可到 24，不凑水镜）；
#       关闭则严格按下拉框硬执行（旧行为兜底）。
#     - 每镜时长：智能模式在拆镜 prompt 新增 `dur` 字段，AI 按内容给每镜 4~12 秒（动作短/抒情长/对白中）；
#       关闭则所有镜统一用下拉框秒数。Agnes 视频 API 硬约束 4~12 秒，故任何 dur 都必须夹到该区间。
#   改动点：
#     - video_pipeline：prepare/run 增 `smart` 参数；剧本/口播/拆镜三处 prompt 按 smart 分支
#       （智能模式去掉写死的 n×秒）；拆镜 prompt 智能分支新增 dur 字段与 JSON 示例。
#     - _norm_shot 解析并夹取 dur（有值越界→夹边界保留意图；缺失/非数字→回落默认且夹合法）；
#       新增 _shot_dur 辅助；generate_all_clips / regenerate_clip 改传每镜 dur（不再全局 self.duration）。
#     - director_panel：加勾选框（默认勾选）+ 分镜数上限放宽到 40；prepare 参数透传 smart；
#       _on_shots_ready 把 AI 真实镜数回写数字框；每镜编辑行新增「时长」可调框（4~12）并回写 p.shots；
#       _read_shot_rows / _add_shot_row 纳入 dur；存档恢复经 p.shots 重建行（dur 随序列化存活）。
#   验证：video_pipeline/director_panel py_compile 全过；dur 夹取逻辑单测 10 用例 PASS
#     （有效保持 / 越界夹 4~12 / 缺失回落默认）；对话 Agent 路径（表单点开始，同管线）自动覆盖，无需另改。
# v4.153.4（2026-09-17）**异常卫生专项（第五组：P3-12 except 卫生）**：
#   来源：v4.152.3 GPT 架构审查 → 真蒸馏核验，第五组 P3-12（P3 最低档，可维护性/健壮性）。
#   本组重点 = 实测核验异常处理的真实分布 + 最小侵入改进 + 加回归守卫，不盲改 80+ 模块。
#   【实测核验】AST 审计 82 个核心模块：裸 except: = 0（最危险轴已干净）；纯吞错（只 pass / 只日志无 raise）
#     = 524 处（pass_only 349 + log_only 175）。其中"关键边界函数内 pass_only"133 处，逐条核验后认定
#     **绝大多数本就该静默**：Qt 信号 disconnect、UI 控件 setup、best-effort 清理（proc.kill/rmtree/
#     os.remove）、后台落盘 fallback（os.replace，已有"磁盘失败不影响本轮对话"注释）、best-effort 重连
#     （request_stop/mark_done）——这些是正确行为，不是掩盖错误。
#   【针对性修复】仅动 5 处"静默落盘用户数据/授权态且零日志"的真问题（失败=静默丢数据，用户无感知）：
#     context_manager._save_summaries / _save_key_info（摘要/密钥信息落盘）、legion._trust_save（信任态）、
#     config.load_config 迁移落盘、agent.run 任务完成标记、trace_log.prune（轨迹裁剪落盘）→ 均改为
#     `except Exception as e: log.warning(...)` 让失败可观测，**不改变控制流**（仍 swallow，仅发声）。
#     注：context_manager / trace_log 原无 logger，新增自包含 `log = logging.getLogger(__name__)`（不 import
#     项目模块，避免循环依赖）；config/agent/legion 复用既有模块级 `log`。
#   【回归守卫】新增 `_verify_except_hygiene.py`：硬规则①核心模块零裸 except:；②锁定本轮修复的 4 个
#     具名持久化函数（_save_summaries/_save_key_info/_trust_save/prune）不得退回只-pass 静默吞错；
#     其余"关键持久化函数"内的只-pass（大量为 load 默认返回 / 后台落盘刻意 fallback，本就该静默）列为
#     观测项仅报告不 fail；并报告 pass_only/log_only 计数供趋势观测。已注册 `_run_v4152_regress.py`（L1）。
#   验证：py_compile 全过；`_verify_except_hygiene.py` 跑通 RESULT=PASS（裸 except 0 + 持久化边界无只-pass）。
# v4.153.3（2026-09-16）**体验回归与状态机专项（第四组：P2-04 + P3-11）**：
#   来源：v4.152.3 GPT 架构审查 → 真蒸馏核验，第四组两条经核验均为「改进建议·非 bug」
#   （区别于前三组的真实 bug），故本组重点 = 源码核验现状 + 最小侵入式改进/回归测试。
#   【P2-04】设置页 DPI / 小屏矩阵回归。v4.152.3 已落排版修复（QScrollArea + 卡片 117→262px、
#     combo 不再溢出）；本版补「125%/150% 缩放 + 小屏」体验回归项，新增 `_verify_settings_dpi.py`
#     以 QT_SCALE_FACTOR 多进程覆盖 100%/125%/150% 三档，offscreen 实构造 ChatWindow 设置页，
#     断言：含且仅含一个 widgetResizable 的 QScrollArea（结构性防压扁）、Agnes 卡片 minimumHeight>=150
#     （抗压扁下限、与 DPI 无关）、卡片内控件未压扁、基线档卡片整体高度未压、设置弹窗宽>=360 +
#     model_combo 最小宽>=200（best-effort）。高缩放档等效「小屏+高 DPI」压力场景，守住边界。
#   【P3-11】导演阶段状态机（轻量，不重写）。导演台运行态长期由散落 app.director_* 动态属性
#     （busy/clips_state/final_path/step/pipeline/thread…）共表状态，60+ 处 getattr 读取。
#     新增 DirectorPhase 枚举（IDLE/RUNNING/DONE/ERROR）+ _set_director_phase 集中 setter，在
#     初始化 / _set_busy(True) / _on_merge_ready(ok) / _on_error / 重置 五处显式标阶段，
#     收敛「生命周期」语义；_set_busy(False) 不动阶段（DONE/ERROR 必须在解锁后存活——关键不变量）。
#     全部为新增属性 app.director_phase，零破坏现有 60+ 读取点。配套 `_verify_director_phase_v41533.py`
#     断言四态齐全、落属性、RUNNING 入口、以及「_set_busy(False) 不重置阶段」不变量。
#   验证：py_compile 全过；两个新回归脚本纳入 `_run_v4152_regress.py` canonical 入口（L3 / L1）。
# v4.153.2（2026-09-16）**测试体系专项（第三组：P2-08 + P2-09 + P3-10）**：
#   来源：v4.152.3 GPT 架构审查 → 真蒸馏核验，第三组聚焦"回归体系可信度"。
#   全组均按本机 Python 跑通、py_compile 通过、缺失套件端到端退出码验证。
#   【P2-08】回归脚本假绿修复。旧 `_run_v4151_regress.py` 缺失套件只 SKIP 不计入 fail，
#     且末尾无 `sys.exit` → Python 默认 exit 0，导致"2/12 缺失"也能全绿。
#     修复：引入 `missing` 计数，SKIP 分支 `missing += 1`；末尾 `sys.exit(1 if (fail or missing) else 0)`；
#     报告行标注 SKIP 数。已用"传入不存在套件"端到端验证 EXIT=1（修复前为 0）。
#   【P2-09】建 v4.152.x canonical 回归入口 `_run_v4152_regress.py`，取代 legacy `_run_v4151_regress.py`。
#     沿用 12 个套件 + 纳入上轮 `_verify_p21531_guards.py`（P2-03/P2-06 守卫验证）；标注 v4151 为 legacy。
#   【P3-10】L0~L5 测试分层。每个套件打层级标签并支持 `--level N` 过滤（含 N 及以下）；
#     新增 `--fast`（L1~L3 跳过 heavy 的 `_import_smoke`）+ `--list`（仅打印分层）。
#     层级约定：L0 导入冒烟 / L1 纯逻辑 / L2 逻辑+mock / L3 Qt offscreen / L4 全链路 / L5 人工。
#   验证：两个回归跑手 py_compile 通过；`--list` 正常；缺失套件 EXIT=1；现有套件路径全部存在。
# v4.153.1（2026-09-16）**稳定性与任务隔离专项（第二组：P2-03 + P2-06）**：
#   【P2-03】导演台统一 generation 守卫。跨线程信号是排队投递——线程结束、emit 已入队，
#   但交付时项目已「开始导演/重置」换发新令牌 → 旧项目迟到结果会污染新项目剧本框/分镜/成片。
#   复用 _on_web_action 既有的 project_token 令牌机制：在 _run_thread 创建线程时捕获当时令牌，
#   给全部 9 个线程结果回调套 _gen_guard（交付侧比对，令牌不符即丢弃并记日志；空令牌向后兼容）。
#   handler 签名零改动，只改 9 处 connect + 新增 _gen_guard（director_panel.py）。
#   【P2-06】memory_store._configure 重置目录时漏清延迟计算的 _SALT_PATH → 二次 configure(B)
#   后 salt 仍指向 A。补进 global 并置 None，使其按新 MEMORY_DIR 惰性重算（仅测试/隔离路径触发）。
#   验证：py_compile 通过；导演守卫逻辑级核对（正常交付放行、切换后迟到丢弃）。
# v4.153.0（2026-09-15）**稳定性与任务隔离专项（第一组：GPT 架构审查蒸馏回的真实 bug）**：
#   来源：把 v4.152.3 源码交给 GPT 做架构级审查（非单点 BUG 扫描），逐条核真实源码后
#   蒸馏出本组三个真 bug，全部按军团宪法第二章「未明确授权不继续」改 fail-closed。
#   【P1-01】replan「异常即继续」→ fail-closed。
#     旧 `_request_replan` 在弹窗派发异常 / 超时 / abort 中断时都返回 True（继续）。
#     其中 abort() 塞的是字符串 "abort"，旧收尾用 truthy 判定（`bool("abort")=True`）
#     会把「已停下」误判成「继续」—— 直接违反宪法。
#     新实现：任何异常 / 超时 / 非严格 `True` 一律判「停下重编」；收尾用 `is True` 严格布尔，
#     "abort" 哨兵被正确判为停下，不误判继续。
#   【P1-02】checkpoint 续跑「不一致仅警告」→ fail-closed 拒绝续跑。
#     旧实现阵容 / 任务与存档不一致时只打警告、仍静默续跑，会张冠李戴错乱产出。
#     新实现：`waves_fingerprint` + 任务文本双比对，任一不一致即拒绝续跑，
#     走 log + _board(refused) + done(明确说明) + return，不执行任何波次。
#     （已 grep 确认 save_checkpoint 恒写 task / waves_fingerprint 两字段，不会 false-refuse。）
#   【P2-07】human 验收模式缺 PM → 阻断启动（不再静默降级为无闸门）。
#     旧实现 human 模式找不到项目经理角色时，PM 静默降成 off（闸门消失），违反「人手把关」。
#     新实现：human 模式缺 PM 直接拒绝启动（log + board(refused) + done 文案 + return）；
#     off / advisory 模式 PM 仅出建议权，缺了才降级并说明。
#   【abort() 注释勘误】去掉过时「白等 600s」描述，注明 P1-01 的 `is True` 收尾已修 truthy 误判。
#   验证：legion_worker.py 全量 py_compile 通过（`PY_COMPILE_OK`）。
# v4.152.3（2026-09-16）**设置页排版修复（大哥实拍反馈）**：
#   「设置页打开设置弹层下面的显示框非常乱，下拉菜单挤在文字中间」。
#   根因：设置页内容总高超过可视区，但**没有滚动容器** → QVBoxLayout 把 4 张卡片
#   **平均压缩**（实测每张只剩 117px，卡片内每个控件被压到 5~6px、互相重叠）；
#   而 QComboBox 是 `v=Fixed`(32px) **不参与压缩** → 溢出到相邻行
#   —— 这就是「下拉挤在文字中间」的真身。
#   ⚠️ 先排除了「本轮懒建引入的回归」：同一探针跑 `lazy` / `eager` 两种模式，
#   **数值逐项完全相同**（card h=117、combo x=84 w=687、各项 h=5~6px）
#   → 是**既有问题**，与 v4.152.2 的改动无关。
#   修法：给设置页套一层 QScrollArea（沿用 `_build_tools_page` 的既有模式），
#   卡片按内容撑开、超出部分滚动。修后：卡片 117→**262px**，标题/说明/复选框/
#   按钮行全部恢复 **21~34px**，combo 正常拉满一行。
#   顺带做了**全页面压缩体检**（实际高度 / sizeHint 比值）：只有设置页是真问题；
#   军团/生图/生视频/数字人/任务页的「比值 < 0.9」经人眼复核截图均为**假阳性**
#   （QListWidget / QTextEdit 自带滚动，QLabel+wordWrap 的 sizeHint 按最窄宽度估算偏大）。
# v4.152.2（2026-09-15）**启动提速第三轮 —— 非首屏页面全部改成「首次切页才建」**。
#   前情：v4.152.1 用 **offscreen** 打桩测分段，与 exe 差 2.3 倍（1892 vs 1127ms）——
#   因为 QWebEngineView 在真实 GUI 下要起渲染进程。这轮把探针改成 `--gui` 与 exe 对齐，
#   结果**瓶颈直接换人**：`_build_title_bar` 从 9.9ms 爆到 1068ms。
#   逐层深挖（cProfile → 拦截 addWidget 记调用栈 → 隔离实验 → QSS 变体对照）：
#     · `QBoxLayout.addWidget` 331 次共 558ms（占构造 44%），其中 3 次慢调用占 363ms
#       （罪魁 = `main_layout.addWidget(title_bar)` 251ms）
#     · 那 251ms 的真身是**首次样式匹配**：QMainWindow 挂了复杂 QSS 后，第一个含
#       `QLineEdit` 的子树被 polish 时触发 QStyleSheetStyle 初始化
#       （空 QWidget 只要 0.1ms，QLineEdit 要 210~298ms，且只有第一个贵）
#     · ⚠️ **但简化 QSS / 去掉 font-family 只是把成本在「构造 vs show」之间挪位置，
#       总时间几乎不变**（H1 251+87=339 / H3 0.5+351=352 / H5 0.7+322=323ms）
#       —— 这条路**自己否掉了**，省下一次白改。
#   真正出路是**减少首屏要挂的 widget 数**：首屏只用得到 welcome(0) 与 chat(1)。
#   定量（stub 把 7 个非首屏页内容置空、外壳保留）：构造 1562→742 / 900→750ms，
#   即 **178~820ms**（full 波动大，stub 稳定）。
#   ① 编排 / 生图 / 生视频 / 数字人 / 工具 / 自动化 / 设置 七页：`_init_ui` 只建外壳
#      并加入 main_stack，**内容改由 `_ensure_lazy_page` 在首次切页时补建**。
#   ② `_ensure_lazy_page` 扩成统一入口：**控件对象比对**（不硬编码下标）+
#      `_lazy_built` 集合去重（`_switch_nav` 每次切页都会进来）+ 失败不置位可重试。
#   ③ 依赖核对（AST 扫全项目）：7 页建出的 `self.X` **无一被启动关键路径使用** ✓
#      （试点阶段撞过两次：`_build_skill_popup` 返回值要被解包、
#        `_build_settings_popup` 里建的 `self.model_combo` 后续要用 —— 那两个 popup 因此没动）
#   验证：行为 **41/41**（逐页切过去全部建好、幂等、真实数据根零污染）。
# v4.152.1（2026-09-15）**启动提速第二轮 —— 这次是拆到段级的真收益**。
#   缘起：v4.152.0 用源码 `-X importtime` 测出「voice 子树 1877ms」，据此做惰性加载，
#   但**打包实测只省 0.15s** —— 源码环境是冷读几百个 .py，而 exe 里它们已在 PYZ 走
#   zipimport，本来就快。拿前者推后者，方法论就是错的（已记 LEARNINGS L082）。
#   于是改用项目**已有的**启动埋点（perf_baseline → perf/startup.jsonl，真实 GUI 口径）
#   找真瓶颈：`window_shown` 独占启动总时长 ~89%（1.2~1.5s），其余阶段合计仅 0.15s；
#   再加 `window_built` 埋点拆开 → **ChatWindow 构造占 1.03~1.08s，show() 仅 0.03s**。
#   再用运行时打桩（monkey-patch ChatWindow 的方法与构造器，**零改源码**）把构造拆到段级：
#     chat_web.ChatWebView()  222ms ← QWebEngineView 固有成本，且是首屏内容，不动
#     voice.Recorder()        202ms ← **启动根本用不上**
#     _init_ui 裸语句         188ms ← QSS/控件创建，Qt 固有
#     _build_legion_page      172ms ← 不点军团页就不需要
#     _build_director_page     92ms ← 不点导演台就不需要
#   顺手证伪了一个候选：`_nav_icon_pixmap` 虽被调 93 次，合计仅 25ms，**不是瓶颈**。
#   ① `Recorder.backend` 改**惰性 property**：此前 `self.backend = ... if _have_sd()`
#      在构造时就加载 sounddevice+numpy（~200ms）—— 把 v4.152.0 惰性化的收益原样吐了回去。
#   ② 军团页 / 导演台 / 麦克风探测的**内容构建推迟到 show() 之后**：窗口外壳、导航项、
#      main_stack 页序**全部照旧**（页数 / nav 下标 / 嵌入契约不变），只把重活挪走。
#   ③ 分两批 + 延时：第 1 批轻量刷新（~50ms）紧跟 show；第 2 批重活（~450ms）延后 600ms
#      —— 否则会**把首屏重绘一起卡住**，那是「窗口出来了但半秒不出内容」，并不比原来好。
#   ④ 兜底三件套（提速绝不降级功能）：切页 `_ensure_lazy_page` 同步补建；`_open_legion`
#      先补建再判断（否则会掉进「新开独立窗口」分支 —— 正是 v4.135 明令消除的
#      「两实例同写存档」）；两个 build 函数**幂等**（重复调用不二次 addWidget）。
#   实测（源码口径，同一把尺子）：构造 **1102ms → 490ms（−612ms / −56%）**。
#   验证：行为 33/33、回归 12 套件全绿、真实数据根零污染。
# v4.156.0（2026-09-17）**启动提速第三轮 —— 啃最后的硬骨头 ChatWebView**。
#   前两轮把构造从 1.1s 压到 ~0.49s，剩下唯一大头就是 `chat_web.ChatWebView()`（QWebEngineView
#   渲染进程固有成本 ~200–244ms）。v4.152.1 当时判「是首屏内容，不动」—— 但深究发现
#   `setHtml` 本就**异步**：首屏本来就有一段空白期，要等 loadFinished 才渲染，所以把它的
#   **创建**从 __init__ 移到 show() 之后（QTimer.singleShot(0) 兜底的 `_post_show_init` 开头），
#   用户可见首屏时序几乎不变，却能把这 ~200ms 从「窗口出现」之前摘掉。
#   方案：构造期在聊天列放空 `QWidget` 占位容器撑住 stretch=1 布局；`_build_chat_view()`（幂等）
#   在 show 后创建真实 ChatWebView、连好 anchorActivated/pageReloaded/ready 三信号、用
#   `layout.insertWidget(idx, ...)` 在**原位置**替换占位（避免布局跳动）。4s 首屏看门狗 +
#   60s 心跳 + 崩溃熔断等自愈机制全部保留，白屏风险比既往判定更低。⚠️ 仍属 P0 稳定性，需真机验证。
# v4.156.1（2026-09-17）**热修：ChatWebView 延迟创建引发首屏 AttributeError 崩溃**。
#   根因：v4.156.0 把 chat_view 的创建从 __init__ 移到 show() 之后（_build_chat_view），
#     但 _init_ui 行 ~2029 的首屏渲染 `self._render_messages(force_bottom=True)` 在**构造期**
#     就调用、且 _render_messages 内部访问 `self.chat_view.render_all(...)`，此时 chat_view
#     尚未创建 → AttributeError: 'ChatWindow' object has no attribute 'chat_view'，启动必崩。
#   修复：_render_messages 顶部加防御 `if getattr(self, "chat_view", None) is None: return`，
#     构造期首屏渲染直接跳过；首屏历史渲染由 _build_chat_view 之后的 ready 信号
#     （_on_chat_page_reloaded，v4.120.2 权威全量重渲染）兜底，幂等无副作用。
#     性能收益不变：ChatWebView 创建仍留在 show() 之后。
#   验证：py_compile 通过；字节码核对 v4.156.1 + 延迟创建特征全过；真机首屏不崩。
# v4.152.0（2026-09-15）三件事一起做（大哥：「剩下2件都做，然后优化一下程序打开速度」）：
#   ① **启动提速 2.1 秒**（实测启动 import 总耗时 2914.5ms → 809.5ms，降 72%）：
#      voice.py 的 sounddevice + numpy 原本是**模块级** import，而 main → ui → voice 是启动
#      必经链，冷启动光 numpy 就要 1695ms。改为**首次使用时才加载**（用 PEP 562 模块级
#      __getattr__ 让 `voice.HAVE_SD / _sd / _np` 对外仍照旧可读，内部改用 _have_sd()）。
#      实测：voice 子树 1876.9ms → 36.0ms，ui 子树 2378.2ms → 220.6ms。
#   ② **打包面瘦身**：spec.excludes 增加 cv2 / pyarrow / scipy / pandas —— 四者在全部
#      349 个源码文件（含 core/）里零 import，却占 _internal 约 298.6MB（28%）。
#      ⚠️ lxml（python-docx）/ pdfminer（pdfplumber）/ numpy（matplotlib 等）是真传递依赖，不能排。
#   ③ **风格表收口**：删掉 video-agent/core/config.py 的 STYLE_OPTIONS + STYLE_PROMPTS
#      —— 死代码（core 内部与 api.py/core_cli.py 从未引用），却被 core/ 整包打进 exe，
#      容易被误认为「小臭这里还有一套风格表」。小臭侧唯一来源仍是
#      video_pipeline.STYLE_ORDER / STYLE_LABELS / STYLE_PROMPTS。
# v4.151.0（2026-09-15）导演台「需求理解」根治 —— 大哥反馈：「还有一个很大的问题，它对我的
#   需求理解太差，还有我要的是水墨动画，它把主角三视图干成真人，每次我写入主题，它首先给我
#   编造个故事，剧本写的不行」，外加「酒樽（道具）被生成了一个人捧着大酒壶的三视图」。
#   实盘取证（dist/小臭玩AI/director_session.json）：
#     topic='水调歌头水墨动画，主角苏轼' / style='anime'（下拉框里根本没有水墨）
#     characters=[Su Shi (苏轼), Antique Wine Vessel (酒樽)]  clues=[]  锁里也带着酒樽。
#   因果链（五环，每环放大前一环的错）：主题里的"水墨"只喂给剧本模型 → 剧本提示词要求
#   「明确主角与**关键配角**」却没区分人物/道具，模型把酒樽写成"关键配角" → 抽取器照剧本收
#   → 三视图模板硬编码 `of ONE person` 且是**全项目唯一不注入 style_prompt 的生图环节**
#   → 写成人物锁污染每一镜。
#   ① 画风系统修复：新增「水墨 inkwash」风格；`_gen_character_views` 与 `_gen_clue_image`
#      的 prop 分支补上画风注入（空画风时与旧版逐字节相同，见新增 `style_sfx()`）；
#      风格表**单一来源**（原 STYLE_PROMPTS 在 video_pipeline、STYLE_ITEMS 在 director_panel
#      各存一份，正是「加了一处忘另一处」的温床）；新增 `STYLE_KEYWORDS`/`style_from_topic`
#      从主题识别画风（确定性关键词，长词优先，不花钱）——**主题优先于下拉框**，
#      确认后自动切换并明确告知。
#   ② 人物/道具分离：剧本提示词把「关键配角」改为「人或拟人化活物」并要求**单列
#      【关键道具/陈设】**；抽取提示词明确只抽活物；新增机器闸门 `looks_non_person()`
#      （两判据：有物件词且无强活物线索 / 一条活物线索都没有；英文用**词边界**匹配，
#      否则 "man" 会命中 "mansion"；中文只用双字词，否则「神**秘**」会被当成活物），
#      接入 gen_characters（剔除并提示去处）、_gen_character_views（**拦在调接口之前，
#      零费用**）、_build_character_lock（防脏数据再污染全片）；`_load_session` 加
#      `_heal_characters()` **存档自愈**（大哥那份被污染的真实存档，重开即自动清干净）。
#   ③ 需求确认卡：点「开始导演」不再直接开写，先出「需求理解卡」（题材/立意、画风、
#      镜头数、每镜秒数、叙事方式、原文使用、声音、补充要求）——内容由
#      `understand_topic()` 一次 LLM 调用产出，**失败返回 None 即降级为原行为**；
#      「取消」= 什么都不发生（不换令牌、不清存档、不建工程、连画风下拉都不动）。
#   明确不做：不碰分镜/关键帧/逐镜/合成逻辑；不引入 video-agent 的其它风格（ghibli 等）；
#      不清理 dist 里那份外来项目 config 副本（属打包面收敛，另记待办）。
# v4.150.0（2026-09-15）导演台「对话化」改造（B 档：打通三条通道）—— 大哥看 Pavo 导演台后
#   提出：「要改哪张关键帧图、要改哪个分镜、怎么改，全流程都是在对话框完成，每一步只要
#   还没有点确定都可以对话告诉它改」。逐行审过导演台后确认：**能力层八九成已齐**（带意见的
#   单镜修改工具 v4.106 就有、7 步采用闸门也在），缺的是「壳」。本轮补四条通道：
#   ① 意见入口统一到输入框：_revise_story/_revise_shots/_revise_characters/_revise_keyframes
#      加 _gen_clues 的「重新抽取」分支，全部从 QInputDialog.getText 弹窗改为 prefill()
#      预填模板（「重写剧本：」）到下方导演对话框并聚焦。弹窗不再打断，全流程一处在说话。
#   ② 卡片加「✎ 说一句怎么改」：关键帧/角色/道具/分镜四类卡片各加一个入口（act ask_*），
#      点了只预填不执行；原先只有裸「↻重生成」，想描述怎么改只能去批量弹窗。
#   ③ 补齐对话侧缺失工具：新增 director_revise_story / director_revise_shots /
#      director_confirm（对话内「采用当前步 → 下一步」）；白名单从 5 个补到 11 个
#      （rollback / gen_clues / revise_clue 三个早已实现却被白名单挡在外面）。
#      安全收口：keyframe/character 的 idx 支持显式 "all" 整批，**不带 idx 仍 fail-closed**
#      —— 绝不允许模型漏参时静默降级成整批烧钱。
#   ④ 对话区升级：110→200px（可 ⤢ 展开到 340）、顶部常驻状态行「第 N/6 步 · 名称 ·
#      ⏸ 待你确认」、工具成功后**回缩略图**（addResource + insertHtml）并自动切到对应
#      步骤页，省掉「说完自己翻页找新图」。DIRECTOR_SYS 同步补「引导前缀 → 工具」映射
#      与「现在是第几步、等不等确认」，模型才知道怎么响应「采用」。
#   明确不做：不动 7 页 StackedWidget 结构（A 档「产物搬进对话流」会牺牲「一眼看全 15 张
#   分镜」的能力，且要重构 170KB 文件的渲染链）、不动 Prompt 预审与版本回滚栈。
# v4.149.0（2026-09-15）主模型「被静默改写」根治 —— 大哥反馈「我今天一直用 Auto 档，
#   普通任务 Agnes、复杂任务自动切 DeepSeek，主模型一直显示 Agnes，但刚打开显示智谱」。
#   他明确否掉了「滚轮」这个解释，追下去发现**滚轮只是两个触发源之一**，真凶是信号选错：
#   ① 🔴 **档位下拉连了 currentTextChanged**：该信号对「滚轮滑过 / 键盘上下 / 打字跳到
#      某字母 / 任何程序化 setCurrentIndex」统统触发，而处理器 `_on_model_change` 会把
#      base_url+model+api_key **整套写进 config.json** —— 用户零意图也能被换模型。
#   ② 🔴 **启动兜底强制切档**：`__init__` 里 `for...else` —— 当前三件套匹配不到任何档位时
#      直接 `_on_model_change(names[0])`，把主模型改成档位列表第一项并落盘。每次启动都可能
#      触发，且**完全没有用户操作**，正是「一打开就变了」的形态。
#  ③ 可见性缺口（事故的另一半）：Auto 档的标签只写「Auto · 智能路由」，主模型是谁看不见；
#     设置页「当前模型」在失配时显示「—」，等于把真实值藏起来。
#   铁证（debug.log + config 备份）：主模型 **2026-09-06 20:28 起被写成 `glm-4-flash`
#   （智谱 GLM）**，此后每次启动都记「程序启动，模型=glm-4-flash」；09-10 一天内在
#   glm / deepseek 之间来回跳 5 次（证明不是用户主动选）；09-14 23:13 变 `zhipu` +
#   `http://127.0.0.1:8000/v1`（下拉**最后一项**「免费网关」，滚轮的锅，v4.148.8 已堵）。
#   即「普通任务走 Agnes」这件事**实际错了 8 天**，而界面毫无提示。
#   修法（四件）：
#     a) 档位下拉与工具栏下拉一律只连 `activated`（Qt 语义 = 用户真选；已用微测试证实
#        `setCurrentIndex` 只发 currentIndexChanged、不发 activated）；
#     b) `_on_model_change(name, explicit=False)` 加**写入闸门**：非 explicit 直接拒写 +
#        WARNING 留审计痕；
#     c) 启动改为 `_init_settings_model_combo()`：**只显示、不落盘**；匹配不到档位时报
#        WARNING 并保持原样；新增 `_match_model_profile()` 两级匹配（全等 → base_url 唯一
#        命中），让「档位 model 名漂移」（profile 写 2.5 / 主配置 3.0）不再被当成认不出档；
#     d) Auto 标签写出「Auto · 智能路由（主模型 X → 复杂升 Y）」，设置页失配时显示
#        「未匹配档位 · <model>」而不是「—」。
#   注：`ChatWindow._save_cfg()` **不走 `config.save_config`**（自己 json.dump + os.replace
#   直写），所以验证脚本以**文件哈希**为权威判据，探针只作辅助（首次用探针判定「没写盘」
#   是假阴性，详见 LEARNINGS L071）。
#   验证：`_verify_ui_model_v41490.py` **27/27 全绿**（沙箱 %TEMP% 隔离，真实 config 首尾
#   哈希一致零污染）→ `_verify_out_v41490.txt` / `_VERIFY_REPORT_v41490.md`。
# v4.148.8（2026-09-14）修两个 UI 级 BUG（大哥实测反馈：设置页挤成一团 / 默认模型变了）：
#   ① **模型被鼠标滚轮静默切走**（根因）：QComboBox 默认响应滚轮 —— 光标滑过就切档，并触发
#      currentTextChanged → `_on_model_change` 把 model/base_url/api_key **整套写进 config.json**。
#      实测大哥的主模型被切到「免费网关 free-api-gw」（`zhipu` + http://127.0.0.1:8000/v1，
#      本地网关进程没起时模型直接不可用）；而该档恰是下拉的**最后一项** —— 往下滚到底就中招，
#      零提示、零确认。修：新增 `_NoWheelCombo`（**未聚焦时忽略滚轮**，聚焦后仍可滚轮换档），
#      ui.py 内 13 处 `= QComboBox()` 全部替换（不止模型，会话/尺寸等下拉同样受益）。
#   ② **设置弹窗挤成一团**：弹窗 `setFixedWidth(300)`，而内部 `model_combo.setMinimumWidth(260)`
#      + popup 边距 16×2 + 分组内边距 12×2 = 316 > 300 —— 容器比子控件最小宽度还窄，
#      Qt 只能强行压缩/重叠。修：弹窗宽 300 → 360，combo 最小宽 260 → 200。
#   附：大哥的主模型已设为 **Agnes 3.0**（`agnes-3.0-flash`，实测 1.2s 可用），
#      并同步把 `model_profiles[Agnes].model` 升到 3.0（否则主配置与档位对不上、UI 认不出档）。
#   验证：`_verify_ui_model_v41488.py` 9/9（含真实 QWheelEvent 行为验证，滚轮不切档 0→0）；
#        `_verify_ui_v41481.py` 基线 26/26（确认 13 处批量替换无副作用）。
# v4.148.7（2026-09-14）修正 v4.148.6 的两处实测错误（同日第二轮真机实测推翻）：
#   ① **加密跳转链不可读**：v4.148.6 假设「Google `goto?url=` / Bing `ck/a?p=` 在浏览器里
#      打开会自行 302，browser_read 能用」——实测为假：返回 `400 malformed or illegal request`
#      （97 字垃圾页）。故改为：默认引擎 **DDG 优先**（其 `uddg=` 可还原真实 URL）；
#      某引擎结果**全为加密链时继续试下一个引擎**；实在只能给加密链时**显式标注
#      「链接不可直接读取，仅作标题线索」**，绝不假装可用。
#   ② 默认 engines 顺序仍是 google 优先（v4.148.6 里那次 Edit 被并行吞掉，见 LEARNINGS L068）
#      → 修正为 duckduckgo 优先。
# 附带：新增 `_verify_source_v41486.py`（静态锚点 19 项 + 分支逻辑 5 场景，改完就跑，
#   专治「同文件并行 Edit 静默丢改动」连踩 N 次的坑）。
# v4.148.6（2026-09-14）搜索通道补全：web_search 加「浏览器搜索兜底」——
#   背景：search.py:http_get 是小臭自己的 Python 网络栈直连（纯 urllib、零代理），
#   **不吃浏览器 VPN** —— 「VPN 一直连着、搜索却全空/只回噪声」由此而来；唯一吃梯子
#   的通道是 browser_*（subprocess 起 playwright 接管真实 Edge）。
#   ① browser_runner：read 支持 --links / --links-root，按结果容器抽 a[href] 还原真实 URL；
#   ② browser_control_tools：新增 browser_search() —— 用被接管的 Edge 打开搜索结果页，
#      解跳转链（DDG uddg / Google /url?q / Bing base64），按 URL 分组还原标题+摘要；
#      Google/Bing 已把链接加密（goto?url= / ck/a?p=）且不可解，靠 _is_engine_redirect
#      保留原链 —— 它们在浏览器里会自行 302，browser_read 照样能用；
#   ③ tools.tool_web_search：**境外主题优先走浏览器通道**（实测 7.3s 拿到 inseller.my
#      费率详解、seller-my.tiktok.com 官方卖家中心政策页 —— 军团四轮 FAIL 缺的就是这个），
#      失败回落原链路；非境外主题仍在链路全空时才兜底。开关：
#      config.json 的 web_search_browser_fallback=false 可关。
# v4.148.5（2026-09-14）修「VPN 误报」+ 澄清通道边界（大哥反馈「桌面 lnk 一直都连着VPN」）：
#   ① 误报根治：_cdp_has_vpn 原判据只看 /json/list 里的扩展 service worker —— 而 MV3 的
#      SW **空闲约 1 分钟就被回收**，target 随之消失 → 浏览器开着超过一分钟，
#      必然误报「未加载 VPN 扩展」。实测：t≈4s 有 worker.js（True）；t≈80s SW 被回收
#      （旧判据 False，误报）→ 新判据 True。改为**双证据**：① /json/list 活跃 target；
#      ② profile 的 Secure Preferences/Preferences 里有扩展条目（权威记录，不随 SW
#      休眠变化）。只有两者都无才提示，且提示文案改成准确说法。
#   ② 通道边界澄清（写进注释留档）：小臭的 web_search / web_fetch 是**自己的 Python
#      网络栈直连**（search_mod.http_get，无任何代理），**不走浏览器 VPN** ——
#      VPN 只代理 Edge 本身，仅对 browser_open/browser_read 生效。这正是「VPN 连着、
#      成员仍抓回空壳/噪声」的原因，也是 A 方案（数据岗只留 browser_*）的依据。
#   验证：实测 84 秒（SW 已回收）后判据仍 True、警告不再出现；拉起/重入均无警告。
# v4.148.4（2026-09-14）竞品监控团第 2 次跑复盘揪出的三个真问题（一个比一个深）：
#   ① 🔴 **角色卡快照不跟随代码**（最深）：数据（role_library + 项目成员）里存的是
#      角色卡**快照**，代码里打磨的「员工简历」范式、A 方案（数据岗移除 web_fetch）
#      全都没进实际运行 —— 跑的还是几个月前的旧卡，成员当然还是老行为。
#      修法：new_role 加 card_ver 版本戳 + _upgrade_preset_role_cards() 在加载时把
#      预置卡升到当前版本（能力字段以代码为准；技能并集不丢用户挂载；工具=代码卡+
#      用户额外追加；升前备份 legion.json；幂等，二跑不重复）。
#   ② 🔴 **自动垫能力把 A 方案绕过**：v4.136「按职责标签自动补工具」把 web_fetch
#      又加回数据岗（日志白纸黑字：「按职责标签自动补工具 web_fetch」）——
#      兜底机制推翻了角色卡的显式决定。修法：AUTO_FILL_DENY={"web_fetch"}，
#      该单里的工具只有角色卡显式列了才会出现在工具集（自动垫/技能回填都不许复活）。
#   ③ 差技能请示漏出正文碎片（「所以链条是」「我可以报」「第2波」「Wave structure」
#      「Cleanup on 澄清」等 9 条假请示）：加句子碎片闸（连接词/主谓碎片/中文占比）
#      + 段落边界加「第N波」「分隔线」两类停止条件。真名（content-gap-analysis、
#      TikTok Shop 马来站合规清单校验）不受影响。
#   验证：21 项断言（含大哥截图里 10 个垃圾名逐条复现 + 老数据降级后升级 29 处 + 幂等）。
# v4.148.3（2026-09-14）修「差技能请示报垃圾」：PM 写「- 新增：需要补一个数据抓取技能」
#   → 解析出 技能名=「新增」、角色=「未指定」，大哥收到的请示既没说缺什么也没说给谁。
#   ① 中心垃圾闸：纯动词/量词名（新增/添加/补充/需要/一个…）不再是技能名 ——
#      能从描述里打捞真名（取最靠近「技能」的候选+剥动词前缀+过形态闸）就救，
#      救不回来整条丢弃（宁可少报不报垃圾）；归一化前后各去重一次防双条。
#   ② _GAP_NAME_BAD 移除「清单」——「合规清单校验」这类正经技能名被它误杀（实测踩过）。
#   ③ PM 指令第 9 条收紧：技能名必须具体、必须写「给谁用」，说不清的缺口不许报。
#   ④ 日志/报告：缺角色的请示显式标「⚠️ PM 没写给谁，装完自选角色」，缺用途同理。
#   验证：7 用例（垃圾复现/打捞/合规放行/纯垃圾丢弃/无/自由表述/回归原格式）+ 去重复验全过。
# v4.148.2（2026-09-14）团队配方化五件套（大哥拍板「5项都补」，依据当日调研报告）：
#   ① 配方 JSON 化 + 模板变量（对标 CrewAI crew.jsonc inputs）：项目新增
#      inputs（[{key,label,default,placeholder}]）+ task_template 字段；
#      render_task_template() 填空渲染；团队库点启动弹填空表单→拼任务→直接开跑；
#      ProjectEditor 可编辑；克隆/存配方继承；老项目字段自愈（缺失=普通项目，行为不变）。
#   ② 跑通即存配方：团队库「💾 存为配方（当前项目）」——深拷贝阵容/波次/闸门，
#      剥运行期状态（锁定标的/否决黑名单/补录/存档），命名+配填空后进团队库（📦配方）。
#   ③ 自 agency-agents（MIT，144 专家库）批量吸收 6 角色（避开与既有 19 专家重名）：
#      🚀增长黑客 / 🎵TikTok策略师（跨境 Shop）/ 🎯广告创意策略师 / 💰付费社交策略师 /
#      📝内容创作者 / 🎬视频优化专员 —— 全部中文简历范式（4 段结构+成功指标），
#      老数据经 PRESET_ROLE_NAMES 自动补齐。
#   ④ 竞品监控团成品配方（对标 awesome-llm-apps Competitor Intelligence Team 分工：
#      采集并行→分析→简报）：inputs 3 项 + task_template；一次性种子进老数据
#      （preset_projects_seeded 标记，用户删掉不复活）；公众号养生文同步配方化。
#   ⑤ 小件：团队库卡片加流程线（🌐研究员+⚔️竞品分析师 → 📊分析师 → ✍️写手）+
#      📦配方标记；RoleEditor 模型分档指引（取证岗便宜快档位/终稿走主模型，
#      不硬编码——档位依赖用户 model_profiles 配置）；6 核心角色 quality 补成功指标句式。
#   验证：_verify_recipes_v41482.py offscreen 15/15；功能测试（新角色入库/模板渲染/
#   老数据自愈/PRESET 补齐/成功指标 6/6）全过。
# v4.148.1（2026-09-14）角色打磨 + PM 澄清需求 + UI 化繁为简（大哥拍板三项一起做）：
#   ① 全部核心角色卡升级「员工简历」范式（对标 Omnify TeamWork）：
#      角色说明 / 工作流程（按序）/ ✅可做 / ❌不可做 / #Examples —— LLM 对
#      「清单+例子」的遵守率远高于散文。研究员先行样板，本轮铺满 12 执行角色
#      （分析师/写手/配图师/审校/策划/选品官/竞品分析师/带货文案/主图策划/
#      投放运营/转化话术师/研究员）。专家库 19 个次级角色留待后续批量迁移。
#   ② PM 加「先澄清需求」硬要求（对标 TeamWork 队长「澄清需求必须先做」）：
#      开工指令新增第 0 条 —— 五项口径（细分领域/平台/人群/数据源/交付形态）缺失时
#      整份计划只输出【澄清问题】节；runner 检测到该标记即拦停军团（status=clarify）、
#      把问题透给大哥，答案补进任务描述后重跑。PM 角色卡同步加硬要求。
#   ③ UI 化繁为简（只留聊天页 + 角色库 + 团队库）：
#      · 页面重排 0=💬聊天(默认) / 1=👥角色库 / 2=🧩团队库 / 3=📐编排(高级，默认隐藏，
#        ⚙️ 按钮展开 —— 续跑/波次微调/补录/清记忆等高级操作全保留，不删不破)；
#      · 角色库页：清单+「员工简历」详情（工具集/技能/交付形态/自检），
#        ✏️ 编辑写回角色成长库 role_library_override.json（与装技能挂角色同落点）；
#      · 团队库页：项目即配方（成员头像串+波次数+续跑标记），▶启动 / ⧉克隆 /
#        编辑 / 删除；双击条目=启动；
#      · 聊天框「启动军团 <任务>」/「开团 <任务>」直接开团（运行中不抢，防误判只认
#        两个明确前缀）。
#      验证：_verify_ui_v41481.py offscreen 实构造 26/26 全过。
# v4.148.0（2026-09-14）架构定调（大哥拍板）：**PM 职责收窄为「拉队伍、分任务、做总结」**，
#   「用 PM 帮队员配能力」的流程整体下线：
#   · 四轮实测 PM 配能力弊大于利：禁《浏览器自动化》技能的理由静态分析就错、
#     「限定工具」漏列 browser 且**存档复用**把缺口带进每一波（浏览器从未被拉起的根因）、
#     禁 web_search 割裂正当手段；PM 的精力该花在数据口径上而不是配手脚上。
#   · 落地：① _capability 恒为 {}（PM 计划的【能力配置】不再解析、不再存档/载回，
#     旧存档里的坏配置一律作废）—— apply_capability 收到空 cap 自动走
#     「角色卡 + 职责标签自动垫能力 + 技能 requires_tools 回填」路径；
#   ② PM 计划指令第 8 条改为「能力说明」（告知职责变更，缺角色/缺技能走【差技能】上报，
#     由**老板**来建新角色/装技能 —— 人来把关，对齐宪法第二章）；
#   ③ 成员派发打印 🧰 本波工具集（排查利器）。
# v4.147.9（2026-09-14）A 方案落地：数据岗强制走 browser（「浏览器从没被拉起」的根治）：
#   大哥观察：四轮军团跑下来浏览器从未被自动拉起 = 成员从未调用过 browser 工具
#   （_ensure_cdp 只在调 browser 时触发）。40+ 次抓取全走 web_fetch，C 方案
#   「靠提示倒逼」实战证无效。实验实锤：PM 能力配置若用 `tools` 限定且漏列
#   browser → browser 全没，且配置存档复用会把这个缺口带进后续每一波。
#   修：研究员/竞品分析师角色卡**移除 web_fetch**（web_search 只找 URL，
#   抓正文一律 browser_*；runner 的 read 导航 BUG 已修并同步 _internal，
#   browser_read 实测 kalodata 免登录商品榜 2399 字 ✅）。
#   另：成员派发时打印「🧰 本波工具集」日志，成员手里有什么一眼可见。
#   注意：browser_runner.py 是 _internal 外部脚本，热修不随版本号。
# v4.147.8（2026-09-14）交付形态闸「空转」根治（TK 马来团第 1 波重跑仍 FAIL）：
#   落库修复（v4.147.5）生效后，PM 终于能读到成员真实产出（研究员 30字→2716字、
#   竞品 1291字traceback→4006字）。但暴露出**形态闸形同虚设**：交的是「裸 URL 清单」
#   与「原始字段 dump」，闸却判 bad=False 全部放行。三处叠加（缺一不可，全部实锤）：
#     ① 契约只从 self.task 解析 —— 大哥的任务往往只有一句（实测「TK马来区选品运营
#        售后全链路」15 字），而「列固定 7 列 / 行数 ≥10」全写在 PM 的开工计划里。
#        契约恒空 → audit_deliverable_form 的 `if cols or min_rows:` 为假 → **跳过全部检查**。
#        修：_submit_gate 的契约源并入 PM 的【交付形态】【交接物】+ 计划全文
#        （新增 self._plan_text 保存 PM 计划）。
#     ② 列名正则不兼容 Markdown：PM 写「列**固定 7 列**：`A | B | C`」，原正则要求
#        「列」后紧接「固定」，被 ** 隔断 → table_cols 恒为空。
#        修：解析前先剥掉 * 与 ` ；并去掉首段的「7 列：」前缀。
#     ③ min_rows 取 min → 被「每类 ≥2 行」这类**分布**要求拉到 2，10 行的要求被废。
#        修：改取 max（漏检代价远大于误伤）。
#   实锤：同一份裸 URL 清单，修复前 bad=False（放行）→ 修复后 bad=True 并给出
#   「要求表格 ≥10 行，实得 0 行」；合格政策表仍 bad=False（不误伤）。
# v4.147.5（2026-09-14）根治「打回重跑永远无效」的死循环（TK 马来团第 1 波重跑仍 FAIL）：
#   症状：成员回炉明明改好了（研究员 2363 字），但 PM 用工具读产出只看到首版
#         「抓取失败：<urlopen error timed out>」（30 字）→ 判 FAIL → 打回重跑 →
#         成员又回炉改好 → 又读不到 → 又 FAIL（第 0 次「改稿 2740 字却以 30 字入账」同因）。
#   根因（两处叠加，缺一不可）：
#     ① legion_worker._submit_gate：自检回炉后重新收集产出时写死 `record=False`
#        —— 回炉新稿只进内存 texts、**不落库**；而 PM 是用 legion_get_output /
#        legion_list_outputs **工具**读产出的（读的正是落库那份）。
#        修：回炉后的 texts 就是交 PM 的最终稿，改为 record=True。
#     ② legion.record_output：落库用 `outputs.append(...)` —— 即便落了库，
#        工具会把同一成员的新旧两条**一起**输出，PM 仍读得到旧稿。
#        修：改为「同 (wave, attempt, role) 覆盖」，与 rebuild_output_text
#        「同波同 attempt 取最后一条」的既有语义对齐；jsonl 仍 append 保留审计。
#   附：build_safe.py 的 qt.conf 竞态修复（先清空中转目录再预创建）同批。
# v4.147.4（2026-09-14）TK 马来电商团第 1 波 FAIL 的根因修复（PM 验收报告实锤）：
#   问题不是成员不干活，是**代码/指令缺口让成员想干也干不了**（打回重跑会再撞同一堵墙）：
#   ① 竞品分析师 / 研究员 **有 browser 工具、却没有「JS 渲染页必须用浏览器读」的指令**
#      （只有选品官写了这条）→ web_fetch 一 timeout 就上报失败。已补齐两岗的 constraints
#      + self_check（同款硬约束）。
#   ② `_JS_HEAVY_DOMAINS` 漏了 kalodata.com 等电商数据站 → web_fetch 抓 kalodata 只返
#      17 字 title 空壳且**不给改道提示**（静默拿空壳当战绩）。已补 kalodata/fastmoss/
#      Shopee/Lazada/1688/Temu/Shein/Amazon 等。
#   ③ 补录提示文案「以下是文件内容（已截断，需读全文请用 read_file 工具）」**误导 PM**
#      以为成员能读 → PM 在任务书里让成员 read_file 读补录 JSON → 文件在 MANUAL_DIR
#      （工作区外）→ 成员报「文件不存在」卡住整波。已改为明确警告 + 给正确做法
#      （PM 必须把内容原文写进成员任务书）。
#   另：研究员「把 browser_read 原文当交付物」→ 两岗 self_check 增加形态自检。
# v4.147.3（2026-09-14）导演台台词两处 BUG（大哥实测反馈 + 复现实锤）：
#   A 多出旁白：某镜缺英文画面提示词(en)时，原写法 `shot.get("en") or shot.get("zh")`
#     会退回 zh —— 而 zh 的定义是「这一镜的中文字幕/旁白」。拿旁白当画面提示词，
#     视频模型就把旁白念出来/当字幕渲染 → 成品凭空多一条旁白，且全程静默。
#     修：en 缺失时改为据 zh 用 LLM 反推英文视觉描述；失败则用中性兜底并显式打日志，
#     **绝不拿中文旁白凑画面**。
#   B 英文台词：分镜 prompt 虽硬性要求 line 纯中文，LLM 仍会漏 "AI Agent"/"Hi"/"powerful"。
#     原 clean_dialogue 只剥杂质、不做语言修复 → 英文原样送去配音被照念。
#     修：generate_all_clips / regenerate_clip 在清洗后调用 _localize_line 转写为纯中文；
#     转写失败（LLM 偶发超时）先重试，再退「剥离英文片段」，最差本镜不配音 ——
#     宁缺毋滥，绝不把英文送去念。
#   另外按大哥指示：① VPN 提示已渲染到对话 UI（tool_browser_* 成功/失败分支均挂载 warn）
#     ② 桌面 lnk 与 _ensure_cdp 的调试 profile 统一到 %LOCALAPPDATA%\小臭玩AI\cdp_edge_profile。
#     ⚠️ 更正（v4.163.1，2026-09-26）：② 当时**只写了注释、实现没改到位** ——
#     browser_control_tools._ensure_cdp 里仍是 `if app_dir: profile = app_dir/cdp_edge_profile`，
#     而 app_dir 就是 exe 所在目录（dist，同时也是分发源）。实测 dist 下 profile 长到
#     1.2GB / 7558 文件，含 Login Data(51 条登录)/Cookies(396)/Local State，
#     打包分发即等于分发登录态。v4.163.1 已把实现对齐到本注释：抽出
#     _default_cdp_profile()，固定 %LOCALAPPDATA% 且**永不回落 app_dir**。
# v4.147.2（2026-09-14）修复「静默接管无 VPN 浏览器」BUG（BUG 巡检实锤）：
#   _ensure_cdp 开头 `if _cdp_ready(cdp): return True, ""` 是短路——只要 9222 通就
#   直接返回，完全不校验那个已存在实例有没有 VPN 扩展。实测：先用「不带扩展」的
#   参数占住 9222，再调 _ensure_cdp，返回 (True, "")，但 /json/list 里无忧行
#   worker target 数为 0 → 小臭接管到一个没有梯子的浏览器且零提示，境外抓取失败
#   还会被误判成「被墙/反爬」（与 v4.146 的三分类混淆）。
#   修法：新增 _cdp_has_vpn() 抓 /json/list 校验扩展是否真加载；若本地有扩展目录
#   而运行实例没有，返回 ok=True 但带明确 msg（不强行重启用户正在用的调试浏览器）。
#   _run_runner 把 msg 透出为 out["warn"] 并旁路落 log/cdp_vpn_warn.log。
# v4.147.1（2026-09-14）修复 v4.147.0 的引号 BUG（真机端到端实测揪出）：
#   v4.147.0 把 .lnk 的写法照搬进 _ensure_cdp，写成 f'--load-extension="{p}"'。
#   但 args 是 list 传给 subprocess.Popen，subprocess 会自动加引号，内嵌引号被
#   转义成 \" → Edge 收到带字面引号的路径值 → 当作相对路径 → 弹「无法加载该扩展…
#   清单文件丢失或不可读取」，且 CDP 9222 端口都起不来。改为不带引号
#   f"--load-extension={p}" 后：端口就绪 + /json/list 出现
#   chrome-extension://bkpoijbobhmbglhjjmnoedomdoabilol/worker.js（对照实验实锤）。
#   教训：raw 命令行字符串（.lnk ARGS）必须自带引号；list→Popen 绝不能带。
# v4.147（2026-09-13）浏览器接管通道补全：_ensure_cdp 自动拉起 Edge 时一并注入
# 无忧行 VPN 扩展（--load-extension，动态解析最新版本目录），使小臭自动接管的浏览器
# 也带梯子（此前自动拉起路径无 VPN）。扩展加载 ≠ 已连上，启动后仍需手动点「连接」。
# 桌面手动快捷方式（VPN浏览器-9222调试端口.lnk）早已带同等参数。
# v4.120（2026-09-04）白屏真凶+对话逻辑修复（基于 sessions.json 实锤）：
# ① 聊天区不再渲染 role=tool 气泡（v4.108 断点回写的全量工具结果 ≤6000 字，
#    与 tool_log 卡片重复显示、且撑爆 DOM——1204 条会话全量渲染致渲染进程 OOM，
#    这才是 v4.119 禁 GPU 后白屏仍复发的真凶）；② 全量重建只渲染最近 300 条；
# ③ 心跳探活容错：None（页面忙/流式中）不再误判死页，False 才立即重建、
#    None 连击 2 次才重建（旧逻辑每分钟白屏闪一次的风险源）；
# ④ chat_web 诊断日志落 logs/webengine_debug.log（版本/flags/崩溃/探活/重建全记录）；
# ⑤ image_gen 同秒并发文件名撞车互相覆盖 → 毫秒+随机后缀；
# ⑥ Agent 批内完全重复调用（同名同参，实测 8ms 双发）自动去重并补占位回执；
# ⑦ 系统提示新增：执行任务前禁止先调 sys_info 自检（实测生图任务连续两轮乱入）；
# ⑧ main.py flags 去掉 --disable-software-rasterizer（诊断日志实锤：与 --disable-gpu
#    叠加后 SwiftShader 也被禁 → GL 上下文 kFatalFailure → renderer 启动即 Killed、
#    崩溃-重建死循环）；崩溃自愈加熔断（60s 内 >3 次停手，防死循环吃满 CPU）。
# v4.119（2026-09-04）聊天区白屏治本：main.py 在 import PySide6 之前设
# QTWEBENGINE_CHROMIUM_FLAGS 禁 GPU 合成（Windows 上 GPU 加速撞显卡驱动/HDR/高DPI →
# renderer 周期性崩溃约 1 分钟一次，v4.118 自愈只是崩后重建治标不治本）。
# 禁 GPU 后走软件光栅化，聊天渲染无感知差异；v4.118 的崩溃自愈+心跳探活保留作双保险。
# v4.118（2026-09-04）聊天区空白自愈：ChatWebView 挂 renderProcessTerminated——渲染进程
# 崩溃（OOM/GPU）自动重建页面；新增 60s DOM 心跳探活兜底（渲染进程静默死亡/DOM 被清空），
# 重建完成经 pageReloaded 通知上层全量重渲染。修复「过几分钟对话框空白、手动刷新才恢复」。
# v4.102（2026-08-22）图像输入链路：DeepSeek 通道模型换 deepseek-flash，
# ui.py 支持视觉模型保留 image_url、普通对话/Agent 带图路由视觉模型。
# v4.101（2026-08-21）停止按钮 + 断点续传：普通 Agent 任务停止→检查点 paused→「▶ 继续上次任务」
# + 编排取消保留检查点可续跑（task_resume.mark_paused / _resume_agent_task / _scan_agent_resume）。
# v4.85（2026-08-17）集成版：生视频分辨率选择器（8 预设，实测 Agnes 透传任意 WxH 至 4K）
# + 数字人分身面板（digital_twin_panel，本人形象库+口播+首帧锁定）
# + 导演台面板（director_panel + video_pipeline 内核：LLM 剧本/分镜→逐镜生成→尾帧接力→ffmpeg 合成）。

# 更新检查源（留空=本地构建，无自动更新通道；联系构建者重打包新版即可）
UPDATE_CHECK_URL = ""

# 视觉模型「追加识别」清单（子串匹配，小写）——v4.174.0
# 用途：`ui._model_supports_vision()` 的内置词表之外，用它补认新模型名。
# **什么时候必须动它**：改了 `model_routing.complex_model` 指向的 profile 或其模型名，
# 而新模型不是内置词表能认出来的（内置含 vision/vl/gpt-4o/deepseek-flash 等）。
# 认不出来的后果很隐蔽：图像链路会把图**归一化成纯文本**发给模型 ——
# 图进了会话、路由日志也记了 reason="image"，但模型收到的是文字，只能回
# 「我这边没有收到任何图片」（v4.174.0 实测事故根因）。
# 改完跑 `tests/test_vision_channel.py`：它会拿配置里图像链路实际指向的模型
# 来核对这里认不认，用真实配置而不是写死的名字。
VISION_MODEL_EXTRA_HINTS = ()

# ---------- 日志 ----------
# v4.134.6：四条诊断日志统一改成「有上限 + 自动滚动」。
# 病根：此前它们全是裸 `open(path, "a")`，只增不减 —— 实测（2026-09-11 清点）
#   APP_DIR/debug.log                       427 KB（dist，7 周，根 logger，DEBUG 全开）
#   USER_DATA_DIR/logs/app.log              706 KB（未捕获异常兜底）
#   USER_DATA_DIR/vision_debug.log          537 KB（视觉链路）
#   USER_DATA_DIR/logs/webengine_debug.log  133 KB（白屏诊断）
# 做法**沿用 route_log.py 已验证的「超限就把当前文件滚成 .1」单备份方案**，抽成
# 公共入口给四处共用（而不是各写各的）—— 顺带保证全程只用 os.replace、**不删任何
# 文件**：本仓库的沙箱会把 os.remove 换成「移入回收站」并在回收站不可用时 fail-closed
# 抛 OSError，stdlib 的 RotatingFileHandler 滚动时恰好要 os.remove，所以不适用。
LOG_MAX_BYTES = 1 * 1024 * 1024   # 单文件上限 1 MB（约 4 个月的正常使用量）
LOG_BACKUP_COUNT = 1              # 只留一份历史（x.log.1）→ 单条日志最多占 2 MB

_LOG_ROTATE_LOCK = threading.Lock()


def _rotate_log_if_needed(path, max_bytes=None, backups=None):
    """超限就把 path 滚成 path.1（旧的 .1 被覆盖）。返回是否滚动过。

    默认单备份 —— 那条路径上**只需要一个 os.replace**，不涉及任何删除操作。
    backups>1 时才需要整理历史档（会用到 os.remove），失败也吞掉：历史档整不整齐
    是次要的，「至少把当前文件滚走」才是必须的。
    """
    try:
        mb = LOG_MAX_BYTES if max_bytes is None else int(max_bytes)
        nb = LOG_BACKUP_COUNT if backups is None else max(0, int(backups))
        if mb <= 0 or nb <= 0 or not os.path.exists(path):
            return False
        if os.path.getsize(path) < mb:
            return False
        if nb > 1:
            try:
                oldest = "%s.%d" % (path, nb)
                if os.path.exists(oldest):
                    os.remove(oldest)
                for i in range(nb - 1, 0, -1):
                    src = "%s.%d" % (path, i)
                    if os.path.exists(src):
                        os.replace(src, "%s.%d" % (path, i + 1))
            except Exception:
                pass
        os.replace(path, path + ".1")
        return True
    except Exception:
        return False


def append_log_line(path, text, max_bytes=None, backups=None):
    """把文本追加进日志文件，超限自动滚动（v4.134.6）。返回是否写入成功。

    铁律：日志是旁路，**任何异常都吞掉并返回 False**，绝不许拖垮主链路
    （崩溃兜底自己就调用它，更要保证不抛）。
    """
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with _LOG_ROTATE_LOCK:
            _rotate_log_if_needed(path, max_bytes, backups)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        return True
    except Exception:
        return False


def app_log_path():
    """根日志（debug.log）落点。
    v4.134.6：支持 `XC_LOG_DIR` 改道 —— 测试/诊断子进程不再往源码树或 dist 里写
    （实测源码树那份 debug.log 已被测试跑到 553 KB）。
    v4.164.0：默认落点由 APP_DIR 改为 WORKSPACE_DIR —— 日志属运行数据，
    落 APP_DIR（= exe 目录 = dist = 分发源）会随包分发出去。仍可用 XC_LOG_DIR 改道。
    """
    return os.path.join(os.environ.get("XC_LOG_DIR") or WORKSPACE_DIR, "debug.log")


# ---------- QtWebEngine 自己的日志（v4.134.8）----------
# ⚠️ 这个文件和上面四条**性质不同**：不是我们写的，是 **QtWebEngine 的 C++ 层**按
# 「PySide6 包目录」写死的（Chromium 子进程日志）。所以：
#   ① 源码里 grep "debug.log" 找不到任何写入点（PySide6 的 py 层零命中）；
#   ② 只有**冻结版**才会出现 —— 开发机模式下同样的日志走 stderr，
#      site-packages/PySide6/debug.log 根本不存在。
# 实测（2026-09-11）：dist 里这份 4 天涨到 **255,676 字节 / 1461 行**，
# 且 1461 行**全部是 WARNING**，来源单一：
#   web_engine_library_info.cpp:63 的「--webengine-resources-path /
#   --webengine-locales-path not passed to renderer process」（每次启动 × 每个渲染进程）。
# 🔴 治理方式 = **只能代它滚动**（本函数）。别再去试 flags 了：
#    2026-09-11 冻结版五组对照实测 —— `--log-level=2` / `=3` / `--disable-logging` /
#    `--enable-logging` 无论怎么组合，每次启动**照写 12 行**。因为它走的是
#    QtWebEngine→Qt 消息系统→包目录 debug.log 这条通道，**不受 Chromium --log-level 过滤**。
#    （v4.134.7 一度加了 `--log-level=2`，实测净效果为零，v4.134.8 已撤掉。）
#    有效性实测：灌到 1,241,423 字节 → 启动后滚成 debug.log.1，新文件 2,100 字节 / 12 行。
def qtwebengine_log_path():
    """QtWebEngine 的 Chromium 日志落点；非冻结运行返回 None（该文件不存在）。"""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    return os.path.join(base, "PySide6", "debug.log")


def cap_qtwebengine_log(max_bytes=None):
    """给 QtWebEngine 自己的 debug.log 套上限（超限滚成 .1）。返回是否滚动过。

    Qt 的渲染子进程可能正占着这个文件 → `os.replace` 会抛 PermissionError，
    但 `_rotate_log_if_needed` 内部已把所有异常吞掉，所以调用它是安全的。
    """
    p = qtwebengine_log_path()
    if not p:
        return False
    try:
        return _rotate_log_if_needed(p, max_bytes)
    except Exception:
        return False


class _CappedFileHandler(logging.Handler):
    """把日志经 append_log_line 落盘 —— 与其余三条诊断日志共用同一套滚动逻辑。

    刻意不用 stdlib 的 RotatingFileHandler：它 doRollover 里会 os.remove(旧 .1)，
    在本仓库沙箱下会被拦并抛错（等于滚动失败）。这里走 os.replace，无删除。
    """

    def __init__(self, path):
        logging.Handler.__init__(self)
        self._path = path
        # 加 %(name)s：此前格式里没有 logger 名，排查时根本无法判断某行是哪个库写的
        # （2026-09-11 量化 debug.log 噪音时就吃了这个亏，只能靠内容猜）。
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))

    @property
    def path(self):
        return self._path

    def emit(self, record):
        try:
            append_log_line(self._path, self.format(record) + "\n")
        except Exception:
            self.handleError(record)


logging.basicConfig(level=logging.DEBUG,
                    handlers=[_CappedFileHandler(app_log_path())])

# 三方库的 DEBUG/INFO 是噪音不是线索 —— 实测 dist/debug.log 3976 行里绝大多数是：
# onnxruntime 的 C 层日志（714 行）、httpx 的每请求一行（584 行）、llama_index 的
# 文件系统/embedding 细节（400+257 行）。它们既撑大文件、又把真正的线索冲淡。
# 我们自己模块的 logger（dsdesktop / rag / legion / video-agent.* / workflow_manager）
# 保持 DEBUG 不动，只压这些外部库；它们的 WARNING 以上照常落盘。
for _noisy in ("matplotlib", "PIL", "httpx", "httpcore", "urllib3",
               "onnxruntime", "llama_index", "asyncio", "duckduckgo_search"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

log = logging.getLogger("dsdesktop")

# ---------- 配置 ----------
# v4.79 起：config.json 迁到用户文档目录，与 sessions/记忆等数据同处。
# 原因：存程序目录(dist)时，每次重打包整个目录被移走，用户 API key/模型设置全丢。
# v4.134.4：支持环境变量改道（XC_USER_DATA_DIR）—— 测试子进程写 config.json 时
# 落在副本上，真实配置零污染（此前套件是「改真实 config 再还原」，中途崩溃就脏了）。
# 不设该变量 → 行为完全不变。
USER_DATA_DIR = os.path.expanduser(
    os.environ.get("XC_USER_DATA_DIR")
    or os.path.join("~", "Documents", "小臭玩AI"))
LEGACY_CONFIG_PATH = os.path.join(APP_DIR, "config.json")   # 旧位置（仅迁移用）
CONFIG_PATH = os.path.join(USER_DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "api_key": "",
    # v4.165.0：run_python 后端解释器路径（留空 = 自动探测）。
    # 与机型解耦：绿色版 Python / 多版本并存等非标准安装时，在此显式指定。
    "python_exe": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "hotkey": "ctrl+shift+d",
    "system_prompt": (
        "你是「小臭玩AI」—— 一个 Windows 本地桌面工作台。\n\n"
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
        "| 清空回收站/系统控制操作 | clean_recycle_bin | system_* 桌面系统控制（清空回收站等） |\n"
        "| 打开文件/打开应用/控制软件/输入文字/点按钮 | software_run | software_* 软件控制 |\n"
        "| 公众号/写文章/写稿子/续写 | use_skill(公众号文章) | 写作技能 |\n"
        "| 技能/装技能/搜索技能 | skill_search/skill_install | 技能管理 |\n"
        "| 浏览器打开/网页点击/填表 | browser_open/browser_click | 浏览器操作 |\n"
        "| 偏好/记住/长期/约定 | remember | 写入长期记忆 |\n\n"
        "## 路由防误触 · 强制\n"
        "**先判「是不是动作指令」，再判关键词。** 用户仅仅是**评价 / 夸赞 / 感慨**工具或模型效果"
        "（如「这能力颠覆认知」「效果真好」「太强了」「生成得不错」），**严禁**触发 image_gen / video_gen "
        "等生成类工具；只有**明确的祈使动作指令**（含「生成 / 做一张 / 画 / 出个视频 / 帮我做」等）才允许路由到生成类工具。"
        "评价句即使命中生成类关键词也不是指令，一律走普通对话，禁止编造或跑生成。\n\n"
        "## 全部能力速查\n"
        "联网搜索(web_search) | 网页抓取(web_fetch) | 文件读写(read_file/write_file) | "
        "命令(run_command) | Python(run_python) | 生图(image_gen) | 生视频(video_gen) | "
        "定时(schedule) | 自动化任务(create_automation/list_automation/delete_automation) | 截图(screenshot) | 知识库RAG(rag_index/rag_search，Obsidian Vault 可在设置中配置) | "
        "数据库(db_insert/db_query/db_update/db_delete：notes/todos/assets) | 图表(chart_gen) | "
        "日志(log_query) | 上下文(context_compress/context_summary) | "
        "Webhook(webhook_start/events/stop，端口9000) | 技能(use_skill/skill_search/skill_install) | "
        "ASR(SenseVoiceSmall) | TTS(edge-tts) | 浏览器(browser_open/click/fill/read) | "
        "系统控制(system_*：剪贴板/窗口/进程/输入) | 软件控制(software_*：pywinauto) | 长期记忆(remember)\n\n"
        "## 模型\n"
        "默认 Agnes（agnes-2.5-flash，官方当前限免，价格随时可能调整）；DeepSeek 为可选付费通道。\n\n"
        "## 硬约束\n"
        "1. 命中路由表关键词→直接调工具，禁止纯文字回答\n"
        "2. 时间/事实性问题→调 web_search，禁止凭模型知识猜测\n"
        "3. 工具结果为准，严禁编造不存在的数据\n"
        "4. 能推断参数不追问，用合理默认值\n"
        "5. 成功报产物路径，失败报真实错误，禁止谎称已完成\n"
        "6. 「再来/重新/重做/regenerate」→重新调工具，禁止复用历史结果\n"
        "7. 产物路径统一到 ~/Documents/小臭玩AI/ 对应子目录\n"
        "8. **禁止承诺式循环**：禁止连续多轮只输出「我现在开始做/马上做/下一步执行」之类的承诺而不真去调工具。每一步要么调工具、要么给出最终成品，否则算任务失败。\n"
        "9. **用户意图优先**：用户原话意图明确时，禁止跳到不相关技能/工具（如「清空回收站」→调 clean_recycle_bin，不许跑去调 PPT生成）。\n"
        "10. **选题/盘点/列方向 类需求优先用训练知识直接出文本**（见下方【爆款选题与盘点模板】），"
        "仅在用户明确说「去搜/查最新/爬数据/看实时榜单」时才调 web_search。"
        "「搜索」一词在该语境下指的是「检索联网最新数据」，不是「列选题方向」——"
        "不要把「给我列几个方向」误解成「去搜实时榜单」。\n"
    ),
    "max_history": 30,
    # v4.177.0：历史**字符预算**（第二道闸 —— 按体量，不是按条数）。
    #   0 = 关闭（行为与 v4.176 完全一致）。
    #   80000 的来由：它**不是为了平时省 token**（正常纯文本会话 30 条 ≈ 十几 KB，
    #   远够不着），而是兜住"条数不多、每条巨大"的灾难 —— 带图历史、Agent 长循环的
    #   工具结果（实测见过单次 payload 264KB）。留这个数还给 system prompt（技能表 +
    #   铁律，本身就不小）和模型输出留了余量。
    #   单位用字符而不是 token：引 tokenizer 要加依赖和打包体积，不划算（见
    #   ui._fit_history_to_budget 的说明）。
    "history_char_budget": 80000,
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
    # ---- v4.128：Agnes 文本模型（军团 PM/成员 + 导演台剧本/分镜/提示词/验收）----
    # 两档：agnes-3.0-flash（新，512K 上下文 / 65,536 输出，官方主打长任务遵循与减少空转）
    #      agnes-2.5-flash（现役稳定）
    # 默认 2.5：3.0 发布初期第三方实测稳定性差（开思考后强约束题仅半数通顺、长推理流中断），
    # 只做可选 + 自动回退，观察一两周社区稳定性与回退率数据后再议默认值。
    "agnes_text_model": "agnes-2.5-flash",
    # 选用 3.0 时：超时 / 5xx / 429 / 流中断（无 finish_reason）/ 空响应 / 结构异常
    # → 自动回退 agnes-2.5-flash 重试一次，日志标注「3.0→2.5 回退」及失败原因。
    "agnes_text_fallback": True,
    "agnes_text_fallback_model": "agnes-2.5-flash",
    # Thinking 默认关：仅 PM 验收 / 复杂任务规划等重推理环节按需开。
    # 开思考时 max_tokens 必须给足（思考过程占用输出额度，否则正文被截断）。
    "agnes_thinking_enabled": False,
    "agnes_thinking_max_tokens": 65536,
    # 域名：两个域名 = 两套独立账号体系，**key 不通用**（2026-09-28 实测）——
    #   api.agnes-ai.cn（官方国内站，会员账号在此）←→ 国内站 key
    #   apihub.agnes-ai.cn（另一套账号）            ←→ apihub 的 key
    # 拿 key 打一次 GET /models：200=配对、401=配错站。运行期实际地址见本文件
    # model_profiles["Agnes"]（优先生效）。本列表仅登记候选、运行期无人读取。
    "agnes_base_candidates": ["https://apihub.agnes-ai.cn/v1", "https://api.agnes-ai.cn/v1"],
    # ---- v4.129：产物落盘分层 ----
    # "dated"（默认）：产物/YYYY-MM-DD/<项目>/<类型>/ —— 按日期与项目归类，不再全平铺；
    # "flat"：旧行为，产物/<类型>/ —— 需要时可切回，与 v4.128 及之前完全一致。
    # 项目名三级兜底：调用方显式指定 > 当前会话标题（非泛化）> 「未分类」。
    # 注意：切换只影响**新产物**，已有文件不会自动搬家（用「归档旧产物」手动整理）。
    "products_layout": "dated",
    "sd_webui_url": "http://127.0.0.1:7860",
    "gateway_url": "http://127.0.0.1:8000",
    "gateway_autostart": True,   # v4.79：识图后端(free-api-gateway)随 APP 自动拉起
    "gateway_dir": "",  # 网关项目目录（识图后端 free-api-gateway，含 run_gateway.bat / app/main.py）；留空则跳过自启
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
                     "~/Documents"],
            "enabled": True
        }
    ],
    "model_profiles": {
        "DeepSeek 官方": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": ""},
        "硅基流动": {"base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3", "api_key": ""},
        # 端点修正：api/ai/v1 实测返回 HTTP 200 但 body 是 {"code":500,"msg":"404 NOT_FOUND"}，
        # 正确端点是 api/paas/v4。这是写入新用户 config.json 的默认值，必须保持可用
        # （老用户需自行改 ~/Documents/小臭玩AI/config.json，默认值不会覆盖已有配置）。
        "智谱 GLM": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "api_key": ""},
        # 端点修正：混元 2026-06-22 已迁移 TokenHub，旧平台域名 api.hunyuan.cloud.tencent.com
        # 连同 hunyuan-lite 一起下线——旧端点拿同一把 key 会报 401 invalid_api_key，
        # 极易误判成"key 失效"。新端点 + 新模型名配对才通（实测 1.5s 返回）。
        "腾讯混元": {"base_url": "https://tokenhub.tencentmaas.com/v1", "model": "hy3-preview", "api_key": ""},
        "免费网关 free-api-gw": {"base_url": "http://127.0.0.1:8000/v1", "model": "zhipu", "api_key": ""},
        # 端点修正：api.modelscope.cn 域名已无法解析（getaddrinfo failed），必须用
        # api-inference 子域；原 model qwen2.5-7b-instruct 也已下线（has no provider supported）。
        "魔搭 ModelScope": {"base_url": "https://api-inference.modelscope.cn/v1", "model": "Qwen/Qwen3.5-27B", "api_key": ""},
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
    "obsidian_index_delay_sec": 0,  # 增量索引延迟秒数（>0 则启动后延迟到空闲再跑，错开启动期网络；默认 0 立即）
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
    "不要先探测再操作、不要「先看看再动手」、不要做多余的前置调用。"
    "【例外·v4.193】本条的禁止对象是「无目的的探测性调用」；"
    "当用户明确要求核对/对账某文件、或本轮带附件时，"
    "「先完整读取该文件」是**必须的前置**，不算违规探测——此时读全文件优先。\n"
    "- 只调真正必要的工具：回答能直接给的就不调工具；一个工具能拿到的结果不要拆成两个。\n"
    "- 禁止自问自答、禁止复述步骤、禁止「我先做 X 然后做 Y 然后…」式的计划播报，"
    "直接执行并汇报结果。\n"
    "- 调工具前想清楚：这一步调用后能否离目标更近？不能就不调。\n"
    "- sys_info 仅当用户明确问「系统状态/能力盘点/你有哪些工具」时才可调用；"
    "执行生图/搜索/文件/技能等具体任务前禁止先自检——直接动手，别浪费轮次。\n"
    "- 最终回答只给结论和必要信息（产物路径/关键数字），不要重复工具过程。\n"
    "- 【v4.194 计数纪律】说「共 N 节/章/条/项/行」这类**数量**时，N 必须是"
    "**对整个文件的确定性计数结果**（读完后逐个数，或用 run_python 数），"
    "**禁止凭感觉估一个数**。若没数过就**不要给具体数字**——改说「很多节"
    "（我没逐个数）」或「需要用脚本数一下」。读完 ≠ 数得对：全局计数最容易出错、"
    "且最难被用户发现，宁可不说数，不可给错数。\n"
    "\n## 工具调用补充规则\n"
    "【重要】你运行在 Windows 系统上（PowerShell），不是 Linux / macOS。"
    "禁止使用 cat / grep / ls / head / tail / sed / awk 等 Unix 命令，"
    "它们会报错「不是内部或外部命令」导致工具调用浪费。"
    "读取文件内容请用 read_file 工具，脚本请用 run_python，批量操作请用 PowerShell。\n"
    "run_command 在 Windows 走 PowerShell：列文件用 Get-ChildItem -Recurse，错误重定向用 2>$null"
    "（不是 2>nul），文本搜索用 Select-String（不是 findstr），否则命令会报错浪费次数。\n"
    "- 最多连续调 12 轮工具；复杂任务分批执行（每批 ≤5 项），接近上限优先落盘。"
    "【例外·v4.193】为把用户指定的文件读全而做的 read_file 分段续读，不计入这 12 轮预算"
    "（读全优先于省轮次）：只要还没读到文件末尾，就继续用 offset 续读，不要因轮次顾虑中途停读；"
    "此例外只适用于 read_file 续读，其他工具仍按 12 轮上限约束。\n"
    "- 文件路径用相对于程序目录的相对路径（如 notes/todo.txt）\n"
    "- write_file / run_command / run_python / browser 类操作执行前弹确认框\n"
    "- image_gen 返回的图片路径直接在对话中显示\n"
    "- use_skill 需传入准确的 skill_name（见【可用技能】清单）\n"
    "- skill_install 自动拉取并安全审计（P0 危险指令拒绝安装，P1 放行并提示）\n"
    "- 工具结果超 6000 字符时会被截断，有截断时告知用户\n"
    "- 做数据分析/报告时：先用 web_search 抓 2-3 个来源的具体数据（务必带数字、年份、平台名），"
    "再用 run_python 把关键指标汇总成表格/图表，最后 write_file 落盘；禁止凭空编造数据\n"
    "- 引用文件必带凭证（v4.189）：凡声称『文件里写了X / 版本记录里有Y / 清单里列了Z』，"
    "必须先用 read_file 真读该文件，回复时给出【路径+行号+原文摘录】三件套，缺一即该条作废。"
    "禁止引用没有读过的文件内容——凭记忆的印象（包括你训练知识里的版本号、日期、功能名）"
    "一律不算数，必须以本轮工具真实读到的原文为准；文件里没有的条目就直接说没有，"
    "严禁从记忆补全。\n"
    "- 【v4.195 证据绑定协议·⟦EV#n⟧】每次工具调用返回的内容，顶上都带\n"
    "  `[EV#n] 证据登记` 抬头、结尾带 `[/EV#n]` 钉子——n 是这条返回结果的登记编号。\n"
    "  · 凡给出**具体事实**（版本号、百分比、文件路径、日期、标识符、原文摘录等），"
    "必须在句末标注它出自哪条证据：⟦EV#n⟧；能定位到行的写 ⟦EV#n:L12-30⟧。\n"
    "  · **禁止编造编号**：只能引用本轮真实出现过的编号。机器会把你的断言逐句拿去和"
    "该编号登记的原文做核对，对不上的当场标红——判据是「这条证据原文里到底有没有你说的"
    "这个事实」，不是「有没有提到来源」。\n"
    "  · 工具返回显示「本次调用失败」的证据，**不得作为任何事实的依据**，只能如实报告失败。\n"
    "  · 同一条回复里不要有的标、有的不标：已经是事实断言却没标 ⟦EV#n⟧ 的句子，"
    "会被判定为「无证据主张」。\n"
    "  · 拿不准就写「推测/待核实」——如实承认没有证据，永远好过给一个对不上的编号。\n"
    "- 【v4.196 不确定性纪律·证据水平决定语气】**标了编号不等于可以下结论**，"
    "说到什么程度要看证据够不够：\n"
    "  · **只有一条来源** → 只能写「初步判断 / 大概是 / 待核实」，禁止「确定是/答案就是/百分之百」；\n"
    "  · **两条来源互相打架** → 必须把两边数字/说法都摆出来给人看，不许挑一个下结论；\n"
    "  · **零条来源 / 引用的是失败调用 / 问今天天气股价最新版本却没调任何查询工具** → "
    "直接说「我没查到，不敢下结论」，**不许补全、不许凭印象给数字**；\n"
    "  · 只有 ≥2 条互相独立的成功证据，才可以写成肯定句。\n"
    "  （判据是「口气有没有超出证据」，一句话都没编错也可能说得太满——效果跟错一样。）\n"
    "- 【v4.196 高风险任务】任务若涉及**删改/迁移数据、合同税务合规、转账付款、"
    "用药剂量、生产/线上发布、密钥口令**，每一步写入或执行都必须等人点确认，"
    "「本次会话全部信任」与自动模式都不豁免；这类任务禁止批量连做，宁慢一步。\n"
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


def skills_disabled_all():
    """是否**显式**禁用了全部技能（v4.169.0，审查 P1-2）。

    背景：`enabled_skills = []` 同时承担了两个含义 ——「未配置」（向后兼容 = 全启用）
    与用户想表达的「全部不启用」，而实现只会当成前者，于是**没法真正关掉所有技能**。
    这里给一个显式开关，不再靠"空列表"猜意图。

    配置：config.json 里 `"skills_disabled_all": true`
    """
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return bool(json.load(f).get("skills_disabled_all"))
    except Exception as e:
        log.warning("读取 skills_disabled_all 失败: %s", e)
    return False


def is_skill_enabled(name):
    """该技能是否处于启用状态（v4.169.0 抽公共判据）。

    口径（必须与清单渲染一致，否则会出现「清单里看不到、却能按名加载」）：
      1) `skills_disabled_all` = True → 全部禁用；
      2) `enabled_skills` 非空      → 白名单，只放行列出的；
      3) `enabled_skills` 为空      → 全部启用（向后兼容）。

    为什么需要它：`tool_use_skill` 原来只按名字扫目录找技能，**不查启用状态** ——
    模型记住或猜出一个已被禁用技能的名字，照样能加载进来。
    """
    try:
        n = normalize_skill_name(name or "")
    except Exception:
        n = (name or "").strip()
    if not n:
        return False
    if skills_disabled_all():
        return False
    enabled = _load_enabled_skills()
    if not enabled:
        return True
    try:
        keep = {normalize_skill_name(x) for x in enabled}
    except Exception:
        keep = set(enabled)
    return n in keep


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

    # 技能执行要求（v4.169.0 收窄，审查 P1-1）
    #
    # 原版写的是「加载技能后**必须立即调用 run_python / write_file / run_command 落地**」
    # —— 那会把「模型觉得某技能可能合适」升级成「必须产生文件或执行动作」，
    # 是"技能带偏 + 自动调工具"的重要放大器：用户只是问一句、或只是聊聊，
    # 一旦模型自行 use_skill，就被这条规则逼着去写文件。
    #
    # 改为：**技能只服务用户当前明确的目标；要不要动工具由本轮请求与权限判定决定。**
    # 保留的部分是治"承诺式循环"（只输出计划不落地）—— 但那要在**用户确实要产出**时生效。
    HARD = [
        "## ⚠️ 技能执行要求（适用所有可用技能）",
        "1. 技能只服务**用户当前明确的目标**。不要因为「这个技能看起来相关」就自行开工或加载。",
        "2. 用户确实要产出时：**动手做**（调工具），禁止只输出大纲 / 计划 / 承诺。",
        "3. 交付物以**实物**为准（文件 / 写入 / 计算结果），完成后给出产物绝对路径"
        "（默认 ~/Documents/小臭玩AI/ 对应子目录）。",
        "4. **讨论、咨询、评估、问原因**类请求不需要产出文件或执行动作——直接回答即可。",
        "5. 用户说「做 X」= 产出 X，不是「先列 X 的章节大纲」。",
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
# v4.120：聊天区全量重建最多渲染最近 N 条消息——长会话（1000+ 条含大段工具结果）
# 一次性塞进 DOM 会把渲染进程撑到 OOM 崩溃（白屏真凶）。增量追加不受限。
MAX_RENDERED_MSGS = 300


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
                # 审计修复 F1：deepcopy 防止返回的 cfg 与 DEFAULT_CONFIG 共享
                # 嵌套可变对象（mcp_servers/model_profiles/enabled_skills 等），
                # 调用方原地改嵌套结构会污染进程级默认值。
                for k, v in DEFAULT_CONFIG.items():
                    cfg_data.setdefault(k, copy.deepcopy(v))
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
                            cfg_data.setdefault(k, copy.deepcopy(v))
                        return cfg_data
                    except Exception as e:
                        log.warning("Failed to copy config.json: %s", e)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        log.info("Created default config.json")
        # 审计修复 F1：dict() 浅拷贝共享 mcp_servers/model_profiles 等嵌套可变对象，
        # 调用方原地修改即污染进程级 DEFAULT_CONFIG。深拷贝返回。
        return copy.deepcopy(DEFAULT_CONFIG)
    # 审计修复 A1：读取失败/坏档时**绝不落盘**。原实现 cfg={} 填默认后，
    # 迁移分支会立即 save_config，把用户配置（API key 等）永久覆盖成默认值。
    parse_failed = False
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config.json 顶层不是对象")
    except Exception as e:
        log.error("Failed to read config.json: %s", e)
        parse_failed = True
        try:  # 留存坏档取证，便于用户手工找回
            os.replace(CONFIG_PATH,
                       CONFIG_PATH + ".bad." + time.strftime("%Y%m%d_%H%M%S"))
        except OSError:
            pass
        cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, copy.deepcopy(v))  # 审计修复 F1：同上，深拷贝填充
    if parse_failed:
        return cfg  # 仅在内存返回默认值，本次禁止任何写盘

    # v4.84 迁移：自进化默认开启（轨迹自动提炼）。仅对仍处旧默认 False 的存量配置一次性打开，
    # 之后再尊重用户手动开关（标记置位后不再翻回）。
    if not cfg.get("_mig_v484_self_evolve", False):
        if cfg.get("orch_trace_auto_refine", False) is False:
            cfg["orch_trace_auto_refine"] = True
        cfg["_mig_v484_self_evolve"] = True
        try:
            save_config(cfg)
        except Exception as e:
            log.warning("load_config 迁移落盘失败: %s", e)

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
            # v4.134.9：异常分支不再 os.remove(tmp)。沙箱把删除换成「移回收站」、
            # 回收站不可用时 fail-closed 抛错（正是 R2 根因）；半截 tmp 残留危害远低于丢配置。
            # 正常路径 os.replace 已消费 tmp；仅写失败才到这，留着下次写覆盖即可。
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
    # v4.164.0：旧值可能指向 APP_DIR（dist）下的 rag_data —— 属运行数据误落分发目录。
    # 这里做一次迁移改写（用户若自定义到别处则原样保留）。
    if rag_data_dir and os.path.abspath(rag_data_dir).startswith(
            os.path.abspath(APP_DIR) + os.sep):
        rag_data_dir = ""
    if not rag_data_dir:
        rag_data_dir = os.path.join(WORKSPACE_DIR, "rag_data")
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
    skipped = 0
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
            else:
                # None = 增量索引判定「未变化」，跳过不重复 embedding
                skipped += 1
    except Exception as e:
        log.warning("Obsidian 索引遍历异常（已安全中止）: %s", e)

    return f"已索引 Obsidian 仓库 ({vault_path}): {count} 个文件（{skipped} 个未变化跳过）"


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

