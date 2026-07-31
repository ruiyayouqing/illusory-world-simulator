// 太虚幻境 v1.6 — 江湖见闻面板：展示 NPC-NPC 对话传闻流
// 依赖：core.js (api, escHtml, escAttr), dom.js ($)

// ===== 全局状态 =====
var _rumorFeed = [];           // 当前已渲染的传闻列表
var _rumorExpanded = new Set(); // 已展开对话详情的 rumor 索引
var _rumorLastFetch = 0;        // 上次拉取时间戳（节流）
var _rumorFetching = false;     // 防止并发拉取

// 场景类型 → 图标
var RUMOR_SCENE_ICONS = {
  encounter: '🍃', council: '🏛️', conflict: '⚔️',
  chat: '💬', secret: '🤫', default: '🎭'
};

// ===== 主入口：拉取并渲染传闻流 =====
async function refreshRumors(force) {
  // 节流：非强制情况下，10 秒内不重复拉取
  var now = Date.now();
  if (!force && _rumorFetching) return;
  if (!force && now - _rumorLastFetch < 10000) return;

  _rumorFetching = true;
  try {
    var d = await api('GET', '/api/npc-dialogues/rumors?limit=15');
    if (d && d.error) {
      // 静默失败：保持原内容，避免刷屏报错
      console.warn('[Rumors] fetch failed:', d.error);
      return;
    }
    _rumorLastFetch = now;
    _rumorFeed = (d && d.rumors) ? d.rumors : [];
    renderRumorFeed(_rumorFeed);
  } catch (e) {
    console.warn('[Rumors] refreshRumors failed:', e);
  } finally {
    _rumorFetching = false;
  }
}

// ===== 渲染传闻列表 =====
function renderRumorFeed(rumors) {
  var box = $('rumor_feed');
  if (!box) return;

  // 更新计数
  var countEl = $('rumor_count');
  if (countEl) {
    countEl.textContent = rumors.length ? ('(' + rumors.length + ')') : '';
  }

  if (!rumors || rumors.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.8em">江湖风平浪静，暂无见闻...</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < rumors.length; i++) {
    var r = rumors[i];
    var isWitnessed = (r.type === 'witnessed');
    var cls = 'rumor-item' + (isWitnessed ? ' witnessed' : '');
    var icon = RUMOR_SCENE_ICONS[r.scene_name] || RUMOR_SCENE_ICONS.default;

    // 头部：场景 + 地点 + 时间
    html += '<div class="' + cls + '" data-idx="' + i + '">';
    html += '<div class="rscene">' + icon + ' ' + escHtml(r.scene_name || '见闻') + '</div>';
    html += '<div class="rloc">📍 ' + escHtml(r.location || '未知') + ' · 第' + (r.day || '?') + '天';
    if (r.time) html += ' ' + escHtml(r.time);
    html += '</div>';

    // 参与者
    var parts = r.participants || [];
    if (parts.length > 0) {
      html += '<div class="rparts">👤 ' + escHtml(parts.join(' 与 ')) + '</div>';
    }

    // 摘要
    if (r.summary) {
      html += '<div class="rsummary">' + escHtml(r.summary) + '</div>';
    }

    // 话题标签
    var tags = r.topic_tags || [];
    if (tags.length > 0) {
      html += '<div class="rtags">';
      for (var t = 0; t < tags.length; t++) {
        html += '<span class="rtag">#' + escHtml(tags[t]) + '</span>';
      }
      html += '</div>';
    }

    // 目击对话：可展开查看完整内容
    if (isWitnessed && r.dialogue && r.dialogue.length > 0) {
      var expanded = _rumorExpanded.has(i);
      html += '<div class="rtoggle" onclick="toggleRumorDialogue(' + i + ')">' + (expanded ? '收起 ▲' : '查看对话 ▼') + '</div>';
      if (expanded) {
        html += renderRumorDialogue(r.dialogue);
      }
    } else if (isWitnessed) {
      html += '<div class="rhint">（无声内容）</div>';
    } else {
      html += '<div class="rhint">（道听途说，未亲见）</div>';
    }

    html += '</div>';
  }
  box.innerHTML = html;
}

// ===== 渲染完整对话内容 =====
function renderRumorDialogue(dialogue) {
  if (!dialogue || dialogue.length === 0) return '';
  var html = '<div class="rumor-dialogue">';
  for (var i = 0; i < dialogue.length; i++) {
    var line = dialogue[i];
    var speaker = line.speaker || '？';
    var content = line.content || '';
    var action = line.action || '';
    html += '<div class="dline">';
    html += '<span class="dspeaker">' + escHtml(speaker) + '：</span>';
    if (action) {
      html += '<span class="daction">（' + escHtml(action) + '）</span>';
    }
    html += '<span class="dcontent">' + escHtml(content) + '</span>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

// ===== 展开/收起对话详情 =====
function toggleRumorDialogue(idx) {
  if (_rumorExpanded.has(idx)) {
    _rumorExpanded.delete(idx);
  } else {
    _rumorExpanded.add(idx);
  }
  // 仅重渲染当前列表，避免重新拉取
  renderRumorFeed(_rumorFeed);
}

// ===== 清空见闻（切换存档/世界时调用） =====
function clearRumors() {
  _rumorFeed = [];
  _rumorExpanded = new Set();
  _rumorLastFetch = 0;
  var box = $('rumor_feed');
  if (box) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.8em">江湖风平浪静，暂无见闻...</div>';
  }
  var countEl = $('rumor_count');
  if (countEl) countEl.textContent = '';
}
