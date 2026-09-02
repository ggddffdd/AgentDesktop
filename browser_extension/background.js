// background.js — service worker：协调抓取并推送到本地桥接服务
const BRIDGE_URL = "http://127.0.0.1:9100/page";

function getToken() {
  return new Promise(function (resolve) {
    chrome.storage.local.get(["bridge_token"], function (o) {
      resolve(o.bridge_token || "");
    });
  });
}

async function captureActiveTab(note) {
  var token = await getToken();
  if (!token) {
    return { ok: false, error: "未配置 token，请在弹窗里粘贴小臭显示的配对码" };
  }
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs || !tabs.length) {
    return { ok: false, error: "没有活动标签页" };
  }
  var tab = tabs[0];
  var resp;
  try {
    resp = await chrome.tabs.sendMessage(tab.id, { type: "extract" });
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
    note: note || "",
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
      return { ok: false, error: "token 不匹配，请检查弹窗里的配对码" };
    }
    if (!r.ok) {
      return { ok: false, error: "桥接服务返回 " + r.status };
    }
    return { ok: true, chars: resp.text.length };
  } catch (e) {
    return {
      ok: false,
      error: "连不上小臭（确认小臭已打开，且桥接服务在运行）：" + e
    };
  }
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (msg && msg.type === "capture") {
    captureActiveTab(msg.note || "").then(sendResponse);
    return true;
  }
});
