// 太虚幻境 v8 — 游戏: 交互/操作

// [Bug] 全局加载提示：显示/隐藏加载中遮罩
function showLoadOverlay(msg) {
  var el = document.getElementById('loadingOverlay');
  var txt = document.getElementById('loadingText');
  var stage = document.getElementById('loadingStage');
  if (el && txt) {
    txt.textContent = msg || '正在加载，请稍候...';
    if (stage) stage.textContent = '';
    el.style.display = 'flex';
    if (typeof window.startNebulaAnimation === 'function') window.startNebulaAnimation();
  }
}
function hideLoadOverlay() {
  var el = document.getElementById('loadingOverlay');
  if (el) el.style.display = 'none';
  if (typeof window.stopNebulaAnimation === 'function') window.stopNebulaAnimation();
}

// [v8] 世界类型切换：显示/隐藏金手指选项
function onWorldTypeChange() {
  var wt = $('wt').value;
  var gfSection = $('goldenFingerSection');
  var gfDesc = $('gfDesc');
  // [v8] 所有世界类型都显示金手指选项
  gfSection.style.display = 'block';
  // 根据世界类型更新描述
  var descMap = {
    'historical': '关闭后，AI将严格遵循历史逻辑，拒绝任何超自然元素（系统面板、现代物品具现化等）。',
    'modern': '关闭后，AI将严格遵循现实逻辑，拒绝任何超能力或超自然元素。',
    'fantasy': '关闭后，AI将遵循传统奇幻设定，不允许系统面板等meta元素。',
    'xianxia': '关闭后，AI将遵循传统修仙设定，不允许系统面板等meta元素。',
    'wuxia': '关闭后，AI将遵循传统武侠设定，不允许系统面板等meta元素。',
    'scifi': '关闭后，AI将遵循硬科幻逻辑，不允许超自然元素。',
    'postapocalyptic': '关闭后，AI将遵循现实末日逻辑，不允许超自然元素。',
    'urban_fantasy': '关闭后，AI将限制超能力范围，不允许系统面板等meta元素。',
    'custom': '关闭后，AI将严格遵循该世界的既有设定，不允许超出设定的元素。',
  };
  gfDesc.textContent = descMap[wt] || descMap['custom'];
}

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

async function loadSaves() {
  const d = await api('GET', '/api/worlds');
  const l = $('slist');
  const worlds = d.worlds || [];
  if (!worlds.length) {
    l.innerHTML = '<div style="color:#5a4a3a;text-align:center;padding:12px">暂无存档</div>';
    return;
  }
  l.innerHTML = worlds.map(function(w) {
    var timeStr = w.last_saved_at_display || w.created_at_display || '';
    var wName = w.world_name || '未知';
    return '<div class="sitem" onclick="showWorldSlots(\'' + escAttr(w.world_id) + '\',\'' + escAttr(wName) + '\')">' +
      '<div><div class="nm">' + escHtml(wName) + '</div>' +
      '<div class="inf">第' + escHtml(w.current_day || '?') + '天 | ' +
      escHtml(w.player_name || '?') + ' ' + escHtml(w.player_age || '?') + '岁 | ' +
      escHtml(w.save_count || 1) + '个存档' +
      (timeStr ? ' | ' + escHtml(timeStr) : '') + '</div></div>' +
      '<span onclick="event.stopPropagation();deleteSave(\'' + escAttr(w.world_id) + '\',\'' +
      escAttr(wName) + '\')" ' +
      'style="color:#9a5a5a;cursor:pointer;font-size:1.1em;padding:4px 8px;border-radius:4px" title="删除存档">&#10005;</span></div>';
  }).join('');
}

async function showWorldSlots(wid, worldName) {
  const l = $('slist');
  l.innerHTML = '<div style="color:var(--dim);text-align:center;padding:8px">加载中...</div>';
  try {
    const d = await api('GET', '/api/worlds/' + wid + '/saves');
    const slots = (d.saves || []).filter(function(s) { return s.slot_id !== 'auto'; });
    var html = '<div style="margin-bottom:8px"><span style="color:var(--gold);font-weight:700">' + escHtml(worldName) + '</span> ' +
      '<span style="color:var(--dim);cursor:pointer;font-size:.82em" onclick="loadSaves()">← 返回</span></div>';
    if (slots.length === 0) {
      html += '<div style="color:var(--dim);text-align:center;padding:12px">只有自动存档，点击直接加载</div>';
      html += '<div class="sitem" onclick="loadGame(\'' + escAttr(wid) + '\')" style="justify-content:center">加载存档</div>';
    } else {
      slots.forEach(function(s) {
        var slotTime = s.created_at || s.saved_at || '';
        var sName = s.name || '存档';
        html += '<div class="sitem" onclick="loadSlotGame(\'' + escAttr(wid) + '\',\'' + escAttr(s.slot_id) + '\')">' +
          '<div><div class="nm">' + escHtml(sName) + '</div>' +
          '<div class="inf">第' + escHtml(s.day || '?') + '天 | ' + escHtml(s.player_age || '?') + '岁' +
          (slotTime ? ' | ' + escHtml(slotTime) : '') + '</div></div>' +
          '<span onclick="event.stopPropagation();deleteSlot(\'' + escAttr(s.slot_id) + '\',\'' + escAttr(wid) + '\',\'' + escAttr(sName) + '\')" ' +
          'style="color:#9a5a5a;cursor:pointer;font-size:1.1em;padding:4px 8px;border-radius:4px" title="删除此存档">&#10005;</span></div>';
      });
      html += '<div class="sitem" onclick="loadGame(\'' + escAttr(wid) + '\')" style="justify-content:center;color:var(--dim)">加载最新自动存档</div>';
    }
    l.innerHTML = html;
  } catch(e) {
    l.innerHTML = '<div style="color:var(--dim);text-align:center;padding:12px">加载失败</div>';
  }
}

