// 太虚幻境 — 游戏面板功能（从game.js拆分）

// ===== 世界新闻系统 =====
var newsItems = [];
var newsExpanded = false;

function addNews(items) {
  if (!items || !items.length) return;
  var now = Date.now();
  items.forEach(function(item) {
    var dup = newsItems.find(function(n) { return n.description === item.description; });
    if (dup) return;
    newsItems.push({
      id: 'n' + now + '_' + Math.random().toString(36).substr(2, 5),
      description: item.description || item.text || '',
      type: item.type || 'event',
      day: item.day || (GS ? GS.day : '?'),
      time: item.time || new Date().toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}),
    });
  });
  newsItems.sort(function(a, b) { return (b.day || 0) - (a.day || 0) || b.id.localeCompare(a.id); });
  if (newsItems.length > 200) newsItems = newsItems.slice(0, 200);
  saveNewsToLocal();
  renderNews();
}

function renderNews() {
  var feed = $('news_feed');
  var count = $('news_count');
  var expand = $('news_expand');
  if (!feed) return;

  var total = newsItems.length;
  count.textContent = total > 0 ? '(' + total + '条)' : '';

  if (total === 0) {
    feed.innerHTML = '<div style="color:var(--dim);text-align:center;padding:10px;font-size:.78em">暂无新闻</div>';
    expand.style.display = 'none';
    return;
  }

  var visibleCount = newsExpanded ? total : Math.min(20, total);
  var visible = newsItems.slice(0, visibleCount);

  var typeLabels = { death: '💀', birth: '👶', marriage: '💒', event: '📌', war: '⚔️', economy: '💰' };
  var typeClasses = { death: 'death', birth: 'birth', marriage: 'marriage', event: '', war: 'death', economy: '' };

  feed.innerHTML = visible.map(function(n) {
    var label = typeLabels[n.type] || '📌';
    var cls = typeClasses[n.type] || '';
    return '<div class="news-item ' + cls + '">' +
      '<div class="ndate">' + label + ' 第' + escHtml(n.day) + '天</div>' +
      escHtml(n.description) +
    '</div>';
  }).join('');

  if (total > 20) {
    expand.style.display = 'block';
    expand.textContent = newsExpanded
      ? '收起 ▲（显示最新20条）'
      : '查看更多历史新闻 ▼（共' + total + '条，显示最新20条）';
  } else {
    expand.style.display = 'none';
  }
}

function toggleNewsExpand() {
  newsExpanded = !newsExpanded;
  renderNews();
  // 展开后滚动新闻框到底部查看最新
  if (!newsExpanded) {
    $('news_feed').scrollTop = 0;
  }
}

function clearNews() {
  if (GS && GS.world_id) {
    localStorage.removeItem('cv_news_' + GS.world_id);
  }
  newsItems = [];
  newsExpanded = false;
  renderNews();
}

function saveNewsToLocal() {
  try {
    if (GS && GS.world_id) {
      localStorage.setItem('cv_news_' + GS.world_id, JSON.stringify(newsItems));
    }
  } catch(e) {
    // [Bug] localStorage 配额溢出时（QuotaExceededError），清理最旧世界的新闻后重试
    if (e && (e.name === 'QuotaExceededError' || e.code === 22)) {
      try {
        // 找到所有 cv_news_ 开头的 key，按最后修改时间排序，删除最旧的几个
        var keys = [];
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.indexOf('cv_news_') === 0) {
            keys.push(k);
          }
        }
        // 删除一半最旧的 key（localStorage 没有时间戳，按 key 顺序删）
        var toRemove = Math.ceil(keys.length / 2);
        for (var j = 0; j < toRemove && j < keys.length; j++) {
          if (keys[j] !== 'cv_news_' + GS.world_id) {
            localStorage.removeItem(keys[j]);
          }
        }
        // 重试保存当前世界新闻
        try {
          localStorage.setItem('cv_news_' + GS.world_id, JSON.stringify(newsItems));
          console.warn('[CV] localStorage was full, cleaned old world news and retried');
        } catch(e2) {
          console.warn('[CV] localStorage still full after cleanup, news not saved');
        }
      } catch(e3) {}
    }
  }
}

function loadNewsFromLocal(worldId) {
  try {
    var saved = localStorage.getItem('cv_news_' + worldId);
    if (saved) {
      newsItems = JSON.parse(saved);
      renderNews();
    }
  } catch(e) {}
}

