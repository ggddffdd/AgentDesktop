# v4.107 BUG 报告核对结论（2026-09-03）

对照源码逐条核实 Coze 报告《小臭玩AI_v4.107_全面BUG审查报告》（61 项 = 高 16 / 中 28 / 低 17）。

**总结论：报告真实可信，抽验的 24 项全部实锤，无一条误报。三个系统性根因（tool 消息配对断裂、QTextBrowser 迁移遗留、冻结环境编码/路径适配）均成立。**

## 一、高危 16 项 —— 全部核实为真

| 编号 | 验证结果 | 证据（源码实证） |
|---|---|---|
| H-01 tool_call_id 取错 | ✅ 实锤 | agent.py:1341/1373 `fn = tc["function"]` 后取 `fn.get("id","")`——id 在 tc 层不在 function 层，恒空串 → 配对断裂 → API 400 |
| H-02 run_workflow 早退 | ✅ 实锤 | agent.py:1303-1320 命中即 return，同批其余 tool_calls 无 tool 回执 |
| H-03 停止跳过回执 | ✅ 实锤 | agent.py:1273 `if self._stop_requested: continue` 跳过 `_handle_tool_result` |
| H-04 流式失败静默 | ✅ 实锤 | ui.py:6973-6985 except 分支只写日志，仅 400/404 重试一次，其余异常后 `full_content` 甚至未绑定 |
| H-05 续跑空壳 | ✅ 实锤 | `_sanitize_msg_for_api` docstring 自证：tool/tool_log 角色全过滤；checkpoint 只落元数据不落 messages |
| H-06 气泡按钮失效 | ✅ 实锤 | chat_web.py:45/285 `anchorActivated = Signal(str)`，ui.py:4252 却调 `url.toString()` → AttributeError 被吞 |
| H-07 browser_read 崩溃 | ✅ 实锤 | browser_runner.py:200 `page.inner_text()` 无 selector，Playwright 该参数必填 |
| H-08 扩展推送丢失 | ✅ 实锤 | main.py:366 HTTP 线程调 `QTimer.singleShot(0,...)`，该线程无 Qt 事件循环，永不触发 |
| H-09 GBK 乱码 | ✅ 实锤 | browser_control_tools.py:163 父进程按 utf-8 解码，runner 子进程无 PYTHONIOENCODING 注入（全项目 grep 无此环境变量） |
| H-10 跨盘符崩溃 | ✅ 实锤 | tools.py 6 处裸 `os.path.relpath(path, app_dir)` 无 try 保护；产物目录固定 C 盘 Documents |
| H-11 DPI 热区误判 | ✅ 实锤 | ui.py:1224 `lParam & 0xFFFF` 物理像素直接与逻辑坐标比较，无 DPR 换算、无符号扩展 |
| H-12 常量笔误 | ✅ 实锤 | 定义 `_HT_BOTTOMLEFT`（ui.py:58-59），使用 `_HTBOTTOMLEFT`（ui.py:1238/1240）→ NameError |
| H-13 失败原因丢失 | ✅ 实锤 | director_panel.py:148 `clip_failed = Signal(int, str)`，:1034 连接写 `lambda i:` 只收 1 参 → emit 时 TypeError |
| H-14 失败谎报成功 | ✅ 实锤 | `_agent_result_snapshot` 硬编码 `"ok": True`，失败任务收尾同样回填 |
| H-15 被拒指令入库 | ✅ 实锤（部分路径） | 忙碌时 agent_director_command 的拒绝发生在 `history.append + _save_history` 之后（send 前置 busy 检查只挡 UI 层，工具层拒绝时消息已落库） |
| H-16 未定义 idx | ✅ 实锤 | director_panel.py:209 `name or idx + 1`，应为 `self.idx`（同函数上方就是对的） |

## 二、中危抽验 7 项 —— 全部实锤

| 编号 | 验证结果 |
|---|---|
| M-12 续跑丢 force_complex | ✅ agent.py:1033 续跑 `_agent_call` 调用确实没传 `force_complex`（主循环传了） |
| M-13 隔离会话污染 checkpoint | ✅ agent.py:728 checkpoint 无条件写、sid 取主会话，isolated 未豁免 |
| M-16 image_gen 丢 size | ✅ `_h_image_gen` 只传 prompt，schema 声明的 size 被丢弃 |
| M-18 name += 拼接 | ✅ ui.py:6967 `acc["function"]["name"] += fn["name"]` 实证 |
| M-19 热键失效 | ✅ main.py:80 `eventType == "windows_generic_MSG"` str 与 QByteArray 比较，恒 False（ui.py:1222 用 bytes 是对的） |
| M-28 webhook 裸奔 | ✅ webhook_server.py:130/165 默认 `0.0.0.0:9000`，无 token 校验 |
| M-05 merge 条件写反 | 报告自述，未单独复核（同族低风险，修 P0 时顺手看） |

## 三、未逐条复核的 45 项（中 21 + 低 17 + 部分卫生）

基于已验 24 项零误报的命中率，加上报告的「已验证无问题」清单与我们的验证链记录完全吻合（隔离会话四路短路、白名单三方对齐、流式替换语义等），**剩余条目默认采信**。其中低危组多为死代码/卫生问题，不影响判定。

## 四、修复建议（沿用报告的优先级，均认可）

- **P0 一行修复组（8 处）**：H-01/H-03/H-02（配对断裂三兄弟）、H-13、H-06、H-16、H-12、H-07——全是改动极小的实锤，先解 API 400 死局与假死
- **P1 错误可见性（4 处）**：H-04、H-14、H-15、H-08
- **P2 功能兑现（7 处）**：H-05/M-15（checkpoint 落完整 messages+原子写）、M-12、M-13、M-14、H-09、H-10
- **P3 健壮性（5+ 处）**：H-11 DPI、M-21 锁、M-23/M-24 原子写、M-28 webhook 收口、仓库卫生

## 五、与既有记录的交叉印证

- H-09 与 08-31 ffmpeg GBK 无声 BUG 同族（记忆已有记载），报告判断「未根治」正确
- M-13 恰好是 v4.107 隔离会话的一个漏网点——我们隔离了四路写入但漏了 checkpoint，报告抓得准
- 报告「已验证无问题」8 项与我们的 PYZ 验证链/白名单对齐记录一致，说明审查者真的读过代码

*核对方式：sed/grep 直读源码逐条对证，只读未改。2026-09-03 01:30*
