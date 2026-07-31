// [v1.3] 因果链可视化模块
// 依赖: cytoscape.js (已在 index.html 中全局引入)
// API:
//   GET /api/causal-graph           → { elements: [...], count, min_importance }
//   GET /api/causal-graph/stats     → { total, min_importance, max_importance, avg_importance, by_event, by_day, ... }
//   POST /api/causal-graph/clear    → { success, cleared }

var causalCy = null;

function openCausalGraph() {
  $('causalGraphModal').classList.add('on');
  loadCausalGraph();
}

function closeCausalGraph() {
  $('causalGraphModal').classList.remove('on');
  if (causalCy) {
    try { causalCy.destroy(); } catch (e) {}
    causalCy = null;
  }
  $('causalDetail').style.display = 'none';
}

async function loadCausalGraph() {
  var canvas = $('causalCanvas');
  if (!canvas) return;
  canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载中...</div>';
  $('causalStats').innerHTML = '';
  $('causalDetail').style.display = 'none';

  try {
    // 并行加载图数据和统计
    var [graphRes, statsRes] = await Promise.all([
      api('GET', '/api/causal-graph'),
      api('GET', '/api/causal-graph/stats')
    ]);

    if (graphRes.error) {
      canvas.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:40px">' + escHtml(graphRes.error) + '</div>';
      return;
    }

    renderCausalStats(statsRes);
    renderCausalGraph(graphRes);
  } catch (e) {
    canvas.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:40px">加载失败: ' + escHtml(e.message || '') + '</div>';
  }
}

function renderCausalStats(s) {
  if (!s || s.error) {
    $('causalStats').innerHTML = '';
    return;
  }
  var html = '<span>📊 节点: <b style="color:var(--gold)">' + (s.total || 0) + '</b></span>' +
    '<span>重要性阈值: <b style="color:var(--gold)">' + (s.min_importance || 0) + '</b></span>' +
    '<span>最高: <b style="color:#d44">' + (s.max_importance || 0) + '</b></span>' +
    '<span>平均: <b style="color:#fa4">' + (s.avg_importance || 0) + '</b></span>';
  if (s.earliest_turn && s.latest_turn) {
    html += '<span>回合范围: T' + s.earliest_turn + ' → T' + s.latest_turn + '</span>';
  }
  if (s.by_event && Object.keys(s.by_event).length > 0) {
    var eventLabels = {
      'butterfly_effect': '蝴蝶效应',
      'personality_shift': '性格转折',
      'foreshadow': '伏笔',
      'novel_divergence': '小说偏离'
    };
    var eventParts = [];
    for (var k in s.by_event) {
      var label = eventLabels[k] || k;
      eventParts.push(label + '×' + s.by_event[k]);
    }
    html += '<span>事件: ' + eventParts.join(' · ') + '</span>';
  }
  $('causalStats').innerHTML = html;
}

function renderCausalGraph(d) {
  var canvas = $('causalCanvas');
  canvas.innerHTML = '';

  var elements = d.elements || [];
  if (elements.length === 0) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:60px;line-height:1.8">' +
      '<div style="font-size:2em;margin-bottom:10px">📭</div>' +
      '<div>暂无因果节点</div>' +
      '<div style="font-size:.85em;margin-top:6px">玩家做出重要决策后，会自动记录到这里（阈值 ' + (d.min_importance || 6) + '）</div>' +
      '</div>';
    return;
  }

  causalCy = cytoscape({
    container: canvas,
    elements: elements,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(color)',
          'label': 'data(label)',
          'color': '#e0d5c1',
          'text-valign': 'center',
          'text-halign': 'center',
          'font-size': '10px',
          'width': 'data(size)',
          'height': 'data(size)',
          'border-width': 2,
          'border-color': '#2a1a0a',
          'text-wrap': 'wrap',
          'text-max-width': '60px'
        }
      },
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#7a6b5a',
          'target-arrow-color': '#7a6b5a',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          'arrow-scale': 1.2
        }
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 4,
          'border-color': '#c9a96e'
        }
      }
    ],
    layout: {
      name: 'cose',
      idealEdgeLength: 100,
      nodeOverlap: 20,
      refresh: 20,
      randomize: true,
      componentSpacing: 40,
      nodeRepulsion: 8000,
      edgeElasticity: 100,
      nestingFactor: 1.2,
      gravity: 0.3,
      animate: false
    }
  });

  // 点击节点显示详情
  causalCy.on('tap', 'node', function (evt) {
    var node = evt.target;
    showCausalNodeDetail(node.data());
  });

  // 鼠标悬停高亮
  causalCy.on('mouseover', 'node', function (evt) {
    evt.target.style('opacity', 1.0);
  });
  causalCy.on('mouseout', 'node', function (evt) {
    evt.target.style('opacity', 0.9);
  });
}