function toggleAllRels() {
  const el = $('allRels');
  if (!el) return;
  const isHidden = el.style.display === 'none';
  el.style.display = isHidden ? 'block' : 'none';
  const trigger = el.previousElementSibling;
  if (trigger) {
    trigger.textContent = trigger.textContent.replace(/[▼▲]/, isHidden ? '▲' : '▼');
  }
}

async function confirmSuicide() {
  var nb = $('nb');
  var btns = nb.querySelectorAll('button');
  btns.forEach(function(b) { b.disabled = true; b.style.opacity = '0.5'; });
  addSystem('你选择了终结自己的生命...');
  try {
    var d = await api('POST', '/api/suicide-confirm');
    if (d.error) { addNarrative(d.error); return; }
    if (d.death) {
      showDeathScreen(d.death);
    }
  } catch(e) {
    addNarrative('错误:' + e.message);
  }
}

function cancelSuicide() {
  var nb = $('nb');
  var lastDiv = nb.querySelector('div[style*="rgba(154,90,90"]');
  if (lastDiv) lastDiv.remove();
  addNarrative('你放弃了这个念头。', false, false);
  $('ot').textContent = '选择你的行动：';
}

async function pickDeathOpt(type) {
  clearOpts();
  if (type === 'reload') {
    // [v11] 打开百世书回滚存档选择
    openRewindModal();
  } else if (type === 'reincarnate') {
    addSystem('正在准备重生...');
    try {
      var d = await api('POST', '/api/death-choice', {choice: 'reincarnate'});
      if (d.error) { addNarrative(d.error); return; }
      if (d.narrative) addNarrative(d.narrative, false, false);
      if (d.pages_remaining !== undefined) {
        addSystem('百世书剩余页数: ' + d.pages_remaining);
      }
      if (d.karma_narrative) addNarrative(d.karma_narrative, false, false);
      if (d.revival_restriction) addSystem(d.revival_restriction);
      var gsResp = await api('GET', '/api/state');
      if (gsResp.error) { addNarrative(gsResp.error); return; }
      GS = gsResp.state;
      updateStatus();
      $('ot').textContent = '选择你的行动：';
    } catch(e) {
      addNarrative('重生失败: ' + e.message);
    }
  } else if (type === 'new_world') {
    addSystem('请在首页描述你想要的新世界');
  }
}

async function doAdvance() {
  try {
    var d = await api('POST', '/api/advance');
    if (d.error) return;
    GS = d.state;
    updateStatus();
    addSystem('⏰ 时间流逝...' + (GS.time_status ? GS.time_status.display : ''));
    if (d.sleeping_events) d.sleeping_events.forEach(function(e) {
      var evText = e.detail || e.description || '';
      addSystem('💤 ' + evText);
    });
    // NPC 行动事件（仅在叙事中显示，不加入世界新闻）
    if (d.npc_events && d.npc_events.length > 0) {
      d.npc_events.forEach(function(e) {
        var npcName = e.npc_name || e.npc_id || '某人';
        var action = e.action || e.detail || '';
        var evText = npcName + ': ' + action;
        addSystem('👤 ' + evText);
      });
    }
    // 年度演化仅在叙事中显示，不加入世界新闻
    if (d.yearly_evolution && d.yearly_evolution.length > 0) {
      var newsHtml = '<div class="world-news"><div class="wn-title">📅 年度变迁 | 第' + GS.day + '天</div>';
      d.yearly_evolution.forEach(function(e) {
        var cls = e.type && e.type.indexOf('death') >= 0 ? 'death' : '';
        newsHtml += '<div class="wn-item ' + cls + '">' + e.description + '</div>';
      });
      newsHtml += '</div>';
      $('nb').insertAdjacentHTML('beforeend', newsHtml);
      $('nb').scrollTop = $('nb').scrollHeight;
    }
    if (d.intro) addNarrative(d.intro);
  } catch(e) {}
}

