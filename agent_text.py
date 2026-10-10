# -*- coding: utf-8 -*-
"""agent_text.py —— Agent 纯文本判据层（v4.216.0 从 agent.py AgentWorker 拆出）

内容：生成意图判据族（v4.159 组合式：_gen_intent/_verb_near/_phrase_hit/
_neg_hit/_ref_by_position/_ref_existing_artifact）、浏览器路由判据
（v4.168.1：_prog_fetch_intent/_is_bare_url/_route_force_tool）、
动作意图（_detect_action_intent/_content_creation_only）、
承诺/疑问/伪工具调用识别（_looks_like_promise/_looks_like_question/
_looks_like_fake_tool_call/_is_question）、引用审计（_audit_ref_needed）
及其全部关键词常量（原 AgentWorker 类属性，整族闭合、零实例状态）。

零行为变化：函数体逐行搬移，仅去 self；调用方 agent.py 以
agent_text._x(...) 形式调用。
"""
import logging
import re

# 与 agent.py 同名 logger（getLogger 单例，两处拿到同一对象）——
# _detect_action_intent 的 intent_guard 前置短路里有 log.info，
# 缺了会被 except Exception 静默吞掉，判据悄悄失效（v4.216.0 搬移时实测踩中）。
log = logging.getLogger("dsdesktop")

# 对账意图词（需与文件语境词同时命中才触发，防「核对一下这道题」误伤）
_AUDIT_KW = ("核对", "对账", "交叉核验", "逐条核验", "查证", "核实一下",
             "比对一下", "比对清单", "对得上", "对不上",
             "changelog", "change log", "更新日志", "版本记录")
# 文件语境词（小写比较；中文不受 lower 影响）
_AUDIT_FILE_CTX = (".md", ".json", ".txt", ".py", ".yaml", ".yml",
                   "changelog", "change log", "更新日志", "版本记录",
                   "文件", "清单", "日志")
