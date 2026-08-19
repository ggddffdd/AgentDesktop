# Agent玩 AI（Agent 操作平台）

一个常驻 Windows 桌面的本地 Agent 操作系统，类似豆包桌面端的极简版，但功能更强：
**系统托盘常驻 + 全局快捷键一键呼出 + 多步推理 Agent 循环 + 24+ 内置工具 + MCP 可扩展**。

完全免费，技术栈：Python + PySide6 + OpenAI 兼容 API + Chroma 向量库 + pyautogui/pywinauto。代码可改可练手。

**v3 新增能力：**
- 🔍 **联网搜索增强**：默认百度 + 搜狗 + 维基三后端（免费、无需 key，大陆直连可用），把实时资料喂给模型，回答附【来源】链接。搜不到自动降级为纯模型回答，不卡住。
- ⌨️ **流式输出**：逐字渲染（打字机效果），不用干等。
- 🗂️ **多会话标签**：顶部标签栏可新建 / 切换 / 关闭多个对话，互不干扰。
- 💾 **历史本地保存**：所有对话存 `sessions.json`，关掉再开还在。

**v3.1 / v3.2 增强：**
- 🔗 **来源链接可点击**：模型回答和搜索结果里的 `http(s)` 链接会自动变蓝可点，点一下用系统浏览器打开。
- 🔄 **模型切换下拉**：界面顶部「模型」下拉，内置 DeepSeek / 硅基流动 / 智谱 GLM / 腾讯混元 / **你的免费网关 free-api-gw**，切换即改 `base_url`+`model` 并保存。
- 📤 **对话导出**：一键把当前会话导出为 `.md` 或 `.txt` 存本地。
- 📦 **打包 exe**：配好 `build_exe.bat`，双击即出免装 Python 的独立 exe。

**v4 / v4.5 升级（Agent 操作平台）：**
- 🧠 **Agent 循环引擎**：多步推理（MAX_AGENT_STEPS=8），LLM 自主决定调用工具链
- 🖱️ **系统操控**（14 个工具）：截屏、鼠标移动/点击/滚轮、键盘输入/组合键、剪贴板读写、窗口管理、进程管理
- 📦 **软件操控**（10 个工具）：应用启动/强杀/聚焦、窗口状态控制、控件树枚举、UI 自动化（pywinauto）
- 📚 **RAG 知识库**：Chroma 本地向量数据库，语义检索
- 🔌 **MCP 协议**：动态加载外部工具，OpenAI function calling 格式统一路由
- 🔀 **串行/并发执行**：危险工具逐个确认执行，安全工具最多 5 线程并发
- 🛡️ **行为规范层**：agent_rules.md 定义 8 条工作方法原则

---

## 一、环境准备
- Windows 11
- Python 3.10 或以上（装好并把 python 加入 PATH）

## 二、安装依赖
在项目目录打开终端，执行：
```bash
pip install -r requirements.txt
```

## 三、填 API Key
首次运行会在目录里生成 `config.json`。打开它，把 `api_key` 填上：
```json
{
  "api_key": "你的DeepSeek密钥",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "hotkey": "ctrl+shift+d",
  "system_prompt": "你是一个简洁、有用的中文助手，回答直接说重点。",
  "max_history": 20,
  "search_enabled": true,
  "search_provider": "auto",
  "search_top_k": 5
}
```
> 去 https://platform.deepseek.com 注册，在「API Keys」页面创建密钥。
> 新用户有免费额度，先用着；额度用完才扣费，注意控制用量。

## 四、运行
两种方式任选：
- 双击 `run.bat`
- 终端执行 `python main.py`

## 五、使用
- **双击托盘图标** 或按 **Ctrl+Shift+D** 呼出 / 隐藏窗口
- 输入框里打字，回车发送（Shift+Enter 换行）
- **顶部标签栏**：点「＋」新建会话，点标签切换，点标签上的「×」关闭（至少保留 1 个）
- **模型下拉**：选不同模型预设即时切换（含你的免费网关）；想增删模型改 `config.json` 的 `model_profiles`
- **导出对话**：点「导出对话」按钮，把当前会话存成 `.md`/`.txt`
- **联网搜索开关**：勾选后每次提问先联网检索再回答；取消勾选则纯模型回答
- **来源链接**：回答里出现的网址变蓝可点，直接浏览器打开
- 关窗口不退出，缩回托盘；托盘右键 → 退出 才真正关掉
- 对话自动存 `sessions.json`，下次打开自动恢复

## 六、联网搜索说明（重要）
- **免费、零配置**：内置五个后端，都不需要任何 API Key：
  - **百度 / 搜狗**：国内源，大陆直连可用，结果偏中文实时网页（天气/新闻/最新政策等）。百度/搜狗页面偶有反爬，解析失败会换下一个源。
  - **Bing**：国内通常可访问，HTML 结构相对稳，作为百度的快速替补。
  - **维基百科**：百科类，英文站访问稳定，中文站偶尔受限。
  - **DuckDuckGo**：海外源，大陆常被墙，仅作为兜底。