async function loadSlotGame(wid, slotId) {
  const cfg = await getConfig();
  showLoadOverlay('正在加载存档，请稍候...');
  try {
    // 先确保游戏已加载（用于初始化引擎）
    await api('POST', '/api/load', {api_key: cfg.api_key, base_url: cfg.base_url, model_name: cfg.model_name, world_id: wid});
    // 再加载指定槽位（覆盖引擎状态为槽位状态）
    const slotRes = await api('POST', '/api/slot/load', {slot_id: slotId});
    if (!slotRes.status || slotRes.status !== 'ok') { hideLoadOverlay(); alert('槽位加载失败'); return; }
    const d = await api('GET', '/api/state');
    if (d.error) { hideLoadOverlay(); alert(d.error); return; }
    GS = d.state;
    showGame(true);
    clearNews();
    restoreHistory(d.history || [], d.images || []);
    // 优先使用 slot 加载后重新生成的选项（更符合当前状态）
    const opts = (slotRes.initial_options && slotRes.initial_options.length) ? slotRes.initial_options : (d.initial_options || []);
    if (opts.length) showOpts(opts);
    updateStatus();
    if (!d.images || d.images.length === 0) {
      var worldDesc = (GS.world?.description || '') + ' ' + (GS.world?.name || '');
      if (worldDesc.trim()) {
        autoGenerateWorldImage(worldDesc, '');
      }
    }
  } catch(e) {
    alert('加载失败');
  } finally {
    hideLoadOverlay();
  }
}

async function deleteSlot(slotId, wid, name) {
  if (!confirm('确定删除存档「' + name + '」？')) return;
  try {
    await api('DELETE', '/api/slot/' + slotId);
    showWorldSlots(wid, '');
  } catch(e) {
    alert('删除失败');
  }
}

async function deleteSave(wid, name) {
  if (!confirm('确定删除存档「' + name + '」？此操作不可撤销。')) return;
  try {
    await api('DELETE', '/api/save/' + wid);
    loadSaves();
  } catch(e) {
    alert('删除失败');
  }
}

async function createWorld() {
  const cfg = await api('GET', '/api/config/raw');
  var apiKey = cfg.llm?.api_key || '';
  var baseUrl = cfg.llm?.base_url || 'https://token-plan-cn.xiaomimimo.com/v1';
  var modelName = cfg.llm?.model_name || 'mimo-V2.5-Pro';
  
  if (!apiKey) { 
    alert('请先在设置中配置API Key'); 
    openSettings(); 
    return; 
  }
  
  var wd = $('wd').value.trim();
  if (!wd) {
    wd = '一个神秘的奇幻世界，有魔法、精灵和龙。你是一名年轻的冒险者，刚刚离开家乡，踏上旅途。';
  }
  // [UX] 世界生成加载动画：主标题 + 阶段轮播文字，让等待过程更有沉浸感
  var worldGenStages = [
    '（正在生成世界观）',
    '（正在加载子系统）',
    '（正在生成NPC智能体）',
    '（正在推演因果链）',
    '（正在注入记忆系统）',
    '（正在追踪蝴蝶效应）',
    '（世界事件已加载）',
    '（虚拟世界马上就绪......）',
  ];
  var stageIdx = 0;
  var stageTimer = null;
  var _loadingStageEl = document.getElementById('loadingStage');
  var _loadingTextEl = document.getElementById('loadingText');
  var _loadingOverlay = document.getElementById('loadingOverlay');
  if (_loadingOverlay && _loadingTextEl && _loadingStageEl) {
    _loadingTextEl.textContent = '正在初始化虚拟世界，需要3分钟时间';
    _loadingStageEl.textContent = worldGenStages[0];
    _loadingOverlay.style.display = 'flex';
    if (typeof window.startNebulaAnimation === 'function') window.startNebulaAnimation();
    stageTimer = setInterval(function() {
      if (stageIdx < worldGenStages.length - 1) {
        stageIdx++;
        // 淡出→切换→淡入
        _loadingStageEl.style.opacity = '0';
        setTimeout(function() {
          _loadingStageEl.textContent = worldGenStages[stageIdx];
          _loadingStageEl.style.opacity = '1';
        }, 400);
      }
      // 到达最后一条后不再循环，停留在"马上就绪"
    }, 20000);
  } else {
    // 回退方案：修改按钮文字
    document.querySelector('.btn').textContent = '正在初始化虚拟世界，需要3分钟时间...';
  }
  document.querySelector('.btn').disabled = true;
  var worldType = $('wt').value;
  var goldenFinger = $('goldenFinger') ? $('goldenFinger').checked : false;
  try {
    var d = await api('POST', '/api/generate-world', {
      description: wd,
      world_type: worldType,
      golden_finger: goldenFinger,
      api_key: apiKey,
      base_url: baseUrl,
      model_name: modelName
    });
    if (d.error) { alert(d.error); return; }
    GS = d.state;
    showGame();
    clearNews();
    if (d.world_intro) {
      addNarrative('【' + (GS.world?.name || '新世界') + ' 世界观简介】', true);
      addNarrative(d.world_intro, false, false);
    }
    if (d.initial_event) addNarrative(d.initial_event, false, false);
    if (d.initial_options && d.initial_options.length) showOpts(d.initial_options);
    updateStatus();
    if (d.world_intro || d.initial_event) {
      autoGenerateWorldImage(d.world_intro || '', d.initial_event || '');
    }
  } catch(e) {
    alert('失败:' + e.message);
  } finally {
    // 清理加载动画
    if (stageTimer) { clearInterval(stageTimer); stageTimer = null; }
    if (_loadingOverlay) { _loadingOverlay.style.display = 'none'; }
    if (typeof window.stopNebulaAnimation === 'function') window.stopNebulaAnimation();
    document.querySelector('.btn').textContent = '生成世界并开始冒险';
    document.querySelector('.btn').disabled = false;
  }
}