# v4.102 fix10：纯内容创作豁免——区分「写文字内容」（不需工具）vs「写文件/产物」（需工具）。
# _CREATE_VERB_KW：内容创作动词（产出是文字/文本内容）
_CREATE_VERB_KW = (
    "写文案", "写一篇", "写一段", "写个", "写点", "写一下", "写文章", "写小说",
    "写故事", "写个故事", "写剧本", "写脚本", "写大纲", "写总结", "写回答",
    "写内容", "写文字", "写标题", "写简介", "写介绍", "写个介绍", "帮我写",
    "给我写", "写嘛", "写个嘛", "写个什么", "写点什么", "写一篇什么",
    "创作", "编个", "编个故事", "编文案", "生成文案", "生成文章", "生成内容",
    "生成一段", "生成一篇", "生成大纲", "生成脚本", "生成故事", "生成标题",
    "生成回答", "生成介绍", "生成摘要", "生成总结", "写作文", "写作业",
    "拟个", "拟一个", "拟定", "起草", "改写", "润色", "扩写", "起个标题", "起标题",
)
# _ARTIFACT_KW：需要真实落盘/产物的词——命中即不豁免（应走工具）。
# 注意：不放宽泛的「图片/视频」（会误伤「口播视频的文案」这类内容创作场景词），
# 只放真正指「文件/表格/文档类产物」的明确词。
_ARTIFACT_KW = (
    "写文件", "写入文件", "保存", "保存到", "导出", "导出文件", "导出到",
    "存到", "存文件", "存盘", "落盘", "生成文件", "创建文件", "新建文件",
    "建个文件", "生成excel", "生成表格", "生成csv", "生成ppt", "生成word",
    "生成pdf", "生成报告文件", "生成一个文件", "制作表格", "表格文件",
    "做成表格", "整理成表格", "统计成表", "做成word", "做成pdf", "做成ppt",
    "生成报表", "输出文件", "输出为", "保存为",
)
# _REAL_TOOL_KW：明确工具操作/数据动作——命中即不豁免（应走工具/数据动作）。
_REAL_TOOL_KW = (
    "搜索", "搜一下", "查一下", "上网查", "爬取", "爬虫", "数据分析", "数据处理",
    "运行", "python", "代码", "脚本", "执行", "计算", "统计分析", "报表",
    "打开文件", "读取文件", "读文件", "提取", "解析", "爬", "抓取",
    "做张表", "整理", "批量", "翻译", "总结文档", "分析文档",
    # 明确的多媒体/产物生成（走 image_gen / video_gen 工具）：
    "生成图片", "生成一张图片", "生图", "画一张", "画图片", "配个图", "做张图",
    "出图", "生成视频", "生成一段视频", "生成个视频", "做视频", "做一段视频",
    "做个视频", "剪辑", "配音", "生成一张", "做一段视频", "生成一个小视频",
    # v4.254.0（A3 漏活）：**混合指令里的配图信号** —— 「写篇文章并配张封面图」
    # 这种「文字内容 + 配图」的组合，此前因配图动词不在本表，被
    # `_content_creation_only` 判成纯文本创作（text_only=True、needs_action=False）
    # → 文章写了、**封面图根本不会生成**（实测探针不符清单第 11 条）。
    # 命中即不豁免，交模型按工具能力自己分解。
    "配图", "配张图", "配张封面", "配个封面", "封面图", "生成封面", "做张封面",
    "出张图", "配几张",
)
# 任务意图关键词：命中即认为本次需要执行操作（搜索/写文件/分析等），
# 用于循环护栏——还没真正动手时强制模型调用工具，避免「说要搜却不动」。
_ACTION_KEYWORDS = (
    "搜索", "搜", "查", "查一下", "写", "生成", "做", "执行", "分析", "数据",
    "文件", "定时", "截图", "打开", "运行", "创建", "整理", "发", "导出",
    "翻译", "总结", "爬", "抓", "下载", "安装", "监控", "提醒", "找",
    # v4.60：催促类——用户催着动但没指定具体动作
    # v4.245.0（审查报告 I-7）：删裸「动」「快」单字（「我动了一下」「快看这个」这类
    # 闲聊被误判成要干活）；「动起来/动手/快点/赶紧/加速」双字形式已覆盖真催促指令。
    "继续", "动起来", "干活", "动手", "开始", "快点", "麻利", "赶紧",
    "行动", "加速",
    # v4.186：诊断/检视类动词——自检/排查/巡检/盘点等系统检查请求。
    # 缺这些会让 needs_action=False，连带关掉 step-1 force_required 与 nudge
    # 两道防线（模型"只说不做"空转）。刻意不含裸"检查/看看"（太泛，会把
    # 「检查这句话有没有语病」也推去调工具）。
    "自检", "自查", "排查", "排错", "诊断", "巡检", "体检", "盘点", "核验", "扫描",
)
# v4.80：意图→工具路由——能明确推断要调哪个工具时直接指定 tool_choice。
# v4.158：移除裸 "视频"子串白名单（覆盖面过广易误触）。
# v4.159：彻底废除子串白名单，改为「组合式生成意图判据」——详见 _gen_intent。
#   根因：子串命中=下达指令 既误报（「做视频的稿子」被路由 video_gen、
#   「短视频的标题」被路由）又漏报（「做个视频」不构成『做视频』子串、真指令反漏），
#   且白名单枚举永远补不完。组合式判据（生成动词+媒体宾语+宾语为实头+非疑问句+非平台名）
#   一次堵三洞。
_GEN_VERBS = (
    "做", "生成", "来个", "来一个", "来一张", "来一条", "来一段", "来幅", "来段", "来一期",
    "搞", "整", "弄", "制作", "产出", "输出", "上一张", "上一条",
    "给我做", "给我生成", "给我来", "来点", "来些",
    "画", "画张", "画个", "画幅", "拍", "拍个", "拍一条",
    "剪辑", "配音", "渲染", "设计", "创作",
    "剪", "合成", "合并", "拼", "接", "出", "出片",
)
_VIDEO_OBJ = (
    "视频", "短片", "短视频", "片子", "mv", "预告片", "宣传片", "动画",
    "视频片段", "小视频", "竖屏视频", "口播视频", "混剪", "vlog", "vlog视频",
    "成品", "成片", "作品", "口播",
)
_IMAGE_OBJ = (
    "图片", "照片", "插画", "海报", "封面", "头像", "表情包", "配图",
    "画作", "动图", "壁纸", "banner", "logo", "图",
)
# 明确祈使短语（动词+量词、无显式宾语名词）：compositional 名词判据覆盖不到，
# 仅补「画一张/拍一张」这类。均为完整祈使短语、非裸宾语子串，误报面极小。
_MEDIA_PHRASE = (
    "画一张", "画个图", "画幅图", "画张", "画个", "画一幅",
    "拍一张", "拍条", "拍一个", "拍张", "配个图", "出张图", "来张图", "做张图",
)
_STATUS_KW = (
    "进度", "如何", "怎么样", "咋样", "好了吗", "完成了吗", "完成没",
    "到哪", "到哪了", "状态", "做了吗", "生成了没", "出来没", "出来了吗",
    "还好吗", "还在吗", "啥情况", "怎么样了", "进行到", "现在怎样",
    "完了吧", "生成完了", "跑完了", "完了没",
)
_BARE_VERB_KW = (
    "生成", "做", "出", "画", "用agnes", "做啊", "生成啊", "重做", "重新", "再来",
)
# v4.103 五次：浏览器路由——「打开xx网页/网址/链接」强制 browser_open。
# 背景：用户说「打开知乎网页」，模型不选 browser_open 反而用 window_list/process_start
# 自行拉 Edge（拉起后无窗口、还触发重复调用护栏卡死）。路由词表：
# ① URL 特征直接命中；② 站点/网页词 与 打开类动词 共现才命中（防「打开文件」误伤）。
_BROWSER_URL_KW = ("http://", "https://", "www.", ".com/", ".cn/")
_BROWSER_SITE_KW = (
    "网页", "网址", "网站", "链接", "浏览器", "页面",
    "知乎", "微博", "b站", "bilibili", "淘宝", "京东", "百度", "哔哩哔哩",
    "谷歌", "google", "github", "csdn", "掘金", "搜狐", "网易", "腾讯网",
)
# v4.103 五次：浏览器「打开」类动词。
# v4.168.1 收窄：**移除「抓取」** —— 它是"抓下来喂程序"的意思（系统提示里
# 「抓取」本来就映射 web_fetch），不是"打开这个网页"。
# 事故：自动化任务写「requests 直连 https://github.com/trending」，被
# 「抓取/URL 同现」判成浏览器意图，每天被强制调 browser_open。
_BROWSER_OPEN_VERB_KW = ("打开", "访问", "浏览", "逛逛", "逛一下", "看一下", "看看", "截")
# v4.168.1：**程序化抓取**信号 —— 命中即一票否决浏览器路由。
# 理由：这些词说明用户要的是"把数据拿到手"，通道由任务自己指定
# （RSS / requests / API / 浏览器渲染只是兜底手段之一），
# 不该由本地判据替他选浏览器，更不该注入"必须调用 browser_open"的伪指令。
_PROG_FETCH_KW = (
    "抓取", "爬取", "爬虫", "拉取", "采集", "抓下来", "取回来", "爬数据",
    "rss", "feed", "requests", "urllib", "httpx", "curl",
    "直连", "服务端直出", "纯 http", "无需浏览器", "不用浏览器", "别上浏览器",
    "接口", "api.", "/api/", "api.github.com",
)
# 用于"整句就是一个 URL"的判定与清洗
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。；、）)】\]\"']+")
# 选题/盘点/列方向 类关键词（v4.56）：命中时不算"需要执行"——AI 应直接出文本，
# 不要被 force_required 强行推到调搜索。
_TOPIC_KEYWORDS = (
    "列方向", "列选题", "想几个", "盘点", "选题", "爆款方向", "做什么内容",
    "给我想", "给我建议", "推荐方向", "哪些方向", "哪些选题", "哪些赛道",
    "有什么选题", "给我几个", "列几个", "出主意", "给我列", "写什么",
)
# 隐式匹配：内容平台 + 方向词（v4.56 补）——避免「小红书爆款」被推到搜狗
_TOPIC_PLATFORMS = ("小红书", "抖音", "视频号", "公众号", "知乎", "微博", "b站", "bilibili", "快手")
_TOPIC_DIR_KEYWORDS = (
    "爆款", "趋势", "风向", "方向", "赛道", "品类", "选题", "增长", "画像",
    "做什么", "写什么", "发什么", "内容", "玩法", "风格", "推荐", "建议",
    "榜单", "最新", "热门", "爆火", "火",
)
# v4.159.2：引用/质疑语境标记——命中即一票否决（在生成意图判断【之前】），
# 解决「豁免挪过头」+「否定侧空白」副作用：用户在【谈论/质疑/引用】某个生成动作
# 而非下达指令时（如「分析下生成视频这件事」「你刚才说的生成个视频，是BUG」
# 「我什么时候让你生成视频了」），强制路由会误触 video_gen → 向思考模型注入伪用户
# 指令，用户喊停反而被强制生成（危害远大于漏报）。
#
# v4.188 P3（词表漂移归一）：与 intent_guard._STRONG_REF_KW 语义相同但各存一份，
# intent_guard 侧补词 agent 永远跟不上（ui._looks_like_learning_question 在
# v4.172.0 踩过同款坑）。写法约定：**先字面量直赋（冻结副本），再 try 块里并集
# 增强为「intent_guard 权威表 + 本地宽词」**（行为只增不减，与 v4.168 收归
# is_praise 的先例同一原则）——字面量直赋是给两条路的：① intent_guard 不可用时
# 的 fallback；② tests/test_route_injection_guard.py 的 AST 提取器只认类体
# 直接 Tuple 赋值（try 内赋值不收），它拿冻结版即可测出路由行为不回归。
_REF_KW = (
    "这件事", "你说的", "你刚才说的", "是BUG",
)
# v4.244.0（审查报告 I-3）：裸回指词——必须与回指锚点（「刚才/上面/之前/你说的」等）共现
# 才算回指语境，避免「明天什么时候下雨」「讨论一下AI」「评价方案」见词就丢强制路由。
# 锚点刻意不收「这个/那个」（泛指代词，会误伤「评价这个方案」这类祈使），只认明确回指短语。
_REF_BARE_KW = ("什么时候", "让你", "讨论", "评价")
_REF_ANCHOR = ("刚才", "上面", "之前", "你说的", "这句话", "这句", "那条")
# v4.158→v4.159.1：讨论/复盘/质疑类弱豁免（分析/解释/聊聊等），仅在【未命中生成意图】
# 时兜底生效；v4.159.2 将 讨论/评价 上提为 _REF_KW 引用标记（前置一票否决），
# 此处仅留弱讨论词，降低误伤真指令概率。
#
# ⚠️ v4.188 P3 改名 _DISCUSS_KW → _WEAK_DISCUSS_KW：intent_guard._DISCUSS_KW
# 是**另一张表**（「讨论工具动作」判定，为什么会/是不是/是否危险…），与本表
# （媒体语境弱豁免）同名不同义——同名漂移比内容漂移更危险（将来谁「对齐」
# 它们就出事），改名消歧并在此声明两者无关。
_WEAK_DISCUSS_KW = (
    "聊聊", "聊", "谈", "谈一下", "说说", "说说看",
    "分析", "分析下", "分析一下", "诊断", "复盘", "怎么看", "怎么理解",
    "为什么", "怎么回事", "咋回事", "什么原因", "原因在哪", "是不是",
    "算不算", "是bug", "是 bug", "bug", "BUG", "隐患", "解释", "解释下",
    "解释一下", "评价下", "评估", "评估下", "你的行为", "自我诊断", "自检",
)
# v4.159.3（P2）：位置判据的引用/分析语境辅助表。
# 元话语动词：分析(下/一下)/聊聊/讨论/评价/解释/诊断… 其宾语是「被讨论的对象」而非用户指令。
# 注意：刻意【不收】裸「分析」「聊」「谈」单字——会误中『大数据分析』『刚才聊的』『话题』
#       等复合词；标准口语形式用 分析下/分析一下/聊聊/谈一下/谈谈 覆盖即可。
#       「讨论/评价」已在 _REF_KW（位置判定同样认得），此处不再重复。
# v4.188 P3：与 intent_guard._META_VERBS / _CLAUSE_SEP 是同一份语义——
# 字面量直赋（冻结副本，AST 提取器/fallback 用）后再 try 块引用权威表，
# 将来单边补词自动同步。
_META_VERBS = (
    "分析下", "分析一下",
    "聊聊", "聊一下", "聊一聊", "谈一下", "谈谈",
    "说说", "说说看",
    "解释", "解释下", "解释一下", "诊断", "复盘",
    "怎么看", "怎么理解", "为什么", "怎么回事", "咋回事", "什么原因", "原因在哪",
    "评价", "评价下", "评估", "评估下", "隐患", "自检", "自我诊断",
)
# 独立分句分隔符：生成短语【之前】若出现这些，说明生成短语是独立指令而非被分析的对象。
# 例：『生成个视频，顺便分析下这个题材』中的「，」使分析句不回头压制生成指令（O4 仍返 video_gen）。
_CLAUSE_SEP = ("，", "。", "；", "？", "?", "然后", "顺便", "并且", "而且",
               "再", "之后", "完后", "以及", "并", "、")

