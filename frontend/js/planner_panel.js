// 太虚幻境 v1.6 — 思维树面板：NPC BranchPlanner 分支规划可视化
// 依赖：core.js (api, escHtml), dom.js ($), cytoscape (全局)

// ===== 全局状态 =====
var _plannerTreeCy = null;       // cytoscape 实例
var _plannerRecentPlans = [];    // 最近规划列表
var _plannerSelectedNpc = "";    // 当前选中的 NPC ID
var _plannerLastFetch = 0;       // 节流
var _preplanPolling = false;     // 预规划轮询中标志，防止重复触发

// ===== 英文→中文翻译映射（思维树面板专用） =====
var _PLANNER_I18N = {
  branch_type: {
    'survival': '生存',
    'social': '社交',
    'career': '职业',
    'exploration': '探索'
  },
  search_mode: {
    'tot': '思维树搜索',
    'fallback': '兜底规划',
    'perception_skip': '视野跳过',
    'legacy': '旧版规划'
  },
  node_type: {
    'root': '根节点',
    'branch': '分支',
    'action': '行动',
    'pruned': '已剪枝',
    'node': '节点'
  },
  action_type: {
    'work': '工作',
    'rest': '休息',
    'social': '社交',
    'travel': '移动',
    'explore': '探索',
    'trade': '交易',
    'study': '学习',
    'craft': '制作'
  }
};
function _trBranchType(t) { return _PLANNER_I18N.branch_type[t] || t || ''; }
function _trSearchMode(m) { return _PLANNER_I18N.search_mode[m] || m || ''; }
function _trNodeType(t) { return _PLANNER_I18N.node_type[t] || t || '节点'; }
function _trActionType(t) { return _PLANNER_I18N.action_type[t] || t || ''; }

// ===== 主入口：刷新面板 =====
async function refreshPlannerPanel() {
  var now = Date.now();
  if (now - _plannerLastFetch < 5000) return;
  _plannerLastFetch = now;

  try {
    var d = await api('GET', '/api/planner/recent?limit=15');
    if (d && d.error) {
      console.warn('[Planner] fetch failed:', d.error);
      return;
    }
    _plannerRecentPlans = (d && d.plans) ? d.plans : [];

    // 更新 NPC 下拉框
    updatePlannerNpcSelect();

    // 更新状态文字
    var statusEl = $('planner_status');
    if (statusEl) {
      statusEl.textContent = _plannerRecentPlans.length
        ? '(' + _plannerRecentPlans.length + ' 条记录)'
        : '';
    }

    // 如果已选中 NPC，刷新其思维树
    if (_plannerSelectedNpc) {
      loadThoughtTree(_plannerSelectedNpc);
    }
  } catch (e) {
    console.warn('[Planner] refreshPlannerPanel failed:', e);
  }
}

// ===== 更新 NPC 下拉框 =====
function updatePlannerNpcSelect() {
  var sel = $('planner_npc_select');
  if (!sel) return;

  // 提取有规划记录的 NPC（去重）
  var npcMap = {};
  for (var i = 0; i < _plannerRecentPlans.length; i++) {
    var p = _plannerRecentPlans[i];
    if (p.agent_id && p.npc_name) {
      npcMap[p.agent_id] = p.npc_name;
    }
  }

  var html = '<option value="">选择 NPC...</option>';
  for (var id in npcMap) {
    var selected = (id === _plannerSelectedNpc) ? ' selected' : '';
    html += '<option value="' + escAttr(id) + '"' + selected + '>' + escHtml(npcMap[id]) + '</option>';
  }
  sel.innerHTML = html;
}

// ===== NPC 选择回调 =====
function onPlannerNpcSelect() {
  var sel = $('planner_npc_select');
  if (!sel) return;
  _plannerSelectedNpc = sel.value;
  if (_plannerSelectedNpc) {
    loadThoughtTree(_plannerSelectedNpc);
  } else {
    clearThoughtTree();
  }
}

// ===== 加载思维树 =====
async function loadThoughtTree(npcId) {
  try {
    var d = await api('GET', '/api/planner/thought-tree/' + encodeURIComponent(npcId));
    if (d && d.error) {
      showPlannerError(d.error);
      return;
    }
    renderThoughtTree(d);
    renderPlannerDetail(d.plan, npcId);
  } catch (e) {
    showPlannerError(e.message);
  }
}

