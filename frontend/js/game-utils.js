// 太虚幻境 — 工具函数 + 日志/回溯（从game.js拆分）

// [v10] escHtml / escAttr 已统一至 core.js，此处不再重复定义（L10c）

// escapeHtml 保留 \n→<br> 的显示语义，转义部分委托给统一的 escHtml（L10c）
function escapeHtml(text) {
  if (text == null) return '';
  return escHtml(text).replace(/\n/g, '<br>');
}

function buildMap() {
  var canvas = $('mapCanvas');
  if (!mapData || !mapData.locations || !mapData.locations.length) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:60px;font-size:1em">暂无地图数据<br><span style="font-size:.8em">世界生成时未包含地点信息</span></div>';
    return;
  }

  canvas.innerHTML = '';

  var locations = mapData.locations;
  var playerLoc = mapData.player_location || '';
  var npcLocs = mapData.npc_locations || {};
  var edges = mapData.edges || [];

  $('map_node_count').textContent = locations.length + ' 个地点';
  $('map_edge_count').textContent = (edges.length || (locations.length - 1)) + ' 条路径';

  // 如果没有连线，自动基于距离创建（如果有 map 数据）
  if (!edges.length && locations.length > 1) {
    for (var i = 0; i < locations.length; i++) {
      for (var j = i + 1; j < locations.length; j++) {
        edges.push({ source: locations[i].id, target: locations[j].id, distance: 50 });
      }
    }
  }

  // 构建 Cytoscape 元素
  var elements = [];
  var nodeIds = {};
  locations.forEach(function(loc) {
    var isPlayer = playerLoc && (loc.id === playerLoc || loc.name === playerLoc ||
      playerLoc.indexOf(loc.name) >= 0 || loc.name.indexOf(playerLoc) >= 0);
    var npcCount = 0;
    Object.keys(npcLocs).forEach(function(key) {
      if (key === loc.id || key === loc.name) npcCount += npcLocs[key].length;
    });

    var label = loc.name;
    if (npcCount > 0) label += '\n👤×' + npcCount;

    nodeIds[loc.id] = true;
    elements.push({
      data: {
        id: loc.id,
        label: label,
        isPlayer: isPlayer,
        npcCount: npcCount,
        description: loc.description || '',
        fullName: loc.name,
      }
    });
  });

  edges.forEach(function(e) {
    var src = e.source, tgt = e.target;
    // 确保两端节点都存在
    if (!nodeIds[src] && locations.find(function(l) { return l.name === src || l.id === src; })) {
      nodeIds[src] = true;
    }
    if (!nodeIds[tgt] && locations.find(function(l) { return l.name === tgt || l.id === tgt; })) {
      nodeIds[tgt] = true;
    }
    elements.push({
      data: {
        id: src + '_' + tgt,
        source: src,
        target: tgt,
        distance: e.distance || 50,
        label: '',
      }
    });
  });

  // 创建 Cytoscape 实例
  mapCy = cytoscape({
    container: canvas,
    elements: elements,
    style: [
      // 地点节点
      {
        selector: 'node',
        style: {
          'background-color': '#3a4a5a',
          'label': 'data(label)',
          'color': '#c8d6e5',
          'text-valign': 'bottom',
          'text-halign': 'center',
          'font-size': '13px',
          'font-weight': 'bold',
          'text-outline-color': '#0a0a0f',
          'text-outline-width': 2,
          'text-max-width': '100px',
          'text-wrap': 'wrap',
          'width': 50,
          'height': 50,
          'border-width': 2,
          'border-color': '#4a5a6a',
          'text-background-color': 'rgba(10,10,15,.7)',
          'text-background-opacity': 0.8,
          'text-background-padding': '3px',
          'text-background-shape': 'roundrectangle',
        }
      },
      // 玩家位置高亮
      {
        selector: 'node[isPlayer="true"]',
        style: {
          'background-color': '#f0c040',
          'border-color': '#f0c040',
          'border-width': 4,
          'width': 60,
          'height': 60,
          'color': '#f0c040',
          'font-size': '14px',
          'text-outline-color': '#0a0a0f',
          'text-outline-width': 3,
          // [Fix] Cytoscape.js 不支持 shadow-* 属性，改用 overlay 实现高亮光晕
          'overlay-color': '#f0c040',
          'overlay-padding': 8,
          'overlay-opacity': 0.3,
        }
      },
      // 有NPC的地点
      {
        selector: 'node[npcCount>0]',
        style: {
          'border-color': '#6ea9c9',
          'border-width': 2,
        }
      },
      // 连线
      {
        selector: 'edge',
        style: {
          'width': 2,
          'line-color': '#3a4a5a',
          'curve-style': 'haystack',
          'haystack-radius': 0.3,
          'label': 'data(label)',
          'font-size': '9px',
          'color': '#5a6a7a',
          'text-outline-color': '#0a0a0f',
          'text-outline-width': 1,
          'text-background-color': '#0a0a0f',
          'text-background-opacity': 0.6,
          'text-background-padding': '2px',
        }
      },
      // 以玩家为端点的连线
      {
        selector: 'edge[source="' + (locations.find(function(l) { return l.id === playerLoc || l.name === playerLoc || playerLoc.indexOf(l.name) >= 0 || l.name.indexOf(playerLoc) >= 0; }) || {}).id + '"], edge[target="' + (locations.find(function(l) { return l.id === playerLoc || l.name === playerLoc || playerLoc.indexOf(l.name) >= 0 || l.name.indexOf(playerLoc) >= 0; }) || {}).id + '"]',
        style: {
          'line-color': 'rgba(240,192,64,.4)',
          'width': 3,
        }
      },
    ],
    layout: {
      name: 'cose',
      idealEdgeLength: 180,
      nodeOverlap: 30,
      refresh: 20,
      randomize: false,
      componentSpacing: 60,
      nodeRepulsion: 8000,
      edgeElasticity: 100,
      nestingFactor: 1.2,
      gravity: 0.3,
      numIter: 2000,
      animate: true,
      animationDuration: 800,
    },
  });

  // 点击节点显示详情
  mapCy.on('tap', 'node', function(evt) {
    var node = evt.target;
    var data = node.data();
    $('mapDetail').style.display = 'block';
    $('mapDetailTitle').textContent = '📍 ' + data.fullName + (data.isPlayer ? ' （你在这里）' : '');
    $('mapDetailDesc').textContent = data.description || '暂无描述';
    // 显示该地点NPC
    var npcHere = [];
    if (npcLocs) {
      Object.keys(npcLocs).forEach(function(key) {
        if (key === data.id || key === data.fullName) {
          npcHere = npcLocs[key];
        }
      });
    }
    if (npcHere.length > 0) {
      $('mapDetailNpcs').innerHTML = '👥 NPC: ' + npcHere.map(function(n) {
        return '<span style="color:var(--gold)">' + n.name + '</span>' + (n.role ? '（' + n.role + '）' : '');
      }).join('、');
    } else {
      $('mapDetailNpcs').textContent = '';
    }
  });

  mapCy.on('tap', function(evt) {
    if (evt.target === mapCy) {
      $('mapDetail').style.display = 'none';
    }
  });

  // 自适应缩放
  setTimeout(function() {
    if (mapCy) { mapCy.resize(); mapCy.fit(undefined, 60); }
  }, 1000);
}