# ---- intent_guard 运行时归一（v4.188 P3 词表漂移归一，v4.216.0 随词表迁入）----
try:
    import intent_guard as _ig_ref
    _REF_KW = tuple(_ig_ref._STRONG_REF_KW) + _REF_KW
except Exception:
    pass
try:
    from intent_guard import _META_VERBS as _ig_meta
    from intent_guard import _CLAUSE_SEP as _ig_sep
    _META_VERBS, _CLAUSE_SEP = tuple(_ig_meta), tuple(_ig_sep)
except Exception:
    pass

def model_rejects_tool_required(model, base_url=""):
    """v4.234（C 修复）：判定某模型是否**不支持** tool_choice=required / 指定函数调用。

    返回 True = 该模型会 400，上层应降级（不强制 required，改指令注入）。

    实测结论（v4.234 探针 2026-10-08）：
      - agnes-3.0-flash（api.agnes-ai.cn / apihub.agnes-ai.cn）实测 *支持* required 与
        指定函数调用，均返回 200 并正常调工具。v4.162 将其列入「推理模型豁免」是基于
        DeepSeek 思考模式 400 的错误类推、从未实测，本次撤销。
      - 仅 DeepSeek 官方推理模型（api.deepseek.com + 含 think/reason/r1 特征）及模型名
        带思考特征的（think/reason/-r1/reasoning/thinking）才真拒 required。
    """
    m = (model or "").lower()
    b = (base_url or "").lower()
    if any(k in m for k in ("think", "reason", "-r1", "reasoning", "thinking")):
        return True
    if "api.deepseek.com" in b:
        return True
    return False


def _looks_like_promise(text):
    """判断模型返回是否像『承诺执行却不行动』——收紧版（v4.186 接线兜底用）。

        命中需三条**同时**满足，避免误伤正常最终回答（长周报/代码块/带路径交付）：
          ① 短文本（≤400 字；真成果通常更长）；
          ② 不含代码块 / URL / 路径 / 『已保存·已生成·已写入·已完成』等实质交付标记；
          ③ 同时含「承诺短语」与「动作词」。
        已刻意剔除裸『看看/查查/用/下一步/帮你/让我/确认』——它们会把正常回复
        （如周报里『下一步建议…』或『我确认过了，没问题』）误判成空转
        （那是 local-judge-false-positive 踩过的坑，不在防空转上重犯）。
        """
    t = (text or "").strip()
    if not t or len(t) > 400:                       # ① 空头承诺通常很短
        return False
    _low = t.lower()
    if any(m in _low for m in ("```", "http://", "https://", "~/", ":\\",
                               "已保存", "已生成", "已写入", "已落盘", "已导出",
                               "已完成", "已执行", "已修复")):
        return False                                 # ② 已给实质交付物 → 不是空转
    promise = ("我来", "我先", "我这就", "我马上", "现在开始", "马上开始",
               "这就去", "去搜索", "去查", "去写", "去执行", "去生成", "去排查",
               "开始自检", "开始检查", "开始排查", "开始诊断", "开始扫描",
               "继续自检", "继续检查", "继续排查", "继续诊断", "继续扫描",
               "先检查", "先排查", "先诊断", "先自检", "先扫一遍", "先查一下",
               "我检查", "我排查", "我诊断", "我扫描", "我核验", "我复核",
               # v4.234（B 修复）：截断截图真实空转语料的承诺/意图特征
               "再补", "接着", "继续补", "现在联网", "联网", "准备去",
               "我去", "直接", "我准备", "打算去", "我这就去")
    action = ("搜索", "排查", "检查", "诊断", "巡检", "自检", "核验", "扫描",
              "复核", "抓取", "写入", "写文件", "执行", "读取", "调用工具",
              "跑一下", "跑个",
              # v4.234（B 修复）：覆盖「再补一次搜」「直接查」等单字动作
              "搜", "查")
    return any(p in t for p in promise) and any(a in t for a in action)  # ③ 承诺 ∧ 动作