async function loadGame(wid) {
  const cfg = await getConfig();
  if (!cfg.api_key) { alert('请先在设置中配置API Key'); openSettings(); return; }
  showLoadOverlay('正在加载世界，请稍候...');
  try {
    var d = await api('POST', '/api/load', {
      api_key: cfg.api_key, base_url: cfg.base_url,
      model_name: cfg.model_name, world_id: wid
    });
    if (d.error) { hideLoadOverlay(); alert(d.error); return; }
    GS = d.state;
    showGame(true);
    clearNews();
    restoreHistory(d.history || [], d.images || []);
    if (d.initial_options && d.initial_options.length) showOpts(d.initial_options);
    updateStatus();
    if (!d.images || d.images.length === 0) {
      var worldDesc = (GS.world?.description || '') + ' ' + (GS.world?.name || '');
      if (worldDesc.trim()) {
        autoGenerateWorldImage(worldDesc, '');
      }
    }
  } catch(e) {
    alert('加载失败');
  } finally {
    hideLoadOverlay();
  }
}

async function doBack() {
  var choice = confirm('是否保存当前游戏？\n\n确定 = 保存并返回\n取消 = 不保存直接返回');
  if (choice) await doSave();
  $('game').style.display = 'none';
  $('home').style.display = 'flex';
  loadSaves();
}

async function pickOpt(id, txt) {
  clearOpts();
  addNarrative('> ' + txt, false, true);
  await sendInput(txt, true);
}

async function doCustom() {
  var i = $('ci');
  var t = i.value.trim();
  if (!t) return;

  // [v10] 斜杠命令处理
  if (t.startsWith('/')) {
    i.value = '';
    handleSlashCommand(t.toLowerCase().trim());
    return;
  }

  i.value = '';
  clearOpts();
  addNarrative('> ' + t, false, true);
  await sendInput(t);
}

// [v10] 斜杠命令处理器
function handleSlashCommand(cmd) {
  var parts = cmd.split(/\s+/);
  var c = parts[0];
  var arg = parts.slice(1).join(' ');

  switch(c) {
    case '/save':
      doSave();
      break;
    case '/load':
      if (typeof loadSaves === 'function') loadSaves();
      toast('已刷新存档列表', 'success');
      break;
    case '/map':
      if (typeof openMap === 'function') openMap();
      else toast('请先开始游戏', 'error');
      break;
    case '/graph':
      if (typeof doGraph === 'function') doGraph();
      else toast('请先开始游戏', 'error');
      break;
    case '/v10':
      if (typeof openV10Panel === 'function') openV10Panel();
      break;
    case '/settings':
      if (typeof openSettings === 'function') openSettings();
      break;
    case '/undo':
      if (typeof undo === 'function') undo();
      else toast('回退功能暂不可用', 'error');
      break;
    case '/redo':
      if (typeof redo === 'function') redo();
      else toast('重做功能暂不可用', 'error');
      break;
    case '/help':
    case '/?':
      addNarrative('📋 可用斜杠命令：\n' +
        '/save - 快速存档\n' +
        '/load - 打开读档面板\n' +
        '/map - 打开世界地图\n' +
        '/who - 打开名人谱\n' +
        '/graph - 打开NPC关系图谱\n' +
        '/v10 - 打开v10高级面板\n' +
        '/settings - 打开设置\n' +
        '/undo - 回退一步\n' +
        '/redo - 重做一步\n' +
        '/time - 查看当前游戏时间\n' +
        '/status - 查看玩家状态\n' +
        '/inventory - 查看背包\n' +
        '/help - 显示此帮助', false, false);
      break;
    case '/who':
      openWhoIsWho();
      break;
    case '/time':
      if (GS && GS.time_status) {
        addNarrative('⏰ ' + GS.time_status.display + ' | ' + GS.time_status.season + '·' + GS.time_status.time_of_day + ' | ' + GS.time_status.weather + '\n（故事已过' + GS.time_status.total_days + '天）', false, false);
      } else {
        addNarrative('⏰ 时间信息加载中...', false, false);
      }
      break;
    case '/status':
      if (GS && GS.player) {
        var p = GS.player;
        addNarrative('📊 玩家状态：\n姓名: ' + p.name + ' | 年龄: ' + p.age + '岁 | 身份: ' + (p.position || '无') + '\n生命: ' + p.health + '/' + p.max_health + ' | 精力: ' + p.energy + '/' + p.max_energy + '\n力量: ' + p.strength + ' | 敏捷: ' + p.agility + ' | 智力: ' + p.intelligence + ' | 运气: ' + p.luck + '\n金币: ' + p.gold + ' | 声望: ' + p.reputation, false, false);
      } else {
        addNarrative('📊 状态信息加载中...', false, false);
      }
      break;
    case '/inventory':
      doInventory();
      break;
    default:
      toast('未知命令: ' + c + '，输入 /help 查看可用命令', 'error');
  }
}

