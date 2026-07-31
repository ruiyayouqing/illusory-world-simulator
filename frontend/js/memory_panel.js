// 太虚幻境 v1.6 P1-6 — 长期记忆面板：L1/L2/L3 摘要 + 审计日志
// 依赖：core.js (api, escHtml, escAttr), dom.js ($)

// ===== 全局状态 =====
var _memoryFilterLevel = "";           // 当前摘要筛选级别
var _memoryLastFetch = 0;              // 节流
var _memoryAutoRefresh = false;
var _memoryAutoRefreshTimer = null;
var _memoryAuditEnabled = true;        // 假定默认启用，刷新时同步真实状态

// ===== 主入口：刷新记忆面板（摘要 + 审计 + 总览） =====
async function refreshMemoryPanel(force) {
  var now = Date.now();
  if (!force && now - _memoryLastFetch < 3000) return;
  _memoryLastFetch = now;

  // 并发拉取总览 + 摘要列表 + 审计日志
  var ovPromise = api('GET', '/api/memory/overview');
  var sumPromise = api('GET', '/api/memory/summaries?level=' + encodeURIComponent(_memoryFilterLevel) + '&limit=30');
  var audPromise = api('GET', '/api/memory/audit?limit=50');

  try {
    var results = await Promise.all([ovPromise, sumPromise, audPromise]);
    var overview = results[0];
    var summaries = results[1];
    var audit = results[2];

    if (overview && overview.error) {
      showMemoryError(overview.error);
      return;
    }

    renderMemoryOverview(overview || {});
    renderMemorySummaries((summaries && summaries.summaries) || []);
    renderMemoryAudit((audit && audit.records) || [], (audit && audit.stats) || {});
  } catch (e) {
    console.warn('[Memory] refresh failed:', e);
    showMemoryError(e.message || '加载失败');
  }
}

// ===== 渲染总览 =====
function renderMemoryOverview(d) {
  var box = $('memory_overview');
  if (!box) return;

  // 同步审计开关状态
  var auditStats = d.audit || {};
  _memoryAuditEnabled = auditStats.enabled !== false;
  var btn = $('memory_audit_btn');
  if (btn) {
    btn.textContent = _memoryAuditEnabled ? '关闭审计' : '开启审计';
    btn.classList.toggle('active', _memoryAuditEnabled);
  }

  var ltm = d.long_term_summaries || {};
  var byLevel = ltm.by_level || {};
  var html = '<div class="mem-overview">';
  html += '<div class="mem-stat"><span class="mem-stat-label">长期摘要</span><b>' + (ltm.total || 0) + '</b></div>';
  html += '<div class="mem-stat mem-l1"><span class="mem-stat-label">L1 日常</span><b>' + (byLevel['L1'] || 0) + '</b></div>';
  html += '<div class="mem-stat mem-l2"><span class="mem-stat-label">L2 周期</span><b>' + (byLevel['L2'] || 0) + '</b></div>';
  html += '<div class="mem-stat mem-l3"><span class="mem-stat-label">L3 里程碑</span><b>' + (byLevel['L3'] || 0) + '</b></div>';
  html += '<div class="mem-stat"><span class="mem-stat-label">向量库记忆</span><b>' + (d.memory_store_count || 0) + '</b></div>';
  html += '<div class="mem-stat"><span class="mem-stat-label">Curator 摘要</span><b>' + (d.curator_summary_count || 0) + '</b></div>';
  html += '<div class="mem-stat"><span class="mem-stat-label">审计记录</span><b>' + (auditStats.total_records || 0) + '/' + (auditStats.max_capacity || 200) + '</b></div>';
  html += '</div>';
  box.innerHTML = html;
}