def _audit_ref_needed(cur, prev):
    """v4.189 批②：判定本轮是否「对账/核对文件」类请求。

        双条件命中才触发（防误伤）：
          ① 对账意图词（核对/对账/交叉核验/changelog…）；
          ② 文件语境词（.md/.json/文件/清单/更新日志…）。
        「核对一下这道题的答案」无文件语境 → 不触发；
        「帮我核对这份 CHANGELOG」双条件命中 → 触发。
        cur/prev：当前句与上一句用户原话（v4.80b 同款取样），任一命中即触发。
        """
    for t in (cur, prev):
        t = (t or "").strip()
        if not t:
            continue
        _low = t.lower()
        hit_audit = (any(k in t for k in _AUDIT_KW)
                     or any(k in _low for k in ("changelog", "change log")))
        if hit_audit and any(k in _low for k in _AUDIT_FILE_CTX):
            return True
    return False
def _looks_like_question(text):
    """v4.61：判断模型输出是否像「向用户追问」而非推进任务。
        用于循环护栏：连续追问则早停防刷屏。
        """
    t = text or ""
    if "？" not in t and "?" not in t:
        return False
    markers = ("需要你", "请告诉", "请给", "请提供", "你的", "提供", "几个",
               "多少", "什么", "如何", "怎么", "告诉", "补充", "了解", "信息")
    return any(m in t for m in markers)
def _looks_like_fake_tool_call(text):
    """v4.98 撒谎检测器：模型不真发 tool_call，却用文字"演"工具调用
        （伪造 [工具] run_python / ✅ 已保存:路径 / run_python( / 伪 tool_call JSON 等）。
        在无 tool_calls 的纯文本响应里命中这些标记，即视为撒谎，不当最终结果展示。"""
    t = text or ""
    if not t:
        return False
    markers = (
        "[工具]", "[工具调用]", "run_python(", "run_python (",
        "已保存:", "✅ 已保存", "🔄 重新生成", "✏️ 改写问题",
        "工具调用：", "工具调用:", "调用了工具", "已调用工具",
        "已写入文件", "代码已保存", "文件已生成", "文件已创建",
        '"name": "run_python"', '"name": "write_file"', '"name": "web_search"',
        '"name": "image_gen"', '"name": "run_command"',
    )
    if any(m in t for m in markers):
        return True
    # 伪 tool_call JSON 结构：含 "function" 且像工具调用
    low = t.lower()
    if '"function"' in low and ("run_python" in low or "write_file" in low
                                or "web_search" in low or '"name"' in low):
        return True
    return False
# ---------- v4.159 组合式生成意图判据 ----------
def _is_question(text):
    """非疑问句才视为下达生成指令。疑问句（含/结尾 吗/呢/怎么/为什么 等）一律不算。"""
    if not text:
        return False
    s = text.strip()
    if s.endswith(("？", "?", "吗", "呢", "么")):
        return True
    # v4.244.0（审查报告 I-2 尾巴）：剥离书名号/引号内的内容（标题/引用文案），
    # 避免「标题叫《如何用AI赚钱》」「文案是『状态拉满』」里的疑问词被误判成提问。
    _stripped = re.sub(r'[《「『“"‘\'][^》」』”"‘\']*[》」』”"‘\']?', "　", s)
    if any(k in _stripped for k in ("怎么", "为什么", "为何", "是否", "是不是",
                                     "能不能", "可以吗", "行吗", "如何", "咋")):
        return True
    # v4.159.5：补闭集疑问代词（什么/啥/哪些/哪个/多少/多久），结构性识别疑问句，
    # 堵死『做视频需要什么条件/有啥技巧/有哪些坑/走哪个接口/要花多少钱/要多久』类
    # 危险侧误触（此前缺疑问代词，单靠 吗/呢/怎么 漏判）。绝不补短语（开集坑第五次警示）。
    if any(k in _stripped for k in ("什么", "啥", "哪些", "哪个", "多少", "多久")):
        return True
    return False
def _verb_near(scan, obj_idx, obj_len, window=30):
    """宾语前后 window 字符内是否存在生成类动词（祈使结构：动词…宾语 或 宾语…动词）。"""
    lo = max(0, obj_idx - window)
    hi = min(len(scan), obj_idx + obj_len + window)
    pre = scan[lo:obj_idx]
    post = scan[obj_idx + obj_len:hi]
    for v in _GEN_VERBS:
        if v in pre or v in post:
            return True
    return False
def _gen_intent_span(text, objs):
    """返回首个【有效生成意图】的宾语起始位置（供位置判据使用），无则 -1。
        逻辑与 _gen_intent 完全一致：生成动词 + 媒体宾语共现、宾语为实头、非疑问、非平台名。"""
    if not text:
        return -1
    if _is_question(text):
        return -1
    # 平台名排除：视频号 是平台而非视频宾语，临时占位避免命中
    scan = text.replace("视频号", "　　")
    for obj in objs:
        idx = scan.find(obj)
        while idx != -1:
            # 宾语若为修饰语（后接『的』，如『做视频的稿子』『短视频的标题』）→ 跳过，
            # 继续找下一处出现；只有作实头的宾语才算真下达生成指令
            if idx + len(obj) < len(scan) and scan[idx + len(obj)] == "的":
                idx = scan.find(obj, idx + len(obj))
                continue
            # v4.159.4：『视频封面』『短视频海报』等——视频宾语紧贴图片宾语（无『的』）→
            # 视频是封面/海报的修饰语，封面才是真宾语，跳过该处视频匹配，避免『做个视频封面』
            # 被误判为生成视频（封面是图片意图）。仅对视频宾语生效，图片宾语不受影响。
            after = scan[idx + len(obj):]
            if obj in _VIDEO_OBJ and any(after.startswith(io) for io in _IMAGE_OBJ):
                idx = scan.find(obj, idx + len(obj))
                continue
            if _verb_near(scan, idx, len(obj)):
                return idx
            idx = scan.find(obj, idx + len(obj))
    return -1
def _gen_intent(text, objs):
    """组合式生成意图：生成类动词 + 媒体宾语 共现，且宾语为实头（非『X的宾语』修饰语）、
        非疑问句、非平台名（视频号）。取代 v4.158 子串白名单，一次性堵死误报/漏报/枚举三洞。"""
    return _gen_intent_span(text, objs) != -1
def _phrase_hit(text):
    """明确祈使短语命中（动词+量词无显式宾语，如『画一张』），仅补 compositional 名词判据。"""
    if not text:
        return False
    if _is_question(text):
        return False
    return any(p in text for p in _MEDIA_PHRASE)