var actionCount = 0;
var lastImageAction = 0;
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

async function sendInput(t, fromOption) {
  actionCount++;
  $('ot').textContent = '思考中...';

  // [v11] 上下文衔接检测（已禁用，改由后端 cheap_llm 校验）
  // if (!fromOption && checkContextMismatch(t)) {
  //   var confirmed = confirm('⚠️ 你输入的行动似乎与当前剧情不太衔接。\n\n是否仍要按此行动继续？\n\n确定 = 强制按你的行动写\n取消 = 重新输入');
  //   if (!confirmed) {
  //     $('ot').textContent = '选择你的行动：';
  //     $('ci').value = t;
  //     $('ci').focus();
  //     return;
  //   }
  // }

  // [v11] 检查流式输出开关：设置中关闭流式时，强制使用 HTTP 模式
  var streamingEnabled = true;
  try {
    var cfg = await api('GET', '/api/config');
    streamingEnabled = (cfg.game?.streaming_enabled) !== false;
  } catch(e) {}

  // 如果 WebSocket 已连接且流式输出启用，使用流式模式
  if (streamingEnabled && ws && ws.readyState === WebSocket.OPEN) {
    return sendInputStream(t);
  }
  // 否则使用传统 HTTP 模式
  return sendInputHTTP(t);
}

function sendInputStream(t) {
  // 准备流式渲染
  var nb = $('nb');
  var streamP = document.createElement('p');
  streamP.className = 'ai-narrative streaming';
  streamP.innerHTML = '<span class="cursor-blink">▌</span>';
  nb.appendChild(streamP);
  nb.scrollTop = nb.scrollHeight;

  var streamText = '';
  var streamDone = false;
  var finalResult = null;
  var finalState = null;

  wsOnToken = function(token) {
    streamText += token;
    streamP.innerHTML = sanitizeHTML(streamText).replace(/\n/g, '<br>') + '<span class="cursor-blink">▌</span>';
    nb.scrollTop = nb.scrollHeight;
  };

  wsOnStreamEnd = function() {
    // 移除光标（保留 streaming 类，由 processStreamResult 统一清理）
    streamP.innerHTML = sanitizeHTML(streamText).replace(/\n/g, '<br>');
    streamDone = true;
    // 如果有最终结果，处理它
    if (finalResult) {
      // [Bug] 先更新 GS，再处理结果，确保 processStreamResult 内部使用的 GS.day 等字段是最新的
      if (finalState) { GS = finalState; updateStatus(); }
      var r = finalResult;
      finalResult = null;  // [Bug#32] 清空防止重复处理
      finalState = null;
      processStreamResult(r);
    }
  };

  wsOnThinking = function() {
    $('ot').textContent = 'AI 正在书写...';
  };

  wsOnResult = function(result, state) {
    if (streamDone) {
      // [Bug#32] 防止重复处理：如果 finalResult 已被 wsOnStreamEnd 消费，跳过
      if (finalResult !== null) return;
      processStreamResult(result);
      if (state) { GS = state; updateStatus(); }
    } else {
      finalResult = result;
      finalState = state;
    }
  };

  // 发送流式输入请求
  sendWS({ type: 'stream_input', text: t });

  // 设置超时回退（120秒后如果还没有流式响应，回退到 HTTP）
  var streamTimeout = setTimeout(function() {
    if (!streamText && !finalResult) {
      // 流式失败，清理并回退
      if (streamP.parentNode) streamP.remove();
      wsOnToken = null; wsOnResult = null; wsOnStreamEnd = null; wsOnThinking = null;
      sendInputHTTP(t);
    } else if (finalResult && !streamDone) {
      // [Bug] result 已到达但 stream_end 未到达，直接处理结果避免 UI 挂起
      streamP.innerHTML = sanitizeHTML(streamText).replace(/\n/g, '<br>');
      streamDone = true;
      processStreamResult(finalResult);
      if (finalState) { GS = finalState; updateStatus(); }
    }
  }, 120000);

  // 保存超时引用以便清理
  streamP._streamTimeout = streamTimeout;
}

