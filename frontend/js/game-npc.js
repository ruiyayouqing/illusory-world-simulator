// 太虚幻境 — NPC对话 + NPC编辑 + 角色卡（从game.js拆分）

// ═══════════════════════════════════════════════════════════════
// 功能2：角色聊天系统
// ═══════════════════════════════════════════════════════════════

var _currentChatNpcId = null;
var _chatHistory = {};
var _npcChatPanelOpen = false;

function toggleNpcChatPanel() {
  var panel = $('npcChatPanel');
  if (!panel) return;
  
  _npcChatPanelOpen = !_npcChatPanelOpen;
  if (_npcChatPanelOpen) {
    panel.classList.add('on');
    loadChatNpcList();
  } else {
    panel.classList.remove('on');
  }
}

function openNpcChat() {
  toggleNpcChatPanel();
}

function closeNpcChat() {
  if (_npcChatPanelOpen) {
    toggleNpcChatPanel();
  }
}

async function loadChatNpcList() {
  var listEl = $('chatNpcList');
  if (!listEl) return;

  try {
    var npcRes = await api('GET', '/api/npcs');
    var npcs = npcRes.npcs || [];
    var playerName = '主角';
    if (GS && GS.player && GS.player.name) {
      playerName = GS.player.name;
    }

    var html = '<div class="chat-npc-item" onclick="selectChatNpc(\'player\', \'' + playerName + '\')" style="padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:6px;background:var(--panel);border:1px solid var(--border);transition:all .2s hover:border-color:var(--gold);display:flex;align-items:center;gap:10px">';
    html += '<div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg, var(--gold), #b8953f);display:flex;align-items:center;justify-content:center;color:#0d0c0f;font-weight:700;font-size:.85em">我</div>';
    html += '<div><div style="font-weight:600;color:var(--text-bright)">' + playerName + '</div><div style="font-size:.75em;color:var(--dim)">主角</div></div></div>';

    if (!npcs || npcs.length === 0) {
      html += '<div style="color:var(--dim);font-size:.8em;text-align:center;padding:20px">暂无其他角色</div>';
    } else {
      npcs.forEach(function(npc) {
        var npcId = npc.id || '';
        var npcName = npc.name || '未知';
        var npcRole = npc.role || '';
        var relation = npc.relation_type || '陌生人';
        var avatar = npcName.charAt(0);
        html += '<div class="chat-npc-item" onclick="selectChatNpc(\'' + npcId + '\', \'' + npcName + '\')" style="padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:6px;background:var(--panel);border:1px solid var(--border);transition:all .2s hover:border-color:var(--gold);display:flex;align-items:center;gap:10px">';
        html += '<div style="width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg, #5a8bc9, #4a7bb9);display:flex;align-items:center;justify-content:center;color:white;font-weight:700;font-size:.85em">' + avatar + '</div>';
        html += '<div><div style="font-weight:600;color:var(--text-bright)">' + npcName + '</div><div style="font-size:.75em;color:var(--dim)">' + (npcRole || relation) + '</div></div></div>';
      });
    }

    listEl.innerHTML = html;
  } catch(e) {
    listEl.innerHTML = '<div style="color:#e07a7a;font-size:.8em;text-align:center;padding:20px">加载失败</div>';
    console.error('loadChatNpcList failed:', e);
  }
}

function selectChatNpc(npcId, npcName) {
  _currentChatNpcId = npcId;
  $('chatHeader').textContent = '💬 ' + npcName;

  var messagesEl = $('chatMessages');
  if (!_chatHistory[npcId]) {
    _chatHistory[npcId] = [];
    messagesEl.innerHTML = '<div style="color:var(--dim);font-size:.85em;text-align:center;padding:30px">开始与 ' + npcName + ' 聊天...</div>';
  } else {
    renderChatMessages(npcId);
  }

  $('chatInput').focus();
}