async function doWhispers() {
  // [Bug] 防连点 + loading 提示 + 错误反馈，避免点击后无任何输出
  var btn = $('btn_whispers');
  var originalText = btn ? btn.textContent : '';
  if (btn) {
    if (btn.dataset.loading === '1') return;  // 防重复点击
    btn.dataset.loading = '1';
    btn.style.opacity = '0.6';
    btn.style.pointerEvents = 'none';
    btn.textContent = '💭 思绪飘忽中...';
  }
  try {
    var d = await api('GET', '/api/whispers');
    if (d && d.error) {
      if (typeof addSystem === 'function') addSystem('碎碎念生成失败：' + d.error);
      else alert('碎碎念生成失败：' + d.error);
    } else if (d && d.whispers && d.whispers.length) {
      d.whispers.forEach(function(w) {
        if (w && w.text) addWhisper(w.text);
      });
    } else {
      // 后端返回空列表，给个提示
      if (typeof addSystem === 'function') addSystem('内心一片平静，暂无碎碎念');
    }
  } catch(e) {
    if (typeof addSystem === 'function') addSystem('碎碎念请求失败：' + (e.message || e));
    else console.error('doWhispers failed:', e);
  } finally {
    if (btn) {
      btn.dataset.loading = '0';
      btn.style.opacity = '';
      btn.style.pointerEvents = '';
      btn.textContent = originalText;
    }
  }
}

async function doMemoir() {
  try {
    var d = await api('GET', '/api/memoir');
    if (d.memoir) {
      $('mth').textContent = '回忆录';
      $('mtb').textContent = d.memoir;
      $('mov').classList.add('on');
    }
  } catch(e) {}
}

async function doMarket() {
  try {
    var d = await api('GET', '/api/market');
    if (d.report) addNarrative(d.report, true);
  } catch(e) {}
}

async function doInventory() {
  try {
    var d = await api('GET', '/api/inventory');
    if (d.summary) addNarrative(d.summary, true);
  } catch(e) {}
}

async function doNovel() {
  try {
    var p = await api('GET', '/api/novel/preview');
    if (!p.has_content) { addSystem('没有新的互动记录'); return; }
    if (!confirm('将把第' + p.from_day + '天到第' + p.to_day + '天的' + p.entries_count + '条记录编写成小说。\n\n确认？')) return;
    $('ot').textContent = '正在生成小说...';
    var d = await api('POST', '/api/novel/generate');
    if (d.chapter) {
      addNarrative(d.chapter, false, false);
      addSystem('小说已生成：第' + d.from_day + '天到第' + d.to_day + '天');
    }
    $('ot').textContent = '选择你的行动：';
  } catch(e) {
    $('ot').textContent = '选择你的行动：';
  }
}

async function doViewChapters() {
  try {
    var d = await api('GET', '/api/novel/chapters');
    var chapters = d.chapters || [];
    if (!chapters.length) { addSystem('暂无已生成的小说章节'); return; }
    $('mth').textContent = '已生成的小说章节';
    $('mtb').innerHTML = chapters.map(function(c, i) {
      return '<div style="padding:8px;margin:6px 0;border:1px solid var(--border);border-radius:6px;cursor:pointer" onclick="readChapter(\'' + c.file + '\')">' +
        '<b>第' + (i+1) + '章</b> (第' + c.from_day + '天~第' + c.to_day + '天, ' + c.entries_count + '条记录)' +
        '<div style="color:var(--dim);font-size:.85em;margin-top:4px">' + c.preview + '...</div></div>';
    }).join('');
    $('mov').classList.add('on');
  } catch(e) {}
}

async function readChapter(file) {
  try {
    var d = await api('GET', '/api/novel/chapters/' + file);
    if (d.chapter) {
      $('mth').textContent = '小说章节';
      $('mtb').textContent = d.chapter.chapter || '';
    }
  } catch(e) {
    $('mtb').textContent = '加载失败';
  }
}

async function doButterfly() {
  try {
    var d = await api('GET', '/api/butterfly');
    if (d.summary) {
      addNarrative('行动数: ' + d.summary.total_actions + ' | 影响分: ' + d.summary.world_impact_score + '/10', true);
    }
  } catch(e) {}
}

async function doImage() {
  var nb = $('nb');
  var lastP = nb.lastElementChild;
  var text = lastP ? lastP.textContent : '';
  addSystem('正在生成插图...');
  try {
    var d = await api('POST', '/api/generate-image', {prompt_override: text.substring(0, 300)});
    if (d.image && d.image.generated) {
      var img = document.createElement('img');
      img.src = d.image.image_url + '?t=' + Date.now();
      img.className = 'inline-img';
      nb.appendChild(img);
      nb.scrollTop = nb.scrollHeight;
      addSystem('插图已生成');
    } else {
      addSystem('生成失败: ' + (d.image ? d.image.error : '未知'));
    }
  } catch(e) {
    addSystem('生成失败');
  }
}