// ===== 渲染摘要列表 =====
function renderMemorySummaries(summaries) {
  var box = $('memory_summary_list');
  if (!box) return;

  var countEl = $('memory_summary_count');
  if (countEl) {
    countEl.textContent = '共 ' + summaries.length + ' 条';
  }

  if (summaries.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px;font-size:.85em">暂无长期记忆摘要。<br>游戏中推进剧情即可自动生成 L1 日常摘要；<br>关键事件（突破/死亡/结婚等）会触发 L3 里程碑摘要。</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < summaries.length; i++) {
    var s = summaries[i];
    var lv = s.level || '?';
    var lvClass = 'mem-item-L' + lv;
    var lvColor = lv === 'L1' ? '#4caf50' : (lv === 'L2' ? 'var(--gold)' : (lv === 'L3' ? '#ff5722' : 'var(--dim)'));

    var dayStr = '';
    if (s.day_range && s.day_range.length === 2) {
      dayStr = s.day_range[0] === s.day_range[1]
        ? '第' + s.day_range[0] + '天'
        : '第' + s.day_range[0] + '-' + s.day_range[1] + '天';
    } else if (s.day) {
      dayStr = '第' + s.day + '天';
    }

    var title = '';
    if (lv === 'L3' && s.milestone_type) {
      title = '[' + milestoneLabel(s.milestone_type) + '] ';
    }
    title += s.summary_id || ('记忆 #' + (s.id || ''));

    var preview = (s.text || '').substring(0, 120);
    if (s.text && s.text.length > 120) preview += '...';

    html += '<div class="mem-item ' + lvClass + '" onclick="toggleMemoryDetail(this)">';
    html += '<div class="mem-item-head">';
    html += '<span class="mem-badge" style="background:' + lvColor + '">' + escHtml(lv) + '</span>';
    html += '<span class="mem-title">' + escHtml(title) + '</span>';
    if (dayStr) html += '<span class="mem-day">' + escHtml(dayStr) + '</span>';
    if (s.importance) {
      html += '<span class="mem-importance" title="重要性">★ ' + Number(s.importance).toFixed(2) + '</span>';
    }
    if (s.entry_count) {
      html += '<span class="mem-entry-count">' + s.entry_count + ' 条</span>';
    }
    html += '</div>';
    html += '<div class="mem-preview">' + escHtml(preview) + '</div>';
    html += '<div class="mem-detail" style="display:none">';
    html += '<div class="mem-row"><span class="mem-label">摘要ID:</span> ' + escHtml(s.summary_id || '') + '</div>';
    html += '<div class="mem-row"><span class="mem-label">记忆ID:</span> ' + escHtml(s.id || '') + '</div>';
    html += '<div class="mem-row"><span class="mem-label">全文:</span><div class="mem-fulltext">' + escHtml(s.text || '') + '</div></div>';
    html += '</div>';
    html += '</div>';
  }
  box.innerHTML = html;
}