function showCausalNodeDetail(data) {
  var panel = $('causalDetail');
  var content = $('causalDetailContent');
  if (!panel || !content) return;

  var eventLabels = {
    'butterfly_effect': '🦋 蝴蝶效应',
    'personality_shift': '💔 性格转折',
    'foreshadow': '🔮 伏笔',
    'novel_divergence': '🌐 小说偏离'
  };

  var eventsHtml = '';
  if (data.triggered_events && data.triggered_events.length > 0) {
    eventsHtml = '<div style="margin:8px 0">' +
      data.triggered_events.map(function (ev) {
        return '<span style="display:inline-block;padding:2px 8px;margin:2px;background:rgba(212,175,55,.15);border:1px solid rgba(212,175,55,.3);border-radius:10px;font-size:.8em">' +
          (eventLabels[ev] || ev) + '</span>';
      }).join('') + '</div>';
  }

  var importanceColor = data.importance >= 10 ? '#d44' : data.importance >= 8 ? '#fa4' : '#4af';
  var importanceBar = '<div style="background:var(--bg-deep);height:6px;border-radius:3px;margin:6px 0;overflow:hidden">' +
    '<div style="width:' + Math.min(100, data.importance * 100 / 15) + '%;height:100%;background:' + importanceColor + '"></div></div>';

  var divergenceHtml = '';
  if (data.novel_divergence && data.novel_divergence > 0) {
    divergenceHtml = '<div style="margin:6px 0;color:#aaf">🌐 偏离度: ' + data.novel_divergence + '</div>';
  }

  var html = '<div style="border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:8px">' +
    '<div style="color:var(--gold);font-weight:600;font-size:1.05em">回合 T' + data.turn_id + '</div>' +
    '<div style="color:var(--dim);font-size:.85em">第 ' + data.day + ' 天</div>' +
    '</div>' +
    '<div style="margin:8px 0">' +
    '<div style="color:var(--dim);font-size:.8em;margin-bottom:2px">重要性</div>' +
    '<div style="color:' + importanceColor + ';font-weight:600">' + data.importance + ' / 15</div>' +
    importanceBar +
    '</div>' +
    eventsHtml +
    divergenceHtml +
    '<div style="margin:10px 0">' +
    '<div style="color:var(--dim);font-size:.8em;margin-bottom:4px">玩家输入</div>' +
    '<div style="background:var(--bg-deep);padding:8px 10px;border-radius:6px;font-size:.85em;line-height:1.5;max-height:80px;overflow-y:auto">' + escHtml(data.player_input || '(无)') + '</div>' +
    '</div>' +
    '<div style="margin:10px 0">' +
    '<div style="color:var(--dim);font-size:.8em;margin-bottom:4px">AI 叙事</div>' +
    '<div style="background:var(--bg-deep);padding:8px 10px;border-radius:6px;font-size:.85em;line-height:1.5;max-height:100px;overflow-y:auto">' + escHtml(data.narrative || '(无)') + '</div>' +
    '</div>' +
    '<div style="margin:10px 0">' +
    '<div style="color:var(--dim);font-size:.8em;margin-bottom:4px">后果摘要</div>' +
    '<div style="background:var(--bg-deep);padding:8px 10px;border-radius:6px;font-size:.85em;line-height:1.5">' + escHtml(data.effects || '(无)') + '</div>' +
    '</div>';

  content.innerHTML = html;
  panel.style.display = 'block';
}

async function clearCausalGraph() {
  if (!confirm('确定要清空所有因果链节点吗？此操作不可撤销。')) return;
  try {
    var res = await api('POST', '/api/causal-graph/clear');
    if (res.error) {
      alert('清空失败: ' + res.error);
      return;
    }
    // 兼容 Alpine store 的 toast
    try {
      if (window.Alpine && Alpine.store('app')) {
        Alpine.store('app').showToast('已清空 ' + res.cleared + ' 个节点', 'success');
      }
    } catch (e) {}
    loadCausalGraph();
  } catch (e) {
    alert('清空失败: ' + (e.message || ''));
  }
}
