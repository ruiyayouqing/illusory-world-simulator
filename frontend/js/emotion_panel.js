// 太虚幻境 v1.6 P1-8 — 情感记忆面板：玩家 + NPC 情感状态 + 情感记忆检索
// 依赖：core.js (api, escHtml, escAttr), dom.js ($)

// ===== 全局状态 =====
var _emotionLastFetch = 0;
var _emotionAutoRefresh = false;
var _emotionAutoRefreshTimer = null;

// 八情绪配置（与后端 Plutchik 模型一致）
var EMOTION_META = {
  joy:          { label: '喜悦',     color: '#ffeb3b', icon: '😊' },
  sadness:      { label: '悲伤',     color: '#2196f3', icon: '😢' },
  anger:        { label: '愤怒',     color: '#f44336', icon: '😠' },
  fear:         { label: '恐惧',     color: '#9c27b0', icon: '😨' },
  surprise:     { label: '惊讶',     color: '#ff9800', icon: '😮' },
  disgust:      { label: '厌恶',     color: '#795548', icon: '🤢' },
  trust:        { label: '信任',     color: '#4caf50', icon: '🤝' },
  anticipation: { label: '期待',     color: '#00bcd4', icon: '🌟' },
};

// ===== 主入口：刷新情感面板 =====
async function refreshEmotionPanel(force) {
  var now = Date.now();
  if (!force && now - _emotionLastFetch < 3000) return;
  _emotionLastFetch = now;

  // 并发拉取总览 + NPC 情感
  var ovPromise = api('GET', '/api/emotional/overview');

  try {
    var overview = await ovPromise;
    if (overview && overview.error) {
      showEmotionError(overview.error);
      return;
    }
    overview = overview || {};
    renderEmotionPlayer(overview.player || {});
    renderEmotionNpcs(overview.npcs || []);
    // 总览中的 summary 仅在初次或筛选改变时刷新记忆列表
    if (!$('emotion_filter').value) {
      renderEmotionSummary(overview.summary || {});
    }
  } catch (e) {
    console.warn('[Emotion] refresh failed:', e);
    showEmotionError(e.message || '加载失败');
  }
}

// ===== 渲染玩家情感状态 =====
function renderEmotionPlayer(state) {
  var box = $('emotion_player');
  if (!box) return;
  var dominant = state.dominant_emotion || 'neutral';
  var intensity = state.dominant_intensity || 0;
  var valence = state.valence || 0;
  var arousal = state.arousal || 0;
  var vector = state.vector || {};
  var meta = EMOTION_META[dominant] || { label: '平静', color: '#888', icon: '😌' };

  var html = '<div class="emotion-player">';
  html += '<div class="emotion-player-head">';
  html += '<span class="ep-name">主角</span>';
  html += '<span class="ep-dominant" style="color:' + meta.color + '">' + meta.icon + ' ' + meta.label + '</span>';
  html += '<span class="ep-intensity">强度 ' + (intensity * 100).toFixed(0) + '%</span>';
  html += '<span class="ep-valence">效价 ' + (valence >= 0 ? '+' : '') + valence.toFixed(2) + '</span>';
  html += '<span class="ep-arousal">唤醒 ' + (arousal * 100).toFixed(0) + '%</span>';
  html += '</div>';
  // 八情绪条形图
  html += '<div class="emotion-bars">';
  Object.keys(EMOTION_META).forEach(function(e) {
    var v = vector[e] || 0;
    var m = EMOTION_META[e];
    html += '<div class="ebar" title="' + m.label + ' ' + (v * 100).toFixed(0) + '%">';
    html += '<span class="ebar-label">' + m.icon + ' ' + m.label + '</span>';
    html += '<div class="ebar-track"><div class="ebar-fill" style="width:' + (v * 100).toFixed(0) + '%;background:' + m.color + '"></div></div>';
    html += '<span class="ebar-val">' + (v * 100).toFixed(0) + '%</span>';
    html += '</div>';
  });
  html += '</div>';
  // 历史最近 5 条
  var history = state.history || [];
  if (history.length > 0) {
    html += '<div class="ep-history">';
    html += '<div class="ep-history-title">最近情感事件</div>';
    history.slice(-5).reverse().forEach(function(h) {
      var hm = EMOTION_META[h.emotion] || { label: h.emotion, color: '#888', icon: '•' };
      html += '<div class="ep-history-item">';
      html += '<span style="color:' + hm.color + '">' + hm.icon + ' ' + hm.label + '</span>';
      html += '<span>强度 ' + (h.intensity * 100).toFixed(0) + '%</span>';
      html += '<span class="ep-source">' + escHtml(h.source || '') + '</span>';
      if (h.detail) html += '<span class="ep-detail">' + escHtml(h.detail) + '</span>';
      html += '</div>';
    });
    html += '</div>';
  }
  html += '</div>';
  box.innerHTML = html;
}

