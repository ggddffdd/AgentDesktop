// background.js — service worker：协调抓取并推送到本地桥接服务（L1 增强版）
const BRIDGE_URL = "http://127.0.0.1:9100/page";
const PAIR_URL = "http://127.0.0.1:9100/pair";

function getToken() {
  return new Promise(function (resolve) {
    chrome.storage.local.get(["bridge_token"], function (o) {
      resolve(o.bridge_token || "");
    });
  });
}

async function captureActiveTab(note, opts) {
  opts = opts || {};
  var mode = opts.mode || "article";
  var autosend = !!opts.autosend;
  var token = await getToken();
  if (!token) {
    return { ok: false, error: "未配置 token，请在弹窗里点「一键配对」或粘贴小臭显示的配对码" };
  }
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs || !tabs.length) {
    return { ok: false, error: "没有活动标签页" };
  }
  var tab = tabs[0];
  var resp;
  try {
    resp = await chrome.tabs.sendMessage(tab.id, { type: "extract", mode: mode });
  } catch (e) {
    return { ok: false, error: "无法注入页面（可能是浏览器内置页/新标签页）：" + e };
  }
  if (!resp || !resp.ok) {
    return { ok: false, error: (resp && resp.error) || "页面提取失败" };
  }
  if (!resp.text) {
    return { ok: false, error: "页面没有可提取的正文，试试先选中一段文字" };
  }

  var payload = {
    title: resp.title,
    url: resp.url,
    text: resp.text,
    selection: resp.bySelection ? resp.text : "",
    markdown: resp.markdown || resp.text,
    note: note || "",
    mode: mode,
    autosend: autosend,
    meta: resp.meta || {},
    ts: Date.now()
  };

  try {
    var r = await fetch(BRIDGE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Bridge-Token": token
      },
      body: JSON.stringify(payload)
    });
    if (r.status === 401) {
      return { ok: false, error: "token 不匹配，请重新点「一键配对」" };
    }
    if (!r.ok) {
      return { ok: false, error: "桥接服务返回 " + r.status };
    }
    // #756：解析桥接 JSON 回执，反映真实投递结果（而非仅 HTTP 200）
    var delivered = true, detail = "", rstatus = "ok";
    try {
      var out = await r.json();
      delivered = !!out.delivered;
      detail = out.detail || out.note || "";
      rstatus = out.status || "ok";
    } catch (e) { /* 兜底：200 即视为已接收 */ }
    return {
      ok: true,
      chars: resp.text.length,
      autosend: autosend,
      delivered: delivered,
      status: rstatus,
      detail: detail
    };
  } catch (e) {
    return {
      ok: false,
      error: "连不上小臭（确认小臭已打开，且桥接服务在运行）：" + e
    };
  }
}

// ---- L1.5 右键菜单 ----
function buildContextMenus() {
  try {
    chrome.contextMenus.removeAll(function () {
      chrome.contextMenus.create({
        id: "xc_capture_article",
        title: "小臭：抓取当前页正文",
        contexts: ["page"]
      });
      chrome.contextMenus.create({
        id: "xc_capture_selection",
        title: "小臭：抓取选中文字",
        contexts: ["selection"]
      });
    });
  } catch (e) { /* 忽略 */ }
}

if (chrome.contextMenus) {
  buildContextMenus();
  chrome.contextMenus.onClicked.addListener(function (info, tab) {
    var mode = (info.menuItemId === "xc_capture_selection") ? "selection" : "article";
    captureActiveTab("", { mode: mode, autosend: true });
  });
}

// ---- L1.6 全局快捷键（manifest 中定义 Ctrl+Shift+S / Ctrl+Shift+X）----
if (chrome.commands) {
  chrome.commands.onCommand.addListener(function (command) {
    var mode = (command === "capture-selection") ? "selection" : "article";
    captureActiveTab("", { mode: mode, autosend: true });
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (msg && msg.type === "capture") {
    captureActiveTab(msg.note || "", {
      mode: msg.mode || "article",
      autosend: !!msg.autosend
    }).then(sendResponse);
    return true;
  }
});