async function autoGenerateWorldImage(worldIntro, initialEvent) {
  var prompt = (worldIntro + ' ' + initialEvent).substring(0, 400);
  if (!prompt.trim()) return;
  addSystem('正在生成世界场景图...');
  try {
    var d = await api('POST', '/api/generate-image', {prompt_override: prompt});
    if (d.image && d.image.generated) {
      var nb = $('nb');
      var img = document.createElement('img');
      img.src = d.image.image_url + '?t=' + Date.now();
      img.className = 'inline-img';
      nb.appendChild(img);
      nb.scrollTop = nb.scrollHeight;
      addSystem('场景图已生成');
    } else {
      addSystem('场景图生成失败: ' + (d.image ? d.image.error : '未知'));
    }
  } catch(e) {
    addSystem('场景图生成失败');
  }
}

async function doGraph() {
  $('grmv').classList.add('on');
  var canvas = $('graphCanvas');
  canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载中...</div>';
  try {
    var d = await api('GET', '/api/influence-graph');
    if (!d.nodes || !d.nodes.length) { canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">暂无关系数据</div>'; return; }
    var elements = [];
    d.nodes.forEach(function(n) { elements.push({data:{id:n.id,label:n.label||n.id.replace('npc_',''),influence:n.influence_score||50}}); });
    d.edges.forEach(function(e) {
      var color = e.weight >= 70 ? '#5a9a5a' : e.weight >= 40 ? '#9a9a5a' : '#9a5a5a';
      elements.push({data:{source:e.source,target:e.target,weight:e.weight,label:e.relation_type||'',color:color}});
    });
    canvas.innerHTML = '';
    cytoscape({container:canvas, elements:elements, style:[
      {selector:'node',style:{'background-color':'#6ea9c9','label':'data(label)','color':'#e0d5c1','text-valign':'center','font-size':'11px','width':'mapData(influence,0,100,30,60)','height':'mapData(influence,0,100,30,60)','border-width':2,'border-color':'#2a1a0a'}},
      {selector:'node[id="player"]',style:{'background-color':'#c9a96e','width':50,'height':50,'border-color':'#c9a96e','border-width':3}},
      {selector:'edge',style:{'width':'mapData(weight,0,100,1,5)','line-color':'data(color)','curve-style':'bezier','label':'data(label)','font-size':'9px','color':'#7a6b5a','text-background-color':'#111120','text-background-opacity':0.8,'text-background-padding':'2px'}},
    ], layout:{name:'cose',idealEdgeLength:120,nodeOverlap:30,refresh:20,randomize:false,componentSpacing:40,nodeRepulsion:6000,edgeElasticity:100,nestingFactor:1.2,gravity:0.25,animate:false}});
    $('graphInfo').innerHTML = '节点: ' + d.nodes.length + ' | 关系: ' + d.edges.length;
  } catch(e) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载失败: ' + e.message + '</div>';
  }
}

// ===== 世界地图系统 =====
var mapData = null;
var mapCy = null;

async function updateMapPreview() {
  try {
    var d = await api('GET', '/api/map-data');
    if (d.error) return;
    mapData = d;
    var locs = d.locations || [];
    var playerLoc = d.player_location || '未知';
    var npcTotal = 0;
    if (d.npc_locations) {
      Object.values(d.npc_locations).forEach(function(arr) { npcTotal += arr.length; });
    }
    $('map_player_loc').textContent = '📍 ' + playerLoc;
    $('map_loc_count').textContent = locs.length;
    $('map_npc_count').textContent = npcTotal;
  } catch(e) {}
}

function openMap() {
  $('mapModal').classList.add('on');
  buildMap();
  setTimeout(function() {
    if (mapCy) { mapCy.resize(); mapCy.fit(undefined, 60); }
  }, 200);
}

function closeMap() {
  $('mapModal').classList.remove('on');
  if (mapCy) { mapCy.destroy(); mapCy = null; }
}

/* ========== 📜 名人谱系统 ========== */
var whoData = null;
var whoSelectedNpc = null;

async function openWhoIsWho() {
  $('whoModal').classList.add('on');
  await loadWhoIsWho();
}

