// 太虚幻境 — 世界创建辅助 + 世界观 + 历史世界（从game.js拆分）

var _generatedWorldview = "";

function openWorldviewModal() {
  $('worldviewModal').classList.add('on');
}

function closeWorldviewModal() {
  $('worldviewModal').classList.remove('on');
}

function confirmWorldview() {
  if (_generatedWorldview) {
    $('wd').value = _generatedWorldview;
    closeWorldviewModal();
  }
}

async function generateWorldview() {
  var generateBtn = $('btnGenerateWorldview');
  var regenerateBtn = $('btnRegenerateWorldview');
  var resultBox = $('worldviewResult');

  if (!resultBox) return;

  if (generateBtn) generateBtn.disabled = true;
  if (regenerateBtn) regenerateBtn.disabled = true;
  var activeBtn = (generateBtn && generateBtn.style.display !== 'none') ? generateBtn : regenerateBtn;
  if (activeBtn) activeBtn.textContent = '⏳ 生成中...';

  var worldType = $('wt').value;
  var existingDesc = $('wd').value.trim();

  resultBox.innerHTML = '<div style="text-align:center;color:var(--gold);padding:40px">正在生成世界观，大概需要30秒，请稍候...</div>';

  try {
    var res = await api('POST', '/api/generate-worldview', {
      world_type: worldType,
      existing_description: existingDesc
    });
    if (res && res.ok && res.worldview) {
      _generatedWorldview = res.worldview;
      resultBox.textContent = res.worldview;
      if (generateBtn) generateBtn.style.display = 'none';
      if (regenerateBtn) regenerateBtn.style.display = '';
    } else {
      _generatedWorldview = "";
      var msg = res && res.msg ? res.msg : "生成失败";
      resultBox.innerHTML = `<div style="text-align:center;color:#e07a7a;padding:40px">${msg}</div>`;
    }
  } catch(e) {
    _generatedWorldview = "";
    resultBox.innerHTML = `<div style="text-align:center;color:#e07a7a;padding:40px">请求失败: ${e.message || e}</div>`;
  } finally {
    if (generateBtn) generateBtn.disabled = false;
    if (regenerateBtn) regenerateBtn.disabled = false;
    if (generateBtn && generateBtn.style.display !== 'none') {
      generateBtn.textContent = '🧠 AI生成世界观';
    }
    if (regenerateBtn && regenerateBtn.style.display !== 'none') {
      regenerateBtn.textContent = '🔄 再次生成';
    }
  }
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
    var html = '<div style="display:flex;flex-direction:column;gap:6px">';
    saves.forEach(function(save) {
      var wName = save.world_name || save.world_id;
      var timeStr = save.last_saved_at_display || save.created_at_display || '';
      var dayStr = save.current_day ? ('第' + save.current_day + '天') : '';
      html += '<div style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:rgba(212,175,55,.06);border:1px solid rgba(212,175,55,.15);border-radius:6px">' +
        '<div onclick="loadHistoryContent(\'' + save.world_id + '\')" style="cursor:pointer;flex:1;min-width:0">' +
          '<div style="color:#e0d5c0;font-size:.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + escapeHtml(wName) + '</div>' +
          '<div style="color:var(--dim);font-size:.72em">' + escapeHtml(dayStr + (timeStr ? (' | ' + timeStr) : '')) + '</div>' +
        '</div>' +
        '<span onclick="event.stopPropagation();loadHistoryWorldIntoGame(\'' + escAttr(save.world_id) + '\')" ' +
          'style="color:#7a9a7a;cursor:pointer;font-size:.9em;padding:4px 8px;border-radius:4px;border:1px solid rgba(122,154,122,.3)" title="加载此世界进入游戏">&#9654; 加载</span>' +
        '<span onclick="event.stopPropagation();deleteHistoryWorld(\'' + escAttr(save.world_id) + '\',\'' + escAttr(wName) + '\')" ' +
          'style="color:#9a5a5a;cursor:pointer;font-size:.9em;padding:4px 8px;border-radius:4px;border:1px solid rgba(154,90,90,.3)" title="删除此世界">&#10005; 删除</span>' +
        '</div>';
    });
    html += '</div>';
    listEl.innerHTML = html;
  } catch(e) {
    listEl.innerHTML = '<div style="color:var(--dim);font-size:.85em">加载失败</div>';
  }
}

async function deleteHistoryWorld(wid, name) {
  if (!confirm('确定删除世界「' + name + '」？此操作不可撤销，所有剧情和存档都会丢失。')) return;
  try {
    await api('DELETE', '/api/save/' + wid);
    loadHistoryWorlds();
    $('history_content').innerHTML = '<div style="color:var(--dim);text-align:center;padding:30px">选择一个存档查看内容</div>';
    try {
      if (window.Alpine && Alpine.store('app')) {
        Alpine.store('app').showToast('已删除世界「' + name + '」', 'success');
      }
    } catch(e) {}
  } catch(e) {
    alert('删除失败: ' + (e.message || ''));
  }
}

async function loadHistoryWorldIntoGame(wid) {
  try {
    closeViewHistory();
    await loadGame(wid);
  } catch(e) {
    alert('加载失败: ' + (e.message || ''));
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