// ===== 渲染审计日志 =====
function renderMemoryAudit(records, stats) {
  var box = $('memory_audit_list');
  if (!box) return;

  var countEl = $('memory_audit_count');
  if (countEl) {
    countEl.textContent = '(' + records.length + ' 条 / 共 ' + (stats.total_records || 0) + ')';
  }

  if (records.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px;font-size:.85em">暂无审计记录。<br>记忆操作（创建/摘要/归档等）会自动记录。</div>';
    return;
  }

  var opLabels = {
    'create': '创建', 'summarize': '摘要', 'archive': '归档',
    'delete': '删除', 'modify': '修改', 'retrieve': '检索', 'promote': '提升',
  };
  var opColors = {
    'create': 'var(--accent-green)', 'summarize': 'var(--gold)',
    'archive': 'var(--accent-blue)', 'delete': 'var(--accent-red)',
    'modify': 'var(--accent)', 'retrieve': 'var(--dim)', 'promote': '#ff5722',
  };

  var html = '';
  for (var i = 0; i < records.length; i++) {
    var r = records[i];
    var ts = new Date((r.ts || 0) * 1000).toLocaleString('zh-CN', {hour12: false});
    var opLabel = opLabels[r.operation] || r.operation || '?';
    var opColor = opColors[r.operation] || 'var(--dim)';
    var details = r.details || {};
    var detailText = Object.keys(details).map(function(k) {
      return k + ': ' + JSON.stringify(details[k]);
    }).join(' | ');

    html += '<div class="mem-audit-item" onclick="toggleMemoryDetail(this)">';
    html += '<div class="mem-audit-head">';
    html += '<span class="mem-audit-time">' + escHtml(ts) + '</span>';
    html += '<span class="mem-audit-op" style="color:' + opColor + '">' + escHtml(opLabel) + '</span>';
    html += '<span class="mem-audit-seq">#' + (r.seq || 0) + '</span>';
    if (r.target_id) {
      var tid = r.target_id.length > 16 ? r.target_id.substring(0, 16) + '...' : r.target_id;
      html += '<span class="mem-audit-target" title="' + escAttr(r.target_id) + '">→ ' + escHtml(tid) + '</span>';
    }
    if (r.memory_type) {
      html += '<span class="mem-audit-type">' + escHtml(r.memory_type) + '</span>';
    }
    html += '</div>';
    if (detailText) {
      html += '<div class="mem-audit-detail">' + escHtml(detailText) + '</div>';
    }
    if (r.summary) {
      var s = r.summary.length > 120 ? r.summary.substring(0, 120) + '...' : r.summary;
      html += '<div class="mem-audit-summary">摘要: ' + escHtml(s) + '</div>';
    }
    html += '</div>';
  }
  box.innerHTML = html;
}

// ===== 切换展开/收起详情 =====
function toggleMemoryDetail(el) {
  var detail = el.querySelector('.mem-detail') || el.querySelector('.mem-audit-detail');
  if (!detail) return;
  // 摘要条目使用 .mem-detail；审计条目使用 .mem-audit-detail（已默认显示，这里只切换摘要详情）
  if (detail.classList.contains('mem-detail')) {
    detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
  }
}

// ===== 级别筛选 =====
function setMemoryFilter(level) {
  _memoryFilterLevel = level;
  // 更新按钮 active 状态
  var btns = document.querySelectorAll('.mem-filter-btn');
  for (var i = 0; i < btns.length; i++) {
    var bl = btns[i].getAttribute('data-level') || '';
    btns[i].classList.toggle('active', bl === level);
  }
  refreshMemoryPanel(true);
}

// ===== 切换审计开关 =====
async function toggleMemoryAudit() {
  var newEnabled = !_memoryAuditEnabled;
  try {
    var d = await api('POST', '/api/memory/audit/toggle', { enabled: newEnabled });
    if (d && d.error) {
      console.warn('[Memory] toggle audit failed:', d.error);
      return;
    }
    _memoryAuditEnabled = newEnabled;
    var btn = $('memory_audit_btn');
    if (btn) {
      btn.textContent = _memoryAuditEnabled ? '关闭审计' : '开启审计';
      btn.classList.toggle('active', _memoryAuditEnabled);
    }
    refreshMemoryPanel(true);
  } catch (e) {
    console.warn('[Memory] toggle audit failed:', e);
  }
}

// ===== 清空审计日志 =====
async function clearMemoryAudit() {
  if (!confirm('确定清空所有记忆审计日志？此操作仅清空缓冲区，不影响已写入向量库的记忆。')) return;
  try {
    await api('POST', '/api/memory/audit/clear');
    refreshMemoryPanel(true);
  } catch (e) {
    alert('清空失败');
  }
}

// ===== 自动刷新 =====
function toggleMemoryAutoRefresh() {
  _memoryAutoRefresh = !_memoryAutoRefresh;
  var btn = $('memory_autorefresh_btn');
  if (_memoryAutoRefresh) {
    if (btn) btn.textContent = '停止自动刷新';
    _memoryAutoRefreshTimer = setInterval(function() {
      refreshMemoryPanel(false);
    }, 5000);
  } else {
    if (btn) btn.textContent = '自动刷新';
    if (_memoryAutoRefreshTimer) {
      clearInterval(_memoryAutoRefreshTimer);
      _memoryAutoRefreshTimer = null;
    }
  }
}

