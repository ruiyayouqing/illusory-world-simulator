// 太虚幻境 v1.6 P1-4 — 势力图面板：GraphRAG 社区检测可视化
// 依赖：core.js (api, escHtml, escAttr), dom.js ($), cytoscape (全局)

// ===== 全局状态 =====
var _factionCy = null;             // cytoscape 实例
var _factionData = null;           // 最近一次拉取的数据
var _factionLastFetch = 0;         // 节流
var _factionMethod = 'louvain';    // 检测算法
var _factionActiveOnly = true;     // 仅基于有效关系
var _factionSelectedFaction = null; // 当前选中的势力ID

// ===== 主入口：刷新面板 =====
async function refreshFactionPanel(force) {
  var now = Date.now();
  if (!force && now - _factionLastFetch < 10000) return;
  _factionLastFetch = now;

  try {
    var url = '/api/faction-graph?method=' + encodeURIComponent(_factionMethod) +
              '&active_only=' + (_factionActiveOnly ? 'true' : 'false');
    var d = await api('GET', url);
    if (d && d.error) {
      console.warn('[Faction] fetch failed:', d.error);
      showFactionError(d.error);
      return;
    }
    _factionData = d;
    renderFactionGraph(d);
    renderFactionList(d);
    renderFactionStats(d);
  } catch (e) {
    console.warn('[Faction] refreshFactionPanel failed:', e);
    showFactionError(e.message || '加载失败');
  }
}

// ===== 渲染势力图（cytoscape） =====
function renderFactionGraph(data) {
  var canvas = $('faction_canvas');
  if (!canvas) return;

  var elems = data.elements || {};
  var nodes = elems.nodes || [];
  var edges = elems.edges || [];

  if (nodes.length === 0) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px;font-size:.8em">暂无实体数据，需先在游戏中产生叙事</div>';
    if (_factionCy) {
      _factionCy.destroy();
      _factionCy = null;
    }
    return;
  }

  // 准备 cytoscape elements
  var cyElements = [];
  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i].data;
    // 节点尺寸基于 mention 次数，首领更大
    var size = 18 + Math.min(20, (n.mentions || 1) * 2);
    if (n.is_leader) size += 8;
    cyElements.push({
      data: {
        id: n.id, label: n.label,
        type: n.type, mentions: n.mentions || 0,
        community: n.community || 'lone',
        color: n.color || '#8a7d6b',
        is_leader: n.is_leader ? 1 : 0,
        size: size,
      }
    });
  }
  for (var j = 0; j < edges.length; j++) {
    var e = edges[j].data;
    cyElements.push({
      data: {
        source: e.source, target: e.target,
        label: e.label, color: e.color || 'rgba(212,175,55,0.4)',
        internal: e.internal ? 1 : 0,
      }
    });
  }

  // 销毁旧实例
  if (_factionCy) {
    _factionCy.destroy();
  }

  _factionCy = cytoscape({
    container: canvas,
    elements: cyElements,
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(color)',
          'label': 'data(label)',
          'color': '#e8e0d0',
          'font-size': '10px',
          'width': 'data(size)',
          'height': 'data(size)',
          'text-valign': 'bottom',
          'text-margin-y': 4,
          'text-wrap': 'ellipsis',
          'text-max-width': '70px',
          'border-width': 2,
          'border-color': 'rgba(212,175,55,0.4)',
          'transition-property': 'background-color, border-width',
          'transition-duration': '0.2s',
        }
      },
      {
        selector: 'node[is_leader = 1]',
        style: {
          'shape': 'diamond',
          'border-color': '#d4af37',
          'border-width': 3,
          'font-weight': 'bold',
        }
      },
      {
        selector: 'node[type = "person"]',
        style: {
          'shape': 'ellipse',
        }
      },
      {
        selector: 'node[type = "place"]',
        style: {
          'shape': 'round-rectangle',
        }
      },
      {
        selector: 'node[type = "org"]',
        style: {
          'shape': 'hexagon',
        }
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.7,
          'line-color': 'data(color)',
          'target-arrow-color': 'data(color)',
          'label': 'data(label)',
          'font-size': '7px',
          'color': 'var(--dim)',
          'text-rotation': 'autorotate',
          'width': 1.2,
          'opacity': 0.7,
        }
      },
      {
        selector: 'edge[internal = 0]',
        style: {
          'line-style': 'dashed',
        }
      },
      {
        selector: '.faded',
        style: {
          'opacity': 0.15,
        }
      },
      {
        selector: '.highlighted',
        style: {
          'border-width': 4,
          'border-color': '#f0d060',
          'opacity': 1,
        }
      }
    ],
    layout: {
      name: 'cose',
      animate: true,
      animationDuration: 400,
      padding: 10,
      nodeRepulsion: function(node) { return 6000; },
      idealEdgeLength: function(edge) { return 60; },
      nodeOverlap: 12,
      randomize: false,
      componentSpacing: 40,
    }
  });

  // 点击节点：高亮该节点所在势力
  _factionCy.on('tap', 'node', function(evt) {
    var nodeData = evt.target.data();
    highlightFaction(nodeData.community);
  });

  // 点击空白：清除高亮
  _factionCy.on('tap', function(evt) {
    if (evt.target === _factionCy) {
      clearFactionHighlight();
    }
  });
}