// [v11] 输出汇总：存储每次AI返回的原始输出（声明在 ui.js 中）

function openOutputLog() {
  try {
    var modal = document.getElementById('outputLogModal');
    var content = document.getElementById('outputLogContent');
    if (!modal || !content) {
      console.error('[CV] outputLogModal or outputLogContent not found');
      return;
    }
    if (outputLog.length === 0) {
      content.innerHTML = '<div style="color:var(--dim);text-align:center;padding:30px">暂无输出记录</div>';
    } else {
      var html = '';
      for (var i = outputLog.length - 1; i >= 0; i--) {
        var entry = outputLog[i];
        html += '<div style="margin-bottom:16px;padding:12px 14px;background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:8px">';
        html += '<div style="color:var(--gold);font-size:.82em;margin-bottom:6px">第' + (i + 1) + '轮 | ' + escHtml(entry.time) + ' | ' + escHtml(entry.input) + '</div>';
        html += '<div style="white-space:pre-wrap;color:var(--text)">' + escHtml(entry.narrative) + '</div>';
        if (entry.options && entry.options.length) {
          html += '<div style="margin-top:8px;color:var(--dim);font-size:.85em">选项: ' + entry.options.map(function(o) { return '[' + escHtml(o.id || '') + '] ' + escHtml(o.text || ''); }).join(' | ') + '</div>';
        }
        html += '<div style="margin-top:4px;color:var(--dim);font-size:.78em">叙事字数: ' + (entry.narrative ? entry.narrative.length : 0) + '</div>';
        html += '</div>';
      }
      content.innerHTML = html;
    }
    modal.style.display = 'flex';
    modal.classList.add('on');
  } catch(e) {
    console.error('[CV] openOutputLog error:', e);
  }
}

function closeOutputLog() {
  var m = document.getElementById('outputLogModal');
  if (m) { m.classList.remove('on'); m.style.display = ''; }
}