function closeWhoIsWho() {
  $('whoModal').classList.remove('on');
  whoSelectedNpc = null;
  $('who_detail').style.display = 'none';
}

async function loadWhoIsWho() {
  $('who_content').innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载中...</div>';
  try {
    var d = await api('GET', '/api/who-is-who');
    if (d.error) {
      $('who_content').innerHTML = '<div style="color:var(--danger);text-align:center;padding:40px">' + d.error + '</div>';
      return;
    }
    whoData = d;
    $('who_visibility').value = d.info_visibility || 'immersive';
    renderWhoIsWho(d);
    updateWhoPreview(d);
    if (d.recent_rumors && d.recent_rumors.length > 0) {
      renderRumors(d.recent_rumors);
    }
  } catch (e) {
    $('who_content').innerHTML = '<div style="color:var(--danger);text-align:center;padding:40px">加载失败: ' + e.message + '</div>';
  }
}

function updateWhoPreview(d) {
  $('who_known_count').textContent = d.known_count + ' 位已知人物';
  // [Bug] 先设置 innerHTML 确保 who_unknown_count 元素存在，避免空引用
  if (d.unknown_count === 0 && d.known_count > 0) {
    $('who_unknown_hint').textContent = '天下英雄尽入彀中';
  } else {
    $('who_unknown_hint').innerHTML = '还有 <span id="who_unknown_count">' + d.unknown_count + '</span> 位隐世高人';
  }
}

function renderWhoIsWho(d) {
  var factions = d.factions || {};
  var stats = d.total_world_npcs + ' 位风云人物 · 已知 ' + d.known_count + ' 位 · 未知 ' + d.unknown_count + ' 位';
  if (d.local_npcs_count > 0) stats += ' · ' + d.local_npcs_count + ' 位本地人物';
  if (d.recent_passersby_count > 0) stats += ' · 近期遇到 ' + d.recent_passersby_count + ' 位路人';
  $('who_stats').textContent = stats;

  var html = '';
  var factionOrder = Object.keys(factions).sort(function(a, b) {
    var la = (factions[a] || []).length;
    var lb = (factions[b] || []).length;
    return lb - la;
  });
  for (var i = 0; i < factionOrder.length; i++) {
    var fac = factionOrder[i];
    var npcs = factions[fac];
    if (!npcs || npcs.length === 0) continue;
    html += '<div style="margin-bottom:16px">';
    html += '<h3 style="color:var(--gold);font-size:.95em;margin:0 0 8px 0;padding-bottom:4px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px">';
    html += '🏛️ ' + escHtml(fac);
    html += '<span style="color:var(--dim);font-size:.75em;font-weight:normal">（' + npcs.length + '人）</span>';
    html += '</h3>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px">';
    for (var j = 0; j < npcs.length; j++) {
      var npc = npcs[j];
      var knownLvl = npc.knowledge_level || 0;
      var isUnknown = knownLvl === 0;
      var label = npc.knowledge_label || '❓ 未知';
      var nameDisplay = isUnknown ? '？？？' : (npc.name || '未知');
      var titleDisplay = isUnknown ? '？？？' : (npc.title || '');
      var powerDisplay = isUnknown || !npc.power_level || npc.power_level === '？？？' ? '？？？' : npc.power_level;
      var favorColor = '';
      var rel = npc.relation_to_player || {};
      var favorVal = rel.favor || 50;
      if (favorVal >= 70) favorColor = 'color:#5a9a5a';
      else if (favorVal <= 30) favorColor = 'color:#9a5a5a';
      html += '<div class="who-npc-card' + (isUnknown ? ' who-unknown' : '') + '" onclick="showWhoNpcDetail(\'' + escAttr(npc.npc_id) + '\')" style="cursor:pointer;padding:8px 10px;background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:5px;transition:.15s;font-size:.85em" onmouseover="this.style.borderColor=\'var(--gold)\';this.style.background=\'rgba(201,169,110,.08)\'" onmouseout="this.style.borderColor=\'var(--border)\';this.style.background=\'rgba(255,255,255,.03)\'">';
      html += '<div style="font-weight:600;color:' + (isUnknown ? 'var(--dim)' : 'var(--text)') + '">' + escHtml(nameDisplay) + '</div>';
      if (titleDisplay) html += '<div style="color:var(--dim);font-size:.78em;margin-top:1px">' + escHtml(titleDisplay) + '</div>';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;font-size:.75em">';
      html += '<span style="color:var(--accent)">' + escHtml(powerDisplay) + '</span>';
      html += '<span style="font-size:.75em;opacity:.7">' + label + '</span>';
      html += '</div>';
      if (!isUnknown && rel.relation_type) {
        html += '<div style="margin-top:2px;font-size:.75em" ' + favorColor + '>' + escHtml(rel.relation_type) + ' (' + favorVal + ')</div>';
      }
      html += '</div>';
    }
    html += '</div></div>';
  }
  if (d.unknown_count > 0 && d.info_visibility === 'immersive') {
    html += '<div style="text-align:center;padding:16px;color:var(--dim);font-size:.85em;border-top:1px dashed var(--border);margin-top:8px">';
    html += '🔒 还有 ' + d.unknown_count + ' 位人物尚未知晓，闯荡江湖时多听多问，自会慢慢了解...';
    html += '</div>';
  }
  if (Object.keys(factions).length === 0) {
    html = '<div style="color:var(--dim);text-align:center;padding:40px;font-size:.9em">尚未知晓任何人物，多与人交谈、打听消息吧</div>';
  }
  $('who_content').innerHTML = html;
}