# v4.243.0（审查报告 I-1）：_neg_hit 的本地降级词表，与 intent_guard._NEG_PHRASES
# 对齐（含「停一下/暂停」）。正常路径走 intent_guard.is_negation（含长度/约束闸），
# 仅在 intent_guard 不可用时退回本表，绝不放过喊停。
_NEG_HIT_FALLBACK = (
    "别生成", "别做", "别搞", "别弄", "别画", "别拍", "别发", "别去",
    "别再", "先别", "别急", "别管", "别碰", "别了", "不用", "不要",
    "取消", "停止", "别理", "别动", "别提", "停一下", "暂停",
    "不做", "不弄", "不搞", "不生成", "不剪", "不拍", "不画",
    "不做了", "先不做了", "再想想", "以后再说", "算了", "先不做",
)
def _neg_hit(text):
    """否定一票否决（v4.159.2 引入；v4.159.4 重构裸『别』判据）：
        用户在取消/拒绝/喊停生成指令时，绝不路由生成工具。
        误报（错杀真指令）仅造成安全侧漏报，可接受；漏报（没拦住取消）会触发强制生成，危害大，故覆盖面略宽。

        v4.159.4 关键重构（方法论定式：凡『排除类』判断一律改【后向/位置判据】或【一票否决表】，
        不再用『枚举复合词』——前向白名单是开集，辨别/识别/类别/个别/送别/别扭 永远补不完）：
          · 裸『别』字由【前向白名单排除复合词】改为【后向词法判据】——只看后 1-3 字是否跟生成动词
            或语气助词（闭集，可枚举），实测修好 5 条误杀（辨别/识别/类别/个别/送别/别扭 不再误杀真指令）。
          · 补一般否定短语（不做/不弄/不搞/不生成/不剪/不拍/不画/不做了/先不做了/再想想/以后再说/算了/先不做），
            修『视频先不做了』类（否定词与生成短语跨分句，旧 window=30 够不到）。
        v4.159.5 收口（用户自检 54 用例复测暴露的 5 误杀真指令）：
          · 前向复合词闭集豁免——『别』前一字属可左缀成词闭集（特/分/差/辨/识/类/个/送/级/性/告/离/
            区/辞/样/致）时跳过，根治『特别想做/特别出彩/分别拍/差别做』被当否定误杀。
          · 后向窗口收紧到紧贴后一字 + 动词集剔除『出』，根治『别出心裁做个视频』被当否定误杀
            （『出』在后向 3 字窗内把『别出心裁』命中）。绝不补短语（开集坑第五次警示）。"""
    if not text:
        return False
    # v4.243.0（审查报告 I-1）：委托 intent_guard.is_negation —— 唯一真源，含长度闸
    # （>40 字不算喊停）与约束句式闸（任务祈使后的「不要」是加约束不是叫停）。
    # intent_guard 不可用时退回下方本地词表（绝不放过喊停）。
    try:
        import intent_guard as _ig
        return _ig.is_negation(text)
    except Exception:
        pass
    # 否定短语一票否决表（闭集、可枚举）—— 降级兜底，与 intent_guard._NEG_PHRASES 对齐
    for k in _NEG_HIT_FALLBACK:
        if k in text:
            return True
    # 裸『别』字【后向词法判据 + 前向复合词闭集豁免】（v4.159.5）：
    #   ① 前向复合词闭集豁免——『别』前一字若属可左缀成词的闭集
    #      （特/分/差/辨/识/类/个/送/级/性/告/离/区/辞/样/致），则该『别』是复合词
    #      （特别/分别/差别/辨别/识别/类别/个别/送别/级别/性别/告别/离别/区别/辞别/
    #      别样/别致）的一部分，非否定词，跳过。闭集可穷举（非开集枚举，根治 v4.159.4
    #      仍误杀『特别想做/特别出彩/分别拍/差别做』类真指令）。
    #   ② 后向窗口收紧到【紧贴后一字】(tail0)，且动词集剔除『出』：避免『别出心裁』
    #      (别+出+心裁) 被当否定误杀真指令『做个视频』；『出』作否定前接词极罕见
    #      （别出成品），漏报代价可控（闭集判据优先于开集枚举）。
    _NEG_COMPOUND_BEFORE = set("特分差辨识类个送级性别离区辞样致")
    for m in re.finditer("别", text):
        i = m.start()
        if i > 0 and text[i - 1] in _NEG_COMPOUND_BEFORE:
            continue
        if text[i:i + 2] == "别扭":
            continue  # 别别扭扭
        tail0 = text[i + 1:i + 2]
        if tail0 in ("做", "搞", "弄", "画", "拍", "剪", "发", "去", "来", "写", "合", "拼", "接"):
            return True
        if tail0 in "了呀啊吧":
            return True
    return False
def _ref_by_position(text):
    """位置判据的引用/分析语境（v4.159.3 / P2）：元话语动词出现在生成短语【之前】且同一
        分句内 → 生成短语是被分析/讨论的对象，而非用户下达的指令，不该强制生成工具。
        解决裸「分析下生成视频」类残留风险（v4.159.2 仅靠前置词表 _REF_KW，覆盖不到无标记的
        分析句）。逐步退役 _WEAK_DISCUSS_KW 词表——强语境（元话语动词辖制生成短语）改由位置判据
        接管，_WEAK_DISCUSS_KW 仅保留给「无生成短语的纯讨论」兜底。
          例：『分析下生成视频』→ 分析下 在 生成视频 之前、同分句 → 引用语境 → None；
              『生成个视频，顺便分析下这个题材』(O4) → 生成短语在前、其后才出现分析，且含「，」
              → 不触发（用户确要视频）；
              『分析完数据再生成个视频』→ 含分隔符『再』→ 不触发（用户确要视频）。"""
    if not text:
        return False
    span = _gen_intent_span(text, _VIDEO_OBJ)
    if span == -1:
        span = _gen_intent_span(text, _IMAGE_OBJ)
    if span == -1:
        return False
    prefix = text[:span]
    # 前置若存在独立分句分隔符，生成短语已是独立指令，不算引用语境
    if any(sep in prefix for sep in _CLAUSE_SEP):
        return False
    for mv in _META_VERBS:
        if mv in prefix:
            return True
    # v4.159.4（P1）：反向位置判据——【疑问/追问类】元话语动词出现在生成短语【之后】且其后
    # （到句末）无 _CLAUSE_SEP 分隔符 → 生成短语是被追问的对象而非指令，同样拦截。
    # 修「生成视频是什么原因」类（『什么原因』在生成短语之后，旧版仅查前置漏过）。
    # 关键收窄：仅认疑问类（什么原因/咋回事/怎么回事/为什么/怎么看/怎么理解/原因在哪/为何），
    # 不认动作类（复盘/分析下/解释/诊断/评估…）——否则『生成视频后复盘一下效果』（R6 须返 video_gen：
    # 『后』说明生成是独立指令、复盘是后续动作，而非讨论生成本身）会被误杀。
    # 安全闸：suffix 含分句分隔符即说明生成短语是独立指令，不拦截
    # （『做个视频，顺便识别下素材』中『，』使后置分析句不回头压制生成指令）。
    _REF_INTERROG = ("什么原因", "咋回事", "怎么回事", "为什么", "怎么看", "怎么理解",
                     "原因在哪", "为何", "怎么")
    suffix = text[span:]
    if not any(sep in suffix for sep in _CLAUSE_SEP):
        for mv in _REF_INTERROG:
            if mv in suffix:
                return True
    return False