function processStreamResult(result) {
  var streaming = document.querySelector('.streaming');
  if (streaming && streaming._streamTimeout) {
    clearTimeout(streaming._streamTimeout);
  }
  wsOnToken = null; wsOnResult = null; wsOnStreamEnd = null; wsOnThinking = null;

  var r = result;
  // 移除流式段落（包含raw JSON的也要移除）
  var allPs = document.querySelectorAll('#nb p');
  allPs.forEach(function(p) {
    var txt = p.textContent || '';
    if (p.classList.contains('streaming') || txt.indexOf('"narrative"') >= 0 || txt.indexOf('```') === 0 || txt.trim() === '{' || txt.trim() === '}') {
      p.remove();
    }
  });
  // 显示叙事
  if (r.narrative) addNarrative(r.narrative, false, false);
  if (r.dice_result) showDice(r.dice_result);
  if (r.world_event) addSystem(r.world_event.description);
  if (r.auto_event) {
    addNarrative(r.auto_event.narrative, true);
    addSystem('影响等级: ' + r.auto_event.impact_level + '/10');
    addNews([{ description: r.auto_event.narrative ? r.auto_event.narrative.substring(0, 100) : '世界事件', type: 'event', day: GS.day }]);
  }
  if (r.suicide_confirm) { showSuicideConfirm(r.suicide_confirm); return; }
  if (r.death) { showDeathScreen(r.death); return; }
  if (r.auto_image && r.auto_image.auto && r.auto_image.image_url) {
    var nb = $('nb');
    var img = document.createElement('img');
    img.src = r.auto_image.image_url + '?t=' + Date.now();
    img.className = 'inline-img';
    nb.appendChild(img);
    nb.scrollTop = nb.scrollHeight;
  }
  updateStatus();
  if (r.options && r.options.options) r.options = r.options.options;
  if (r.options && r.options.length) showOpts(r.options);
  $('ot').textContent = '选择你的行动：';

  // [v11] 输出汇总：记录本次AI原始输出
  var lastInput = '';
  try { lastInput = document.querySelector('#nb .player-input') ? document.querySelector('#nb .player-input').textContent.replace(/^>\s*/, '') : ''; } catch(e) {}
  outputLog.push({
    time: new Date().toLocaleTimeString('zh-CN'),
    input: lastInput || '(未知)',
    narrative: r.narrative || '',
    options: r.options || [],
    raw: r,
  });
  if (outputLog.length > 50) outputLog = outputLog.slice(-50);

  // 时间跳跃通知
  if (r.time_skip && r.time_skip.days_advanced > 0) {
    var skipDays = r.time_skip.days_advanced;
    var skipText = skipDays >= 365 ? (Math.floor(skipDays / 365) + '年' + (skipDays % 365 > 0 ? Math.floor((skipDays % 365) / 30) + '个月' : ''))
      : skipDays >= 30 ? Math.floor(skipDays / 30) + '个月' : skipDays + '天';
    addSystem('⏰ 叙事时间跳跃: ' + skipText + ' (' + skipDays + '天)');
    toast('⏰ 时间跳跃: ' + skipText);
  }
  if (r.year_evolution && r.year_evolution.length > 0) {
    // 年度演化仅在叙事中显示，不加入世界新闻
  }
  if (r.identity_log && r.identity_log.length) {
    r.identity_log.forEach(function(l) { toast('🔀 ' + l); });
  }
  if (r.audit_results && r.audit_results.length) {
    r.audit_results.forEach(function(l) {
      var isWarn = l.indexOf('⚠️') >= 0;
      toast(l, isWarn ? 'warn' : 'info');
    });
  }
  if (r._fallback) {
    toast('⚠️ AI响应异常，使用安全模式', 'warn');
    // [改善] 流式模式也显示重试按钮
    if (r._retry_input) {
      showRetryButton(r._retry_input);
    }
  }
}

async function sendInputHTTP(t) {
  try {
    var d = await api('POST', '/api/input', {input: t});
    if (d.error) { addNarrative(d.error); return; }
    var r = d.result;
    if (r.narrative) addNarrative(r.narrative, false, false);
    if (r.dice_result) showDice(r.dice_result);
    if (r.world_event) addSystem(r.world_event.description);
    if (r.auto_event) {
      addNarrative(r.auto_event.narrative, true);
      addSystem('影响等级: ' + r.auto_event.impact_level + '/10');
      addNews([{ description: r.auto_event.narrative ? r.auto_event.narrative.substring(0, 100) : '世界事件', type: 'event', day: GS.day }]);
    }
    if (r.suicide_confirm) { showSuicideConfirm(r.suicide_confirm); return; }
    if (r.death) { showDeathScreen(r.death); return; }
    if (r.auto_image && r.auto_image.auto && r.auto_image.image_url) {
      var nb = $('nb');
      var img = document.createElement('img');
      img.src = r.auto_image.image_url + '?t=' + Date.now();
      img.className = 'inline-img';
      nb.appendChild(img);
      nb.scrollTop = nb.scrollHeight;
    }
    GS = d.state;
    updateStatus();
    if (r.options && r.options.options) r.options = r.options.options;
    if (r.options && r.options.length) showOpts(r.options);
    $('ot').textContent = '选择你的行动：';
    // [v11] 输出汇总：记录本次AI原始输出
    var lastInput2 = '';
    try { lastInput2 = document.querySelector('#nb .player-input') ? document.querySelector('#nb .player-input').textContent.replace(/^>\s*/, '') : ''; } catch(e) {}
    outputLog.push({
      time: new Date().toLocaleTimeString('zh-CN'),
      input: lastInput2 || '(未知)',
      narrative: r.narrative || '',
      options: r.options || [],
      raw: r,
    });
    if (outputLog.length > 50) outputLog = outputLog.slice(-50);
    // 叙事时间跳跃通知
    if (r.time_skip && r.time_skip.days_advanced > 0) {
      var skipDays2 = r.time_skip.days_advanced;
      var skipText2 = skipDays2 >= 365 ? (Math.floor(skipDays2 / 365) + '年' + (skipDays2 % 365 > 0 ? Math.floor((skipDays2 % 365) / 30) + '个月' : ''))
        : skipDays2 >= 30 ? Math.floor(skipDays2 / 30) + '个月' : skipDays2 + '天';
      addSystem('⏰ 叙事时间跳跃: ' + skipText2 + ' (' + skipDays2 + '天)');
      toast('⏰ 时间跳跃: ' + skipText2);
    }
    // 年度NPC演化仅在叙事中显示，不加入世界新闻
    if (r.year_evolution && r.year_evolution.length > 0) {
      var newsHtml = '<div class="world-news"><div class="wn-title">📅 年度变迁 | 时间流逝带来的变化</div>';
      r.year_evolution.forEach(function(e) {
        var cls = e.type && e.type.indexOf('death') >= 0 ? 'death' : '';
        newsHtml += '<div class="wn-item ' + cls + '">' + e.description + '</div>';
      });
      newsHtml += '</div>';
      $('nb').insertAdjacentHTML('beforeend', newsHtml);
      $('nb').scrollTop = $('nb').scrollHeight;
    }
    // 身份变更通知
    if (r.identity_log && r.identity_log.length) {
      r.identity_log.forEach(function(l) { toast('🔀 ' + l); });
    }
    // 身份审计通知
    if (r.audit_results && r.audit_results.length) {
      r.audit_results.forEach(function(l) {
        var isWarn = l.indexOf('⚠️') >= 0;
        toast(l, isWarn ? 'warn' : 'info');
      });
    }
    // LLM回退通知 + 重试按钮
    if (r._fallback) {
      toast('⚠️ AI响应异常，使用安全模式', 'warn');
      // [改善] 显示重试按钮，让玩家可以重新生成
      if (r._retry_input) {
        showRetryButton(r._retry_input);
      }
    }
  } catch(e) {
    addNarrative('错误:' + e.message);
  }
}

