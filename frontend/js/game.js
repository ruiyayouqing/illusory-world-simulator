// 太虚幻境 v8 — 游戏: 交互/操作（核心游戏流程）

// [Bug] GS 全局游戏状态变量，必须显式声明，否则在 showGame() 中访问会报 "GS is not defined"
var GS = null;
// [Bug] 清除小说角色扮演标记（正常创建/加载游戏时恢复背景轮换）
window._isNovelRoleplay = false;

var actionCount = 0;
var lastImageAction = 0;

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
    // [Bug] restoreHistory 会清空叙事面板，世界观简介需在它之后插入到顶部
    if (slotRes.world_intro) {
      var nb = $('nb');
      var introTitle = document.createElement('p');
      introTitle.className = 'event';
      introTitle.innerHTML = sanitizeHTML('【' + (GS.world?.name || '新世界') + ' 世界观简介】').replace(/\n/g, '<br>');
      var introBody = document.createElement('p');
      introBody.className = 'narrative';
      introBody.innerHTML = sanitizeHTML(slotRes.world_intro).replace(/\n/g, '<br>');
      nb.insertBefore(introBody, nb.firstChild);
      nb.insertBefore(introTitle, introBody);
    }
    // 优先使用 slot 加载后重新生成的选项（更符合当前状态）
    const opts = (slotRes.initial_options && slotRes.initial_options.length) ? slotRes.initial_options : (d.initial_options || []);
    if (opts.length) showOpts(opts);
    updateStatus();
    // [v1.5 第一期] 加载槽位后渲染事件列表
    if (typeof updateEvents === 'function') updateEvents(d.player_events, d.world_events);
    // [v1.6] 江湖见闻面板自动刷新
    if (typeof refreshRumors === 'function') refreshRumors(true);
    // [v1.6] 思维树面板自动刷新
    if (typeof refreshPlannerPanel === 'function') refreshPlannerPanel();
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
    _loadingTextEl.textContent = '正在初始化虚拟世界，大约需要3-5分钟时间';
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
    document.getElementById('createWorldBtn').textContent = '正在初始化虚拟世界，大约需要3-5分钟时间...';
  }
  document.getElementById('createWorldBtn').disabled = true;
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
    }, 600000);
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
    document.getElementById('createWorldBtn').textContent = '生成世界并开始冒险';
    document.getElementById('createWorldBtn').disabled = false;
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
    // [Bug] restoreHistory 会清空叙事面板，世界观简介需在它之后插入到顶部
    if (d.world_intro) {
      var nb = $('nb');
      var introTitle = document.createElement('p');
      introTitle.className = 'event';
      introTitle.innerHTML = sanitizeHTML('【' + (GS.world?.name || '新世界') + ' 世界观简介】').replace(/\n/g, '<br>');
      var introBody = document.createElement('p');
      introBody.className = 'narrative';
      introBody.innerHTML = sanitizeHTML(d.world_intro).replace(/\n/g, '<br>');
      nb.insertBefore(introBody, nb.firstChild);
      nb.insertBefore(introTitle, introBody);
    }
    if (d.initial_options && d.initial_options.length) showOpts(d.initial_options);
    updateStatus();
    // [v1.5 第一期] 加载存档后渲染事件列表
    if (typeof updateEvents === 'function') updateEvents(d.player_events, d.world_events);
    // [v1.6] 江湖见闻面板自动刷新
    if (typeof refreshRumors === 'function') refreshRumors(true);
    // [v1.6] 思维树面板自动刷新
    if (typeof refreshPlannerPanel === 'function') refreshPlannerPanel();
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
  var _p = ['createWorldPage','loadSavePage','novelRoleplayPage'];
  _p.forEach(function(id){var el=document.getElementById(id); if(el) el.style.display='none';});
  loadSaves();
  if (typeof setHomeBackground === 'function') {
    setHomeBackground();
  }
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
        // [Bug 修复] 屏蔽天数显示，只显示时段/季节/天气
        addNarrative('⏰ ' + GS.time_status.season + '·' + GS.time_status.time_of_day + ' | ' + GS.time_status.weather, false, false);
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

async function sendInput(t, fromOption, retry) {
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
    return sendInputStream(t, retry);
  }
  // 否则使用传统 HTTP 模式
  return sendInputHTTP(t, retry);
}