def _ref_existing_artifact(text):
    """引用/投诉【已生成产物】：『你生成的视频』『我刚做的图』『刚才生成好的海报』
        这类『生成动词 + 的（attributive）』紧贴媒体宾语，是【带『的』的相对从句】，
        指向【已存在的产物】而非下达新生成指令。

        必须前置一票否决：否则 _gen_intent 会把『你生成』里的『生成』动词 + 『视频』
        宾语（30 字窗内共现）判成新指令 → 路由 video_gen/image_gen → 向思考模型注入
        『必须生成视频』伪指令，而用户其实只是来投诉/质疑的（危害远大于漏报）。

        判定信号：【生成动词紧随『的』】（生+的 / 做+的 / 画+的 / 生成好+的 …），
        真实祈使指令（生成个/做个/画张）动词后接量词或宾语、不含『的』，不会命中，
        故不误伤真指令（已对 『生成个视频』『做个短视频』『画一张海报』『代码有bug帮我
        生成个视频』等全量回归用例验证）。极少数『帮我重新生成你之前生成的视频』这类
        含复述引用的真实指令会被安全侧漏判（模型仍可自行决定生成），按项目铁律
        『安全侧漏报可接受』处理。
        """
    if not text:
        return False
    objs = _VIDEO_OBJ + _IMAGE_OBJ
    for obj in objs:
        idx = text.find(obj)
        while idx != -1:
            pre = text[max(0, idx - 15):idx]
            # attributive 标记：生成动词（可选 completive 好/完/出来）直接接『的』，
            # 且『的』后紧跟媒体宾语 → 引用已生成产物，非指令。
            if re.search(r"(生成|做|画|拍|剪|整|弄|搞|来|产出|制作|设计)(出来|好|完)?的", pre):
                return True
            idx = text.find(obj, idx + len(obj))
    return False
# ---------- v4.168.1：浏览器路由的两个新判据 ----------
def _prog_fetch_intent(text):
    """是否「程序化抓取」意图（RSS / requests / API / 抓数据落盘…）。

        命中 → 不路由 browser_open。含义是：**把数据拿到手**是目标，
        通道（RSS/HTTP/渲染）由任务自己决定，本地判据不该替他选浏览器 ——
        尤其不该注入「必须调用 browser_open」这种伪指令。
        """
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in _PROG_FETCH_KW)
def _is_bare_url(text):
    """整句基本就是一个 URL —— 用户粘贴链接，默认意图是"打开看看"。

        判据：把 URL 抠掉、再抹掉空白与标点后，剩下的可读内容 ≤ 4 字。
        （「看看 https://x.com」剩「看看」也算 —— 反正"看看"本身也是打开动词。）
        """
    if not text or not _URL_RE.search(text):
        return False
    try:
        rest = _URL_RE.sub(" ", text)
        rest = re.sub(r"[\s，。；、,;:：!！?？~～—\-—()（）【】\[\]]+", "", rest)
        return len(rest) <= 4
    except Exception:
        return False
def _is_status_query(text):
    """是否「状态追问」（v4.243.0 审查报告 I-2 引入，替代 _STATUS_KW 裸词一票否决）。

    旧判据 `any(k in text for k in _STATUS_KW)` 把「如何/怎么样/状态/进度」当裸词
    一票否决，但它们在正常需求描述里大量出现（标题叫《如何用AI赚钱》、主题状态拉满、
    项目进度汇报）——命中即丢强制路由，真指令被误判成「问进度」。

    新判据按「追问语气」区分：
      · 完成态词（好了吗/到哪了/怎么样了…）本身即追问，直接算状态追问；
      · 裸状态词（进度/状态/如何/怎么样/咋样）必须句尾是疑问（吗/呢/？/？）才算追问。
    这样「进度如何？」「视频好了吗？」仍判追问，而「标题叫《如何用AI赚钱》」
    「状态拉满」「项目进度汇报」不再误判。
    """
    if not text:
        return False
    t = (text or "").strip()
    _done = ("好了吗", "完成了吗", "完成没", "到哪", "到哪了", "做了吗",
             "生成了没", "出来没", "出来了吗", "怎么样了", "进行到", "现在怎样",
             "完了吧", "生成完了", "跑完了", "完了没", "还好吗", "还在吗", "啥情况")
    if any(k in t for k in _done):
        return True
    _bare = ("进度", "状态", "如何", "怎么样", "咋样")
    if any(k in t for k in _bare) and t.endswith(("吗", "呢", "?", "？")):
        return True
    return False
def _is_ref_context(text):
    """是否「回指/质疑」语境（v4.244.0 审查报告 I-3）。

    强回指词（_REF_KW 运行时已并入 intent_guard._STRONG_REF_KW，含「你刚才说的」
    「什么时候让你」等组合词）一票否决；裸回指词（什么时候/让你/讨论/评价）必须与
    回指锚点（刚才/上面/之前/你说的…）共现才算回指——「帮我搜一下明天什么时候下雨」
    这类真指令不再见词就丢路由。
    """
    if not text:
        return False
    if any(k in text for k in _REF_KW):
        return True
    if any(k in text for k in _REF_BARE_KW):
        return any(a in text for a in _REF_ANCHOR)
    return False
# v4.254.0（A2 词表归一）：媒体生成写法的**共享子集**。
# route_judge.NEEDS_TOOL_EXTRA 从此以本表为单一真源（它 = 本表 + 自有非媒体词），
# 消灭「UI 升舱层认得『生一张/配张图』、Agent 意图层认不得」的两层漂移。
_MEDIA_PHRASE_SHARED = (
    "生一张", "出一张", "配张图", "配张封面", "张封面", "生成封面",
    "做几张", "截张图", "截个图", "截屏",
)

# v4.254.0（A2）：「生 X」→「生成 X」规范化（**判据入口**做一次，全链路同源）。
# 根因：route_judge.NEEDS_TOOL_EXTRA 早在 v4.111 就记下「生一张/出一张/配张图」是
# 真实漏网写法，但那份补词只喂 UI 升舱回放、从没回流 Agent 意图层 —— 于是
# 「用Agnes生视频」「生一张猫的图片」在 Agent 侧 force=None，活没干（实测探针
# 40 条语料里这两条都在不符清单里）。
# 刻意只收「生 + 媒体宾语/量词」的完整形式（闭集），**不收裸「生」** ——
# 「生产/生活/生动/生日/女生」里那个「生」不是生成动词，收了就是新的误触面。
_SHENG_NORM_PAIRS = (
    ("生视频", "生成视频"), ("生个视频", "生成个视频"), ("生条视频", "生成条视频"),
    ("生张视频", "生成张视频"), ("生一条视频", "生成一条视频"),
    ("生图", "生成图"), ("生个图", "生成个图"), ("生张图", "生成张图"),
    ("生一张", "生成一张"), ("生一条", "生成一条"),
)

