// 太虚幻境 云服务版 — 核心: API/状态/WebSocket/JWT 认证

const API_BASE = '';

// ===== JWT Token 管理 =====
function getToken() {
  return localStorage.getItem('taiwu_token') || '';
}

function getCurrentUser() {
  try {
    return JSON.parse(localStorage.getItem('taiwu_user') || '{}');
  } catch(e) { return {}; }
}

function clearAuth() {
  localStorage.removeItem('taiwu_token');
  localStorage.removeItem('taiwu_user');
  localStorage.removeItem('taiwu_quota');
}

function redirectToLogin() {
  clearAuth();
  if (location.pathname !== '/') {
    location.href = '/';
  }
}

// 页面加载时检查登录状态
(function checkAuth() {
  var token = getToken();
  if (!token) {
    redirectToLogin();
    return;
  }
  window.ACCESS_TOKEN = token;
})();

// 统一的 HTML 转义工具
function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

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
  var token = getToken();
  if (token) {
    opts.headers['Authorization'] = 'Bearer ' + token;
  }
  if (body && method !== 'GET') opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(API_BASE + path, opts);
    clearTimeout(timeoutId);
    // 401/403: token 过期或无权限，跳回登录
    if (resp.status === 401) {
      redirectToLogin();
      return { error: '登录已过期，请重新登录' };
    }
    if (resp.status === 403) {
      var errData = await resp.json().catch(function() { return {}; });
      if (errData.code === 'session_expired' || errData.code === 'queued') {
        // 会话过期或排队中
        return errData;
      }
      return { error: errData.error || '无权限访问' };
    }
    if (resp.status === 429) {
      var rateData = await resp.json().catch(function() { return {}; });
      return rateData;
    }
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
var wsOnToken = null;
var wsOnResult = null;
var wsOnThinking = null;
var wsOnStreamEnd = null;
var wsReconnectAttempts = 0;
var wsMaxReconnect = 10;
var wsIntentionalClose = false;

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var token = getToken();
  if (!token) {
    redirectToLogin();
    return;
  }
  var wsUrl = protocol + '//' + location.host + '/ws/' + wsClientId + '?token=' + encodeURIComponent(token);
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

// ===== 配置获取（云版：API Key 由服务端注入，此处返回空值） =====
async function getConfig() {
  // 云版：API Key 不再由前端传递，服务端 engine_pool 自动注入
  return {
    api_key: '',
    base_url: '',
    model_name: ''
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

// ===== 云版：会话心跳 + 计时器 =====
var _sessionTimer = null;
var _remainingTime = 0;

function startSessionHeartbeat() {
  // 每 60 秒发送心跳
  setInterval(async function() {
    var result = await api('POST', '/api/auth/heartbeat');
    if (result.status === 'active') {
      _remainingTime = result.remaining_time || 0;
      updateSessionDisplay();
    } else if (result.status === 'expired') {
      toast('会话已过期，请重新登录', 'error');
      setTimeout(redirectToLogin, 1500);
    } else if (result.status === 'queued') {
      toast(result.message || '正在排队...', 'info');
    }
  }, 60000);
  // 立即查一次
  checkSessionStatus();
}

async function checkSessionStatus() {
  var result = await api('GET', '/api/auth/session');
  if (result.status === 'active') {
    _remainingTime = result.remaining_time || 0;
    updateSessionDisplay();
  } else if (result.status === 'expired') {
    toast('会话已过期，请重新登录', 'error');
    setTimeout(redirectToLogin, 1500);
  }
}

function updateSessionDisplay() {
  var el = document.getElementById('session_timer');
  if (!el) return;
  var min = Math.floor(_remainingTime / 60);
  var sec = _remainingTime % 60;
  el.textContent = '⏱ ' + min + ':' + (sec < 10 ? '0' : '') + sec;
  if (_remainingTime < 300) {
    el.style.color = '#ff8080';
    if (_remainingTime < 60) {
      el.title = '会话即将过期，请及时保存进度';
    }
  }
}

// 倒计时（每秒更新显示）
setInterval(function() {
  if (_remainingTime > 0) {
    _remainingTime--;
    updateSessionDisplay();
  }
}, 1000);

// ===== 登出 =====
async function doLogout() {
  if (!confirm('确定要退出登录吗？')) return;
  try {
    await api('POST', '/api/auth/logout');
  } catch(e) {}
  clearAuth();
  location.href = '/';
}

// ===== 配额检查 =====
async function checkQuota() {
  var result = await api('GET', '/api/auth/session');
  if (result.quota) {
    var q = result.quota;
    if (q.enabled && q.remaining >= 0) {
      var el = document.getElementById('quota_display');
      if (el) {
        el.textContent = '🎲 今日剩余 ' + q.remaining + '/' + q.limit + ' 轮';
      }
      if (q.remaining === 0) {
        toast('今日轮数已达上限', 'error');
        return false;
      }
    }
  }
  return true;
}

// 页面加载完成后启动心跳
document.addEventListener('DOMContentLoaded', function() {
  startSessionHeartbeat();
});