function renderChatMessages(npcId) {
  var messagesEl = $('chatMessages');
  var history = _chatHistory[npcId] || [];

  if (history.length === 0) {
    messagesEl.innerHTML = '<div style="color:var(--dim);font-size:.85em;text-align:center;padding:30px">开始聊天...</div>';
    return;
  }

  var html = '';
  history.forEach(function(msg) {
    if (msg.role === 'user') {
      html += '<div style="display:flex;justify-content:flex-end"><div style="max-width:70%;padding:10px 14px;background:var(--gold);color:#0d0c0f;border-radius:12px 12px 0 12px;font-size:.9em;line-height:1.6">' + msg.content + '</div></div>';
    } else {
      html += '<div style="display:flex;justify-content:flex-start"><div style="max-width:70%;padding:10px 14px;background:var(--panel);color:var(--text);border-radius:12px 12px 12px 0;font-size:.9em;line-height:1.6">' + msg.content + '</div></div>';
    }
  });

  messagesEl.innerHTML = html;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

var _chatStreaming = false;
var _chatStreamContent = "";
var _chatReplyAdded = false;
var _chatTargetNpcId = null;

function sendChatMessage() {
  var input = $('chatInput');
  var message = input.value.trim();

  if (!message || !_currentChatNpcId) return;
  if (_chatStreaming) return;

  var messagesEl = $('chatMessages');

  _chatHistory[_currentChatNpcId].push({role: 'user', content: message});
  renderChatMessages(_currentChatNpcId);

  input.value = '';

  _chatStreaming = true;
  _chatStreamContent = "";
  _chatReplyAdded = false;
  _chatTargetNpcId = _currentChatNpcId;

  var assistantDiv = document.createElement('div');
  assistantDiv.id = 'chatStreamBubble';
  assistantDiv.style.cssText = 'display:flex;justify-content:flex-start';
  assistantDiv.innerHTML = '<div style="max-width:70%;padding:10px 14px;background:var(--panel);color:var(--text);border-radius:12px 12px 12px 0;font-size:.9em;line-height:1.6"><span style="color:var(--dim)">正在输入...</span></div>';
  messagesEl.appendChild(assistantDiv);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  wsOnNpcChatToken = function(token) {
    if (!_chatStreaming) return;
    _chatStreamContent += token;
    if (_chatTargetNpcId === _currentChatNpcId) {
      var bubble = document.getElementById('chatStreamBubble');
      if (bubble) {
        var inner = bubble.querySelector('div');
        if (inner) {
          inner.innerHTML = _chatStreamContent.replace(/\n/g, '<br>');
        }
        var msgsEl = $('chatMessages');
        if (msgsEl) {
          msgsEl.scrollTop = msgsEl.scrollHeight;
        }
      }
    }
  };

  wsOnNpcChatEnd = function() {
    _chatStreaming = false;
    wsOnNpcChatToken = null;
    wsOnNpcChatEnd = null;

    var targetId = _chatTargetNpcId;

    if (_chatStreamContent.trim()) {
      if (!_chatHistory[targetId]) {
        _chatHistory[targetId] = [];
      }
      _chatHistory[targetId].push({role: 'assistant', content: _chatStreamContent});
      _chatReplyAdded = true;
      if (targetId === _currentChatNpcId) {
        var streamBubble = document.getElementById('chatStreamBubble');
        if (streamBubble) {
          streamBubble.remove();
        }
        renderChatMessages(targetId);
      }
    } else {
      if (targetId === _currentChatNpcId) {
        var streamBubble2 = document.getElementById('chatStreamBubble');
        if (streamBubble2) {
          streamBubble2.remove();
        }
      }
    }
    _chatStreamContent = "";
  };

  if (ws && ws.readyState === WebSocket.OPEN) {
    sendWS({
      type: 'npc_chat',
      npc_id: _currentChatNpcId,
      message: message,
      history: _chatHistory[_currentChatNpcId]
    });
  } else {
    _chatStreaming = false;
    var bubble = document.getElementById('chatStreamBubble');
    if (bubble) bubble.remove();
    _chatHistory[_currentChatNpcId].push({role: 'assistant', content: 'WebSocket 未连接，请检查网络。'});
    renderChatMessages(_currentChatNpcId);
  }
}

function handleNpcChatResult(result) {
  wsOnNpcChatToken = null;
  wsOnNpcChatEnd = null;

  var targetId = _chatTargetNpcId;

  if (_chatStreaming) {
    _chatStreaming = false;
    if (targetId === _currentChatNpcId) {
      var streamBubble = document.getElementById('chatStreamBubble');
      if (streamBubble) {
        streamBubble.remove();
      }
    }
  }

  if (result && result.success && result.message) {
    if (!_chatReplyAdded) {
      if (!_chatHistory[targetId]) {
        _chatHistory[targetId] = [];
      }
      _chatHistory[targetId].push({role: 'assistant', content: result.message});
      if (targetId === _currentChatNpcId) {
        renderChatMessages(targetId);
      }
    }
  } else if (!_chatReplyAdded) {
    var errorMsg = result && result.error ? result.error : '聊天失败';
    if (!_chatHistory[targetId]) {
      _chatHistory[targetId] = [];
    }
    _chatHistory[targetId].push({role: 'assistant', content: '❌ ' + errorMsg});
    if (targetId === _currentChatNpcId) {
      renderChatMessages(targetId);
    }
  }

  _chatStreamContent = "";
  _chatReplyAdded = false;
  _chatTargetNpcId = null;
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

// ===== [用户需求] AI 自动生成 NPC（预览-确认流程）=====
var _aiSpawning = false;
var _aiSpawnDesigns = [];  // 当前预览的 designs

async function doAiSpawn() {
  // 防连点
  if (_aiSpawning) return;
  var btn = $('btn_ai_spawn');
  var resultEl = $('ai_spawn_result');
  var isRegen = _aiSpawnDesigns.length > 0;  // 是否为"重新生成"
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.6';
    btn.style.pointerEvents = 'none';
    btn.textContent = '🤖 AI 生成中...（可能需要 30-60 秒）';
  }
  if (resultEl) {
    resultEl.style.display = 'block';
    // [Bug] 重新生成时保留旧预览，不清空，避免"一闪而过"
    // 仅在首次生成（无旧预览）时显示 loading 文字
    if (!isRegen) {
      resultEl.style.color = 'var(--dim)';
      resultEl.textContent = '⏳ AI 正在读取世界设定并生成角色，请稍候...';
    } else {
      // 重新生成：在预览列表顶部加 loading 提示，不清空列表
      var listEl = $('ai_spawn_list');
      if (listEl) {
        // 在列表前插入 loading 横幅
        var banner = document.createElement('div');
        banner.id = 'ai_spawn_regen_banner';
        banner.style.cssText = 'padding:6px 10px;margin-bottom:8px;background:rgba(212,175,55,.1);border:1px solid rgba(212,175,55,.3);border-radius:6px;color:var(--gold);font-size:.85em';
        banner.textContent = '⏳ AI 重新生成中，请稍候...（旧预览仍可参考）';
        resultEl.insertBefore(banner, resultEl.firstChild);
      } else {
        resultEl.style.color = 'var(--dim)';
        resultEl.textContent = '⏳ AI 正在重新生成角色，请稍候...';
      }
    }
  }
  _aiSpawning = true;

  var count = parseInt($('ai_spawn_count').value) || 5;
  var focus = $('ai_spawn_focus').value;
  var requirement = $('ai_spawn_requirement').value.trim();

  try {
    // [Bug] 加最小加载时间，防止缓存命中时 4ms 完成导致"一闪而过"
    var startTime = Date.now();
    var d = await api('POST', '/api/npc/ai-spawn-preview', {
      count: count,
      focus: focus,
      requirement: requirement,
    });
    var elapsed = Date.now() - startTime;
    if (elapsed < 1500) {
      await new Promise(function(r) { setTimeout(r, 1500 - elapsed); });
    }
    if (d && d.error) {
      if (resultEl) {
        resultEl.style.color = 'var(--accent)';
        resultEl.textContent = '❌ ' + d.error;
      }
      toast('AI 生成失败: ' + d.error, 'error');
    } else if (d && d.status === 'ok') {
      var designs = d.designs || [];
      if (designs.length === 0) {
        if (resultEl) {
          resultEl.style.color = 'var(--dim)';
          resultEl.textContent = d.message || 'AI 未返回有效角色设定，请换个需求试试';
        }
        toast('AI 未生成角色，请调整需求重试', 'info');
      } else {
        // 展示预览，让玩家选择确认或重新生成
        _aiSpawnDesigns = designs;
        renderAiSpawnPreview(designs, resultEl);
      }
    } else {
      if (resultEl) {
        resultEl.style.color = 'var(--accent)';
        resultEl.textContent = '❌ 未知响应，请重试';
      }
    }
  } catch(e) {
    if (resultEl) {
      resultEl.style.color = 'var(--accent)';
      resultEl.textContent = '❌ 请求失败: ' + (e.message || e);
    }
    toast('AI 生成请求失败: ' + (e.message || e), 'error');
  } finally {
    _aiSpawning = false;
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '';
      btn.style.pointerEvents = '';
      btn.textContent = '🤖 让 AI 生成角色';
    }
  }
}