function renderRumors(rumors) {
  if (!rumors || rumors.length === 0) {
    $('who_rumors').innerHTML = '';
    return;
  }
  var html = '<div style="padding:8px 12px;background:rgba(201,169,110,.08);border:1px solid rgba(201,169,110,.2);border-radius:6px;font-size:.85em">';
  html += '<div style="color:var(--gold);font-weight:600;margin-bottom:6px">📢 最近传闻</div>';
  for (var i = 0; i < rumors.length; i++) {
    var r = rumors[i];
    html += '<div style="color:var(--text);line-height:1.6;margin-bottom:4px;padding-left:12px;border-left:2px solid var(--border)">';
    if (r.is_major_event) html += '⚠️ ';
    html += escHtml(r.content);
    html += '</div>';
  }
  html += '</div>';
  $('who_rumors').innerHTML = html;
}

async function showWhoNpcDetail(npcId) {
  _currentDetailNpcId = npcId;
  try {
    var d = await api('GET', '/api/who-is-who/' + npcId);
    if (d.error || d.exists === false) {
      toast('无法查看该人物信息', 'error');
      return;
    }
    whoSelectedNpc = d;
    var nameDisplay = d.name || '？？？';
    var titleDisplay = d.title || '';
    $('who_detail_name').textContent = nameDisplay;
    $('who_detail_title').textContent = (d.faction ? d.faction + ' · ' : '') + titleDisplay;
    $('who_detail_knowledge').textContent = d.knowledge_label || '❓ 未知';

    var body = '';
    if (d.knowledge_level >= 2) {
      body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">外貌：</span>' + escHtml(d.appearance || '？？？') + '</div>';
    }
    if (d.knowledge_level >= 3) {
      body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">性别：</span>' + (d.gender || '未知') + '</div>';
      if (d.age) body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">年龄：</span>' + d.age + '岁</div>';
      body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">性格：</span>' + escHtml(d.personality || '？？？') + '</div>';
      if (d.position_in_faction) body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">身份：</span>' + escHtml(d.position_in_faction) + '</div>';
      if (d.relation_to_player) {
        var rel = d.relation_to_player;
        body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">关系：</span>' + escHtml(rel.relation_type || '未知') + '（好感度 ' + (rel.favor || 50) + '）</div>';
      }
      if (d.times_met) body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">相遇次数：</span>' + d.times_met + '次</div>';
    }
    if (d.knowledge_level >= 4) {
      if (d.background) body += '<div style="margin:8px 0;padding:6px 10px;background:rgba(0,0,0,.2);border-radius:4px"><span style="color:var(--dim)">背景：</span>' + escHtml(d.background) + '</div>';
      if (d.goals) body += '<div style="margin-bottom:6px"><span style="color:var(--dim)">目标：</span>' + escHtml(d.goals) + '</div>';
    }
    if (d.knowledge_level >= 5) {
      if (d.secrets) body += '<div style="margin:8px 0;padding:6px 10px;background:rgba(154,90,90,.15);border-radius:4px"><span style="color:#c98080">秘密：</span>' + escHtml(d.secrets) + '</div>';
    }
    if (d.knowledge_level === 0) {
      body = '<div style="color:var(--dim);text-align:center;padding:10px">你对此人一无所知...<br><span style="font-size:.85em">听说过他的传闻或见过面后，信息会逐步解锁</span></div>';
    } else if (d.knowledge_level === 1) {
      body = '<div style="color:var(--dim);padding:4px 0">你只听说过此人的名号，尚未亲眼见过...<br><span style="font-size:.85em">亲眼见到后可了解更多信息</span></div>';
    }
    $('who_detail_body').innerHTML = body;
    $('who_detail').style.display = 'block';
  } catch (e) {
    toast('查看详情失败: ' + e.message, 'error');
  }
}

