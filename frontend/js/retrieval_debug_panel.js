// 太虚幻境 v1.6 P1-5 — 检索调试面板：CRAG + HyDE 检索审计
// 依赖：core.js (api, escHtml, escAttr), dom.js ($)

// ===== 全局状态 =====
var _retrievalDebugEnabled = false;
var _retrievalLastFetch = 0;
var _retrievalAutoRefresh = false;
var _retrievalAutoRefreshTimer = null;

// ===== 主入口：刷新审计日志 =====
async function refreshRetrievalAudit(force) {
  var now = Date.now();
  if (!force && now - _retrievalLastFetch < 3000) return;
  _retrievalLastFetch = now;

  try {
    var d = await api('GET', '/api/retrieval/audit?limit=30');
    if (d && d.error) {
      showRetrievalError(d.error);
      return;
    }
    renderRetrievalAudit(d);
  } catch (e) {
    console.warn('[Retrieval] refresh failed:', e);
    showRetrievalError(e.message || '加载失败');
  }
}

// ===== 渲染审计日志 =====
function renderRetrievalAudit(data) {
  var box = $('retrieval_audit_list');
  if (!box) return;

  var records = data.records || [];
  var stats = data.stats || {};

  // 更新统计栏
  var statsBox = $('retrieval_stats');
  if (statsBox) {
    var html = '<div class="ret-stats">';
    html += '<span>记录: <b>' + (stats.total_records || 0) + '</b></span>';
    html += '<span>容量: <b>' + (stats.max_capacity || 50) + '</b></span>';
    html += '<span>审计: <b style="color:' + (stats.enabled ? 'var(--accent-green)' : 'var(--accent-red)') + '">' + (stats.enabled ? 'ON' : 'OFF') + '</b></span>';
    html += '<span>调试: <b style="color:' + (stats.debug ? 'var(--accent-green)' : 'var(--dim)') + '">' + (stats.debug ? 'ON' : 'OFF') + '</b></span>';
    html += '</div>';
    statsBox.innerHTML = html;
  }

  if (records.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px;font-size:.8em">暂无检索记录。<br>在游戏中发送指令即可产生记录。</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < records.length; i++) {
    var r = records[i];
    var ts = new Date((r.ts || 0) * 1000).toLocaleTimeString('zh-CN', {hour12: false});
    var hydeBadge = r.trigger_hyde
      ? '<span class="ret-badge ret-hyde" title="触发了 HyDE 查询重写">HyDE</span>'
      : '';
    var recalls = r.recalls || {};
    var recallText = Object.keys(recalls).map(function(k) {
      return k + ':' + recalls[k];
    }).join(' ');

    var scoreColor = r.avg_score >= 0.55 ? 'var(--accent-green)' :
                     r.avg_score >= 0.35 ? 'var(--gold)' : 'var(--accent-red)';

    html += '<div class="ret-item" onclick="toggleRetrievalDetail(this)">';
    html += '<div class="ret-head">';
    html += '<span class="ret-time">' + ts + '</span>';
    html += '<span class="ret-query" title="' + escAttr(r.query || '') + '">' + escHtml((r.query || '').substring(0, 40)) + (r.query && r.query.length > 40 ? '...' : '') + '</span>';
    html += hydeBadge;
    html += '<span class="ret-score" style="color:' + scoreColor + '">' + (r.avg_score || 0).toFixed(3) + '</span>';
    html += '<span class="ret-kept">采纳 ' + (r.kept || 0) + '/' + (r.total_candidates || 0) + '</span>';
    html += '<span class="ret-time-ms">' + (r.elapsed_ms || 0) + 'ms</span>';
    html += '</div>';
    html += '<div class="ret-detail" style="display:none">';
    if (r.rewritten_query) {
      html += '<div class="ret-row"><span class="ret-label">HyDE 重写:</span> ' + escHtml(r.rewritten_query) + '</div>';
    }
    if (r.hyde_doc) {
      html += '<div class="ret-row"><span class="ret-label">假设文档:</span> ' + escHtml(r.hyde_doc) + '</div>';
    }
    html += '<div class="ret-row"><span class="ret-label">召回分布:</span> ' + escHtml(recallText || '（无）') + '</div>';
    html += '<div class="ret-row"><span class="ret-label">CRAG 评估:</span> ';
    html += '<span class="ret-crag-high">高 ' + (r.high || 0) + '</span> ';
    html += '<span class="ret-crag-med">中 ' + (r.medium || 0) + '</span> ';
    html += '<span class="ret-crag-low">低 ' + (r.low || 0) + '</span>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
  }

  box.innerHTML = html;
}

// ===== 切换展开/收起详情 =====
function toggleRetrievalDetail(el) {
  var detail = el.querySelector('.ret-detail');
  if (!detail) return;
  detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
}

// ===== 切换调试模式 =====
async function toggleRetrievalDebug() {
  _retrievalDebugEnabled = !_retrievalDebugEnabled;
  try {
    var d = await api('POST', '/api/retrieval/debug?enabled=' + (_retrievalDebugEnabled ? 'true' : 'false'));
    if (d && d.error) {
      console.warn('[Retrieval] toggle debug failed:', d.error);
      return;
    }
    var btn = $('retrieval_debug_btn');
    if (btn) {
      btn.textContent = _retrievalDebugEnabled ? '关闭调试' : '开启调试';
      btn.classList.toggle('active', _retrievalDebugEnabled);
    }
    refreshRetrievalAudit(true);
  } catch (e) {
    console.warn('[Retrieval] toggle debug failed:', e);
  }
}