function sendInputStream(t, retry) {
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

  // [v12.7] 记录本次是否为重试：重试时 processStreamResult 需先清理旧叙事块，
  // 否则旧叙事（多段落 wrapper）残留 + 新叙事追加 → 界面"上下重复"
  var _streamIsRetry = !!retry;

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
    // [v1.5 第一期] 叙事流结束后检查紧急事件弹窗（避免在流式输出中打断）
    if (typeof checkUrgentPopup === 'function') checkUrgentPopup();
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

  // 发送流式输入请求（[v12.1] 重试带 retry 标记）
  sendWS({ type: 'stream_input', text: t, retry: !!retry });

  // 设置超时回退（120秒后如果还没有流式响应，回退到 HTTP）
  var streamTimeout = setTimeout(function() {
    if (!streamText && !finalResult) {
      // 流式失败，清理并回退
      if (streamP.parentNode) streamP.remove();
      wsOnToken = null; wsOnResult = null; wsOnStreamEnd = null; wsOnThinking = null;
      sendInputHTTP(t, retry);
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

  // [v12.7] 重试路径：先清理旧叙事块（最后一个 player-input 之后的所有节点），
  // 再渲染新叙事，避免旧叙事残留 + 新叙事追加导致"上下重复"。
  // HTTP retry 路径（ui.js retryNarrativeCore）已做同样清理。
  if (typeof _streamIsRetry !== 'undefined' && _streamIsRetry) {
    var nb2 = $('nb');
    var lastInput = null;
    var kids = Array.from(nb2.children);
    for (var i = kids.length - 1; i >= 0; i--) {
      if (kids[i].classList && kids[i].classList.contains('player-input')) {
        lastInput = kids[i];
        break;
      }
    }
    if (lastInput) {
      var startIdx = kids.indexOf(lastInput);
      for (var j = kids.length - 1; j > startIdx; j--) {
        if (kids[j].parentNode) kids[j].remove();
      }
    }
    _streamIsRetry = false;
  }
  // 显示叙事
  if (r.narrative) addNarrative(r.narrative, false, false);
  // [v1.6 P1-7] 显示长期记忆引用徽章
  if (r.milestone_detected) {
    showMilestoneBadge(r.milestone_detected);
  }
  fetchLongTermRefs();
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

  // [v1.5 第一期] 玩家回合可能跨日 → 跨日会生成新事件，异步刷新事件列表
  if (typeof refreshEventsList === 'function') refreshEventsList();
  // [v1.6] 江湖见闻：玩家回合后异步刷新（自带节流）
  if (typeof refreshRumors === 'function') refreshRumors();
  // [v1.6] 思维树：玩家回合后异步刷新
  if (typeof refreshPlannerPanel === 'function') refreshPlannerPanel();
}

async function sendInputHTTP(t, retry) {
  try {
    // [v12.1] 重试带 retry 标记：后端回滚到输入前快照再重新生成
    var d = await api('POST', '/api/input', {input: t, retry: !!retry});
    if (d.error) { addNarrative(d.error); return; }
    var r = d.result;
    // [v12.7] retry 路径先清理旧叙事块（最后一个 player-input 之后），避免"上下重复"
    if (retry) {
      var nbR = $('nb');
      var kidsR = Array.from(nbR.children);
      var lastInputR = null;
      for (var iR = kidsR.length - 1; iR >= 0; iR--) {
        if (kidsR[iR].classList && kidsR[iR].classList.contains('player-input')) {
          lastInputR = kidsR[iR];
          break;
        }
      }
      if (lastInputR) {
        var sIdx = kidsR.indexOf(lastInputR);
        for (var jR = kidsR.length - 1; jR > sIdx; jR--) {
          if (kidsR[jR].parentNode) kidsR[jR].remove();
        }
      }
    }
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
    // [v1.5 第一期] HTTP 回退路径同样刷新事件列表（玩家回合可能跨日）
    if (typeof refreshEventsList === 'function') refreshEventsList();
    // [v1.6] 江湖见闻：HTTP 回退路径同样刷新（自带节流）
    if (typeof refreshRumors === 'function') refreshRumors();
    // [v1.6] 思维树：HTTP 回退路径同样刷新
    if (typeof refreshPlannerPanel === 'function') refreshPlannerPanel();
    // 流式失败回退到此处的 HTTP 路径，同样需要检查紧急事件弹窗
    if (typeof checkUrgentPopup === 'function') checkUrgentPopup();
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
  // 重新发送输入（[v12.1] retry=true：后端回滚到输入前快照再重新生成）
  await sendInput(originalInput, false, true);
}

async function doSave() {
  await api('POST', '/api/save');
  addSystem('[已保存]');
}