// ===== 渲染 NPC 情感状态列表 =====
function renderEmotionNpcs(npcs) {
  var box = $('emotion_npc_list');
  if (!box) return;
  var countEl = $('emotion_npc_count');
  if (countEl) countEl.textContent = '共 ' + npcs.length + ' 个 NPC';

  if (!npcs.length) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">暂无 NPC 情感状态记录<br><span style="font-size:.85em">进行叙事后情感状态会自动累积</span></div>';
    return;
  }

  // 按主导情感强度降序
  npcs.sort(function(a, b) {
    return (b.dominant_intensity || 0) - (a.dominant_intensity || 0);
  });

  var html = npcs.map(function(n) {
    var dominant = n.dominant_emotion || 'neutral';
    var intensity = n.dominant_intensity || 0;
    var vector = n.vector || {};
    var meta = EMOTION_META[dominant] || { label: '平静', color: '#888', icon: '😌' };
    var valence = n.valence || 0;
    var arousal = n.arousal || 0;

    var card = '<div class="emotion-npc-card" data-npc-id="' + escAttr(n.npc_id || '') + '">';
    card += '<div class="enc-head">';
    card += '<span class="enc-name">' + escHtml(n.npc_name || n.npc_id || 'NPC') + '</span>';
    card += '<span class="enc-dominant" style="color:' + meta.color + '">' + meta.icon + ' ' + meta.label + '</span>';
    card += '<span class="enc-intensity">强度 ' + (intensity * 100).toFixed(0) + '%</span>';
    card += '<span class="enc-valence">效价 ' + (valence >= 0 ? '+' : '') + valence.toFixed(2) + '</span>';
    card += '<span class="enc-arousal">唤醒 ' + (arousal * 100).toFixed(0) + '%</span>';
    card += '</div>';
    // 迷你八情绪条
    card += '<div class="emotion-bars-mini">';
    Object.keys(EMOTION_META).forEach(function(e) {
      var v = vector[e] || 0;
      if (v < 0.05) return;
      var m = EMOTION_META[e];
      card += '<span class="ebar-mini" title="' + m.label + ' ' + (v * 100).toFixed(0) + '%" style="background:' + m.color + ';opacity:' + (0.3 + v * 0.7) + '">' + m.icon + '</span>';
    });
    card += '</div>';
    card += '</div>';
    return card;
  }).join('');
  box.innerHTML = html;
}

// ===== 渲染情感记忆统计（用于面板顶部信息提示） =====
function renderEmotionSummary(summary) {
  // 暂时只更新 count，详细统计可通过 by-emotion 端点查看
  var emotions = summary.emotions || {};
  var total = summary.total || 0;
  // 如果记忆列表为空且没有筛选，自动加载一次全部情感记忆摘要
  if (!$('emotion_filter').value && !$('emotion_entity_filter').value && total > 0) {
    loadEmotionalMemory();
  }
}

