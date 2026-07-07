/* [v10+] v10 面板前端逻辑 */

function openV10Panel() {
  $('v10PanelModal').style.display = 'flex';
  loadV10Foreshadow();
}

function closeV10Panel() {
  $('v10PanelModal').style.display = 'none';
}

function switchV10Tab(tab) {
  document.querySelectorAll('#v10PanelModal .wp-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('#v10PanelModal .wp-tab').forEach(function(t) { t.classList.remove('active'); });
  $('v10_' + tab).classList.add('active');
  $('v10_tab_' + tab).classList.add('active');

  if (tab === 'foreshadow') loadV10Foreshadow();
  else if (tab === 'tasks') loadV10Tasks();
  else if (tab === 'audit') loadV10Audit();
  else if (tab === 'memory') loadV10Memory();
  else if (tab === 'stats') loadV10Stats();
}

async function loadV10Foreshadow() {
  try {
    var r = await api('GET', '/api/v10/foreshadow');
    if (r.error) { $('v10_fs_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">' + r.error + '</div>'; return; }

    var stats = $('v10_fs_stats');
    stats.innerHTML = '活跃: <b>' + (r.active || 0) + '</b> | 已解决: <b>' + (r.resolved || 0) + '</b> | 过期: <b>' + (r.stale || 0) + '</b> | 模式: <b>' + (r.reminder_mode || 'normal') + '</b>';

    var list = $('v10_fs_list');
    var hooks = r.active_hooks || [];
    if (hooks.length === 0) {
      list.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">暂无活跃伏笔</div>';
      return;
    }

    var importanceColors = { critical: '#ff4444', high: '#ff8844', normal: 'var(--gold)', low: 'var(--dim)' };
    var statusLabels = { active: '🟢 新埋下', mentioned: '🔵 已提及', stale: '🔴 过期', deferred: '⏸️ 推迟' };

    list.innerHTML = hooks.map(function(h) {
      var ic = importanceColors[h.importance] || 'var(--gold)';
      var sl = statusLabels[h.status] || h.status;
      return '<div style="padding:10px;margin-bottom:8px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px;border-left:3px solid ' + ic + '">'
        + '<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
        + '<span style="color:' + ic + ';font-weight:600">' + escHtml(h.hook_id) + '</span>'
        + '<span style="font-size:.8em;color:var(--dim)">' + escHtml(sl) + '</span>'
        + '</div>'
        + '<div style="color:var(--text);margin-bottom:4px">' + escHtml(h.content) + '</div>'
        + '<div style="font-size:.78em;color:var(--dim)">第' + escHtml(h.inserted_day) + '天埋下 | 提及' + escHtml(h.mention_count) + '次</div>'
        + '</div>';
    }).join('');
  } catch (e) {
    $('v10_fs_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function loadV10Tasks() {
  try {
    var r = await api('GET', '/api/v10/task-board');
    if (r.error) { $('v10_tb_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">' + r.error + '</div>'; return; }

    var byStatus = r.by_status || {};
    $('v10_tb_stats').innerHTML = '总计: <b>' + (r.total || 0) + '</b> | 进行中: <b>' + (byStatus.running || 0) + '</b> | 待办: <b>' + (byStatus.pending || 0) + '</b> | 已完成: <b>' + (r.completed_count || 0) + '</b>';

    var list = $('v10_tb_list');
    var tasks = r.active_tasks || [];
    if (tasks.length === 0) {
      list.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">暂无活跃任务</div>';
      return;
    }

    var statusColors = { pending: '#888', ready: '#4488ff', running: '#44bb44', completed: '#22aa66', failed: '#ff4444' };

    list.innerHTML = tasks.map(function(t) {
      var sc = statusColors[t.status] || 'var(--dim)';
      var pri = '';
      var tPriority = Number(t.priority) || 0;
      var tProgress = Number(t.progress) || 0;
      for (var i = 0; i < 10; i++) pri += i < tPriority ? '█' : '░';
      return '<div style="padding:10px;margin-bottom:8px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px">'
        + '<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
        + '<span style="color:var(--gold);font-weight:600">' + escHtml(t.title) + '</span>'
        + '<span style="font-size:.8em;color:' + sc + '">' + escHtml(t.status) + '</span>'
        + '</div>'
        + '<div style="font-size:.8em;color:var(--dim);margin-bottom:4px">负责人: ' + escHtml(t.assigned_to || '未分配') + ' | 优先级: ' + pri + '</div>'
        + '<div style="background:rgba(0,0,0,.3);border-radius:4px;height:6px;overflow:hidden">'
        + '<div style="background:var(--gold);height:100%;width:' + tProgress + '%;transition:width .3s"></div>'
        + '</div>'
        + '<div style="font-size:.75em;color:var(--dim);text-align:right;margin-top:2px">' + tProgress + '%</div>'
        + '</div>';
    }).join('');
  } catch (e) {
    $('v10_tb_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function loadV10Audit() {
  try {
    var r = await api('GET', '/api/v10/continuity-audit');
    if (r.error) { $('v10_audit_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">' + r.error + '</div>'; return; }

    var trend = r.trend || {};
    $('v10_audit_stats').innerHTML = '趋势: <b>' + (trend.trend || '无数据') + '</b> | 总审计次数: <b>' + (trend.total_audits || 0) + '</b>';

    var list = $('v10_audit_list');
    var latest = r.latest_report;
    if (!latest) {
      list.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">暂无审计记录（每5回合自动执行一次）</div>';
      return;
    }

    var sevColors = { pass: '#44bb44', warning: '#ffaa00', critical: '#ff4444' };
    var dimNames = {
      character_identity: '👤 角色身份一致性',
      resource_continuity: '💰 资源连续性',
      timeline_consistency: '⏰ 时间线一致性',
      personality_drift: '🎭 性格漂移检测',
      foreshadow_payoff: '🧵 伏笔偿还检查'
    };

    var dimsHtml = (latest.dimensions || []).map(function(d) {
      var dc = sevColors[d.severity] || 'var(--dim)';
      var dn = dimNames[d.dimension] || d.dimension;
      var issues = (d.issues || []).length > 0 ? '<div style="font-size:.8em;color:var(--dim);margin-top:2px">⚠ ' + d.issues.map(function(x){ return escHtml(x); }).join(' | ') + '</div>' : '';
      return '<div style="padding:6px 0;border-top:1px solid rgba(201,169,110,.1)">'
        + '<span style="color:' + dc + '">' + escHtml(dn) + ': ' + escHtml(d.severity) + '</span>'
        + issues + '</div>';
    }).join('');

    list.innerHTML = '<div style="padding:10px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px;margin-bottom:12px">'
      + '<div style="font-weight:600;color:var(--gold);margin-bottom:8px">第' + latest.turn + '回合 审计结果 (第' + latest.day + '天)</div>'
      + '<div style="margin-bottom:8px">总体: <span style="color:' + (sevColors[latest.overall_severity] || 'var(--dim)') + ';font-weight:600">' + latest.overall_severity + '</span> | 关键问题: ' + latest.critical_count + ' | 警告: ' + latest.warning_count + '</div>'
      + dimsHtml + '</div>';
  } catch (e) {
    $('v10_audit_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function loadV10Memory() {
  try {
    var r = await api('GET', '/api/v10/dashboard');
    if (r.error) { $('v10_mem_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">' + r.error + '</div>'; return; }

    var curator = r.curator || {};
    var procedural = r.procedural_memory || {};
    var memQuality = r.memory_quality || {};

    $('v10_mem_stats').innerHTML = 'Curator整理: <b>' + (curator.total_curations || 0) + '</b>次 | 归档: <b>' + (curator.archived_memories || 0) + '</b>条<br>NPC程序性记忆: <b>' + (procedural.total_npcs || 0) + '</b>个NPC, <b>' + (procedural.total_entries || 0) + '</b>条经验';

    var list = $('v10_mem_list');
    var html = '';

    if (memQuality.working_memory) {
      html += '<div style="padding:10px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px;margin-bottom:8px">'
        + '<div style="font-weight:600;color:var(--gold);margin-bottom:4px">🧠 工作记忆</div>'
        + '<div style="font-size:.85em;color:var(--text);white-space:pre-wrap">' + escHtml(memQuality.working_memory) + '</div>'
        + '</div>';
    }

    if (memQuality.identity_count > 0) {
      html += '<div style="padding:10px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px;margin-bottom:8px">'
        + '<div style="font-weight:600;color:var(--gold);margin-bottom:4px">🪪 身份语义核心</div>'
        + '<div style="font-size:.85em;color:var(--dim)">共 ' + memQuality.identity_count + ' 条身份特质</div>'
        + '</div>';
    }

    if (curator.last_curate) {
      var lc = curator.last_curate;
      var actions = (lc.actions || []).join(' | ') || '无操作';
      html += '<div style="padding:10px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px">'
        + '<div style="font-weight:600;color:var(--gold);margin-bottom:4px">🗄️ 最近一次 Curator 整理 (第' + lc.turn + '回合)</div>'
        + '<div style="font-size:.85em;color:var(--text)">' + actions + '</div>'
        + '</div>';
    }

    list.innerHTML = html || '<div style="color:var(--dim);text-align:center;padding:20px">暂无记忆数据</div>';
  } catch (e) {
    $('v10_mem_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

async function switchForeshadowMode() {
  try {
    var current = await api('GET', '/api/v10/foreshadow');
    if (current.error) { toast('获取状态失败', 'error'); return; }
    var newMode = current.reminder_mode === 'normal' ? 'silent' : 'normal';
    await api('POST', '/api/v10/foreshadow/reminder', { mode: newMode });
    toast('伏笔提醒已切换为: ' + (newMode === 'normal' ? '正常提醒' : '静默模式'));
    loadV10Foreshadow();
  } catch (e) {
    toast('切换失败: ' + e.message, 'error');
  }
}

/* ========== [v10] LLM/性能统计面板 ========== */
async function loadV10Stats() {
  try {
    var r = await api('GET', '/api/stats');
    if (r.status === 'no_engine') {
      $('v10_stats_list').innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">引擎未启动，请先开始游戏</div>';
      return;
    }

    var html = '';

    // 主模型统计
    if (r.main_model) {
      var m = r.main_model;
      var totalCalls = m.total_calls || 0;
      // [Bug] 后端 LLMUsageStats.to_dict() 返回 total_cache_hit_tokens/failed_calls/avg_latency_ms，
      //       而非前端原先期望的 cache_hits/total_errors/total_latency_ms，导致统计始终显示 0
      var cacheHits = m.total_cache_hit_tokens || 0;
      var cacheRate = m.cache_hit_rate != null ? Math.round(m.cache_hit_rate * 100) : (totalCalls > 0 ? Math.round(cacheHits / Math.max(m.total_prompt_tokens || 1, 1) * 100) : 0);
      var avgLat = Math.round(m.avg_latency_ms || 0);
      html += '<div style="padding:12px;background:rgba(201,169,110,.08);border:1px solid rgba(201,169,110,.2);border-radius:8px;margin-bottom:10px">'
        + '<div style="font-weight:600;color:var(--gold);margin-bottom:8px">🧠 主模型: ' + escHtml(m.name) + '</div>'
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.85em">'
        + '<div>总调用次数: <b>' + totalCalls + '</b></div>'
        + '<div>失败次数: <b style="color:#ff6b6b">' + (m.failed_calls || 0) + '</b></div>'
        + '<div>Prompt Tokens: <b>' + (m.total_prompt_tokens || 0).toLocaleString() + '</b></div>'
        + '<div>Completion Tokens: <b>' + (m.total_completion_tokens || 0).toLocaleString() + '</b></div>'
        + '<div>缓存命中: <b style="color:#4ecdc4">' + cacheHits.toLocaleString() + '</b></div>'
        + '<div>平均延迟: <b>' + avgLat + 'ms</b></div>'
        + '</div></div>';
    }

    // 备用模型统计
    if (r.cheap_model) {
      var c = r.cheap_model;
      var cTotal = c.total_calls || 0;
      var cCacheHits = c.total_cache_hit_tokens || 0;
      var cAvgLat = Math.round(c.avg_latency_ms || 0);
      html += '<div style="padding:12px;background:rgba(110,169,201,.08);border:1px solid rgba(110,169,201,.2);border-radius:8px;margin-bottom:10px">'
        + '<div style="font-weight:600;color:#6ea9c9;margin-bottom:8px">⚡ 备用模型: ' + escHtml(c.name) + '</div>'
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:.85em">'
        + '<div>总调用次数: <b>' + cTotal + '</b></div>'
        + '<div>失败次数: <b style="color:#ff6b6b">' + (c.failed_calls || 0) + '</b></div>'
        + '<div>Prompt Tokens: <b>' + (c.total_prompt_tokens || 0).toLocaleString() + '</b></div>'
        + '<div>Completion Tokens: <b>' + (c.total_completion_tokens || 0).toLocaleString() + '</b></div>'
        + '<div>平均延迟: <b>' + cAvgLat + 'ms</b></div>'
        + '<div>💰 节省费用: <b style="color:#4ecdc4">是</b></div>'
        + '</div></div>';
    } else if (!r.main_model) {
      html += '<div style="color:var(--dim);text-align:center;padding:20px">暂无模型调用数据</div>';
    } else {
      html += '<div style="padding:10px;background:rgba(255,255,255,.03);border-radius:6px;margin-bottom:10px;font-size:.85em;color:var(--dim)">⚡ 备用模型未配置 — 在设置中配置廉价模型可节省费用并加速简单任务</div>';
    }

    // 后台任务队列
    if (r.task_queue) {
      var q = r.task_queue;
      html += '<div style="padding:12px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:8px">'
        + '<div style="font-weight:600;color:var(--dim);margin-bottom:8px">📋 后台任务队列</div>'
        + '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;font-size:.85em">'
        + '<div>已完成: <b style="color:#4ecdc4">' + (q.completed || 0) + '</b></div>'
        + '<div>失败: <b style="color:#ff6b6b">' + (q.failed || 0) + '</b></div>'
        + '<div>队列中: <b>' + (q.pending || 0) + '</b></div>'
        + '</div></div>';
    }

    $('v10_stats_summary').innerHTML = '实时运行监控';
    $('v10_stats_list').innerHTML = html;
  } catch (e) {
    $('v10_stats_list').innerHTML = '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

/* ========== [v10] 斜杠命令 + 动态输入提示 ========== */
var SLASH_COMMANDS = [
  { cmd: '/save', desc: '💾 保存当前游戏' },
  { cmd: '/load', desc: '📂 打开读档面板' },
  { cmd: '/map', desc: '🗺️ 打开世界地图' },
  { cmd: '/who', desc: '📜 打开名人谱' },
  { cmd: '/graph', desc: '🕸️ 打开NPC关系图谱' },
  { cmd: '/v10', desc: '🔮 打开v10高级面板' },
  { cmd: '/settings', desc: '⚙️ 打开设置' },
  { cmd: '/undo', desc: '⏪ 回退一步' },
  { cmd: '/redo', desc: '⏩ 重做一步' },
  { cmd: '/help', desc: '❓ 显示帮助' },
  { cmd: '/time', desc: '⏰ 查看当前时间' },
  { cmd: '/status', desc: '📊 查看玩家状态' },
  { cmd: '/inventory', desc: '🎒 查看背包' },
];

var PLACEHOLDERS = [
  '自由输入你的行动...（Enter发送，Shift+Enter换行）',
  '试试输入 / 查看快捷命令',
  '你可以描述观察周围、与人对话、做出行动...',
  '例如："我环顾四周，看看有什么"',
  '例如："走向那位老者，拱手施礼"',
  '例如："打开背包查看物品"',
  '例如："/save" 快速存档',
];

var _phIdx = 0;
var _slashVisible = false;

function initInputHints() {
  var ci = $('ci');
  if (!ci) return;

  // 动态placeholder轮播
  setInterval(function() {
    if (document.activeElement !== ci && !ci.value) {
      _phIdx = (_phIdx + 1) % PLACEHOLDERS.length;
      ci.placeholder = PLACEHOLDERS[_phIdx];
    }
  }, 5000);
  ci.placeholder = PLACEHOLDERS[0];

  // 创建斜杠命令提示下拉框
  var slashBox = document.createElement('div');
  slashBox.id = 'slash_hint_box';
  slashBox.style.cssText = 'display:none;position:absolute;background:var(--panel);border:1px solid var(--gold);border-radius:6px;box-shadow:0 4px 20px rgba(0,0,0,.5);z-index:1000;max-height:280px;overflow-y:auto;min-width:220px';
  ci.parentElement.style.position = 'relative';
  ci.parentElement.appendChild(slashBox);

  ci.addEventListener('input', function() {
    var val = ci.value;
    if (val.startsWith('/')) {
      var query = val.slice(1).toLowerCase();
      var matches = SLASH_COMMANDS.filter(function(c) {
        return c.cmd.slice(1).toLowerCase().startsWith(query);
      });
      if (matches.length > 0 && query.length > 0 || val === '/') {
        showSlashHints(matches.length > 0 ? matches : SLASH_COMMANDS, slashBox, ci);
      } else {
        hideSlashHints(slashBox);
      }
    } else {
      hideSlashHints(slashBox);
    }
  });

  ci.addEventListener('keydown', function(e) {
    if (_slashVisible) {
      var items = slashBox.querySelectorAll('.slash-item');
      var activeIdx = Array.from(items).findIndex(function(el) { return el.classList.contains('active'); });
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = Math.min(activeIdx + 1, items.length - 1);
        updateSlashActive(items, activeIdx);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = Math.max(activeIdx - 1, 0);
        updateSlashActive(items, activeIdx);
      } else if (e.key === 'Tab' || e.key === 'Enter') {
        if (activeIdx >= 0 && !e.shiftKey) {
          e.preventDefault();
          var activeItem = items[activeIdx];
          ci.value = activeItem.dataset.cmd + ' ';
          hideSlashHints(slashBox);
        }
      } else if (e.key === 'Escape') {
        hideSlashHints(slashBox);
      }
    }
  });

  ci.addEventListener('blur', function() {
    setTimeout(function() { hideSlashHints(slashBox); }, 200);
  });
}

function showSlashHints(matches, box, ci) {
  _slashVisible = true;
  box.style.display = 'block';
  box.innerHTML = matches.map(function(c, i) {
    return '<div class="slash-item" data-cmd="' + c.cmd + '" style="padding:8px 12px;cursor:pointer;font-size:.85em;border-bottom:1px solid rgba(255,255,255,.05);' + (i === 0 ? 'background:rgba(201,169,110,.15);' : '') + '">'
      + '<span style="color:var(--gold);font-weight:600;margin-right:8px">' + c.cmd + '</span>'
      + '<span style="color:var(--dim)">' + c.desc + '</span></div>';
  }).join('');
  var items = box.querySelectorAll('.slash-item');
  items.forEach(function(el) {
    el.addEventListener('mouseenter', function() {
      updateSlashActive(items, Array.from(items).indexOf(el));
    });
    el.addEventListener('click', function() {
      ci.value = el.dataset.cmd + ' ';
      hideSlashHints(box);
      ci.focus();
    });
  });
  // 定位到输入框上方
  var rect = ci.getBoundingClientRect();
  box.style.bottom = (ci.offsetHeight + 8) + 'px';
  box.style.left = '0';
}

function hideSlashHints(box) {
  _slashVisible = false;
  box.style.display = 'none';
}

function updateSlashActive(items, idx) {
  items.forEach(function(el, i) {
    if (i === idx) {
      el.classList.add('active');
      el.style.background = 'rgba(201,169,110,.15)';
    } else {
      el.classList.remove('active');
      el.style.background = '';
    }
  });
}

// 页面加载完成后初始化输入提示
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initInputHints);
} else {
  initInputHints();
}

/* ========== [v10] 预设开局快速开始 ========== */
var PRESET_WORLDS = [
  { name: '🌙 明朝书生', desc: '穿越永乐年间，落魄书生的科举之路', prompt: '穿越到明朝永乐年间，成为一名落魄书生，身无分文但心怀壮志，在江南小镇开始你的故事。' },
  { name: '⚔️ 江湖侠客', desc: '武侠世界，身负血海深仇的少年侠客', prompt: '在一个武侠世界中，你是一名刚出师门的少年侠客，背负着师父的血海深仇，初入江湖。' },
  { name: '🏰 奇幻领主', desc: '中世纪奇幻，继承破败领地的小领主', prompt: '在一个剑与魔法的中世纪奇幻世界，你继承了一块破败的边境领地，内忧外患，如何发展壮大？' },
  { name: '🌸 宫廷权谋', desc: '深宫之中，小秀女的后宫晋升路', prompt: '你是大雍王朝新入宫的小秀女，家世普通，在波谲云诡的后宫之中如何自保并向上走？' },
  { name: '🚀 星际纪元', desc: '未来科幻，星际飞船的新晋舰长', prompt: '公元2387年，你是联邦探索舰队"拓荒者号"的新晋舰长，刚刚接到任务前往未知星域探索。' },
  { name: '🎓 都市重生', desc: '回到2000年，重启人生', prompt: '你重生回到了2000年的高三课堂，带着未来30年的记忆，这一次你要怎么活？' },
];

function showPresetWorlds() {
  var wd = $('wd');
  if (!wd) return;
  var existing = document.getElementById('preset_worlds_box');
  if (existing) { existing.remove(); return; }

  var box = document.createElement('div');
  box.id = 'preset_worlds_box';
  box.style.cssText = 'margin-top:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px';
  box.innerHTML = PRESET_WORLDS.map(function(w) {
    return '<div onclick="usePresetWorld(\'' + w.name.replace(/'/g, "\\'") + '\')" style="padding:10px;background:rgba(201,169,110,.06);border:1px solid rgba(201,169,110,.15);border-radius:6px;cursor:pointer;transition:all .2s" onmouseover="this.style.borderColor=\'var(--gold)\';this.style.background=\'rgba(201,169,110,.12)\'" onmouseout="this.style.borderColor=\'rgba(201,169,110,.15)\';this.style.background=\'rgba(201,169,110,.06)\'">'
      + '<div style="font-weight:600;color:var(--gold);font-size:.9em;margin-bottom:4px">' + w.name + '</div>'
      + '<div style="font-size:.78em;color:var(--dim);line-height:1.4">' + w.desc + '</div>'
      + '</div>';
  }).join('');
  wd.parentElement.appendChild(box);
}

function usePresetWorld(name) {
  var wd = $('wd');
  var preset = PRESET_WORLDS.find(function(w) { return w.name === name; });
  if (preset && wd) {
    wd.value = preset.prompt;
    var box = document.getElementById('preset_worlds_box');
    if (box) box.remove();
    toast('已选择预设: ' + name, 'success');
  }
}

// 初始页面添加预设按钮
window.addEventListener('load', function() {
  var wd = $('wd');
  if (wd && !document.getElementById('preset_btn')) {
    var btnWrap = document.createElement('div');
    btnWrap.style.cssText = 'margin-top:6px;text-align:right';
    btnWrap.innerHTML = '<button id="preset_btn" onclick="showPresetWorlds()" style="padding:6px 14px;background:transparent;border:1px solid var(--border);border-radius:5px;color:var(--dim);font-size:.82em;cursor:pointer" onmouseover="this.style.borderColor=\'var(--gold)\';this.style.color=\'var(--gold)\'" onmouseout="this.style.borderColor=\'var(--border)\';this.style.color=\'var(--dim)\'">📋 预设开局</button>';
    wd.parentElement.appendChild(btnWrap);
  }
});
