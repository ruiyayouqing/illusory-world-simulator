// 太虚幻境 v1.5 — 世界时钟 + 主动事件系统：前端渲染/弹窗/响应/世界公告注入

// ===== 全局状态 =====
var _playerEvents = [];          // 当前 pending 的玩家事件
var _worldEvents = [];           // 当前 pending 的世界事件
var _currentPopupEvent = null;   // 当前弹窗中的事件
var _popupQueue = [];            // 待弹窗的紧急事件队列
var _popupShownIds = new Set();  // 已经弹过窗的事件 id（防止重复弹）

// 事件类型 → 图标
var EVENT_ICONS = {
  visit: '🚪', greet: '👋', provoke: '⚔️', faction: '🏴', rumor: '📡',
  war: '⚔️', beast_tide: '🐺', disaster: '🌋', discovery: '✨',
  cult: '🩸', cold_visit: '❄️', trade: '💰', default: '📌'
};

// 优先级 → 标签和样式
var PRIORITY_LABELS = {
  urgent:    { text: '紧急', cls: 'evt-urgent' },
  important: { text: '重要', cls: 'evt-important' },
  normal:    { text: '普通', cls: 'evt-normal' }
};

// ===== 从 /api/state 响应更新事件 =====
function updateEvents(playerEvents, worldEvents) {
  try {
    var newPlayer = (playerEvents || []).filter(function(e) { return e.status === 'pending'; });
    var newWorld = (worldEvents || []);

    // 找出"新出现"的 urgent 玩家事件，加入弹窗队列
    newPlayer.forEach(function(e) {
      if (e.priority === 'urgent' && !_popupShownIds.has(e.event_id)) {
        _popupShownIds.add(e.event_id);
        _popupQueue.push(e);
      }
    });

    _playerEvents = newPlayer;
    _worldEvents = newWorld;

    renderPlayerEvents();
    renderWorldEvents();

    processPopupQueue();
  } catch(e) {
    console.warn('[Events] updateEvents failed:', e);
  }
}

// ===== 渲染玩家事件 =====
function renderPlayerEvents() {
  var box = $('playerEventsBox');
  if (!box) return;

  if (!_playerEvents || _playerEvents.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.78em">今日无人来访</div>';
    return;
  }

  box.innerHTML = _playerEvents.map(function(e) {
    var icon = EVENT_ICONS[e.event_type] || EVENT_ICONS.default;
    var pri = PRIORITY_LABELS[e.priority] || PRIORITY_LABELS.normal;
    var npcName = e.source_npc || e.payload?.npc_name || '未知';
    var remainText = '';
    if (e.expire_day != null && GS && GS.day != null) {
      var remain = e.expire_day - GS.day;
      if (remain <= 0) remainText = '<span style="color:#c94545">今日过期</span>';
      else if (remain === 1) remainText = '<span style="color:#c9a045">明日过期</span>';
      else remainText = '<span style="color:var(--dim)">' + remain + '天后过期</span>';
    }
    return '<div class="event-card ' + pri.cls + '" data-eid="' + escAttr(e.event_id) + '">' +
      '<div class="ec-head">' +
        '<span class="ec-icon">' + icon + '</span>' +
        '<span class="ec-title">' + escHtml(e.title || '事件') + '</span>' +
        '<span class="ec-pri ' + pri.cls + '">' + pri.text + '</span>' +
      '</div>' +
      '<div class="ec-body">' + escHtml(e.summary || '') + '</div>' +
      '<div class="ec-meta">' +
        '<span>来自：' + escHtml(npcName) + '</span>' +
        remainText +
      '</div>' +
      '<div class="ec-actions">' +
        '<button class="evt-btn accept-btn" onclick="respondEvent(\'' + escAttr(e.event_id) + '\', \'accept\')">应门</button>' +
        '<button class="evt-btn reject-btn" onclick="respondEvent(\'' + escAttr(e.event_id) + '\', \'reject\')">不见</button>' +
        '<button class="evt-btn ignore-btn" onclick="respondEvent(\'' + escAttr(e.event_id) + '\', \'ignore\')">稍后</button>' +
      '</div>' +
    '</div>';
  }).join('');
}

// ===== 渲染世界事件 =====
function renderWorldEvents() {
  var box = $('worldEventsBox');
  if (!box) return;

  if (!_worldEvents || _worldEvents.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.78em">天下太平</div>';
    return;
  }

  // 按触发日倒序
  var sorted = _worldEvents.slice().sort(function(a, b) {
    return (b.trigger_day || 0) - (a.trigger_day || 0);
  });

  box.innerHTML = sorted.map(function(e) {
    var icon = EVENT_ICONS[e.event_type] || EVENT_ICONS.default;
    var pri = PRIORITY_LABELS[e.priority] || PRIORITY_LABELS.normal;
    var dayText = e.trigger_day ? '第' + e.trigger_day + '天' : '';
    return '<div class="wevent-card ' + pri.cls + '" data-eid="' + escAttr(e.event_id) + '">' +
      '<div class="ec-head">' +
        '<span class="ec-icon">' + icon + '</span>' +
        '<span class="ec-title">' + escHtml(e.title || '世界事件') + '</span>' +
      '</div>' +
      '<div class="ec-body">' + escHtml(e.summary || '') + '</div>' +
      '<div class="ec-meta"><span>' + escHtml(dayText) + '</span></div>' +
      '<div class="ec-actions">' +
        '<button class="evt-btn accept-btn" onclick="respondEvent(\'' + escAttr(e.event_id) + '\', \'accept\')">关注</button>' +
        '<button class="evt-btn ignore-btn" onclick="respondEvent(\'' + escAttr(e.event_id) + '\', \'ignore\')">忽略</button>' +
      '</div>' +
    '</div>';
  }).join('');
}