// [v11] 百世书回滚：存档选择
async function openRewindModal() {
  var modal = document.getElementById('rewindModal');
  var list = document.getElementById('rewindSlotList');
  if (!modal || !list) return;
  list.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载存档列表中...</div>';
  modal.style.display = 'flex';
  modal.classList.add('on');
  try {
    var d = await api('GET', '/api/slots');
    var slots = d.slots || [];
    if (slots.length === 0) {
      list.innerHTML = '<div style="color:var(--dim);text-align:center;padding:30px">暂无存档，无法回溯</div>';
      return;
    }
    // 按天数倒序排列
    slots.sort(function(a, b) { return (b.day || 0) - (a.day || 0); });
    list.innerHTML = slots.map(function(s) {
      var timeStr = s.created_at ? new Date(s.created_at).toLocaleString('zh-CN') : '';
      return '<div class="ocard" style="cursor:pointer;margin-bottom:8px" onclick="doRewind(\'' + escAttr(s.slot_id) + '\')">' +
        '<div style="display:flex;justify-content:space-between;align-items:center">' +
        '<div><div style="font-weight:600;color:var(--gold)">' + escHtml(s.name || '未命名') + '</div>' +
        '<div style="font-size:.82em;color:var(--dim);margin-top:4px">第' + (s.day || '?') + '天 · ' + escHtml(s.location || '未知') + ' · ' + escHtml(s.description || '') + '</div>' +
        '<div style="font-size:.75em;color:var(--dim);margin-top:2px">' + escHtml(timeStr) + '</div></div>' +
        '<div style="color:var(--accent-blue);font-size:.82em">选择此存档 →</div></div></div>';
    }).join('');
  } catch(e) {
    list.innerHTML = '<div style="color:var(--accent-red);text-align:center;padding:20px">加载失败: ' + escHtml(e.message) + '</div>';
  }
}

function closeRewindModal() {
  var m = document.getElementById('rewindModal');
  if (m) { m.classList.remove('on'); m.style.display = ''; }
}

async function doRewind(slotId) {
  if (!confirm('确定要回溯到这个存档吗？\n该存档之后的所有存档将被清除！')) return;
  closeRewindModal();
  addSystem('📜 百世书发动，时间正在回溯...');
  try {
    var d = await api('POST', '/api/hundred-book/rewind', { slot_id: slotId });
    if (d.error) { addNarrative('回溯失败: ' + d.error); return; }
    if (d.narrative) addSystem(d.narrative);
    if (d.pages_remaining !== undefined) addSystem('百世书剩余页数: ' + d.pages_remaining);
    if (d.state) {
      GS = d.state;
      updateStatus();
    }
    // 清空叙事并从历史重建
    var nb = $('nb');
    nb.innerHTML = '';
    if (GS && GS.narrative_history) {
      restoreHistory(GS.narrative_history);
    }
    // 恢复选项
    if (d.initial_options && d.initial_options.length) {
      showOpts(d.initial_options);
    }
    $('ot').textContent = '选择你的行动：';
  } catch(e) {
    addNarrative('回溯失败: ' + e.message);
  }
}

// [v11] 上下文衔接检测：检查玩家输入是否与最近叙事相关
function checkContextMismatch(input) {
  // 获取最近5条叙事文本
  var nb = $('nb');
  var narratives = nb.querySelectorAll('.ai-narrative');
  var recentText = '';
  var count = 0;
  for (var i = narratives.length - 1; i >= 0 && count < 5; i--) {
    var txt = narratives[i].textContent || '';
    if (txt && !txt.startsWith('>')) {
      recentText += txt;
      count++;
    }
  }
  if (!recentText || recentText.length < 50) return false;

  // 提取玩家输入中的中文关键词（2字以上）
  var inputKeywords = [];
  var inputMatch = input.match(/[\u4e00-\u9fa5]{2,}/g);
  if (inputMatch) inputKeywords = inputMatch;

  // 提取最近叙事中的中文关键词
  var narrativeKeywords = [];
  var narMatch = recentText.match(/[\u4e00-\u9fa5]{2,}/g);
  if (narMatch) narrativeKeywords = narMatch;

  if (inputKeywords.length === 0 || narrativeKeywords.length === 0) return false;

  // 检查是否有关键词重叠
  var narSet = {};
  narrativeKeywords.forEach(function(k) { narSet[k] = true; });
  var overlap = 0;
  inputKeywords.forEach(function(k) {
    if (narSet[k]) overlap++;
  });

  // 如果重叠率低于30%，认为可能不衔接（提高阈值减少误判）
  var overlapRatio = overlap / inputKeywords.length;
  return overlapRatio < 0.30;
}