function renderAiSpawnPreview(designs, resultEl) {
  if (!resultEl) return;
  var html = '<div style="color:var(--gold);margin-bottom:8px;font-weight:600">📋 AI 生成了 ' + designs.length + ' 个角色，请确认：</div>';
  html += '<div style="font-size:.78em;color:var(--dim);margin-bottom:10px">取消勾选不想要的角色，然后点"确认加入"</div>';
  html += '<div id="ai_spawn_list">';
  designs.forEach(function(d, i) {
    var name = escapeHtml(d.name || '');
    var role = escapeHtml(d.role || '');
    var age = d.age || '';
    var personality = escapeHtml(d.personality || '');
    var goal = escapeHtml(d.long_term_goal || '');
    var loc = escapeHtml(d.location || '');
    var rel = escapeHtml(d.relation_to_player || '');
    html += '<div style="padding:8px 10px;margin-bottom:6px;background:var(--panel-light);border:1px solid var(--border);border-radius:6px">';
    html += '<label style="display:flex;align-items:flex-start;gap:8px;cursor:pointer">';
    html += '<input type="checkbox" class="ai-spawn-check" data-idx="' + i + '" checked style="margin-top:3px;flex-shrink:0">';
    html += '<div style="flex:1">';
    html += '<div><b style="color:var(--gold)">' + name + '</b>';
    if (role) html += ' · ' + role;
    if (age) html += ' · ' + age + '岁';
    if (loc) html += ' · 📍' + loc;
    html += '</div>';
    if (personality) html += '<div style="color:var(--dim);font-size:.9em;margin-top:2px">' + personality + '</div>';
    if (goal) html += '<div style="color:var(--dim);font-size:.85em;margin-top:2px">🎯 ' + goal + '</div>';
    if (rel) html += '<div style="color:var(--dim);font-size:.85em">与玩家：' + rel + '</div>';
    html += '</div></label></div>';
  });
  html += '</div>';
  // 按钮区
  html += '<div style="display:flex;gap:8px;margin-top:12px">';
  html += '<button onclick="confirmAiSpawn()" style="flex:2;padding:8px 14px;background:var(--gold);border:none;border-radius:6px;color:#0d0c0f;font-weight:600;font-size:.85em;cursor:pointer">✅ 确认加入世界</button>';
  html += '<button onclick="doAiSpawn()" style="flex:1;padding:8px 14px;background:var(--panel-light);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:.85em;cursor:pointer">🔄 重新生成</button>';
  html += '</div>';
  resultEl.style.color = 'var(--text)';
  resultEl.innerHTML = html;
  // [Bug] 渲染后滚动 modal-box 到顶部，确保用户能看到新内容
  var modalBox = resultEl.closest('.modal-box');
  if (modalBox) {
    modalBox.scrollTop = 0;
  }
}