# 「生」的**前一字**闭集豁免：这些字 + 生 会构成别的词（女生/学生/卫生/医生/
# 先生/谋生/营生/生产/天生），里面的「生」不是生成动词。
# 判据实测踩中：「女生图案设计」含「生图」→ 差点被补成「女生成图案设计」。
# 这是**闭集**（能跟「生」左缀成词且后面又能接媒体宾语的字就这几个，可穷举），
# 与 v4.159.4 反对的那种「辨别/识别/类别…补不完」的开集白名单不是一回事。
_SHENG_NOT_BEFORE = set("女学卫医先谋营产生天")


def _norm_verb(text):
    """v4.254.0（A2）：把口语省略写法补成规范动词，供判据族统一识别。

    纯函数、fail-open（非 str / 空 一律原样返回）。只做「生 X → 生成 X」这一种
    **补全**，绝不改写语义。放在 `_route_force_tool` 最前面，后面所有判据
    （否定 / 引用 / 生成意图 / 浏览器）都吃规范化后的文本 —— 顺带把
    「别生视频了」这类喊停也送进否定判据的射程。

    逐处替换（不是 `str.replace` 一把梭）：每处命中都要过前字闭集豁免，
    否则「女生图案」会被补成「女生成图案」。
    """
    if not isinstance(text, str) or not text:
        return text
    for a, b in _SHENG_NORM_PAIRS:
        if a not in text:
            continue
        out = []
        pos = 0
        while True:
            i = text.find(a, pos)
            if i < 0:
                out.append(text[pos:])
                break
            if i > 0 and text[i - 1] in _SHENG_NOT_BEFORE:
                out.append(text[pos:i + len(a)])     # 复合词里的「生」，原样保留
            else:
                out.append(text[pos:i])
                out.append(b)
            pos = i + len(a)
        text = "".join(out)
    return text


def _route_force_tool(text, prev_text=None):
    """v4.80：依据用户【当前】原话推断最该调用的工具，返回工具名或 None。
        仅用于 step1 强制指定 tool_choice：视频优先于图片（『图生视频』含『图』但属视频）；
        用户点名 Agnes 生成时默认路由 video_gen（本项目 Agnes 主力做视频）。

        关键修复（v4.80b）：
        - 状态类追问（『进度如何』『好了吗』『状态』等）一律返回 None，不强制任何工具，
          避免对已生成的任务反复重触（上一版用『最近 2 条拼接』导致追问被拼上上文的『视频』而强重触）。
        - 仅当【当前句只是裸续接动词】（如『用Agnes生成』『生成』）且自身无对象时，
          才用上一句上下文兜底路由（覆盖『用Agnes生成啊』这类短续接）。"""
    if not text:
        return None
    # v4.254.0（A2）：先补全省略写法（「生视频」→「生成视频」），后续所有判据
    # 一律吃规范化文本 —— 一处归一，全链路同源。
    text = _norm_verb(text)
    t = text.lower()
    # 0) 否定一票否决（v4.159.2）：取消/拒绝/喊停类表述，绝不路由任何工具，
    #    否则用户说「别生成视频了」反被强制生成（危害链：force 非空 → 思考模型注入伪指令）
    if _neg_hit(text):
        return None
    # 0.1) 评价句式前置短路（v4.161 方案A补全集；**v4.168.0 收归共享判据**）：
    #      用户用「生成动词 + 的/得 + 褒义评价」描述已完成/赞赏的结果
    #      （『这个封面做的漂亮』『图画的太好了』『文章写得很棒』），属动补结构
    #      而非祈使生成指令，前置拦截避免误触 image_gen/video_gen。
    #      v4.168.0 不再在本文件维护正则副本 —— UI / Agent / 模型调用三层统一
    #      取 intent_guard.is_praise（正则原件已迁到该模块，行为只增不减）。
    try:
        import intent_guard as _ig
        if _ig.is_praise(text):
            return None
    except Exception:
        # 共享模块不可用时退回最小正则，绝不因此对评价句放行
        if re.search(
            r"(做|干|写|拍|画|剪|整|弄|搞|设计|生成|制作|编辑)"
            r"(的|得)"
            r"(很|挺|真|超|太|非常|十分|特别|蛮|相当|还|更|极|够|贼|巨|老|最)?"
            r"(好|漂亮|不错|棒|美|清楚|干净|到位|赞|妙|厉害|完美|出色|好看|惊艳|绝|顶|"
            r"牛|强|神|一流|优秀)",
            text,
        ):
            return None
    # 0.5) 引用/质疑语境一票否决（v4.159.2）：用户在谈论/质疑/引用某生成动作而非
    #      下达指令时（「分析下生成视频这件事」「你刚才说的生成个视频，是BUG」
    #      「我什么时候让你生成视频了」），前置拦截，避免强制生成。
    if _is_ref_context(text):
        return None
    # 1) 状态追问优先拦截：进度/如何/好了吗/状态/完了吧 → 不强制工具，让模型正常汇报
    # v4.243.0（审查报告 I-2）：改用共现判据 _is_status_query，避免「如何/怎么样/状态/进度」
    # 裸词在正常需求描述里误伤真指令（标题叫《如何用AI赚钱》、主题状态拉满、项目进度汇报）。
    if _is_status_query(text):
        return None
    # 1.5) 位置判据的引用/分析语境（v4.159.3 / P2）：元话语动词辖制生成短语（如『分析下
    #      生成视频』）→ 生成短语是被分析的对象而非指令，前置拦截，避免强制生成。
    if _ref_by_position(text):
        return None
    # 1.6) 引用/投诉已生成产物一票否决（①-A 修复）：『你生成的视频有bug』『我做的图太丑』
    #      这类『生成动词+的』attributive 紧贴媒体宾语，指向已存在产物而非新指令，
    #      前置拦截避免被 _gen_intent 误路由到生成工具。详见 _ref_existing_artifact。
    if _ref_existing_artifact(text):
        return None
    # 2) 当前消息已含生成意图（v4.159 组合式判据） → 优先路由，不容「分析/解释」等
    #    讨论豁免词压制真指令（v4.159.1 修复）。「聊聊…做视频的行为」类句式因『视频的』
    #    修饰语跳过已不命中生成意图，仍由下方讨论豁免兜底。
    if _gen_intent(text, _VIDEO_OBJ):
        return "video_gen"
    if _gen_intent(text, _IMAGE_OBJ) or _phrase_hit(text):
        return "image_gen"
    # 3) 弱讨论/复盘/质疑豁免（v4.159.2：讨论/评价 已上提为 _REF_KW）：仅在【未命中
    #    生成意图】时兜底生效，用户在谈论/分析某事而非下达生成指令时，不强制任何工具。
    if any(k in text for k in _WEAK_DISCUSS_KW):
        return None
    # v4.168.1（BUG 修）：**程序化抓取一票否决**必须先于浏览器路由。
    #
    # 事故（2026-09-27，用户第 4 次遇到）：自动化任务「每日GitHub热榜」的正文里
    # 写着「T0 首选 requests 直连 https://github.com/trending?since=daily」——
    # 这里的 URL 是**数据源清单**，不是"打开这个网页"的指令。
    # 但旧判据只看"文本里含不含 URL"就 return "browser_open"，
    # 于是每天 09:00 的日常任务都被注入一条
    # 「【系统强制指令】当前任务必须通过调用工具 browser_open 完成」的伪用户指令
    # （用户看到的正是这条，且连着看了一周）。
    # 声明要抓取/走 RSS / 走 requests / 走 API / 落盘整理时，一律不路由浏览器。
    if _prog_fetch_intent(text):
        return None
    # v4.103 五次：浏览器路由（先于搜索——「打开xx网页搜一下」应进浏览器而非纯搜索）
    # v4.168.1：URL 分支不再"见 URL 就开浏览器"，必须同时是**打开意图**：
    #   ① 文本里有打开类动词（打开/访问/浏览/看看…），或
    #   ② 整句基本就是一个 URL（用户粘贴链接 = 默认"打开看看"）。
    # 否则只把 URL 当资料（如任务描述里的源清单）→ 不强制任何工具。
    if any(k in t for k in _BROWSER_URL_KW):
        if (any(v in text for v in _BROWSER_OPEN_VERB_KW)
                or _is_bare_url(text)):
            return "browser_open"
        return None
    if (any(k in text for k in _BROWSER_SITE_KW)
            and any(v in text for v in _BROWSER_OPEN_VERB_KW)):
        return "browser_open"
    if "agnes" in t:
        if _gen_intent(text, _IMAGE_OBJ) or _phrase_hit(text):
            return "image_gen"
        if _gen_intent(text, _VIDEO_OBJ):
            return "video_gen"
        # 无生成意图（如『Agnes 中文能力颠覆认知』纯夸赞/评测）→ 不强制工具，
        # 否则裸兜底 video_gen 白跑（v4.161 修复：agnes 分支不再无条件兜底 video_gen）。
        return None
    if "搜索" in text or "搜" in text:
        return "web_search"
    # 3) 当前句仅含裸续接动词、无对象 → 用上一句上下文兜底（仅限此场景）
    if prev_text and any(v in t for v in _BARE_VERB_KW):
        pt = prev_text.lower()
        if _gen_intent(pt, _VIDEO_OBJ):
            return "video_gen"
        if _gen_intent(pt, _IMAGE_OBJ) or _phrase_hit(pt):
            return "image_gen"
    return None