- **`search_provider` 取值**：
  - `"auto"`（默认）：依次尝试 **百度 → Bing → 搜狗 → 维基**，某个失败自动换下一个；都失败则降级为纯模型回答，并在状态栏提示"⚠️ 搜索无结果，使用模型知识回答"。
  - `"baidu"` / `"bing"` / `"sogou"`：只用指定单一源。
  - `"baidu_sogou"`：只用百度+搜狗（纯国内链路）。
  - `"wikipedia"` / `"duckduckgo"`：指定单一海外源（大陆慎选）。
- **如何确认通没通**：连网随便问"今天天气""某新闻"，看回答末尾是否有【来源：】链接。有链接 = 搜索生效；没有 = 当前后端全部失败已降级（不影响正常聊天）。
- **搜索结果仅作参考**：模型会被要求标注引用来源，但联网资料可能过时或不准确，关键事实请自行核对。

## 七、想零成本？换免费模型
你定死只用免费资源，可以这样改 `config.json`：
- **硅基流动**（注册送额度，多模型）：
  ```json
  "base_url": "https://api.siliconflow.cn/v1",
  "model": "deepseek-ai/DeepSeek-V3",
  "api_key": "硅基流动的key"
  ```
- **智谱 / 混元** 等同样兼容 OpenAI 格式，改 `base_url` + `api_key` 即可，代码不用动。
  > 注意：换模型后联网搜索功能不变（搜索是独立做的），但流式输出需该接口支持 `stream`，主流兼容 OpenAI 格式的都支持。

### 模型切换下拉 + 接入你的免费网关（free-api-gw）
界面顶部有「模型」下拉框，切换即生效并写入 `config.json`。预设写在 `config.json` 的 `model_profiles` 里：
```json
"model_profiles": {
  "DeepSeek 官方":       {"base_url": "https://api.deepseek.com",                  "model": "deepseek-chat",            "api_key": ""},
  "硅基流动":           {"base_url": "https://api.siliconflow.cn/v1",             "model": "deepseek-ai/DeepSeek-V3",  "api_key": ""},
  "智谱 GLM":           {"base_url": "https://open.bigmodel.cn/api/ai/v1",        "model": "glm-4-flash",              "api_key": ""},
  "腾讯混元":           {"base_url": "https://api.hunyuan.cloud.tencent.com/v1",  "model": "hunyuan-lite",             "api_key": ""},
  "免费网关 free-api-gw":{"base_url": "http://127.0.0.1:8000/v1",                  "model": "zhipu",                    "api_key": ""}
}
```
- 下拉里的 **「免费网关 free-api-gw」** 就是接你做的统一网关：`base_url` 默认 `http://127.0.0.1:8000/v1`（本地跑网关就是这个），**前提是先把它跑起来（监听 8000 端口）**。网关已提供 OpenAI 兼容的 `/v1/chat/completions` 入口，桌面端直接当标准 OpenAI 客户端用即可。`model` 填网关支持的模型标识（`zhipu` / `hunyuan` / `siliconflow` / `modelscope` / `agnes`，不填则按网关策略自动选），`api_key` 留空即可（网关不校验 key）。
- `api_key` 留空 = 沿用 `config.json` 顶部的 `api_key`；填了就用该模型的 key。
- 想加自己的模型，直接往 `model_profiles` 里加一项即可，下拉自动出现。

## 八、打包成 exe（免装 Python 也能跑）
项目里已备好一键脚本 `build_exe.bat`，**在装了 Python 3.10+ 且能联网的电脑上**双击即可：
```bat
build_exe.bat
```
它会自动 `pip install` 依赖 + 用 PyInstaller 打出单文件窗口版，生成 `dist/DeepSeekDesktop.exe`。
- 把 `DeepSeekDesktop.exe` 单独拿出来放任意目录，首次运行会在它旁边自动生成 `config.json`，填好 key 就能用。
- 配置文件（`config.json` / `sessions.json` / `debug.log`）都会写在 exe 同目录，方便携带。
- 手动打包命令（等价）：`pyinstaller --onefile --windowed --name DeepSeekDesktop main.py`
- ⚠️ 本工作台开发沙箱无法联网也缺 PySide6，所以 exe 需在你本机跑 `build_exe.bat` 生成，这里只交付了打包配置。

## 九、安全提醒
`config.json` 里含你的 API Key，**别分享、别上传公网**。
`sessions.json` 是本地聊天记录，含你跟模型的对话内容，也别随便发给别人。

## 十、v3 配置项说明（config.json）
| 字段 | 默认值 | 说明 |
|------|--------|------|
| `api_key` | 空 | DeepSeek / 兼容平台密钥 |
| `base_url` | `https://api.deepseek.com` | API 地址，换模型改这里 |
| `model` | `deepseek-chat` | 模型名 |
| `hotkey` | `ctrl+shift+d` | 全局呼出/隐藏快捷键 |
| `system_prompt` | 简洁中文助手 | 系统提示词 |
| `max_history` | `20` | 发给模型的上下文保留条数（控 token） |
| `search_enabled` | `true` | 是否开启联网搜索 |
| `search_provider` | `auto` | `auto`=百度→搜狗→维基 / `baidu` / `sogou` / `baidu_sogou` / `duckduckgo` / `wikipedia` |
| `search_top_k` | `5` | 每次搜索取前几条结果喂给模型 |