// ===== 渲染思维树（cytoscape） =====
function renderThoughtTree(data) {
  var canvas = $('planner_tree_canvas');
  if (!canvas) return;

  var nodes = (data.elements && data.elements.nodes) ? data.elements.nodes : [];
  var edges = (data.elements && data.elements.edges) ? data.elements.edges : [];

  if (nodes.length === 0) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px;font-size:.8em">该 NPC 暂无规划记录</div>';
    if (_plannerTreeCy) {
      _plannerTreeCy.destroy();
      _plannerTreeCy = null;
    }
    return;
  }

  // 准备 cytoscape elements
  var cyElements = [];
  for (var i = 0; i < nodes.length; i++) {
    var nd = nodes[i].data;
    var label = nd.label;
    // 分支节点 label 为英文 branch_type，翻译为中文
    if (nd.type === 'branch') {
      label = _trBranchType(label);
    }
    cyElements.push({ data: Object.assign({}, nd, { label: label }) });
  }
  for (var j = 0; j < edges.length; j++) {
    cyElements.push({ data: edges[j].data });
  }

  // 销毁旧实例
  if (_plannerTreeCy) {
    _plannerTreeCy.destroy();
  }

  _plannerTreeCy = cytoscape({
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
          'text-max-width': '60px',
          'border-width': 2,
          'border-color': 'rgba(212,175,55,0.3)',
        }
      },
      {
        selector: 'node[type="root"]',
        style: {
          'shape': 'diamond',
          'border-color': '#d4af37',
          'border-width': 3,
        }
      },
      {
        selector: 'node[type="action"]',
        style: {
          'shape': 'round-rectangle',
        }
      },
      {
        selector: 'node[type="pruned"]',
        style: {
          'opacity': 0.5,
          'line-style': 'dashed',
        }
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.8,
          'line-color': 'rgba(212,175,55,0.3)',
          'target-arrow-color': 'rgba(212,175,55,0.4)',
          'label': 'data(label)',
          'font-size': '8px',
          'color': 'var(--dim)',
          'text-rotation': 'autorotate',
          'width': 1.5,
        }
      }
    ],
    layout: {
      name: 'breadthfirst',
      directed: true,
      padding: 8,
      spacingFactor: 0.8,
      circle: false,
      roots: '#root',
    }
  });

  // 点击节点显示详情
  _plannerTreeCy.on('tap', 'node', function(evt) {
    var nodeData = evt.target.data();
    showPlannerNodeDetail(nodeData);
  });
}

// ===== 渲染详情面板 =====
function renderPlannerDetail(plan, npcId) {
  var box = $('planner_detail');
  if (!box) return;

  if (!plan) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.8em">无规划数据</div>';
    return;
  }

  var html = '<div class="pd-plan">';
  html += '<div class="pd-header">';
  html += '<span class="pd-mode">' + escHtml(_trSearchMode(plan.search_mode || 'tot')) + '</span>';
  html += '<span class="pd-score">分数: ' + (plan.score || 0).toFixed(3) + '</span>';
  html += '<span class="pd-attempts">尝试: ' + (plan.attempts || 1) + '</span>';
  if (plan.replan_count > 0) {
    html += '<span class="pd-replan">重规划: ' + plan.replan_count + '</span>';
  }
  html += '</div>';

  // 选中分支
  if (plan.selected_branch) {
    var sb = plan.selected_branch;
    html += '<div class="pd-branch pd-selected">';
    html += '<div class="pd-branch-head">✓ ' + escHtml(_trBranchType(sb.branch_type)) + ' — ' + escHtml(sb.objective) + '</div>';
    if (sb.actions && sb.actions.length > 0) {
      html += '<div class="pd-actions">';
      for (var i = 0; i < sb.actions.length; i++) {
        var a = sb.actions[i];
        html += '<div class="pd-action">▸ ' + escHtml(a.detail || _trActionType(a.type) || '');
        if (a.energy_cost) html += ' <span class="pd-cost">体力 ' + a.energy_cost + '</span>';
        html += '</div>';
      }
      html += '</div>';
    }
    html += '</div>';
  }

  // 所有分支评分
  if (plan.all_branches && plan.all_branches.length > 1) {
    html += '<div class="pd-all-branches">所有分支：';
    for (var j = 0; j < plan.all_branches.length; j++) {
      var b = plan.all_branches[j];
      var isSel = (plan.selected_branch && b.branch_type === plan.selected_branch.branch_type);
      html += '<span class="pd-branch-tag' + (isSel ? ' pd-tag-selected' : '') + '">';
      html += escHtml(_trBranchType(b.branch_type)) + '(' + (b.score || 0).toFixed(2) + ')';
      html += '</span>';
    }
    html += '</div>';
  }

  // 剪枝信息
  if (plan.pruned_branches && plan.pruned_branches.length > 0) {
    html += '<div class="pd-pruned">剪枝 ' + plan.pruned_branches.length + ' 个分支</div>';
  }

  html += '</div>';
  box.innerHTML = html;
}

// ===== 显示节点详情 =====
function showPlannerNodeDetail(data) {
  var box = $('planner_detail');
  if (!box) return;

  var html = '<div class="pd-node-detail">';
  html += '<div class="pd-node-type">' + escHtml(_trNodeType(data.type)) + '</div>';
  if (data.objective) {
    html += '<div class="pd-node-obj">' + escHtml(data.objective) + '</div>';
  }
  if (data.score != null) {
    html += '<div class="pd-node-score">分数: ' + data.score + '</div>';
  }
  html += '</div>';
  box.innerHTML = html;
}

