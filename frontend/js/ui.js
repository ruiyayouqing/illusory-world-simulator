function showGame(skipNpcSpawn) {
  $('home').style.display = 'none';
  $('game').style.display = 'grid';
  if (GS && GS.world_id) window._currentWorldId = GS.world_id;
  updateMapPreview();
  connectWS();
  // [Bug] 根据当前主题选择对应的渐变背景，而非始终使用深色 'royal'
  var currentTheme = '';
  try { currentTheme = document.documentElement.style.getPropertyValue('--theme') || ''; } catch(e) {}
  // 通过读取 CSS 变量 --bg 判断是否浅色主题
  var bgColor = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  var isLightTheme = bgColor && parseInt(bgColor.replace('#','').slice(0,2), 16) > 128;
  var gradType = isLightTheme ? ('theme-' + (window._currentWorldName || 'ivory')) : 'royal';
  BGManager.setGradientBg(gradType, true);
  // [v10+++] 进入游戏后，后台异步补充重要 NPC（加载存档时跳过）
  if (!skipNpcSpawn) triggerAsyncNpcSpawn();
}

// [v10+++] 触发后台异步 NPC 生成
function triggerAsyncNpcSpawn() {
  try {
    api('POST', '/api/npc/async-create').then(function(res) {
      if (res && res.status === 'started') {
        console.log('[CV] 后台NPC生成已启动：当前' + res.current_count + '个，目标' + res.target + '个');
      } else if (res && res.status === 'skipped') {
        console.log('[CV] 后台NPC生成跳过：' + (res.reason || '未知原因'));
      }
    }).catch(function(e) {
      // 静默失败，不影响游戏
    });
  } catch(e) {}
}

function sanitizeHTML(text) {
  return escHtml(text);
}

function parseDialogue(text) {
  const dialogues = [];
  const regex = /["""「」『』【】]/g;
  const speakerPattern = /^([^：:「"""『』【】]{1,8})[：:]\s*/;
  
  const lines = text.split(/\n+/);
  let currentNarrative = '';
  
  lines.forEach(line => {
    line = line.trim();
    if (!line) return;
    
    const speakerMatch = line.match(speakerPattern);
    const hasQuoteStart = /^[""「『【]/.test(line) || speakerMatch;
    
    if (speakerMatch) {
      if (currentNarrative.trim()) {
        dialogues.push({ type: 'narrative', text: currentNarrative.trim() });
        currentNarrative = '';
      }
      const speaker = speakerMatch[1].trim();
      const rest = line.substring(speakerMatch[0].length).trim();
      const cleanText = rest.replace(/^[""「『【]?/, '').replace(/[""」』】]?$/, '');
      dialogues.push({ type: 'dialogue', speaker: speaker, text: cleanText });
    } else if (/^[""「『【]/.test(line)) {
      if (currentNarrative.trim()) {
        dialogues.push({ type: 'narrative', text: currentNarrative.trim() });
        currentNarrative = '';
      }
      const cleanText = line.replace(/^[""「『【]/, '').replace(/[""」』】]?[。！？…]*$/, match => match);
      dialogues.push({ type: 'dialogue', speaker: null, text: cleanText });
    } else {
      currentNarrative += (currentNarrative ? '\n' : '') + line;
    }
  });
  
  if (currentNarrative.trim()) {
    dialogues.push({ type: 'narrative', text: currentNarrative.trim() });
  }
  
  return dialogues.length > 0 ? dialogues : [{ type: 'narrative', text: text }];
}

// [Bug] 跟踪最后一次玩家输入和最后的AI叙事wrapper，用于重试功能
var _lastPlayerInput = '';
var _lastNarrativeWrapper = null;
var _lastPlayerInputDiv = null;

