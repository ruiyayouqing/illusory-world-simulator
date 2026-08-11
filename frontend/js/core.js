// 太虚幻境 v6 — 核心: API/状态/WebSocket

const API_BASE = '';

// [公网访问] 页面内令牌输入框（兼容 iOS Safari — 其不支持 window.prompt）
function showTokenModal() {
  return new Promise(function(resolve) {
    var ov = document.getElementById('txhjTokenOverlay');
    if (ov) { ov.style.display = 'flex'; return; }
    ov = document.createElement('div');
    ov.id = 'txhjTokenOverlay';
    ov.style.cssText = 'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6)';
    var box = document.createElement('div');
    box.style.cssText = 'background:#1e2233;border:1px solid #445;border-radius:12px;padding:24px;width:84%;max-width:360px;color:#eee;font-family:system-ui,sans-serif;text-align:center';
    var t = document.createElement('div');
    t.textContent = '请输入访问令牌';
    t.style.cssText = 'font-size:17px;font-weight:bold;margin-bottom:8px';
    var d = document.createElement('div');
    d.textContent = '服务器部署时已设置，输入一次后自动保存';
    d.style.cssText = 'font-size:13px;color:#999;margin-bottom:16px';
    var inp = document.createElement('input');
    inp.type = 'password';
    inp.placeholder = 'Access Token';
    inp.style.cssText = 'width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #556;background:#14161f;color:#fff;font-size:15px;margin-bottom:16px';
    var btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:10px;justify-content:center';
    var ok = document.createElement('button');
    ok.textContent = '确定';
    ok.style.cssText = 'flex:1;padding:10px;border:none;border-radius:8px;background:#4a6cf7;color:#fff;font-size:15px;cursor:pointer';
    var cancel = document.createElement('button');
    cancel.textContent = '取消';
    cancel.style.cssText = 'flex:1;padding:10px;border:none;border-radius:8px;background:#333a4d;color:#aaa;font-size:15px;cursor:pointer';
    function done(val) {
      ov.style.display = 'none';
      if (ov.parentNode) ov.parentNode.removeChild(ov);
      resolve(val);
    }
    ok.onclick = function() { done(inp.value.trim()); };
    cancel.onclick = function() { done(''); };
    inp.addEventListener('keydown', function(e) { if (e.key === 'Enter') done(inp.value.trim()); });
    btnRow.appendChild(ok); btnRow.appendChild(cancel);
    box.appendChild(t); box.appendChild(d); box.appendChild(inp); box.appendChild(btnRow);
    ov.appendChild(box);
    document.body.appendChild(ov);
    setTimeout(function(){ inp.focus(); }, 50);
  });
}

// [v12] 确保 Token 已准备好再继续
async function ensureToken() {
  if (window.ACCESS_TOKEN) return;
  try {
    var resp = await fetch(API_BASE + '/api/access-token');
    if (resp.ok) {
      var d = await resp.json();
      if (d.access_token) {
        window.ACCESS_TOKEN = d.access_token;
        return;
      }
    }
  } catch(e) {
    console.warn('Failed to get access token', e);
  }
  // [公网访问] 本地接口不可达（远程访问）时：从 localStorage 读取，或让用户输入一次
  var saved = null;
  try { saved = localStorage.getItem('txhj_access_token'); } catch(e) {}
  if (saved) {
    window.ACCESS_TOKEN = saved;
    return;
  }
  var input = await showTokenModal();
  if (input) {
    window.ACCESS_TOKEN = input;
    try { localStorage.setItem('txhj_access_token', window.ACCESS_TOKEN); } catch(e) {}
  }
}

// 页面加载时尝试获取 Token（非阻塞）
ensureToken();

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
      // [公网访问] 401 = 令牌失效/被重置，清除本地保存并提示重新输入
      if (resp.status === 401) {
        try { localStorage.removeItem('txhj_access_token'); } catch(e) {}
        window.ACCESS_TOKEN = '';
        return { error: '访问令牌无效或已过期，请刷新页面重新输入' };
      }
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
var wsOnNpcChatToken = null;  // function(token) — 收到 NPC 聊天流式 token 时回调
var wsOnNpcChatEnd = null;    // function() — NPC 聊天流结束时回调
// [v10] 有限重连 + 指数退避，避免无限重连（M5c）
var wsReconnectAttempts = 0;
var wsMaxReconnect = 10;
var wsIntentionalClose = false;

async function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  // [Bug] 连接前必须确保 access_token 已就绪，否则后端 WS 鉴权会以 4001 关闭连接，
  // 导致"WebSocket 未连接"且无法与 NPC 聊天。ensureToken 内部有缓存，重复调用无开销。
  await ensureToken();
  var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsUrl = protocol + '//' + location.host + '/ws/' + wsClientId;
  // [Bug] 后端 websocket_endpoint 校验 token（若已设置 access_token），
  // 前端必须把 token 作为 query 参数传入，否则连接被 4001 关闭。
  if (window.ACCESS_TOKEN) {
    wsUrl += '?token=' + encodeURIComponent(window.ACCESS_TOKEN);
  }
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
        } else if (msg.type === 'npc_chat_result' && window.handleNpcChatResult) {
          window.handleNpcChatResult(msg.result);
        } else if (msg.type === 'npc_chat_token' && wsOnNpcChatToken) {
          wsOnNpcChatToken(msg.token);
        } else if (msg.type === 'npc_chat_end' && wsOnNpcChatEnd) {
          wsOnNpcChatEnd();
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