// ===== 高亮某势力 =====
function highlightFaction(communityId) {
  if (!_factionCy) return;
  _factionSelectedFaction = communityId;
  _factionCy.elements().removeClass('faded highlighted');
  if (communityId === 'lone' || !communityId) {
    return;
  }
  _factionCy.nodes().filter(function(n) {
    return n.data('community') !== communityId;
  }).addClass('faded');
  _factionCy.nodes().filter(function(n) {
    return n.data('community') === communityId;
  }).addClass('highlighted');
  // 同社区边保持原色，跨社区边淡化
  _factionCy.edges().filter(function(e) {
    return e.data('internal') !== 1;
  }).addClass('faded');
}

function clearFactionHighlight() {
  if (!_factionCy) return;
  _factionSelectedFaction = null;
  _factionCy.elements().removeClass('faded highlighted');
}

// ===== 渲染势力列表 =====
function renderFactionList(data) {
  var box = $('faction_list');
  if (!box) return;

  var communities = data.communities || [];
  var loneCount = (data.lone_entities || []).length;

  if (communities.length === 0) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.8em">尚未识别出势力</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < communities.length; i++) {
    var c = communities[i];
    var selected = (_factionSelectedFaction === c.id) ? ' fac-selected' : '';
    html += '<div class="fac-item' + selected + '" onclick="selectFaction(\'' + escAttr(c.id) + '\')" style="border-left:3px solid ' + c.color + '">';
    html += '<div class="fac-head">';
    html += '<span class="fac-name" style="color:' + c.color + '">' + escHtml(c.id) + '</span>';
    html += '<span class="fac-size">' + c.size + '人</span>';
    html += '</div>';
    if (c.leader) {
      html += '<div class="fac-leader">⚑ 首领: ' + escHtml(c.leader) + '</div>';
    }
    if (c.cohesion != null) {
      var cohPct = Math.round((c.cohesion || 0) * 100);
      html += '<div class="fac-coh">凝聚度: ' + cohPct + '%</div>';
    }
    // 成员预览（最多显示5个）
    var members = c.members || [];
    var preview = members.slice(0, 5).join('、');
    if (members.length > 5) preview += ' 等';
    html += '<div class="fac-members">' + escHtml(preview) + '</div>';
    html += '</div>';
  }

  if (loneCount > 0) {
    html += '<div class="fac-lone" onclick="selectFaction(\'lone\')">';
    html += '<span>散人 ' + loneCount + ' 人</span>';
    html += '</div>';
  }

  box.innerHTML = html;
}

// ===== 渲染统计 =====
function renderFactionStats(data) {
  var box = $('faction_stats');
  if (!box) return;

  var stats = data.stats || {};
  var html = '<div class="fac-stats">';
  html += '<span>势力数: <b>' + (stats.community_count || 0) + '</b></span>';
  html += '<span>实体: <b>' + (stats.total_entities || 0) + '</b></span>';
  html += '<span>关系: <b>' + (stats.total_relations || 0) + '</b></span>';
  if (stats.modularity != null) {
    var modPct = Math.round((stats.modularity || 0) * 100);
    html += '<span title="模块度，越高表示势力划分越明显">模块度: <b>' + modPct + '%</b></span>';
  }
  html += '</div>';
  box.innerHTML = html;
}

// ===== 势力选择回调 =====
function selectFaction(factionId) {
  highlightFaction(factionId);
  // 更新列表选中态
  var items = document.querySelectorAll('.fac-item');
  for (var i = 0; i < items.length; i++) {
    items[i].classList.remove('fac-selected');
  }
  // 重新渲染列表以更新选中态
  if (_factionData) renderFactionList(_factionData);
}

// ===== 算法切换 =====
function onFactionMethodChange() {
  var sel = $('faction_method');
  if (!sel) return;
  _factionMethod = sel.value;
  refreshFactionPanel(true);
}

// ===== 切换仅显示有效关系 =====
function onFactionActiveOnlyChange() {
  var cb = $('faction_active_only');
  if (!cb) return;
  _factionActiveOnly = cb.checked;
  refreshFactionPanel(true);
}

// ===== 错误提示 =====
function showFactionError(msg) {
  var canvas = $('faction_canvas');
  if (canvas) {
    canvas.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:20px;font-size:.8em">' +
      escHtml(msg || '加载失败') + '</div>';
  }
  if (_factionCy) {
    _factionCy.destroy();
    _factionCy = null;
  }
}

// ===== 打开/关闭势力图模态 =====
function openFactionModal() {
  var modal = $('factionModal');
  if (!modal) return;
  modal.style.display = 'flex';
  // 打开时立即刷新
  refreshFactionPanel(true);
}

function closeFactionModal() {
  var modal = $('factionModal');
  if (!modal) return;
  modal.style.display = 'none';
}