function addNarrative(text, isEvent, isPlayerInput) {
  const c = $('nb');

  if (isPlayerInput) {
    // 记录最后一次玩家输入
    _lastPlayerInput = text.replace(/^[>▸]\s*/, '');
    // 移除上一个重试按钮
    if (_lastNarrativeWrapper) {
      var oldBtn = _lastNarrativeWrapper.querySelector('.retry-btn');
      if (oldBtn) oldBtn.remove();
      _lastNarrativeWrapper = null;
    }
    // [v11] 移除上一个玩家输入的删除按钮
    if (_lastPlayerInputDiv) {
      var oldDelBtn = _lastPlayerInputDiv.querySelector('.undo-btn');
      if (oldDelBtn) oldDelBtn.remove();
    }
  }

  if (isEvent) {
    const p = document.createElement('p');
    p.className = 'event';
    p.innerHTML = sanitizeHTML(text).replace(/\n/g, '<br>');
    c.appendChild(p);
  } else if (isPlayerInput) {
    const div = document.createElement('div');
    div.className = 'player-input';
    const cleanText = text.replace(/^[>▸]\s*/, '');
    div.innerHTML = '<span class="pi-label">你的行动</span>' + sanitizeHTML(cleanText).replace(/\n/g, '<br>');
    // [v11] 最后一个玩家输入添加撤销按钮
    var undoBtn = document.createElement('span');
    undoBtn.className = 'undo-btn';
    undoBtn.textContent = '撤销';
    undoBtn.title = '撤销本次行动及AI回复';
    undoBtn.onclick = function() { undoLastAction(div); };
    div.appendChild(undoBtn);
    _lastPlayerInputDiv = div;
    c.appendChild(div);
  } else {
    const paragraphs = text.split(/\n{2,}/);
    paragraphs.forEach(para => {
      para = para.trim();
      if (!para) return;
      
      const parts = parseDialogue(para);
      parts.forEach(part => {
        if (part.type === 'dialogue' && part.speaker) {
          const d = document.createElement('div');
          d.className = 'npc-dialog';
          d.innerHTML = '<div class="speaker">' + escHtml(part.speaker) + '</div>' +
            '<div class="dialog-text">' + sanitizeHTML(part.text).replace(/\n/g, '<br>') + '</div>';
          c.appendChild(d);
        } else {
          const wrapper = document.createElement('div');
          wrapper.className = 'ai-narrative-wrapper';
          const p = document.createElement('p');
          p.className = 'ai-narrative';
          p.innerHTML = sanitizeHTML(part.text).replace(/\n/g, '<br>');
          wrapper.appendChild(p);
          // [Bug] 只在最后一个AI叙事上添加重试按钮
          if (_lastNarrativeWrapper) {
            var oldBtn = _lastNarrativeWrapper.querySelector('.retry-btn');
            if (oldBtn) oldBtn.remove();
          }
          const retryBtn = document.createElement('button');
          retryBtn.className = 'retry-btn';
          retryBtn.innerHTML = '🔄 重试';
          retryBtn.title = '用相同输入重新生成';
          retryBtn.onclick = function() { retryNarrative(retryBtn); };
          wrapper.appendChild(retryBtn);
          _lastNarrativeWrapper = wrapper;
          c.appendChild(wrapper);
        }
      });
    });
  }
  
  c.scrollTop = c.scrollHeight;
}

// [v11] 撤销最后一次行动：删除玩家输入及随后的AI叙事
function undoLastAction(playerInputDiv) {
  if (!playerInputDiv || !playerInputDiv.parentNode) return;
  if (!confirm('确定要撤销本次行动和AI回复吗？')) return;
  var nb = $('nb');
  var children = Array.from(nb.children);
  var startIdx = children.indexOf(playerInputDiv);
  if (startIdx < 0) return;
  var endIdx = children.length;
  for (var i = startIdx + 1; i < children.length; i++) {
    if (children[i].classList && children[i].classList.contains('player-input')) {
      endIdx = i;
      break;
    }
  }
  for (var j = endIdx - 1; j >= startIdx; j--) {
    if (children[j].parentNode) children[j].remove();
  }
  _lastPlayerInputDiv = null;
  _lastPlayerInput = '';
  _lastNarrativeWrapper = null;
  if (typeof outputLog !== 'undefined' && outputLog.length > 0) {
    outputLog.pop();
  }
  $('ot').textContent = '选择你的行动：';
  clearOpts();
}