// ===== 清空审计日志 =====
async function clearRetrievalAudit() {
  if (!confirm('确定清空所有检索审计日志？')) return;
  try {
    await api('DELETE', '/api/retrieval/audit');
    refreshRetrievalAudit(true);
  } catch (e) {
    alert('清空失败');
  }
}

// ===== 测试检索 =====
async function testRetrieval() {
  var queryInput = $('retrieval_test_query');
  var query = queryInput ? queryInput.value.trim() : '';
  if (!query) {
    alert('请输入测试查询');
    return;
  }

  var resultBox = $('retrieval_test_result');
  if (resultBox) {
    resultBox.innerHTML = '<div style="color:var(--dim);padding:10px;text-align:center">检索中...</div>';
  }

  try {
    var d = await api('GET', '/api/retrieval/test?query=' + encodeURIComponent(query) + '&top_k=5');
    if (d && d.error) {
      resultBox.innerHTML = '<div style="color:var(--accent-red);padding:10px">' + escHtml(d.error) + '</div>';
      return;
    }
    renderRetrievalTestResult(d, resultBox);
    // 刷新审计日志（测试也会被记录）
    setTimeout(function() { refreshRetrievalAudit(true); }, 300);
  } catch (e) {
    if (resultBox) {
      resultBox.innerHTML = '<div style="color:var(--accent-red);padding:10px">测试失败: ' + escHtml(e.message || '') + '</div>';
    }
  }
}

function renderRetrievalTestResult(data, box) {
  if (!box) return;
  var results = data.results || [];
  var html = '<div class="ret-test-meta">';
  html += '查询: <b>' + escHtml(data.query || '') + '</b> | ';
  html += '耗时: <b>' + (data.elapsed_ms || 0) + 'ms</b> | ';
  html += '结果: <b>' + results.length + '</b> 条';
  html += '</div>';

  if (results.length === 0) {
    html += '<div style="color:var(--dim);padding:10px;text-align:center">无检索结果</div>';
  } else {
    for (var i = 0; i < results.length; i++) {
      var r = results[i];
      var labelColor = r.crag_label === 'high' ? 'var(--accent-green)' :
                       r.crag_label === 'medium' ? 'var(--gold)' : 'var(--accent-red)';
      html += '<div class="ret-test-item">';
      html += '<div class="ret-test-head">';
      html += '<span class="ret-rank">#' + (i + 1) + '</span>';
      html += '<span class="ret-source">' + escHtml(r.source || '?') + '</span>';
      html += '<span class="ret-crag-label" style="color:' + labelColor + '">' + (r.crag_label || '?') + '</span>';
      html += '<span class="ret-crag-score">CRAG: ' + (r.crag_score || 0).toFixed(3) + '</span>';
      html += '<span class="ret-rrf-score">RRF: ' + (r.score || 0).toFixed(4) + '</span>';
      html += '</div>';
      html += '<div class="ret-test-text">' + escHtml((r.text || '').substring(0, 200)) + (r.text && r.text.length > 200 ? '...' : '') + '</div>';
      html += '</div>';
    }
  }

  if (data.audit) {
    var a = data.audit;
    html += '<div class="ret-test-audit">';
    if (a.trigger_hyde) {
      html += '<div>HyDE 触发: <b style="color:var(--accent-green)">是</b></div>';
      if (a.hyde_doc) {
        html += '<div class="ret-hyde-doc">假设文档: ' + escHtml(a.hyde_doc.substring(0, 150)) + '</div>';
      }
    } else {
      html += '<div>HyDE 触发: 否</div>';
    }
    html += '<div>平均分: ' + (a.avg_score || 0).toFixed(3) + ' | 高/中/低: ' + (a.high || 0) + '/' + (a.medium || 0) + '/' + (a.low || 0) + '</div>';
    html += '</div>';
  }

  box.innerHTML = html;
}

// ===== 自动刷新 =====
function toggleRetrievalAutoRefresh() {
  _retrievalAutoRefresh = !_retrievalAutoRefresh;
  var btn = $('retrieval_autorefresh_btn');
  if (_retrievalAutoRefresh) {
    if (btn) btn.textContent = '停止自动刷新';
    _retrievalAutoRefreshTimer = setInterval(function() {
      refreshRetrievalAudit(false);
    }, 5000);
  } else {
    if (btn) btn.textContent = '自动刷新';
    if (_retrievalAutoRefreshTimer) {
      clearInterval(_retrievalAutoRefreshTimer);
      _retrievalAutoRefreshTimer = null;
    }
  }
}

// ===== 错误提示 =====
function showRetrievalError(msg) {
  var box = $('retrieval_audit_list');
  if (box) {
    box.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:20px;font-size:.8em">' +
      escHtml(msg || '加载失败') + '</div>';
  }
}

// ===== 打开/关闭检索调试模态 =====
function openRetrievalModal() {
  var modal = $('retrievalModal');
  if (!modal) return;
  modal.style.display = 'flex';
  refreshRetrievalAudit(true);
}

function closeRetrievalModal() {
  var modal = $('retrievalModal');
  if (!modal) return;
  modal.style.display = 'none';
  // 关闭时停止自动刷新
  if (_retrievalAutoRefresh) {
    toggleRetrievalAutoRefresh();
  }
}
