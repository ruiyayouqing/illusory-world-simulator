// 太虚幻境 v6 — 核心: API/状态/WebSocket

const API_BASE = '';

async function initAccessToken() {
  try {
    var resp = await fetch(API_BASE + '/api/access-token');
    if (resp.ok) {
      var d = await resp.json();
      if (d.access_token) {
        window.ACCESS_TOKEN = d.access_token;
      }
    }
  } catch(e) {
    console.warn('Failed to get access token', e);
  }
}

document.addEventListener('DOMContentLoaded', initAccessToken);

// [v10] 统一的 HTML 转义工具（所有文件共用，避免多套实现不一致 — L10c）
function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// [v10] 属性值转义：用于嵌入到 onclick="fn('...')" 等上下文
function escAttr(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'");
}

async function api(method, path, body, timeout = 300000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  
  const opts = { method, headers: {'Content-Type': 'application/json'}, signal: controller.signal };
  if (window.ACCESS_TOKEN) {
    opts.headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
  }
  if (body && method !== 'GET') opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(API_BASE + path, opts);
    clearTimeout(timeoutId);
    if (!resp.ok) {
      var text = await resp.text().catch(function() { return ''; });
      var detail = text.substring(0, 200);
      return { error: '服务器错误 (' + resp.status + ')' + (detail ? ': ' + detail : '') };
    }
    var ct = resp.headers.get('content-type') || '';
    if (ct.indexOf('json') >= 0) return resp.json();
    var txt = await resp.text().catch(function() { return ''; });
    try { return JSON.parse(txt); } catch(e) { return { error: '响应格式错误', raw: txt.substring(0, 200) }; }
  } catch(e) {
    clearTimeout(timeoutId);
    if (e.name === 'AbortError') {
      return { error: '请求超时，请检查网络连接或稍后重试' };
    }
    return { error: '请求失败: ' + e.message };
  }
}

// ===== WebSocket 流式连接 =====
var ws = null;
var wsClientId = 'client_' + Math.random().toString(36).slice(2, 10);
var wsOnToken = null;   // function(token) — 收到流式 token 时回调
var wsOnResult = null;  // function(result, state) — 收到完整结果时回调
var wsOnThinking = null; // function() — AI 开始思考时回调
var wsOnStreamEnd = null; // function() — 流结束时回调
// [v10] 有限重连 + 指数退避，避免无限重连（M5c）
var wsReconnectAttempts = 0;
var wsMaxReconnect = 10;
var wsIntentionalClose = false;

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsUrl = protocol + '//' + location.host + '/ws/' + wsClientId;
  try {
    ws = new WebSocket(wsUrl);
    ws.onopen = function() {
      console.log('WS connected');
      wsReconnectAttempts = 0;
    };
    ws.onmessage = function(event) {
      try {
        var msg = JSON.parse(event.data);
        if (msg.type === 'stream_token' && wsOnToken) {
          wsOnToken(msg.token);
        } else if (msg.type === 'stream_end' && wsOnStreamEnd) {
          wsOnStreamEnd();
        } else if (msg.type === 'thinking' && wsOnThinking) {
          wsOnThinking();
        } else if (msg.type === 'result' && wsOnResult) {
          wsOnResult(msg.result, msg.state);
        } else if (msg.type === 'pong') {
          // keepalive
        }
      } catch(e) { console.error('WS onmessage error', e); }
    };
    ws.onclose = function() {
      console.log('WS disconnected');
      // [v10] 主动关闭不再重连；超过最大重试次数也不再重连
      if (wsIntentionalClose) return;
      if (wsReconnectAttempts < wsMaxReconnect) {
        wsReconnectAttempts++;
        var delay = Math.min(3000 * Math.pow(1.5, wsReconnectAttempts - 1), 30000);
        setTimeout(connectWS, delay);
      }
    };
    ws.onerror = function(e) { console.error('WS error', e); };
  } catch(e) { console.error('WS connect error', e); }
}

function sendWS(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

// ===================== 2. 页面管理 =====================

async function getConfig() {
  // [Bug] 使用 /api/config/raw 获取未脱敏的API Key，避免加载游戏时传入脱敏Key导致LLM调用失败
  const d = await api('GET', '/api/config/raw');
  const c = d || {};
  return {
    api_key: c.llm?.api_key || '',
    base_url: c.llm?.base_url || 'https://token-plan-cn.xiaomimimo.com/v1',
    model_name: c.llm?.model_name || 'mimo-v2.5'
  };
}

function toast(msg, type) {
  type = type || 'info';
  var container = $('toast');
  var el = document.createElement('div');
  el.className = 'toast-msg ' + type;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(function() {
    el.style.opacity = '0';
    el.style.transition = 'opacity .3s';
    setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }, 4000);
}