function addSystem(text) {
  const c = $('nb');
  const d = document.createElement('div');
  d.className = 'system';
  d.textContent = text;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function addWhisper(text) {
  const c = $('nb');
  const d = document.createElement('div');
  d.className = 'whisper';
  d.textContent = text;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function addChapterDivider(icon = '✦') {
  const c = $('nb');
  const d = document.createElement('div');
  d.className = 'chapter-divider';
  d.innerHTML = '<span class="ch-icon">' + icon + '</span>';
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function addTimeSkip(text) {
  const c = $('nb');
  const d = document.createElement('div');
  d.className = 'time-skip';
  d.textContent = '⏳ ' + text;
  c.appendChild(d);
  c.scrollTop = c.scrollHeight;
}

function clearOpts() {
  $('og').innerHTML = '';
}

function restoreHistory(history, images) {
  var nb = $('nb');
  nb.innerHTML = '';
  history.forEach(function(h) {
    if (h.type === 'narrative') {
      if (h.player_input) addNarrative('> ' + h.player_input, false, true);
      addNarrative(h.text);
      if (h.day) addSystem('第' + h.day + '天 ' + h.time);
    } else if (h.type === 'event') {
      addNarrative(h.text, true);
    } else if (h.type === 'summary') {
      var summaryText = '【历史摘要】第' + (h.day_range ? h.day_range[0] + '-' + h.day_range[1] : '?') + '天\n' + h.text;
      addNarrative(summaryText, true);
    }
  });
  addSystem('已加载 ' + history.length + ' 条历史记录');
}

function showOpts(opts) {
  var ids = ['A','B','C','D','E','F','G','H'];
  $('og').innerHTML = opts.map(function(o, i) {
    var text = typeof o === 'string' ? o : (o.text || '');
    var id = typeof o === 'object' && o.id ? o.id : ids[i] || String.fromCharCode(65 + i);
    if (!/^[A-Za-z]+$/.test(id)) id = ids[i] || String.fromCharCode(65 + i);
    var risk = typeof o === 'object' ? o.risk : '';
    var riskClass = risk === 'high' ? 'rh' : risk === 'medium' ? 'rm' : 'rl';
    var riskText = risk === 'high' ? '危险' : risk === 'medium' ? '中等' : '安全';
    var reqText = typeof o === 'object' && o.requirement ? '<div style="color:var(--dim);font-size:.75em;margin-top:4px">' + escHtml(o.requirement) + '</div>' : '';
    return '<div class="ocard" onclick="pickOpt(\'' + escAttr(id) + '\',\'' +
      escAttr(text) + '\')">' +
      '<div class="oid">[' + escHtml(id) + ']</div>' +
      '<div class="otxt">' + escHtml(text) + reqText + '</div>' +
      '<div class="risk ' + riskClass + '">' + riskText + '</div></div>';
  }).join('');
}

function showSuicideConfirm(confirm) {
  clearOpts();
  var nb = $('nb');
  var div = document.createElement('div');
  div.className = 'death-screen';
  div.style.borderColor = 'rgba(201,69,69,0.5)';
  div.innerHTML = '<div class="death-title" style="font-size:1.3em;letter-spacing:4px">⚠ ' + escHtml(confirm.message) + '</div>' +
    '<div style="display:flex;gap:12px;margin-top:16px;justify-content:center">' +
    '<button onclick="confirmSuicide()" style="flex:1;max-width:160px;padding:12px;background:linear-gradient(135deg,#8b2525,#c94545);border:none;border-radius:8px;color:#fff;font-weight:700;cursor:pointer;font-size:.95em;transition:all .2s" onmouseover="this.style.transform=\'translateY(-2px)\';this.style.boxShadow=\'0 4px 15px rgba(201,69,69,.4)\'" onmouseout="this.style.transform=\'\';this.style.boxShadow=\'\'">确认自尽</button>' +
    '<button onclick="cancelSuicide()" style="flex:1;max-width:160px;padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;color:var(--text);cursor:pointer;font-size:.95em;transition:all .2s" onmouseover="this.style.borderColor=\'var(--gold)\';this.style.color=\'var(--gold)\'" onmouseout="this.style.borderColor=\'\';this.style.color=\'\'">放弃</button>' +
    '</div>';
  nb.appendChild(div);
  nb.scrollTop = nb.scrollHeight;
}

function showDeathScreen(death) {
  clearOpts();
  addNarrative('你死了: ' + death.cause, true);
  addNarrative(death.narrative, false, false);
  $('ot').textContent = '你的生命走到了尽头...请选择：';
  var deathOpts = death.options || [];
  if (deathOpts.length > 0) {
    var og = $('og');
    og.innerHTML = deathOpts.map(function(o) {
      return '<div class="ocard" onclick="pickDeathOpt(\'' + escAttr(o.type) + '\')">' +
        '<div class="oid">[' + escHtml(o.id) + ']</div>' +
        '<div class="otxt">' + escHtml(o.text) + '<div style="color:var(--dim);font-size:.78em;margin-top:4px">' + escHtml(o.description || '') + '</div></div></div>';
    }).join('');
  }
}

function updateStatus() {
  if (!GS) return;
  var p = GS.player;
  $('wn').textContent = GS.world?.name || '--';
  
  $('pa').textContent = (p.name || '?').charAt(0);
  $('pn').textContent = p.name || '--';
  $('pt').textContent = p.position || '--';
  $('pi_age').textContent = (p.age || '--') + '岁';
  
  var ts = GS.time_status;
  var tsBar = $('time_status_bar');
  if (ts) {
    tsBar.style.display = 'inline-flex';
    var seasonIcon = {'春季':'🌸','夏季':'☀️','秋季':'🍂','冬季':'❄️'};
    var timeIcon = {'清晨':'🌅','上午':'☀️','中午':'🌤️','下午':'⛅','傍晚':'🌇','深夜':'🌙','夜晚':'🌙'};
    var icon = (seasonIcon[ts.season] || '') + ' ' + (timeIcon[ts.time_of_day] || '');
    tsBar.innerHTML = '📅 ' + escHtml(ts.display) + ' | ' + icon + ' ' + escHtml(ts.season) + '·' + escHtml(ts.time_of_day) + ' | ' + escHtml(ts.weather);
    tsBar.title = '故事已过' + ts.total_days + '天（其中AI识别时间跳跃' + (ts.narrative_offset || 0) + '天）';
  }
  var drEl = $('div_rate');
  var dr = GS.divergence_rate || 0;
  if (dr > 0) {
    drEl.style.display = 'inline-flex';
    $('drate').textContent = dr;
  }
  $('sg').textContent = p.gold;
  $('sr2').textContent = p.reputation;
  var repRank = p.reputation >= 100 ? '天下皆知' : p.reputation >= 80 ? '名动一方' : p.reputation >= 60 ? '颇有声望' : p.reputation >= 40 ? '小有名气' : p.reputation >= 20 ? '略有薄名' : p.reputation >= 10 ? '初露锋芒' : '默默无闻';
  $('sr_title').textContent = repRank;
  $('pi_rep').textContent = repRank;
  var fr = GS.faction_reputation || {};
  var frEl = $('faction_rep');
  if (Object.keys(fr).length > 0) {
    frEl.innerHTML = Object.entries(fr).sort(function(a,b){return b[1]-a[1]}).map(function(e) {
      var k = e[0], v = e[1];
      var s = v >= 80 ? '崇敬' : v >= 50 ? '友好' : v >= 20 ? '中立偏善' : v >= -20 ? '中立' : v >= -50 ? '冷淡' : v >= -80 ? '敌对' : '仇恨';
      var color = v >= 50 ? 'var(--accent-green)' : v >= -20 ? 'var(--dim)' : 'var(--accent-red)';
      return '<div class="faction-item" style="border-left:3px solid ' + color + ';padding-left:10px"><span class="sl">' + escHtml(k) + '</span><span class="sv" style="color:' + color + '">' + s + ' ' + (v >= 0 ? '+' : '') + v + '</span></div>';
    }).join('');
  } else {
    frEl.innerHTML = '';
  }
  if (GS.wanted_level > 0) {
    frEl.innerHTML += '<div class="faction-item" style="border-left:3px solid var(--accent-red);padding-left:10px"><span class="sl" style="color:var(--accent-red)">通缉等级</span><span class="sv" style="color:var(--accent-red)">' + '★'.repeat(GS.wanted_level) + '☆'.repeat(5 - GS.wanted_level) + '</span></div>';
  }
  $('shv').textContent = p.health + '/' + p.max_health;
  $('shb').style.width = (p.health / p.max_health * 100) + '%';
  $('sev').textContent = p.energy + '/' + p.max_energy;
  $('seb').style.width = (p.energy / p.max_energy * 100) + '%';
  $('ss').textContent = p.strength;
  $('sag').textContent = p.agility;
  $('si').textContent = p.intelligence;
  $('sl2').textContent = p.luck;
  $('sm').textContent = p.magic || 0;
  $('stg').innerHTML = (p.tags || []).slice(0, 10).map(function(t) {
    return '<span class="tag">' + escHtml(t) + '</span>';
  }).join('');
  var statusEffects = p.status_effects || [];
  if (statusEffects.length > 0) {
    $('sfx').innerHTML = statusEffects.map(function(s) {
      var cls = 'neutral';
      var lowerS = s.toLowerCase();
      if (/伤|毒|病|弱|疲|死|诅|诅咒|流血|中毒|生病|虚弱|疲劳/.test(s)) cls = 'bad';
      else if (/益|增|强|护|祝福|强化|增益|保护/.test(s)) cls = 'good';
      return '<span class="status-effect ' + cls + '">' + escHtml(s) + '</span>';
    }).join('');
  } else {
    $('sfx').textContent = '正常';
    $('sfx').style.color = 'var(--dim)';
  }
  var sortedRels = Object.entries(p.relations || {}).sort(function(a,b) { return (b[1].favor||0) - (a[1].favor||0); });
  var seenNames = {};
  var dedupedRels = [];
  sortedRels.forEach(function(e) {
    var k = e[0], v = e[1];
    var displayName = v.name || k;
    if (!seenNames[displayName]) {
      seenNames[displayName] = true;
      dedupedRels.push(e);
    }
  });
  var relsHtml = dedupedRels.slice(0, 6).map(function(e) {
    var k = e[0], v = e[1];
    var favorColor = v.favor >= 70 ? 'var(--accent-green)' : v.favor >= 40 ? 'var(--gold)' : v.favor >= 0 ? 'var(--dim)' : 'var(--accent-red)';
    var hearts = v.favor >= 80 ? '💚' : v.favor >= 60 ? '💛' : v.favor >= 30 ? '🧡' : v.favor >= 0 ? '🤍' : '💔';
    return '<div class="relation-item"><span class="rel-name">' + escHtml(v.name || k) + '</span><span class="rel-favor" style="color:' + favorColor + '">' + hearts + ' ' + v.favor + '</span></div>';
  }).join('');
  if (dedupedRels.length > 6) {
    var extraHtml = dedupedRels.slice(6).map(function(e) {
      var k = e[0], v = e[1];
      var favorColor = v.favor >= 70 ? 'var(--accent-green)' : v.favor >= 40 ? 'var(--gold)' : v.favor >= 0 ? 'var(--dim)' : 'var(--accent-red)';
      var hearts = v.favor >= 80 ? '💚' : v.favor >= 60 ? '💛' : v.favor >= 30 ? '🧡' : v.favor >= 0 ? '🤍' : '💔';
      return '<div class="relation-item"><span class="rel-name">' + escHtml(v.name || k) + '</span><span class="rel-favor" style="color:' + favorColor + '">' + hearts + ' ' + v.favor + '</span></div>';
    }).join('');
    relsHtml += '<div style="font-size:.75em;color:var(--gold);margin-top:4px;cursor:pointer;text-align:center;padding:6px;border-radius:6px;background:rgba(212,175,55,.06)" onclick="toggleAllRels()">还有' + (dedupedRels.length - 6) + '人... ▼</div>';
    relsHtml += '<div id="allRels" style="display:none">' + extraHtml + '</div>';
  }
  $('srl').innerHTML = relsHtml;

  var ps = GS.world?.power_system;
  var psSec = $('ps_sec');
  var psName = $('ps_name');
  var psInfo = $('ps_info');
  if (ps && ps.name) {
    // [Bug] 隐藏修真境界区块（AI无法正确识别境界变化）
    // psSec.style.display = 'block';
    psSec.style.display = 'none';
    psName.textContent = '⚔️ ' + ps.name;
    var levelsHtml = (ps.levels || []).map(function(l, i) {
      var isCurrent = ps.player_level && (l.name === ps.player_level || l.name.startsWith(ps.player_level));
      var style = isCurrent ? 'color:var(--gold);font-weight:700' : 'color:var(--dim)';
      var marker = isCurrent ? ' ◀' : '';
      return '<div style="' + style + ';padding:4px 0;border-bottom:1px solid rgba(255,255,255,.03)">' + (i+1) + '. ' + escHtml(l.name) + marker + '</div>';
    }).join('');
    if (ps.level_description) {
      levelsHtml += '<div style="color:var(--text);margin-top:8px;font-size:.9em;border-top:1px solid var(--border);padding-top:8px;line-height:1.6">' + escHtml(ps.level_description) + '</div>';
    }
    psInfo.innerHTML = levelsHtml;
  } else {
    psSec.style.display = 'none';
  }
  if (typeof refreshWhoPreview === 'function') refreshWhoPreview();
}

function showDice(r) {
  var o = $('dov');
  var v = $('dv');
  var det = $('dd');
  o.classList.add('on');
  var c = 0;
  var iv = setInterval(function() {
    v.textContent = Math.floor(Math.random() * 20) + 1;
    c++;
    if (c > 12) {
      clearInterval(iv);
      v.textContent = r.roll;
      v.style.color = r.success ? 'var(--accent-green)' : 'var(--accent-red)';
      v.style.textShadow = r.success ? '0 0 20px rgba(90,154,90,.6)' : '0 0 20px rgba(201,69,69,.6)';
      det.innerHTML = '<div style="margin-bottom:6px">' + escHtml(r.stat) + ': ' + r.stat_value + ' + 幸运: ' + r.luck_bonus + ' + 骰子: ' + r.roll + ' = <b style="color:' + (r.success ? 'var(--accent-green)' : 'var(--accent-red)') + '">' + r.total + '</b></div>' +
        '<div>难度: ' + r.difficulty + ' | <b style="font-size:1.1em;color:' + (r.success ? 'var(--accent-green)' : 'var(--accent-red)') + '">' + (r.success ? '✨ 成功!' : '💔 失败...') + '</b></div>';
      setTimeout(function() { o.classList.remove('on'); v.style.color = 'var(--gold)'; v.style.textShadow = '0 0 20px rgba(212,175,55,.5)'; }, 2800);
    }
  }, 60);
}

function closeModal() { $('mov').classList.remove('on'); }

function closeGraph() { $('grmv').classList.remove('on'); }

async function openContextDebug() {
  $('ctxModal').classList.add('on');
  $('ctx_content').innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">加载中...</div>';
  try {
    var d = await api('GET', '/api/context-debug');
    if (d.error) { $('ctx_content').innerHTML = '<div style="color:var(--accent-red)">加载失败: ' + escHtml(d.error) + '</div>'; return; }
    renderContextDebug(d);
  } catch(e) {
    $('ctx_content').innerHTML = '<div style="color:var(--accent-red)">加载失败</div>';
  }
}

function closeContextDebug() { $('ctxModal').classList.remove('on'); }

function renderContextDebug(d) {
  var totalTokens = d.total_estimated_tokens || 0;
  var maxCtx = d.max_context || 8192;
  var pct = Math.round(totalTokens / maxCtx * 100);
  var barClass = pct > 80 ? 'danger' : pct > 60 ? 'warn' : '';

  var narrativeYears = (d.narrative_offset || 0) > 0 ? Math.floor((d.narrative_offset || 0) / 365) + '年' : '无';

  function card(title, tokens, content, full) {
    var cls = full ? 'ctx-card full' : 'ctx-card';
    var h = '<div class="' + cls + '"><h4>' + title + ' <span style="color:var(--dim);font-weight:400">(' + tokens + ' tokens)</span></h4>';
    if (content) {
      h += '<div class="val' + (full ? '' : '') + '">' + escHtml(content || '(空)') + '</div>';
    }
    h += '</div>';
    return h;
  }

  var html = '';

  html += '<div class="ctx-card full">' +
    '<h4>Token 预算 <span style="color:var(--dim);font-weight:400">' + totalTokens + ' / ' + maxCtx + ' (' + pct + '%)</span></h4>' +
    '<div class="ctx-bar"><div class="ctx-bar-fill ' + barClass + '" style="width:' + Math.min(pct, 100) + '%"></div></div>' +
    '<div style="color:var(--dim);font-size:.75em;margin-top:4px">叙事时间偏移: ' + narrativeYears + ' | NPC数量: ' + (d.npc_count || 0) + ' | 历史轮次: ' + (d.history_turns || 0) + '</div>' +
    '</div>';

  html += card('🌍 世界设定', d.world_tokens || 0, d.world_context);
  html += card('👥 NPC档案', d.npc_tokens || 0, d.npc_context);
  html += card('👤 玩家状态', d.player_tokens || 0, d.player_context);
  html += card('📜 近期历史', d.history_tokens || 0, (d.history_turns || 0) + ' 轮对话', true);
  html += card('📖 Lorebook触发', d.lorebook_tokens || 0, (d.lorebook_matches || 0) + ' 条匹配' + (d.lorebook_entries && d.lorebook_entries.length ? '\n• ' + d.lorebook_entries.join('\n• ') : ''));
  html += card('🔍 RAG向量检索', d.rag_tokens || 0, (d.rag_results && d.rag_results.length ? '• ' + d.rag_results.join('\n• ') : '无匹配'), true);
  if (d.fixed_prompt) {
    html += card('📌 固定提示词', d.fixed_prompt_tokens || 0, d.fixed_prompt);
  }
  if (d.time_context) {
    html += card('⏰ 叙事时间追踪', 0, d.time_context);
  }

  $('ctx_content').innerHTML = html;
}

function toggleRightPanel() {
  var right = $('rightPanel');
  right.classList.toggle('show');
}

// [Bug] 重试功能：用相同输入重新生成叙事，替换当前叙事
async function retryNarrative(btn) {
  if (!_lastPlayerInput) return;
  // 找到这个按钮所属的 ai-narrative-wrapper
  var wrapper = btn.closest('.ai-narrative-wrapper');
  if (!wrapper) return;
  // 禁用按钮，显示加载状态
  btn.disabled = true;
  btn.innerHTML = '⏳ 生成中...';
  try {
    var d = await api('POST', '/api/input', { input: _lastPlayerInput });
    if (d.error) { btn.innerHTML = '❌ 失败'; btn.disabled = false; return; }
    var r = d.result || d;
    // 更新全局状态
    if (d.state) GS = d.state;
    updateStatus();
    // 构建新的叙事HTML
    var newHtml = '';
    if (r.narrative) {
      var paragraphs = r.narrative.split(/\n{2,}/);
      paragraphs.forEach(function(para) {
        para = para.trim();
        if (!para) return;
        var parts = parseDialogue(para);
        parts.forEach(function(part) {
          if (part.type === 'dialogue' && part.speaker) {
            newHtml += '<div class="npc-dialog"><div class="speaker">' + escHtml(part.speaker) + '</div><div class="dialog-text">' + sanitizeHTML(part.text).replace(/\n/g, '<br>') + '</div></div>';
          } else {
            newHtml += '<p class="ai-narrative">' + sanitizeHTML(part.text).replace(/\n/g, '<br>') + '</p>';
          }
        });
      });
    }
    // 添加重试按钮
    newHtml += '<button class="retry-btn" onclick="retryNarrative(this)" title="用相同输入重新生成">🔄 重试</button>';
    // 替换内容
    wrapper.innerHTML = newHtml;
    // 处理其他响应
    if (r.auto_event) addNarrative(r.auto_event.narrative, true);
    if (r.options && r.options.length) showOpts(r.options);
  } catch(e) {
    btn.innerHTML = '❌ 失败';
    btn.disabled = false;
  }
}