function showRetryButton(originalInput) {
  var nb = $('nb');
  var div = document.createElement('div');
  div.id = 'retry-btn-container';
  div.className = 'retry-container';
  div.innerHTML = '<div style="color:var(--dim);font-size:.85em;margin-bottom:10px">⚠️ AI响应出现问题，你可以重试</div>' +
    '<button class="retry-btn" onclick="retryLastInput(\'' + escAttr(originalInput) + '\')">' +
    '🔄 重试生成</button>';
  nb.appendChild(div);
  nb.scrollTop = nb.scrollHeight;
}

// [改善] 重试上一次输入
async function retryLastInput(originalInput) {
  // 移除重试按钮
  var container = $('retry-btn-container');
  if (container) container.remove();
  // 重新发送输入
  await sendInput(originalInput);
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
  try {
    var d = await api('GET', '/api/whispers');
    if (d.whispers) d.whispers.forEach(function(w) { addWhisper(w.text); });
  } catch(e) {}
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

async function doSave() {
  await api('POST', '/api/save');
  addSystem('[已保存]');
}

function openAddNpc() {
  $('addNpcModal').classList.add('on');
}

function closeAddNpc() {
  $('addNpcModal').classList.remove('on');
}

async function doAddNpc() {
  var name = $('npc_name').value.trim();
  if (!name) { alert('请输入角色名字'); return; }
  var tagsStr = $('npc_tags').value.trim();
  var tags = tagsStr ? tagsStr.split(',').map(function(t) { return t.trim(); }).filter(function(t) { return t; }) : [];
    var relationSelect = $('npc_relation').value;
    var relation = relationSelect === '__custom__' ? ($('npc_relation_custom').value.trim() || '自定义') : relationSelect;
    var body = {
    name: name,
    age: parseInt($('npc_age').value) || 20,
    role: $('npc_role').value.trim(),
    personality: $('npc_personality').value.trim(),
    speaking_style: $('npc_speaking').value.trim(),
    dialogue_examples: $('npc_examples').value.split('\n').filter(function(l) { return l.trim(); }),
    location: $('npc_location').value.trim(),
    relation_type: relation,
    favor: parseInt($('npc_favor').value) || 50,
    tags: tags,
  };
  try {
    var d = await api('POST', '/api/add-npc', body);
    if (d.error) { alert(d.error); return; }
    toast('已添加角色: ' + name, 'success');
    closeAddNpc();
    $('npc_name').value = '';
    $('npc_age').value = '20';
    $('npc_role').value = '';
    $('npc_personality').value = '';
    $('npc_speaking').value = '';
    $('npc_examples').value = '';
    $('npc_location').value = '';
    $('npc_relation').value = '陌生人';
    $('npc_relation_custom').style.display = 'none';
    $('npc_relation_custom').value = '';
    $('npc_favor').value = '50';
    $('npc_tags').value = '';
    updateStatus();
  } catch(e) {
    alert('添加失败: ' + e.message);
  }
}

var _editNpcId = '';

function openEditNpc() {
  $('editNpcModal').classList.add('on');
  loadNpcListForEdit();
}

function closeEditNpc() {
  $('editNpcModal').classList.remove('on');
  $('editNpcForm').style.display = 'none';
  $('edit_npc_select').value = '';
  _editNpcId = '';
}

async function loadNpcListForEdit() {
  var sel = $('edit_npc_select');
  sel.innerHTML = '<option value="">-- 加载中 --</option>';
  try {
    var d = await api('GET', '/api/npcs');
    var npcs = d.npcs || [];
    sel.innerHTML = '<option value="">-- 请选择角色 --</option>';
    npcs.forEach(function(npc) {
      var opt = document.createElement('option');
      opt.value = npc.agent_id || npc.id || '';
      opt.textContent = npc.name + '（' + (npc.role || '无职业') + '）';
      sel.appendChild(opt);
    });
  } catch(e) {
    sel.innerHTML = '<option value="">-- 加载失败 --</option>';
  }
}

async function loadNpcForEdit() {
  var npcId = $('edit_npc_select').value;
  if (!npcId) {
    $('editNpcForm').style.display = 'none';
    return;
  }
  _editNpcId = npcId;
  try {
    var d = await api('GET', '/api/npc/' + npcId);
    if (d.error) { alert(d.error); return; }
    var npc = d.npc;
    $('edit_npc_name').value = npc.name || '';
    $('edit_npc_age').value = npc.age || 20;
    $('edit_npc_role').value = npc.role || '';
    $('edit_npc_mbti').value = npc.mbti_type || '';
    $('edit_npc_personality').value = npc.personality || '';
    $('edit_npc_speaking').value = npc.speaking_style || '';
    $('edit_npc_examples').value = (npc.dialogue_examples || []).join('\n');
    $('edit_npc_location').value = npc.current_location || '';
    var relType = npc.relation_to_player ? npc.relation_to_player.relation_type : '陌生人';
    var relSel = $('edit_npc_relation');
    var found = false;
    for (var i = 0; i < relSel.options.length; i++) {
      if (relSel.options[i].value === relType) { relSel.selectedIndex = i; found = true; break; }
    }
    if (!found) { relSel.value = '陌生人'; }
    $('edit_npc_favor').value = npc.relation_to_player ? npc.relation_to_player.favor : 50;
    $('edit_npc_tags').value = (npc.tags || []).join(',');
    var stats = npc.stats || {};
    $('edit_stat_health').value = stats.health || 100;
    $('edit_stat_max_health').value = stats.max_health || 100;
    $('edit_stat_strength').value = stats.strength || 5;
    $('edit_stat_agility').value = stats.agility || 5;
    $('edit_stat_intelligence').value = stats.intelligence || 5;
    $('edit_stat_luck').value = stats.luck || 5;
    var ai = npc.ai_behavior || {};
    $('edit_ai_goal').value = ai.current_goal || '';
    $('edit_ai_long_goal').value = ai.long_term_goal || '';
    $('edit_ai_style').value = ai.decision_style || 'normal';
    $('editNpcForm').style.display = 'block';
  } catch(e) {
    alert('加载角色信息失败: ' + e.message);
  }
}

async function doEditNpc() {
  if (!_editNpcId) { alert('请先选择角色'); return; }
  var tagsStr = $('edit_npc_tags').value.trim();
  var tags = tagsStr ? tagsStr.split(',').map(function(t) { return t.trim(); }).filter(function(t) { return t; }) : [];
  var body = {
    name: $('edit_npc_name').value.trim(),
    age: parseInt($('edit_npc_age').value) || 20,
    role: $('edit_npc_role').value.trim(),
    mbti_type: $('edit_npc_mbti').value.trim(),
    personality: $('edit_npc_personality').value.trim(),
    speaking_style: $('edit_npc_speaking').value.trim(),
    dialogue_examples: $('edit_npc_examples').value.split('\n').filter(function(l) { return l.trim(); }),
    location: $('edit_npc_location').value.trim(),
    relation_type: $('edit_npc_relation').value,
    favor: parseInt($('edit_npc_favor').value) || 50,
    tags: tags,
    stats: {
      health: parseInt($('edit_stat_health').value) || 100,
      max_health: parseInt($('edit_stat_max_health').value) || 100,
      strength: parseInt($('edit_stat_strength').value) || 5,
      agility: parseInt($('edit_stat_agility').value) || 5,
      intelligence: parseInt($('edit_stat_intelligence').value) || 5,
      luck: parseInt($('edit_stat_luck').value) || 5,
    },
    ai_behavior: {
      current_goal: $('edit_ai_goal').value.trim(),
      long_term_goal: $('edit_ai_long_goal').value.trim(),
      decision_style: $('edit_ai_style').value,
    },
  };
  try {
    var d = await api('PUT', '/api/npc/' + _editNpcId, body);
    if (d.error) { alert(d.error); return; }
    toast('已修改角色: ' + body.name, 'success');
    closeEditNpc();
    updateStatus();
  } catch(e) {
    alert('修改失败: ' + e.message);
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

// ===== 查看已有内容 =====
var _historyWorlds = [];

function openViewHistory() {
  $('viewHistoryModal').classList.add('on');
  loadHistoryWorlds();
}

function closeViewHistory() {
  $('viewHistoryModal').classList.remove('on');
}

async function loadHistoryWorlds() {
  var listEl = $('history_world_list');
  var contentEl = $('history_content');
  listEl.innerHTML = '<div style="color:var(--dim);font-size:.85em">加载中...</div>';
  contentEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:30px">选择一个存档查看内容</div>';
  try {
    var d = await api('GET', '/api/saves');
    var saves = d.saves || [];
    _historyWorlds = saves;
    if (saves.length === 0) {
      listEl.innerHTML = '<div style="color:var(--dim);font-size:.85em">暂无存档</div>';
      return;
    }
    var html = '<div style="display:flex;flex-wrap:wrap;gap:6px">';
    saves.forEach(function(save) {
      html += '<div class="abtn" onclick="loadHistoryContent(\'' + save.world_id + '\')" style="cursor:pointer;padding:5px 12px;font-size:.82em">' + (save.world_name || save.world_id) + '</div>';
    });
    html += '</div>';
    listEl.innerHTML = html;
  } catch(e) {
    listEl.innerHTML = '<div style="color:var(--dim);font-size:.85em">加载失败</div>';
  }
}

async function loadHistoryContent(worldId) {
  var contentEl = $('history_content');
  contentEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载叙事内容...</div>';
  try {
    var d = await api('GET', '/api/narrative-history/' + worldId);
    var entries = d.entries || [];
    if (entries.length === 0) {
      contentEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:30px">该存档暂无叙事内容</div>';
      return;
    }
    var html = '';
    var lastDay = 0;
    entries.forEach(function(entry) {
      if (entry.day !== lastDay) {
        html += '<div style="margin:16px 0 8px;padding:6px 12px;background:var(--bg);border:1px solid var(--border);border-radius:5px;font-size:.82em;color:var(--dim)">📅 第' + entry.day + '天 · ' + entry.time + '</div>';
        lastDay = entry.day;
      }
      if (entry.entry_type === 'player_input') {
        html += '<div style="margin:8px 0;padding:8px 14px;background:rgba(201,169,110,.06);border-left:3px solid var(--gold);border-radius:0 5px 5px 0;font-size:.88em"><span style="color:var(--gold);font-weight:700;font-size:.78em">你：</span>' + escapeHtml(entry.player_input) + '</div>';
      } else if (entry.entry_type === 'narrative') {
        html += '<div style="margin:8px 0;padding:8px 14px;font-size:.88em;line-height:1.8">' + escapeHtml(entry.narrative) + '</div>';
        if (entry.image_url) {
          html += '<div style="margin:8px 0;text-align:center"><img src="' + escHtml(entry.image_url) + '" style="max-width:100%;border-radius:6px;border:1px solid var(--border)"></div>';
        }
      } else if (entry.entry_type === 'event') {
        html += '<div style="margin:8px 0;padding:8px 14px;background:rgba(100,150,200,.06);border-left:3px solid #6a9ac9;border-radius:0 5px 5px 0;font-size:.85em;color:#8ab"><span style="font-weight:700;font-size:.78em">🌍 世界事件：</span>' + escapeHtml(entry.narrative) + '</div>';
      }
    });
    contentEl.innerHTML = html;
    contentEl.scrollTop = 0;
  } catch(e) {
    contentEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
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
  ['map', 'graph', 'timeline', 'events'].forEach(function(t) {
    var panel = $('wp_' + t);
    var tabEl = $('wp_tab_' + t);
    if (panel) panel.classList.toggle('active', t === tab);
    if (tabEl) tabEl.classList.toggle('active', t === tab);
  });
  if (tab === 'map') loadWorldMap();
  else if (tab === 'graph') loadWorldGraph();
  else if (tab === 'timeline') loadWorldTimeline();
  else if (tab === 'events') loadWorldEvents();
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

// ========== 角色卡功能 ==========

var _currentDetailNpcId = null;

function showNpcDetail(npcId) {
  _currentDetailNpcId = npcId;
  loadWhoDetail(npcId);
}

async function exportCharacterCard() {
  if (!_currentDetailNpcId) {
    toast('请先选择一个角色', 'error');
    return;
  }
  try {
    var card = await api('GET', '/api/npc/' + _currentDetailNpcId + '/card');
    var blob = new Blob([JSON.stringify(card, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = (card.data?.name || 'character') + '_card.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast('角色卡已导出', 'success');
  } catch (e) {
    toast('导出失败: ' + e.message, 'error');
  }
}

async function handleCardImport(input) {
  var file = input.files[0];
  if (!file) return;

  $('cardImportInfo').textContent = '正在导入...';

  // [v12] 确保 Token 已准备好
  await ensureToken();

  try {
    var formData = new FormData();
    formData.append('file', file);

    var headers = {};
    if (window.ACCESS_TOKEN) {
      headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
    }

    var resp = await fetch(API_BASE + '/api/npc/card/import', {
      method: 'POST',
      headers: headers,
      body: formData,
    });

    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status);
    }

    var res = await resp.json();

    if (res.status === 'ok') {
      $('cardImportInfo').textContent = '✓ 已导入: ' + res.name;
      toast('角色卡导入成功: ' + res.name, 'success');
      if (typeof refreshNpcList === 'function') refreshNpcList();
      if (typeof loadWhoIsWho === 'function') loadWhoIsWho();
      setTimeout(function() {
        closeAddNpc();
      }, 1000);
    } else {
      $('cardImportInfo').textContent = '导入失败';
      toast('导入失败', 'error');
    }
  } catch (e) {
    $('cardImportInfo').textContent = '导入失败';
    toast('导入失败: ' + e.message, 'error');
  }

  input.value = '';
}

// ========== 世界书功能 ==========

var _pendingLorebook = null;

function handleLorebookUpload(input) {
  var file = input.files[0];
  if (!file) return;

  var reader = new FileReader();
  reader.onload = function(e) {
    try {
      var data = JSON.parse(e.target.result);
      _pendingLorebook = data;
      var count = 0;
      if (data.entries && typeof data.entries === 'object') {
        count = Object.keys(data.entries).length;
      } else if (Array.isArray(data)) {
        count = data.length;
      } else if (data.entries && Array.isArray(data.entries)) {
        count = data.entries.length;
      }
      $('lorebookFileInfo').textContent = '✓ 已加载 ' + count + ' 条设定';
      toast('世界书已加载，生成世界时会使用', 'success');
    } catch (err) {
      $('lorebookFileInfo').textContent = '文件格式错误';
      toast('世界书文件格式错误: ' + err.message, 'error');
      _pendingLorebook = null;
    }
  };
  reader.readAsText(file);
}