// ===== 加载情感记忆（按情感类型/实体过滤） =====
async function loadEmotionalMemory() {
  var filter = $('emotion_filter').value;
  var entity = $('emotion_entity_filter').value.trim();
  var listEl = $('emotion_memory_list');
  var countEl = $('emotion_memory_count');
  if (!listEl) return;
  listEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载中...</div>';

  try {
    var d;
    if (!filter) {
      // 没选情感类型：拉取全局摘要
      d = await api('GET', '/api/emotional/summary' + (entity ? ('?entity=' + encodeURIComponent(entity)) : ''));
      renderEmotionSummaryAsList(d || {}, entity);
    } else {
      // 选了情感类型：拉取该情感的明细列表
      var url = '/api/emotional/by-emotion?emotion=' + encodeURIComponent(filter) + '&limit=30';
      if (entity) url += '&entity=' + encodeURIComponent(entity);
      d = await api('GET', url);
      renderEmotionItemsList((d && d.items) || [], filter);
    }
    if (countEl) {
      var cnt = d ? (d.count || (d.emotions ? Object.keys(d.emotions).length : 0)) : 0;
      countEl.textContent = filter ? ('共 ' + cnt + ' 条') : ('共 ' + cnt + ' 类情感');
    }
  } catch (e) {
    listEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载失败</div>';
  }
}

// ===== 渲染：情感统计汇总（无筛选时） =====
function renderEmotionSummaryAsList(d, entity) {
  var listEl = $('emotion_memory_list');
  var emotions = d.emotions || {};
  var total = d.total || 0;
  var avgValence = d.avg_valence || 0;
  var entityLabel = entity ? ('"' + escHtml(entity) + '"') : '全局';

  var html = '<div style="margin-bottom:10px;padding:8px 12px;background:var(--panel);border:1px solid var(--border);border-radius:8px">';
  html += '<span style="color:var(--accent);font-weight:600">' + entityLabel + ' 情感记忆统计</span>';
  html += '<span style="margin-left:14px;color:var(--dim)">共 ' + total + ' 条记忆</span>';
  html += '<span style="margin-left:14px;color:var(--dim)">平均效价 ' + (avgValence >= 0 ? '+' : '') + avgValence + '</span>';
  html += '</div>';

  if (!Object.keys(emotions).length) {
    listEl.innerHTML = html + '<div style="color:var(--dim);text-align:center;padding:20px">尚无情感记忆<br><span style="font-size:.85em">进行叙事后系统会自动评估情感并写入记忆库</span></div>';
    return;
  }

  // 按 avg_weight 降序
  var arr = Object.keys(emotions).map(function(e) {
    return Object.assign({ emotion: e }, emotions[e]);
  }).sort(function(a, b) {
    return (b.avg_weight || 0) - (a.avg_weight || 0);
  });

  html += '<div class="emotion-summary-grid">';
  arr.forEach(function(item) {
    var m = EMOTION_META[item.emotion] || { label: item.emotion, color: '#888', icon: '•' };
    html += '<div class="emotion-summary-card" onclick="setEmotionFilter(\'' + item.emotion + '\')" style="cursor:pointer" title="点击查看该情感的明细">';
    html += '<div class="esc-head"><span style="color:' + m.color + '">' + m.icon + ' ' + m.label + '</span></div>';
    html += '<div class="esc-count">记忆数：' + item.count + '</div>';
    html += '<div class="esc-weight">平均强度 ' + ((item.avg_weight || 0) * 100).toFixed(0) + '%</div>';
    html += '<div class="esc-valence">平均效价 ' + ((item.avg_valence || 0) >= 0 ? '+' : '') + (item.avg_valence || 0).toFixed(2) + '</div>';
    html += '<div class="esc-bar"><div style="width:' + ((item.avg_weight || 0) * 100).toFixed(0) + '%;background:' + m.color + '"></div></div>';
    html += '</div>';
  });
  html += '</div>';
  listEl.innerHTML = html;
}