def _content_creation_only(text):
    """v4.102 fix10：判断是否为「纯内容创作」——用户要的是文本产出（文案/文章/内容/
        大纲/脚本/总结/回答等），不需要调用任何工具。这类任务强制 tool_choice=required 只会
        让模型（尤其 Agnes）「必须调工具但不知道该调啥」→ 返回 0 内容 + 0 工具（用户实证：
        『帮我写一段口播文案』→ content 空、界面『Agent 完成』但无文案输出）。
        区分依据：命中内容创作动词（写/生成/做/创建/编/改），且产出对象是文本级内容，
        且【不含】需要真实落盘/产物的工具词（文件/保存/导出/写入/打开/表格/SQL/图片/视频/
        截图/爬取/运行/代码/数据分析等）。
        """
    if not text:
        return False
    t = text.lower()
    # 纯内容创作动词（写/创作/编/拟/起草/润色等）——命中即大概率是文字产出
    has_create = any(k in text for k in _CREATE_VERB_KW)
    if not has_create:
        return False
    # 产物生成意图：同时命中「产物对象」（视频/图片/文件/表格/PDF/PPT/Word/Excel/截图）
    # 和「产物动词」（做/生成/制作/创建/出/画/剪辑/配音）→ 明确要产物，不豁免。
    # 注意：匹配对象词时不看它前面是否紧贴动词，只做「同句共现」判断——
    # 「写一段口播视频的文案」：视频是场景词，但句中无产物动词 → 豁免；
    # 「生成一段口播视频」：句中有「生成」+「视频」→ 不豁免。
    _obj = ("视频", "图片", "图片", "文件", "表格", "ppt", "pdf", "word", "excel", "截图",
            "csv", "海报", "插画", "配音")
    _verb = ("做", "生成", "制作", "创建", "出", "画", "剪辑", "配音")
    if any(o in text for o in _obj) and any(v in t for v in _verb):
        return False
    # 命中「真实落盘/产物」工具词（文件/保存/导出/搜索/运行等）→ 不是纯内容创作
    if any(k in text for k in _ARTIFACT_KW):
        return False
    if any(k in text for k in _REAL_TOOL_KW):
        return False
    # 未被以上排除，且命中文本产出对象提示 → 视为纯内容创作
    return True
def _detect_action_intent(messages):
    """从最后一条用户消息判断本次是否需要执行操作。"""
    text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, str):
                text = c
            break
    if not text:
        return False
    t = text.lower()
    # v4.168.0（BUG 修，第 2 层）：与 UI 自动路由共用同一条「非指令判据」。
    # 此前本函数只在下方零散拦否定/引用，评价句（「这个视频生成得真不错」）
    # 会一路走到 _gen_intent → _needs_action=True → tool_choice=required。
    # 现在前置短路，三处判据同源（详见 intent_guard.py）。
    try:
        import intent_guard as _ig
        if _ig.is_non_action_message(text):
            log.info("意图判定：判为非指令消息（%s），不置 _needs_action",
                     _ig.why_blocked(text))
            return False
    except Exception:
        pass
    is_topic = any(k in text for k in _TOPIC_KEYWORDS)
    if not is_topic:
        # 隐式：平台 + 方向词 且无"搜/查"字 → 视作选题
        has_platform = any(p in text for p in _TOPIC_PLATFORMS)
        has_dir = any(k in text for k in _TOPIC_DIR_KEYWORDS)
        if has_platform and has_dir and "搜" not in t and "查" not in t:
            is_topic = True
    if is_topic and not any(k in text for k in _ACTION_KEYWORDS):
        return False
    # v4.102 fix10：纯内容创作（写文案/写文章/生成一段内容）优先豁免——即便文中
    # 提到「视频/图片」作为场景（如「口播视频的文案」），只要产出是文字内容就不强制
    # 工具，否则弱模型「必须调工具但不知道该调啥」→ 空 content，无回复气泡。
    if _content_creation_only(text):
        return False
    # v4.159.2：否定/引用语境同样不该强制 action——「取消生成视频」「分析下生成视频这件事」
    # 不应被判定为需要执行操作。v4.159.3：补位置判据（分析下生成视频 无标记也拦截）。
    if _neg_hit(text) or _is_ref_context(text) or _ref_by_position(text):
        return False
    # v4.102 fix10：明确要求生成图片/视频（产物是多媒体文件）→ 必须走工具
    if _gen_intent(text, _IMAGE_OBJ) or _gen_intent(text, _VIDEO_OBJ) or _phrase_hit(text):
        return True
    return any(k in text for k in _ACTION_KEYWORDS)