async function setWhoVisibility(mode) {
  try {
    await api('POST', '/api/npc-visibility', { mode: mode });
    toast('信息可见度已更新', 'success');
    await loadWhoIsWho();
  } catch (e) {
    toast('设置失败: ' + e.message, 'error');
  }
}

// ===== 世界面板 =====
var wpCy = null;
var wpGraphCy = null;

function openWorldPanel() {
  $('worldPanelModal').classList.add('on');
  switchWorldTab('map');
}

function closeWorldPanel() {
  $('worldPanelModal').classList.remove('on');
  if (wpCy) { wpCy.destroy(); wpCy = null; }
  if (wpGraphCy) { wpGraphCy.destroy(); wpGraphCy = null; }
}

function switchWorldTab(tab) {
  ['map', 'graph', 'timeline', 'events', 'chat'].forEach(function(t) {
    var panel = $('wp_' + t);
    var tabEl = $('wp_tab_' + t);
    if (panel) panel.classList.toggle('active', t === tab);
    if (tabEl) tabEl.classList.toggle('active', t === tab);
  });
  if (tab === 'map') loadWorldMap();
  else if (tab === 'graph') loadWorldGraph();
  else if (tab === 'timeline') loadWorldTimeline();
  else if (tab === 'events') loadWorldEvents();
  else if (tab === 'chat') loadChatNpcList();
}

async function loadWorldMap() {
  var canvas = $('wpMapCanvas');
  if (wpCy) { wpCy.destroy(); wpCy = null; }
  try {
    var d = await api('GET', '/api/map-data');
    if (!d.locations || !d.locations.length) {
      canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:60px">暂无地图数据</div>';
      return;
    }
    canvas.innerHTML = '';
    var elements = [];
    var locations = d.locations;
    var playerLoc = d.player_location || '';
    var npcLocs = d.npc_locations || {};

    locations.forEach(function(loc) {
      var isPlayer = playerLoc && (loc.id === playerLoc || loc.name === playerLoc);
      var npcCount = 0;
      Object.keys(npcLocs).forEach(function(key) {
        if (key === loc.id || key === loc.name) npcCount += npcLocs[key].length;
      });
      var label = loc.name;
      if (npcCount > 0) label += '\n👤×' + npcCount;
      elements.push({data:{id:loc.id, label:label, isPlayer:isPlayer}});
    });

    var edges = d.edges || [];
    if (!edges.length && locations.length > 1) {
      for (var i = 0; i < locations.length; i++) {
        for (var j = i + 1; j < locations.length; j++) {
          edges.push({source:locations[i].id, target:locations[j].id, distance:50});
        }
      }
    }
    edges.forEach(function(e) {
      elements.push({data:{source:e.source, target:e.target, weight:e.distance||50}});
    });

    wpCy = cytoscape({container:canvas, elements:elements, style:[
      {selector:'node',style:{'background-color':'#3a5a7a','label':'data(label)','color':'#e0d5c1','text-valign':'center','font-size':'11px','width':40,'height':40,'border-width':2,'border-color':'#2a1a0a'}},
      {selector:'node[?isPlayer]',style:{'background-color':'#c9a96e','width':50,'height':50,'border-color':'#c9a96e','border-width':3}},
      {selector:'edge',style:{'width':2,'line-color':'#3a3a5a','curve-style':'bezier'}},
    ],layout:{name:'cose',idealEdgeLength:120,refresh:20,randomize:false,gravity:0.25,animate:false}});

    setTimeout(function(){ if(wpCy) wpCy.fit(undefined,40); }, 500);
    $('wp_info_left').textContent = locations.length + ' 个地点 · ' + edges.length + ' 条路径';
  } catch(e) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载失败: ' + e.message + '</div>';
  }
}