// ===== 渲染：情感记忆明细列表 =====
function renderEmotionItemsList(items, filter) {
  var listEl = $('emotion_memory_list');
  if (!items.length) {
    listEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">该情感类型暂无记忆</div>';
    return;
  }
  var m = EMOTION_META[filter] || { label: filter, color: '#888', icon: '•' };
  var html = '<div style="margin-bottom:8px;padding:6px 12px;background:rgba(' + hexToRgb(m.color) + ',.1);border-left:3px solid ' + m.color + ';border-radius:4px">';
  html += '<span style="color:' + m.color + ';font-weight:600">' + m.icon + ' ' + m.label + '</span>';
  html += '<span style="margin-left:10px;color:var(--dim);font-size:.85em">共 ' + items.length + ' 条记忆</span>';
  html += '</div>';
  html += items.map(function(it) {
    var card = '<div class="emotion-item">';
    card += '<div class="ei-head">';
    card += '<span class="ei-weight" style="color:' + m.color + '">强度 ' + ((it.emotional_weight || 0) * 100).toFixed(0) + '%</span>';
    card += '<span class="ei-valence">效价 ' + ((it.valence || 0) >= 0 ? '+' : '') + (it.valence || 0).toFixed(2) + '</span>';
    card += '<span class="ei-arousal">唤醒 ' + ((it.arousal || 0) * 100).toFixed(0) + '%</span>';
    card += '</div>';
    card += '<div class="ei-text">' + escHtml((it.text || '').slice(0, 280)) + (it.text && it.text.length > 280 ? '...' : '') + '</div>';
    card += '</div>';
    return card;
  }).join('');
  listEl.innerHTML = html;
}

// ===== 工具：点击情感统计卡片自动设置筛选 =====
function setEmotionFilter(emotion) {
  $('emotion_filter').value = emotion;
  loadEmotionalMemory();
}

// ===== 错误提示 =====
function showEmotionError(msg) {
  var box = $('emotion_player');
  if (box) box.innerHTML = '<div style="color:#d44;padding:20px;text-align:center">⚠ ' + escHtml(msg) + '</div>';
  var list = $('emotion_npc_list');
  if (list) list.innerHTML = '';
}

// ===== 工具：hex 转 rgb（用于透明度计算） =====
function hexToRgb(hex) {
  hex = (hex || '').replace('#', '');
  if (hex.length !== 6) return '128,128,128';
  var r = parseInt(hex.substr(0, 2), 16);
  var g = parseInt(hex.substr(2, 2), 16);
  var b = parseInt(hex.substr(4, 2), 16);
  return r + ',' + g + ',' + b;
}

// ===== 自动刷新 =====
function toggleEmotionAutoRefresh() {
  _emotionAutoRefresh = !_emotionAutoRefresh;
  var btn = $('emotion_autorefresh_btn');
  if (_emotionAutoRefresh) {
    if (btn) {
      btn.textContent = '停止自动刷新';
      btn.classList.add('active');
    }
    _emotionAutoRefreshTimer = setInterval(function() {
      refreshEmotionPanel(false);
    }, 5000);
  } else {
    if (btn) {
      btn.textContent = '自动刷新';
      btn.classList.remove('active');
    }
    if (_emotionAutoRefreshTimer) {
      clearInterval(_emotionAutoRefreshTimer);
      _emotionAutoRefreshTimer = null;
    }
  }
}

// ===== 打开/关闭情感面板 =====
function openEmotionModal() {
  var modal = $('emotionModal');
  if (!modal) return;
  modal.style.display = 'flex';
  refreshEmotionPanel(true);
}

function closeEmotionModal() {
  var modal = $('emotionModal');
  if (!modal) return;
  modal.style.display = 'none';
  if (_emotionAutoRefresh) {
    toggleEmotionAutoRefresh();
  }
}
