// popup.js — 弹窗逻辑：保存 token、触发抓取、显示状态
function $(id) { return document.getElementById(id); }

// 载入已存的 token
chrome.storage.local.get(["bridge_token"], function (o) {
  if (o.bridge_token) $("token").value = o.bridge_token;
});

$("token").addEventListener("change", function () {
  chrome.storage.local.set({ bridge_token: $("token").value.trim() });
});

function setStatus(text, kind) {
  var el = $("status");
  el.textContent = text;
  el.className = "status " + (kind || "");
}

function sendCapture(note) {
  var tok = $("token").value.trim();
  if (!tok) {
    setStatus("请先填配对码", "err");
    return;
  }
  chrome.storage.local.set({ bridge_token: tok });
  setStatus("抓取中…");
  chrome.runtime.sendMessage({ type: "capture", note: note || "" }, function (resp) {
    if (chrome.runtime.lastError) {
      setStatus("出错：" + chrome.runtime.lastError.message, "err");
      return;
    }
    if (resp && resp.ok) {
      setStatus("✅ 已发送 " + resp.chars + " 字到Agent", "ok");
    } else {
      setStatus("⚠️ " + (resp && resp.error || "失败"), "err");
    }
  });
}

$("capture").addEventListener("click", function () {
  sendCapture($("note").value.trim());
});
$("captureSel").addEventListener("click", function () {
  sendCapture($("note").value.trim());
});