async function confirmAiSpawn() {
  // 收集选中的 designs
  var checks = document.querySelectorAll('.ai-spawn-check');
  var selected = [];
  checks.forEach(function(cb) {
    if (cb.checked) {
      var idx = parseInt(cb.dataset.idx);
      if (_aiSpawnDesigns[idx]) selected.push(_aiSpawnDesigns[idx]);
    }
  });

  if (selected.length === 0) {
    toast('请至少选择一个角色', 'info');
    return;
  }

  var resultEl = $('ai_spawn_result');
  var aiCmdEl = $('ai_cmd_result');  // [2026-08-09] 给AI下命令弹窗的结果容器（可选）
  var btn = $('btn_ai_spawn');
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '0.6';
    btn.style.pointerEvents = 'none';
    btn.textContent = '⏳ 加入世界中...';
  }
  if (resultEl) {
    resultEl.style.color = 'var(--dim)';
    resultEl.textContent = '⏳ 正在将 ' + selected.length + ' 个角色加入世界...';
  }
  if (aiCmdEl) {
    aiCmdEl.style.color = 'var(--dim)';
    aiCmdEl.textContent = '⏳ 正在将 ' + selected.length + ' 个角色加入世界...';
  }

  try {
    var d = await api('POST', '/api/npc/ai-spawn-confirm', { designs: selected });
    if (d && d.error) {
      if (resultEl) {
        resultEl.style.color = 'var(--accent)';
        resultEl.textContent = '❌ ' + d.error;
      }
      toast('加入失败: ' + d.error, 'error');
    } else if (d && d.status === 'ok') {
      var spawned = d.npcs || [];
      var html = '✅ 成功加入 ' + spawned.length + ' 个角色到世界中！';
      if (d.skipped > 0) html += '（跳过 ' + d.skipped + ' 个无效设定）';
      html += '<ul style="margin:6px 0 0 18px;padding:0;line-height:1.8">';
      spawned.forEach(function(n) {
        html += '<li><b style="color:var(--gold)">' + escapeHtml(n.name) + '</b>';
        if (n.role) html += ' · ' + escapeHtml(n.role);
        html += '</li>';
      });
      html += '</ul>';
      if (resultEl) {
        resultEl.style.color = 'var(--text)';
        resultEl.innerHTML = html;
      }
      if (aiCmdEl) {
        aiCmdEl.style.color = 'var(--text)';
        aiCmdEl.innerHTML = html;
        var aiCmdStatusEl = $('ai_cmd_status');
        if (aiCmdStatusEl) aiCmdStatusEl.textContent = '';
      }
      toast('已加入 ' + spawned.length + ' 个角色到世界', 'success');
      _aiSpawnDesigns = [];
      updateStatus();
    }
  } catch(e) {
    if (resultEl) {
      resultEl.style.color = 'var(--accent)';
      resultEl.textContent = '❌ 请求失败: ' + (e.message || e);
    }
    toast('加入请求失败: ' + (e.message || e), 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.style.opacity = '';
      btn.style.pointerEvents = '';
      btn.textContent = '🤖 让 AI 生成角色';
    }
  }
}

