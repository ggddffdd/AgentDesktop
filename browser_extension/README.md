# Agent抓网页 · 浏览器扩展（Edge / Chrome）

把看网页时的一键抓取能力接进「AgentDesktop」桌面端，抓下来的正文/选中文字会进Agent输入框，让 AI 帮你总结、提取、分析。

## 安装（开发者模式，永久有效，无需商店）

1. 打开扩展管理页：
   - **Edge**：地址栏输入 `edge://extensions` 回车
   - **Chrome**：地址栏输入 `chrome://extensions` 回车
2. 右上角打开「开发人员模式」开关。
3. 点「加载解压缩的扩展程序」，选择**本文件夹**（`browser_extension`）。
4. 看到「Agent抓网页」即成功；可点「固定」把图标钉到工具栏。

## 配对（只需一次）

5. 打开AgentDesktop → 设置 → 浏览器扩展，复制里面的「配对码」。
6. 点工具栏的Agent图标，把配对码粘贴到「配对码」框里（自动保存）。

## 使用

- 在任意网页点工具栏图标 → 「抓取当前页正文」：把正文发到Agent。
- 先在网页里选中一段文字 → 点「抓取选中文字」：只发选中部分。
- 可选填「附言」作为指令，如「提取里面的表格」「用一句话总结」。
- 抓取后内容出现在Agent输入框，按 Enter 即可让 AI 处理。

## 说明

- 桥接服务只监听本机 `127.0.0.1:9100`，外部网页无法访问。
- 所有抓取请求都必须带配对码（token），恶意网页无法冒充。
- 抓正文用轻量正文提取（类 Readability），复杂页面以「选中文字」更准。

## 进阶：让Agent直接操作你的真实浏览器（CDP 接管）

扩展只负责「读取/抓取」。如果你想让Agent**点击、填表、操作**你正在用的浏览器（带登录态），用 CDP：

1. 关掉所有 Edge/Chrome 窗口，用调试端口重新启动（管理员 PowerShell）：
   - Edge：`& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222`
   - Chrome：把路径换成 `chrome.exe` 同理。
2. 打开 `http://127.0.0.1:9222/json/version`，复制里面的 `webSocketDebuggerUrl`
   （形如 `ws://127.0.0.1:9222/devtools/browser/xxxx`）。
3. 在Agent「设置 → 浏览器扩展」里把这段地址填进 `config.json` 的 `browser_cdp` 字段
   （值为 `http://127.0.0.1:9222` 即可，Agent会自动取 ws 地址）。
4. 之后对话里说「打开 xx 网页并点登录」「去 xx 填表」等，Agent就接管你真实浏览器操作。

> 注意：调试端口下的浏览器等同于你本人操作，涉及账号密码请谨慎授权。不用时关掉调试端口启动的浏览器即可断开接管。