// ===== 错误提示 =====
function showMemoryError(msg) {
  var box = $('memory_summary_list');
  if (box) {
    box.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:20px;font-size:.85em">' +
      escHtml(msg || '加载失败') + '</div>';
  }
}

// ===== 打开/关闭记忆面板 =====
function openMemoryModal() {
  var modal = $('memoryModal');
  if (!modal) return;
  modal.style.display = 'flex';
  refreshMemoryPanel(true);
}

function closeMemoryModal() {
  var modal = $('memoryModal');
  if (!modal) return;
  modal.style.display = 'none';
  // 关闭时停止自动刷新
  if (_memoryAutoRefresh) {
    toggleMemoryAutoRefresh();
  }
}

// ===== 工具：里程碑类型中文标签 =====
function milestoneLabel(type) {
  var labels = {
    'breakthrough': '突破', 'death': '陨落', 'marriage': '大婚',
    'birth': '诞生', 'war': '战役', 'discovery': '发现',
    'betrayal': '背叛', 'alliance': '结盟',
  };
  return labels[type] || type || '里程碑';
}

// ===== [v1.6 P1-7] 叙事下方显示"引用了哪些长期记忆"徽章 =====
async function fetchLongTermRefs() {
  try {
    var d = await api('GET', '/api/memory/last-refs');
    if (d && d.error) return;
    var refs = (d && d.refs) || [];
    renderLongTermRefs(refs);
  } catch (e) {
    // 静默失败：不影响主流程
  }
}

function renderLongTermRefs(refs) {
  var nb = $('nb');
  if (!nb) return;
  // 移除旧徽章容器
  var old = $('lt_refs_badge');
  if (old) old.remove();
  if (!refs || refs.length === 0) return;

  var html = '<div id="lt_refs_badge" class="lt-refs-badge">';
  html += '<span class="lt-refs-label">📚 引用长期记忆:</span>';
  for (var i = 0; i < refs.length; i++) {
    var r = refs[i];
    var lv = r.level || '?';
    var color = lv === 'L3' ? '#ff5722' : (lv === 'L2' ? 'var(--gold)' : '#4caf50');
    var label = lv;
    if (r.milestone_type) {
      label += '·' + milestoneLabel(r.milestone_type);
    }
    if (r.day) {
      label += '(第' + r.day + '天)';
    }
    if (r.forced_recall) {
      label += '⚡';
    }
    var tooltip = (r.text || '').substring(0, 80);
    html += '<span class="lt-ref-chip" style="border-color:' + color + ';color:' + color + '" title="' + escAttr(tooltip) + '">' + escHtml(label) + '</span>';
  }
  html += '</div>';

  // 插入到最新叙事段之后
  var lastP = nb.querySelector('p:last-of-type');
  if (lastP) {
    lastP.insertAdjacentHTML('afterend', html);
  } else {
    nb.insertAdjacentHTML('beforeend', html);
  }
  nb.scrollTop = nb.scrollHeight;
}

// ===== [v1.6 P1-7] 里程碑检测提示徽章 =====
function showMilestoneBadge(milestone) {
  if (!milestone) return;
  var nb = $('nb');
  if (!nb) return;
  var mType = milestone.milestone_type || '';
  var day = milestone.day || 0;
  var label = '⚡ 里程碑触发: ' + milestoneLabel(mType);
  if (day) label += ' (第' + day + '天)';

  var html = '<div class="milestone-badge">';
  html += '<span class="milestone-icon">⚡</span>';
  html += '<span class="milestone-text">' + escHtml(label) + '</span>';
  html += '</div>';

  var lastP = nb.querySelector('p:last-of-type');
  if (lastP) {
    lastP.insertAdjacentHTML('afterend', html);
  } else {
    nb.insertAdjacentHTML('beforeend', html);
  }
  nb.scrollTop = nb.scrollHeight;
}