// ===== 紧急事件弹窗 =====
function processPopupQueue() {
  if (_currentPopupEvent) return;
  if (!_popupQueue || _popupQueue.length === 0) return;
  var evt = _popupQueue.shift();
  showEventPopup(evt);
}

function showEventPopup(event) {
  _currentPopupEvent = event;
  var overlay = $('eventPopupOverlay');
  if (!overlay) { _currentPopupEvent = null; return; }

  var icon = EVENT_ICONS[event.event_type] || EVENT_ICONS.default;
  var pri = PRIORITY_LABELS[event.priority] || PRIORITY_LABELS.normal;

  var npcLine = '';
  if (event.source_npc || (event.payload && event.payload.npc_name)) {
    npcLine = '<div class="ep-meta">来自：' + escHtml(event.source_npc || event.payload.npc_name) + '</div>';
  }

  overlay.innerHTML =
    '<div class="event-popup ' + pri.cls + '">' +
      '<div class="ep-head">' +
        '<span class="ep-icon">' + icon + '</span>' +
        '<span class="ep-pri ' + pri.cls + '">' + pri.text + '</span>' +
        '<button class="ep-close" onclick="closeEventPopup()" title="稍后处理">×</button>' +
      '</div>' +
      '<div class="ep-title">' + escHtml(event.title || '紧急事件') + '</div>' +
      '<div class="ep-body">' + escHtml(event.summary || '') + '</div>' +
      npcLine +
      '<div class="ep-actions">' +
        '<button class="evt-btn accept-btn" onclick="respondPopupEvent(\'accept\')">应门</button>' +
        '<button class="evt-btn reject-btn" onclick="respondPopupEvent(\'reject\')">不见</button>' +
        '<button class="evt-btn ignore-btn" onclick="respondPopupEvent(\'ignore\')">稍后</button>' +
      '</div>' +
    '</div>';
  overlay.style.display = 'flex';
}

function closeEventPopup() {
  var overlay = $('eventPopupOverlay');
  if (overlay) {
    overlay.style.display = 'none';
    overlay.innerHTML = '';
  }
  _currentPopupEvent = null;
  processPopupQueue();
}

// ===== 弹窗中的响应 =====
async function respondPopupEvent(action) {
  if (!_currentPopupEvent) return;
  var eventId = _currentPopupEvent.event_id;
  closeEventPopup();
  await _doRespondEvent(eventId, action);
}

// ===== 卡片中的响应 =====
async function respondEvent(eventId, action) {
  await _doRespondEvent(eventId, action);
}

// ===== 实际调用后端响应事件 =====
async function _doRespondEvent(eventId, action) {
  try {
    var res = await api('POST', '/api/events/' + encodeURIComponent(eventId) + '/respond', { action: action });
    if (!res || res.error) {
      toast('事件响应失败：' + (res && res.error ? res.error : '未知错误'), 'error');
      return;
    }

    // accept 的副作用：注入桥接叙事 / 世界公告 / 打开 NPC 聊天
    if (action === 'accept') {
      // 1. 玩家事件 → 注入桥接叙事到主叙事区
      if (res.bridge_narrative) {
        addNarrative(res.bridge_narrative, false, false);
      }
      // 2. 世界事件 → 注入"【世界公告】"到主叙事区（isEvent=true，用事件样式）
      if (res.announcement) {
        addNarrative(res.announcement, true, false);
      }
      // 3. 玩家事件接受后，自动打开对应 NPC 的聊天面板
      if (res.npc_id) {
        setTimeout(function() {
          try {
            if (!_npcChatPanelOpen && typeof openNpcChat === 'function') openNpcChat();
            if (typeof selectChatNpc === 'function') {
              selectChatNpc(res.npc_id, res.npc_name || '');
            }
          } catch(e) { console.warn('[Events] open chat failed:', e); }
        }, 600);
      }
    }

    // accept / reject 后从本地列表移除或更新
    if (action === 'accept' || action === 'reject') {
      _playerEvents = _playerEvents.filter(function(e) { return e.event_id !== eventId; });
      _worldEvents = _worldEvents.filter(function(e) { return e.event_id !== eventId; });
      renderPlayerEvents();
      renderWorldEvents();
    }
    // ignore 保持 pending，不修改本地状态

    var msgMap = { accept: '已接受', reject: '已拒绝', ignore: '稍后处理', view: '已查看' };
    toast('事件' + (msgMap[action] || '已处理'), 'info');
  } catch(e) {
    toast('事件响应失败：网络错误', 'error');
  }
}

// ===== 在 wsOnStreamEnd 后检查紧急事件弹窗 =====
function checkUrgentPopup() {
  if (!_playerEvents || _playerEvents.length === 0) return;
  _playerEvents.forEach(function(e) {
    if (e.priority === 'urgent' && e.status === 'pending' && !_popupShownIds.has(e.event_id)) {
      _popupShownIds.add(e.event_id);
      _popupQueue.push(e);
    }
  });
  processPopupQueue();
}

// ===== 手动刷新事件列表 =====
async function refreshEventsList() {
  try {
    var res = await api('GET', '/api/events');
    if (res && !res.error) {
      updateEvents(res.player_events || [], res.world_events || []);
    }
  } catch(e) {
    console.warn('[Events] refresh failed:', e);
  }
}

// ===== 手动清理过期事件 =====
async function clearExpiredEvents() {
  try {
    var res = await api('POST', '/api/events/clear-expired');
    if (res && !res.error) {
      var cleared = (res.player_expired || 0) + (res.world_expired || 0);
      toast('已清理 ' + cleared + ' 条过期事件', 'info');
      refreshEventsList();
    }
  } catch(e) {
    toast('清理失败', 'error');
  }
}