async function loadWorldGraph() {
  var canvas = $('wpGraphCanvas');
  if (wpGraphCy) { wpGraphCy.destroy(); wpGraphCy = null; }
  try {
    var d = await api('GET', '/api/influence-graph');
    if (!d.nodes || !d.nodes.length) {
      canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:60px">暂无关系数据</div>';
      return;
    }
    canvas.innerHTML = '';
    var elements = [];
    d.nodes.forEach(function(n) { elements.push({data:{id:n.id, label:n.label||n.id.replace('npc_',''), influence:n.influence_score||50}}); });
    d.edges.forEach(function(e) {
      var color = e.weight >= 70 ? '#5a9a5a' : e.weight >= 40 ? '#9a9a5a' : '#9a5a5a';
      elements.push({data:{source:e.source, target:e.target, weight:e.weight, label:e.relation_type||'', color:color}});
    });
    wpGraphCy = cytoscape({container:canvas, elements:elements, style:[
      {selector:'node',style:{'background-color':'#6ea9c9','label':'data(label)','color':'#e0d5c1','text-valign':'center','font-size':'11px','width':'mapData(influence,0,100,30,60)','height':'mapData(influence,0,100,30,60)','border-width':2,'border-color':'#2a1a0a'}},
      {selector:'node[id="player"]',style:{'background-color':'#c9a96e','width':50,'height':50,'border-color':'#c9a96e','border-width':3}},
      {selector:'edge',style:{'width':'mapData(weight,0,100,1,5)','line-color':'data(color)','curve-style':'bezier','label':'data(label)','font-size':'9px','color':'#7a6b5a','text-background-color':'#111120','text-background-opacity':0.8,'text-background-padding':'2px'}},
    ],layout:{name:'cose',idealEdgeLength:120,nodeOverlap:30,refresh:20,randomize:false,componentSpacing:40,nodeRepulsion:6000,edgeElasticity:100,nestingFactor:1.2,gravity:0.25,animate:false}});
    $('wp_info_left').textContent = '节点: ' + d.nodes.length + ' | 关系: ' + d.edges.length;
  } catch(e) {
    canvas.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">加载失败: ' + e.message + '</div>';
  }
}

async function loadWorldTimeline() {
  var el = $('wpTimelineContent');
  el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载中...</div>';
  try {
    var d = await api('GET', '/api/narrative-history/' + (window._currentWorldId || ''));
    var entries = d.entries || [];
    // [Bug] 时间线只显示重大事件（event），不显示普通叙事和玩家输入
    var eventEntries = entries.filter(function(e) { return e.entry_type === 'event'; });
    if (eventEntries.length === 0) {
      el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">暂无重大事件记录</div>';
      return;
    }
    var html = '';
    var lastDay = 0;
    eventEntries.forEach(function(entry) {
      if (entry.day !== lastDay) {
        html += '<div style="margin:16px 0 8px;color:var(--gold);font-weight:700;font-size:.88em;border-bottom:1px solid var(--border);padding-bottom:4px">📅 第' + entry.day + '天 · ' + entry.time + '</div>';
        lastDay = entry.day;
      }
      html += '<div class="timeline-entry">';
      html += '<div class="event-text">🌍 ' + escapeHtml(entry.narrative) + '</div>';
      html += '</div>';
    });
    el.innerHTML = html;
    $('wp_info_right').textContent = eventEntries.length + ' 条事件记录';
  } catch(e) {
    el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function loadWorldEvents() {
  var el = $('wpEventsContent');
  el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载中...</div>';
  try {
    var d = await api('GET', '/api/narrative-history/' + (window._currentWorldId || ''));
    var entries = (d.entries || []).filter(function(e) { return e.entry_type === 'event'; });
    if (entries.length === 0) {
      el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:40px">暂无世界事件</div>';
      return;
    }
    var html = '';
    entries.forEach(function(entry) {
      html += '<div class="event-item">';
      html += '<div class="event-type">第' + entry.day + '天 · ' + entry.time + '</div>';
      html += '<div class="event-text">' + escapeHtml(entry.narrative) + '</div>';
      html += '</div>';
    });
    el.innerHTML = html;
    $('wp_info_right').textContent = entries.length + ' 个事件';
  } catch(e) {
    el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}