// ===== 清空思维树 =====
function clearThoughtTree() {
  var canvas = $('planner_tree_canvas');
  if (canvas) {
    canvas.innerHTML = '';
  }
  if (_plannerTreeCy) {
    _plannerTreeCy.destroy();
    _plannerTreeCy = null;
  }
  var box = $('planner_detail');
  if (box) {
    box.innerHTML = '<div style="color:var(--dim);text-align:center;padding:14px;font-size:1em;line-height:1.6">请先点击"预规划"，稍等片刻后可选择 NPC 查看其思维树</div>';
  }
}

// ===== 显示错误 =====
function showPlannerError(msg) {
  var box = $('planner_detail');
  if (box) {
    box.innerHTML = '<div style="color:var(--danger);text-align:center;padding:10px;font-size:.8em">加载失败: ' + escHtml(msg) + '</div>';
  }
}

// ===== 触发异步预规划 =====
async function triggerPreplan() {
  if (_preplanPolling) {
    toast('预规划正在进行中，请稍候', 'info');
    return;
  }
  var btn = document.querySelector('.planner-preplan-btn');
  var statusEl = $('planner_status');
  var originalBtnText = btn ? btn.textContent : '';

  // [Bug] 立即置位 + 禁用按钮，防止 await api() 期间用户连点
  // 触发多个并发 preplan 请求（与 doWhispers 连点是同款问题）
  _preplanPolling = true;
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.6';
    btn.style.pointerEvents = 'none';
    btn.textContent = '⏳ 启动中...';
  }

  // 记录启动前已有的 NPC agent_id 集合，用于判断新增数量
  var existingIds = {};
  for (var i = 0; i < _plannerRecentPlans.length; i++) {
    var p = _plannerRecentPlans[i];
    if (p.agent_id) existingIds[p.agent_id] = true;
  }

  // 请求失败/异常时统一恢复按钮和标志位
  var resetBtnAndFlag = function() {
    _preplanPolling = false;
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '';
      btn.style.pointerEvents = '';
      btn.textContent = originalBtnText;
    }
  };

  try {
    var d = await api('POST', '/api/planner/preplan');
    if (d && d.error) {
      toast('预规划失败: ' + d.error, 'error');
      resetBtnAndFlag();
      return;
    }
    if (d.status === 'started') {
      var expectedCount = d.npc_count || 0;
      if (btn) btn.textContent = '⏳ 预规划中...';
      if (statusEl) {
        statusEl.textContent = '预规划中 0/' + expectedCount;
      }
      toast('已启动 ' + expectedCount + ' 个 NPC 的异步预规划，请稍候', 'info');

      // 轮询：每 5 秒刷新一次，最多 3 分钟（36 次）
      var maxAttempts = 36;
      var intervalMs = 5000;
      var attempts = 0;

      var pollTimer = setInterval(async function() {
        attempts++;
        try {
          var r = await api('GET', '/api/planner/recent?limit=30');
          if (r && r.plans) {
            _plannerRecentPlans = r.plans;
            // 实时更新下拉框，让已完成的 NPC 立即可选
            updatePlannerNpcSelect();

            // 计算新增的 NPC 数量（本次预规划新产生的）
            var newCount = 0;
            for (var j = 0; j < r.plans.length; j++) {
              var aid = r.plans[j].agent_id;
              if (aid && !existingIds[aid]) {
                newCount++;
              }
            }
            if (statusEl) {
              statusEl.textContent = '预规划中 ' + newCount + '/' + expectedCount;
            }

            // 完成判断：新增数量达到预期
            if (newCount >= expectedCount) {
              clearInterval(pollTimer);
              _preplanPolling = false;
              if (btn) {
                btn.disabled = false;
                btn.style.opacity = '';
                btn.style.pointerEvents = '';
                btn.textContent = originalBtnText;
              }
              statusEl.textContent = '(' + r.plans.length + ' 条记录)';
              toast('预规划完成，可选择 NPC 查看其思维树', 'success');
              if (_plannerSelectedNpc) {
                loadThoughtTree(_plannerSelectedNpc);
              }
              return;
            }
            // 超时处理
            if (attempts >= maxAttempts) {
              clearInterval(pollTimer);
              _preplanPolling = false;
              if (btn) {
                btn.disabled = false;
                btn.style.opacity = '';
                btn.style.pointerEvents = '';
                btn.textContent = originalBtnText;
              }
              statusEl.textContent = '(' + r.plans.length + ' 条记录)';
              if (newCount > 0) {
                toast('预规划已部分完成（' + newCount + '/' + expectedCount + '），可选择 NPC', 'info');
              } else {
                toast('预规划仍在后台进行，请稍后点击刷新查看', 'info');
              }
              return;
            }
          }
        } catch (e) {
          console.warn('[Planner] poll failed:', e);
        }
      }, intervalMs);
    } else if (d.status === 'no_active_npcs' || d.status === 'no_npcs') {
      toast('当前无活跃 NPC', 'info');
      resetBtnAndFlag();
    } else {
      // 未知状态，兜底恢复
      resetBtnAndFlag();
    }
  } catch (e) {
    toast('预规划请求失败: ' + e.message, 'error');
    resetBtnAndFlag();
  }
}