// ===== [2026-08-09] 🤖 给AI下命令（游戏右侧快捷入口，复用 ai-spawn 预览-确认流程）=====
function openAiCommand() {
  var m = $('aiCommandModal');
  if (m) m.classList.add('on');
}

function closeAiCommand() {
  var m = $('aiCommandModal');
  if (m) m.classList.remove('on');
}

async function sendAiCommand() {
  var input = $('ai_cmd_input');
  var statusEl = $('ai_cmd_status');
  var resultEl = $('ai_cmd_result');
  var cmd = input ? input.value.trim() : '';
  if (!cmd) {
    toast('请先输入指令', 'info');
    return;
  }
  if (_aiSpawning) return;
  _aiSpawning = true;
  if (statusEl) statusEl.textContent = '⏳ AI 正在执行指令，请稍候（30-60秒）...';
  if (resultEl) {
    resultEl.style.display = 'block';
    resultEl.style.color = 'var(--dim)';
    resultEl.textContent = '⏳ AI 正在读取世界设定并执行指令...';
  }
  try {
    var d = await api('POST', '/api/npc/ai-spawn-preview', {
      count: 1,
      focus: 'custom',
      requirement: cmd,
    });
    if (d && d.error) {
      if (resultEl) { resultEl.style.color = 'var(--accent)'; resultEl.textContent = '❌ ' + d.error; }
      toast('指令执行失败: ' + d.error, 'error');
    } else if (d && d.status === 'ok') {
      var designs = d.designs || [];
      if (designs.length === 0) {
        if (resultEl) { resultEl.style.color = 'var(--dim)'; resultEl.textContent = d.message || 'AI 未返回可执行结果，请换个指令试试'; }
        toast('AI 未生成结果，请调整指令', 'info');
      } else {
        _aiSpawnDesigns = designs;
        renderAiSpawnPreview(designs, resultEl);
        if (statusEl) statusEl.textContent = '';
      }
    } else {
      if (resultEl) { resultEl.style.color = 'var(--accent)'; resultEl.textContent = '❌ 未知响应，请重试'; }
    }
  } catch(e) {
    if (resultEl) { resultEl.style.color = 'var(--accent)'; resultEl.textContent = '❌ 请求失败: ' + (e.message || e); }
    toast('指令请求失败: ' + (e.message || e), 'error');
  } finally {
    _aiSpawning = false;
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
    // [功能一] 主角选项置顶，value 固定为 __player__
    var playerOpt = document.createElement('option');
    playerOpt.value = '__player__';
    var playerName = (GS && GS.player && GS.player.name) ? GS.player.name : '主角';
    playerOpt.textContent = '★ ' + playerName + '（主角）';
    sel.appendChild(playerOpt);
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

// [功能一] 切换"主角/配角"表单字段的显隐
function setEditFormMode(isPlayer) {
  var npcOnlyEls = document.querySelectorAll('#editNpcForm .npc-only');
  for (var i = 0; i < npcOnlyEls.length; i++) {
    npcOnlyEls[i].style.display = isPlayer ? 'none' : '';
  }
}

async function loadNpcForEdit() {
  var npcId = $('edit_npc_select').value;
  if (!npcId) {
    $('editNpcForm').style.display = 'none';
    return;
  }
  _editNpcId = npcId;

  // [功能一] 主角分支：加载精简表单
  if (npcId === '__player__') {
    try {
      var pd = await api('GET', '/api/player/profile');
      if (pd.error) { alert(pd.error); return; }
      var p = pd.player || {};
      $('edit_npc_name').value = p.name || '';
      $('edit_npc_age').value = p.age || 18;
      setEditFormMode(true);
      $('editNpcForm').style.display = 'block';
    } catch(e) {
      alert('加载主角信息失败: ' + e.message);
    }
    return;
  }

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
    // [功能二] 根据 hidden 状态切换按钮文案
    updateHideButton(npc.hidden || false);
    setEditFormMode(false);
    $('editNpcForm').style.display = 'block';
  } catch(e) {
    alert('加载角色信息失败: ' + e.message);
  }
}

async function doEditNpc() {
  if (!_editNpcId) { alert('请先选择角色'); return; }

  // [功能一] 主角分支：调用 /api/player/profile
  if (_editNpcId === '__player__') {
    var playerName = $('edit_npc_name').value.trim();
    if (!playerName) { alert('请输入主角名字'); return; }
    try {
      var pd = await api('PUT', '/api/player/profile', {
        name: playerName,
        age: parseInt($('edit_npc_age').value) || 18,
      });
      if (pd.error) { alert(pd.error); return; }
      toast('已修改主角: ' + playerName, 'success');
      closeEditNpc();
      updateStatus();
    } catch(e) {
      alert('修改主角失败: ' + e.message);
    }
    return;
  }

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

// ===== [功能二] 隐藏/恢复 NPC =====
function updateHideButton(isHidden) {
  var btn = $('btn_toggle_hide_npc');
  if (!btn) return;
  if (isHidden) {
    btn.textContent = '👁️ 恢复角色';
    btn.setAttribute('data-hidden', '1');
  } else {
    btn.textContent = '🙈 隐藏角色';
    btn.setAttribute('data-hidden', '0');
  }
}

async function toggleHideNpc() {
  if (!_editNpcId || _editNpcId === '__player__') {
    toast('主角不支持隐藏', 'info');
    return;
  }
  var btn = $('btn_toggle_hide_npc');
  var willHide = btn.getAttribute('data-hidden') !== '1';
  try {
    var d = await api('POST', '/api/npc/' + _editNpcId + '/toggle-hide', { hidden: willHide });
    if (d.error) { alert(d.error); return; }
    updateHideButton(d.hidden);
    toast(d.hidden ? '已隐藏角色' : '已恢复角色', 'success');
    updateStatus();
  } catch(e) {
    alert('操作失败: ' + e.message);
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
