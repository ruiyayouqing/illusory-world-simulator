// 太虚幻境 v6 — 设置: 配置/主题

function openSettings() {
  var stov = $('stov');
  if (stov.parentElement !== document.body) {
    document.body.appendChild(stov);
  }
  stov.classList.add('on');
  loadSettings();
  // 默认显示AI模型标签
  switchSettingsTab('ai');
}

function closeSettings() {
  $('stov').classList.remove('on');
}

// 标签页切换
function switchSettingsTab(tabName) {
  // 移除所有标签的active状态
  document.querySelectorAll('.settings-tab').forEach(function(tab) {
    tab.classList.remove('active');
  });
  // 移除所有内容的active状态
  document.querySelectorAll('.settings-tab-content').forEach(function(content) {
    content.classList.remove('active');
  });
  // 激活选中的标签和内容
  var tab = document.querySelector('.settings-tab[data-tab="' + tabName + '"]');
  var content = document.getElementById('tab-' + tabName);
  if (tab) tab.classList.add('active');
  if (content) content.classList.add('active');
}

// 叙事字数滑块
function onNarrativeCharsSlider() {
  var slider = $('st_narrative_chars_slider');
  var valueSpan = $('st_narrative_chars_value');
  var hiddenInput = $('st_narrative_max_chars');
  if (slider && valueSpan) {
    valueSpan.textContent = slider.value;
    if (hiddenInput) hiddenInput.value = slider.value;
  }
}

// [Bug] max_tokens 开关：不限制时禁用输入框，限制时启用
function onMaxTokensToggle() {
  var cb = $('st_maxtok_enabled');
  var inp = $('st_maxtok');
  if (cb.checked) {
    inp.disabled = true;
    inp.value = '';
    inp.placeholder = '由API默认值决定';
  } else {
    inp.disabled = false;
    inp.placeholder = '8192';
    if (!inp.value) inp.value = '8192';
  }
}
function onMaxTokensInput() {
  // 实时预览：输入时立即生效（无需保存）
  var cb = $('st_maxtok_enabled');
  var val = $('st_maxtok').value;
  if (!cb.checked && val) {
    window._liveMaxTokens = parseInt(val) || 0;
  } else {
    window._liveMaxTokens = 0; // 0 = 不限制
  }
}

var THEMES = {
  obsidian:  { name:'黑曜石', bg:'#0a0a0f', panel:'#111120', text:'#e0d5c1', dim:'#7a6b5a', gold:'#c9a96e', border:'#2a1a0a', player:'#7a9ab5', ai:'#e0d5c1',
               text_bright:'#f5efe0', panel_light:'#1a1a2e', panel_border:'rgba(201,169,110,0.25)', bg_deep:'#080709', gold_light:'#e0c890', gold_dark:'#8b6914' },
  midnight:  { name:'午夜蓝', bg:'#0b0e1a', panel:'#121828', text:'#c8d6e5', dim:'#6b7c93', gold:'#6ea9c9', border:'#1a2a40', player:'#7ab5a9', ai:'#c8d6e5',
               text_bright:'#e0eaf5', panel_light:'#1a2238', panel_border:'rgba(110,169,201,0.25)', bg_deep:'#06080f', gold_light:'#8ac0e0', gold_dark:'#3a7a9a' },
  crimson:   { name:'暗红',   bg:'#0f0a0a', panel:'#1a1111', text:'#d9c8c8', dim:'#8a6b6b', gold:'#c97a6e', border:'#3a1a1a', player:'#b59a7a', ai:'#d9c8c8',
               text_bright:'#f0e0e0', panel_light:'#2a1818', panel_border:'rgba(201,122,110,0.25)', bg_deep:'#0a0606', gold_light:'#e09a8e', gold_dark:'#8a4a3e' },
  forest:    { name:'暗林',   bg:'#0a0f0a', panel:'#111a11', text:'#c8d9c8', dim:'#6b8a6b', gold:'#8ab56e', border:'#1a2a1a', player:'#9ab57a', ai:'#c8d9c8',
               text_bright:'#e0f0e0', panel_light:'#1a2a1a', panel_border:'rgba(138,181,110,0.25)', bg_deep:'#060a06', gold_light:'#a0d08e', gold_dark:'#5a8a4e' },
  parchment: { name:'羊皮纸', bg:'#f5efe6', panel:'#ede4d5', text:'#3a3228', dim:'#6a5a48', gold:'#8b6914', border:'#d4c4a8', player:'#4a7a9a', ai:'#3a3228',
               text_bright:'#1a1410', panel_light:'#f5efe6', panel_border:'rgba(139,105,20,0.3)', bg_deep:'#e8dfd0', gold_light:'#b8901c', gold_dark:'#6b4f0e' },
  sakura:    { name:'樱花',   bg:'#faf0f2', panel:'#f5e6e9', text:'#4a3540', dim:'#a08090', gold:'#c97a8a', border:'#e8d0d8', player:'#6a8a9a', ai:'#4a3540',
               text_bright:'#2a1520', panel_light:'#faf0f2', panel_border:'rgba(201,122,138,0.3)', bg_deep:'#f0e0e4', gold_light:'#e09aa8', gold_dark:'#a05a6a' },
  mint:      { name:'薄荷',   bg:'#f0f8f4', panel:'#e4f0ea', text:'#2a3a30', dim:'#709080', gold:'#2a8a5a', border:'#c8e0d0', player:'#3a7a9a', ai:'#2a3a30',
               text_bright:'#152018', panel_light:'#f0f8f4', panel_border:'rgba(42,138,90,0.3)', bg_deep:'#e0f0e8', gold_light:'#4aa07a', gold_dark:'#1a6a3e' },
  ivory:     { name:'象牙白', bg:'#fafafa', panel:'#f0f0f0', text:'#2a2a2a', dim:'#888888', gold:'#666666', border:'#d0d0d0', player:'#4a6a8a', ai:'#2a2a2a',
               text_bright:'#0a0a0a', panel_light:'#fafafa', panel_border:'rgba(102,102,102,0.3)', bg_deep:'#e8e8e8', gold_light:'#888888', gold_dark:'#444444' },
};

function getTheme(name) { return THEMES[name] || THEMES.obsidian; }

function hexToRgb(hex) {
  hex = hex.replace('#','');
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  return {
    r: parseInt(hex.slice(0,2), 16),
    g: parseInt(hex.slice(2,4), 16),
    b: parseInt(hex.slice(4,6), 16)
  };
}

function applyThemeConfig(ui) {
  var t = getTheme(ui.theme || 'obsidian');
  var root = document.documentElement;
  // [Bug] 必须设置所有 CSS 中使用的颜色变量，否则切换到浅色主题时
  //       --text-bright/--panel-light/--bg-deep 等保持深色默认值，导致文字不可见
  root.style.setProperty('--gold', t.gold);
  root.style.setProperty('--gold-light', t.gold_light || t.gold);
  root.style.setProperty('--gold-dark', t.gold_dark || t.gold);
  root.style.setProperty('--bg', t.bg);
  root.style.setProperty('--bg-deep', t.bg_deep || '#080709');
  root.style.setProperty('--text', t.text);
  root.style.setProperty('--text-bright', t.text_bright || t.text);
  root.style.setProperty('--dim', t.dim);
  root.style.setProperty('--panel', t.panel);
  root.style.setProperty('--panel-light', t.panel_light || t.panel);
  root.style.setProperty('--panel-border', t.panel_border || t.border);
  root.style.setProperty('--border', t.border);
  root.style.setProperty('--player', t.player || t.text);
  // 根据主题明暗自动调整阴影强度
  var isLight = t.text.length <= 7 && parseInt(t.text.replace('#','').slice(0,2), 16) > 128;
  var shadowOpacity = isLight ? '0.1' : '0.5';
  root.style.setProperty('--shadow', '0 8px 32px rgba(0,0,0,' + shadowOpacity + ')');
  // 金色阴影用主题金色，配合合适的透明度
  var goldRgb = hexToRgb(t.gold);
  var goldShadowOpacity = isLight ? '0.25' : '0.15';
  root.style.setProperty('--shadow-gold', '0 0 20px rgba(' + goldRgb.r + ',' + goldRgb.g + ',' + goldRgb.b + ',' + goldShadowOpacity + ')');
  document.body.style.background = t.bg;
  document.body.style.color = t.text;
  // [Bug] 浅色主题时减弱 bg-layer::after 的暗角叠加，避免画面过暗
  root.style.setProperty('--bg-vignette-overlay', isLight ? 'rgba(0,0,0,0.04)' : 'rgba(0,0,0,0.2)');
  if (ui.font_size === 'small') document.body.style.fontSize = '13px';
  else if (ui.font_size === 'large') document.body.style.fontSize = '17px';
  else document.body.style.fontSize = '15px';
  // [Bug] 同步更新 BGManager 的背景层和暗角，使其匹配当前主题配色
  //       否则 bg-layer/bg-vignette 的硬编码深色会覆盖主题效果
  if (typeof BGManager !== 'undefined' && BGManager.updateForTheme) {
    BGManager.updateForTheme(ui.theme || 'obsidian');
  }
  // 记录当前主题名，供 showGame() 等函数判断使用
  window._currentThemeName = ui.theme || 'obsidian';
}

function previewTheme() {
  var name = $('st_th').value;
  var t = getTheme(name);
  var box = $('tp_box');
  box.style.background = t.panel;
  box.style.border = '1px solid ' + t.border;
  box.style.borderRadius = '6px';
  $('tp_title').style.color = t.gold;
  $('tp_text').style.color = t.text;
  $('tp_dim').style.color = t.dim;
}

async function loadSettings() {
  try {
    // [Bug] 使用 /api/config/raw 获取未脱敏的API Key，避免保存时将脱敏Key覆盖真实Key
    var d = await api('GET', '/api/config/raw');
    var c = d || {};
    // [P3-8] 使用共享模块填充 DOM
    fillDOMFromConfig(c);
    // 叙事风格（非通用字段，仍在此处理）
    var ns = c.game?.narrative_style || '网文爽文';
    var sel = $('st_narrative_style');
    if (ns === '自定义') {
      sel.value = '自定义';
      $('custom_style_section').style.display = 'block';
      $('st_custom_style').value = c.game?.narrative_style_custom || '';
    } else {
      sel.value = ns;
      $('custom_style_section').style.display = 'none';
    }
    // [Bug] 叙事视角
    $('st_narrative_perspective').value = c.game?.narrative_perspective || 'third';
    // 叙事字数滑块
    var nmc = c.game?.narrative_max_chars || 1000;
    var slider = $('st_narrative_chars_slider');
    var hiddenVal = $('st_narrative_max_chars');
    if (slider) slider.value = nmc;
    if (hiddenVal) hiddenVal.value = nmc;
    var valueSpan = $('st_narrative_chars_value');
    if (valueSpan) valueSpan.textContent = nmc;
  // [v11] 流式输出开关
  var streamingCb = $('st_streaming_enabled');
  if (streamingCb) streamingCb.checked = (c.game?.streaming_enabled) !== false;
    // 加载v10高级配置
    loadV10Settings(c);
    updateStylePreview();
    previewTheme();
    loadProfiles();
  } catch(e) {}
}

// 加载v10高级配置到DOM
function loadV10Settings(c) {
  var v10 = c.v10 || {};
  // 叙事审查器
  var rv = v10.narrative_reviewer || {};
  var rvEl = $('st_v10_reviewer_enabled');
  if (rvEl) rvEl.checked = rv.enabled !== false;
  var rvInt = $('st_v10_reviewer_interval');
  if (rvInt) rvInt.value = rv.review_interval || 10;
  var rvLes = $('st_v10_reviewer_lessons');
  if (rvLes) rvLes.value = rv.max_lessons || 30;
  // NPC程序记忆
  var npc = v10.npc_procedural_memory || {};
  var npcEl = $('st_v10_npc_memory_enabled');
  if (npcEl) npcEl.checked = npc.enabled !== false;
  var npcMax = $('st_v10_npc_memory_max');
  if (npcMax) npcMax.value = npc.max_entries_per_npc || 30;
  var npcEv = $('st_v10_npc_memory_evolve');
  if (npcEv) npcEv.value = npc.evolve_interval_days || 10;
  // 世界任务板
  var tb = v10.world_task_board || {};
  var tbEl = $('st_v10_taskboard_enabled');
  if (tbEl) tbEl.checked = tb.enabled !== false;
  var tbMax = $('st_v10_taskboard_max');
  if (tbMax) tbMax.value = tb.max_active_tasks || 20;
  var tbAuto = $('st_v10_taskboard_auto');
  if (tbAuto) tbAuto.checked = tb.auto_assign !== false;
  // 记忆整理器
  var mc = v10.memory_curator || {};
  var mcEl = $('st_v10_curator_enabled');
  if (mcEl) mcEl.checked = mc.enabled !== false;
  var mcInt = $('st_v10_curator_interval');
  if (mcInt) mcInt.value = mc.curate_interval || 15;
  var mcMax = $('st_v10_curator_max');
  if (mcMax) mcMax.value = mc.max_archived_memories || 200;
  // 蝴蝶审批门
  var bf = v10.butterfly_approval_gate || {};
  var bfEl = $('st_v10_butterfly_enabled');
  if (bfEl) bfEl.checked = bf.enabled === true;
  var bfTh = $('st_v10_butterfly_threshold');
  if (bfTh) bfTh.value = bf.approval_threshold || 7.0;
  // 分层记忆
  var lm = v10.layered_memory || {};
  var lmImp = $('st_v10_memory_importance');
  if (lmImp) lmImp.value = lm.importance_weight || 0.25;
  var lmDec = $('st_v10_memory_decay');
  if (lmDec) lmDec.value = lm.time_decay_half_life_days || 30;
  var lmEmo = $('st_v10_memory_emotion');
  if (lmEmo) lmEmo.value = lm.emotional_weight || 0.1;
  // 伏笔生命周期
  var fs = v10.foreshadow_lifecycle || {};
  var fsEl = $('st_v10_foreshadow_enabled');
  if (fsEl) fsEl.checked = fs.enabled !== false;
  var fsMode = $('st_v10_foreshadow_mode');
  if (fsMode) fsMode.value = fs.reminder_mode || 'normal';
  var fsStale = $('st_v10_foreshadow_stale');
  if (fsStale) fsStale.value = fs.stale_threshold_days || 30;
  // 连续性审计
  var ca = v10.continuity_auditor || {};
  var caEl = $('st_v10_auditor_enabled');
  if (caEl) caEl.checked = ca.enabled !== false;
  // 多智能体叙事
  var ma = v10.multi_agent_narrative || {};
  var maEl = $('st_v10_multiagent_enabled');
  if (maEl) maEl.checked = ma.enabled !== false;
  var maSense = $('st_v10_multiagent_sensitivity');
  if (maSense) maSense.value = ma.sensitivity || 'low';
}

var _llmProfiles = {};
var _imgProfiles = {};
var _cheapProfiles = {};
var _dlgProfiles = {};
var _activeLlm = '';
var _activeImg = '';
var _activeCheap = '';
var _activeDlg = '';

async function loadProfiles() {
  try {
    var d = await api('GET', '/api/model-profiles');
    _llmProfiles = d.llm_profiles || {};
    _imgProfiles = d.image_profiles || {};
    _cheapProfiles = d.cheap_llm_profiles || {};
    _dlgProfiles = d.dialogue_llm_profiles || {};
    _activeLlm = d.active_llm || '';
    _activeImg = d.active_image || '';
    _activeCheap = d.active_cheap_llm || '';
    _activeDlg = d.active_dialogue_llm || '';

    var selL = $('st_llm_profile');
    selL.innerHTML = '<option value="">-- 手动填写 --</option>';
    Object.keys(_llmProfiles).forEach(function(name) {
      var opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      if (name === _activeLlm) opt.selected = true;
      selL.appendChild(opt);
    });

    var selC = $('st_cheap_profile');
    if (selC) {
      selC.innerHTML = '<option value="">-- 手动填写 --</option>';
      Object.keys(_cheapProfiles).forEach(function(name) {
        var opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        if (name === _activeCheap) opt.selected = true;
        selC.appendChild(opt);
      });
    }

    var selD = $('st_dlg_profile');
    if (selD) {
      selD.innerHTML = '<option value="">-- 手动填写 --</option>';
      Object.keys(_dlgProfiles).forEach(function(name) {
        var opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        if (name === _activeDlg) opt.selected = true;
        selD.appendChild(opt);
      });
    }

    var selI = $('st_img_profile');
    selI.innerHTML = '<option value="">-- 手动填写 --</option>';
    Object.keys(_imgProfiles).forEach(function(name) {
      var opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      if (name === _activeImg) opt.selected = true;
      selI.appendChild(opt);
    });
  } catch(e) {}
}

async function applyProfile(target) {
  var selId, keyId, urlId, modelId;
  if (target === 'llm') {
    selId = 'st_llm_profile';
    keyId = 'st_lk';
    urlId = 'st_lb';
    modelId = 'st_lm';
  } else if (target === 'cheap') {
    selId = 'st_cheap_profile';
    keyId = 'st_cheap_lk';
    urlId = 'st_cheap_lb';
    modelId = 'st_cheap_lm';
  } else if (target === 'dialogue') {
    selId = 'st_dlg_profile';
    keyId = 'st_dlg_lk';
    urlId = 'st_dlg_lb';
    modelId = 'st_dlg_lm';
  } else {
    selId = 'st_img_profile';
    keyId = 'st_ik';
    urlId = 'st_iu';
    modelId = 'st_im';
  }

  var sel = document.getElementById(selId);
  var name = sel.value;
  if (!name) return;
  try {
    var d = await api('POST', '/api/model-profiles/apply', {name: name, target: target});
    if (d.error) { alert(d.error); return; }
    // [Bug] 使用后端返回的真实配置（含未脱敏API Key），而非本地缓存的脱敏Key
    // 后端 /model-profiles/apply 返回 {status, llm, image, cheap_llm, dialogue_llm}
    var sectionMap = {llm: 'llm', cheap: 'cheap_llm', dialogue: 'dialogue_llm', image: 'image'};
    var p = d[sectionMap[target]] || d.config || d;
    if (p && p.api_key !== undefined) {
      $(keyId).value = p.api_key || '';
      $(urlId).value = p.base_url || '';
      $(modelId).value = p.model_name || '';
    }
    if (target === 'llm') _activeLlm = name;
    else if (target === 'cheap') _activeCheap = name;
    else if (target === 'dialogue') _activeDlg = name;
    else _activeImg = name;
  } catch(e) { alert('切换失败'); }
}

async function saveProfile(target) {
  var nameElId, keyId, urlId, modelId, selId;
  if (target === 'llm') {
    nameElId = 'st_llm_pname';
    keyId = 'st_lk';
    urlId = 'st_lb';
    modelId = 'st_lm';
    selId = 'st_llm_profile';
  } else if (target === 'cheap') {
    nameElId = 'st_cheap_pname';
    keyId = 'st_cheap_lk';
    urlId = 'st_cheap_lb';
    modelId = 'st_cheap_lm';
    selId = 'st_cheap_profile';
  } else if (target === 'dialogue') {
    nameElId = 'st_dlg_pname';
    keyId = 'st_dlg_lk';
    urlId = 'st_dlg_lb';
    modelId = 'st_dlg_lm';
    selId = 'st_dlg_profile';
  } else {
    nameElId = 'st_img_pname';
    keyId = 'st_ik';
    urlId = 'st_iu';
    modelId = 'st_im';
    selId = 'st_img_profile';
  }
  
  var nameEl = document.getElementById(nameElId);
  var name = nameEl.value.trim();
  if (!name) { alert('请输入配置名称'); return; }
  try {
    await api('POST', '/api/model-profiles/save', {
      name: name, target: target,
      api_key: document.getElementById(keyId).value,
      base_url: document.getElementById(urlId).value,
      model_name: document.getElementById(modelId).value,
    });
    nameEl.value = '';
    await loadProfiles();
    var sel = document.getElementById(selId);
    sel.value = name;
    alert('配置已保存: ' + name);
  } catch(e) { alert('保存失败'); }
}

async function deleteProfile(target) {
  var selId;
  if (target === 'llm') selId = 'st_llm_profile';
  else if (target === 'cheap') selId = 'st_cheap_profile';
  else if (target === 'dialogue') selId = 'st_dlg_profile';
  else selId = 'st_img_profile';
  var sel = document.getElementById(selId);
  var name = sel.value;
  if (!name) { alert('请先选择要删除的配置'); return; }
  if (!confirm('确定删除配置「' + name + '」？')) return;
  try {
    await api('POST', '/api/model-profiles/delete', {name: name, target: target});
    await loadProfiles();
  } catch(e) { alert('删除失败'); }
}

async function saveSettings() {
  var styleName = $('st_narrative_style').value;
  var customText = styleName === '自定义' ? $('st_custom_style').value : '';
  // [Bug] 叙事视角
  var perspective = $('st_narrative_perspective').value;
  
  var styleRes = await api('POST', '/api/narrative-style', {style_name: styleName, custom_text: customText, narrative_perspective: perspective});
  if (styleRes && styleRes.error) {
    console.error('保存叙事风格失败', styleRes.error);
  }

  var config = collectConfigFromDOM();
  var body = buildFullSettingsBody(config);
  
  // [v11] 流式输出开关
  body.streaming_enabled = ($('st_streaming_enabled') || {checked: true}).checked;

  // 添加v10高级配置
  body.v10 = collectV10Settings();
  
  var res = await api('POST', '/api/full-settings', body);
  if (res && res.error) {
    alert('保存失败：' + res.error);
    return;
  }
  
  applyThemeConfig(config.ui);
  closeSettings();
}

// 收集v10高级配置
function collectV10Settings() {
  return {
    narrative_reviewer: {
      enabled: ($('st_v10_reviewer_enabled') || {checked: true}).checked,
      review_interval: parseInt(($('st_v10_reviewer_interval') || {value: '10'}).value) || 10,
      max_lessons: parseInt(($('st_v10_reviewer_lessons') || {value: '30'}).value) || 30
    },
    npc_procedural_memory: {
      enabled: ($('st_v10_npc_memory_enabled') || {checked: true}).checked,
      max_entries_per_npc: parseInt(($('st_v10_npc_memory_max') || {value: '30'}).value) || 30,
      evolve_interval_days: parseInt(($('st_v10_npc_memory_evolve') || {value: '10'}).value) || 10
    },
    world_task_board: {
      enabled: ($('st_v10_taskboard_enabled') || {checked: true}).checked,
      max_active_tasks: parseInt(($('st_v10_taskboard_max') || {value: '20'}).value) || 20,
      auto_assign: ($('st_v10_taskboard_auto') || {checked: true}).checked
    },
    memory_curator: {
      enabled: ($('st_v10_curator_enabled') || {checked: true}).checked,
      curate_interval: parseInt(($('st_v10_curator_interval') || {value: '15'}).value) || 15,
      max_archived_memories: parseInt(($('st_v10_curator_max') || {value: '200'}).value) || 200
    },
    butterfly_approval_gate: {
      enabled: ($('st_v10_butterfly_enabled') || {checked: true}).checked,
      approval_threshold: parseFloat(($('st_v10_butterfly_threshold') || {value: '7.0'}).value) || 7.0
    },
    layered_memory: {
      importance_weight: parseFloat(($('st_v10_memory_importance') || {value: '0.25'}).value) || 0.25,
      time_decay_half_life_days: parseInt(($('st_v10_memory_decay') || {value: '30'}).value) || 30,
      emotional_weight: parseFloat(($('st_v10_memory_emotion') || {value: '0.1'}).value) || 0.1
    },
    foreshadow_lifecycle: {
      enabled: ($('st_v10_foreshadow_enabled') || {checked: true}).checked,
      reminder_mode: ($('st_v10_foreshadow_mode') || {value: 'normal'}).value || 'normal',
      stale_threshold_days: parseInt(($('st_v10_foreshadow_stale') || {value: '30'}).value) || 30
    },
    continuity_auditor: {
      enabled: ($('st_v10_auditor_enabled') || {checked: true}).checked,
      audit_interval: 10
    },
    multi_agent_narrative: {
      enabled: ($('st_v10_multiagent_enabled') || {checked: true}).checked,
      sensitivity: ($('st_v10_multiagent_sensitivity') || {value: 'low'}).value || 'low',
      max_revisions: 1
    }
  };
}

function applyTheme() {
  api('GET', '/api/config').then(function(d) {
    var ui = (d && d.ui) || {};
    applyThemeConfig(ui);
  }).catch(function(){});
}

// [v8] 叙事风格相关函数
var STYLE_DESCRIPTIONS = {
  '章回体': '以章回体小说风格撰写，语言半文半白，节奏舒缓，注重铺垫和悬念。风格参考《三言二拍》《水浒传》的白话文。',
  '半古半文': '文言句式与白话叙事交融，类似《明朝那些事儿》或《琅琊榜》的风格。句式简练有力，偶用典故，但不晦涩。',
  '大白话': '现代口语化叙事，轻松幽默，像朋友在讲故事。短句为主，偶尔吐槽，贴近当代网文读者的阅读习惯。',
  '严肃文学': '冷峻克制的文学风格，类似余华、莫言。注重细节描写和心理刻画，语言凝练，情感内敛。',
  '网文爽文': '快节奏网文风格，爽点密集，系统提示频繁。数据化呈现，升级打怪。语言直白有力，每段都有钩子。',
  '诗化散文': '意境优先的散文风格，类似迟子建的作品。注重景物描写和氛围营造，语言优美，富有诗意。'
};

function onStyleChange() {
  var sel = $('st_narrative_style').value;
  if (sel === '自定义') {
    $('custom_style_section').style.display = 'block';
  } else {
    $('custom_style_section').style.display = 'none';
  }
  updateStylePreview();
}

function updateStylePreview() {
  var sel = $('st_narrative_style').value;
  var preview = $('style_desc_preview');
  if (sel === '自定义') {
    preview.textContent = '自定义模式：在下方文本框中描述你想要的写作风格，或上传风格文件。';
  } else {
    preview.textContent = STYLE_DESCRIPTIONS[sel] || '';
  }
}

async function uploadStyleFile() {
  $('style_file_input').click();
}

async function handleStyleFile(input) {
  var file = input.files[0];
  if (!file) return;
  var info = $('style_file_info');
  info.textContent = '正在解析...';
  var fd = new FormData();
  fd.append('file', file);
  try {
    // [Bug] 添加认证头，避免被安全中间件拦截
    var headers = {};
    if (window._accessToken) headers['Authorization'] = 'Bearer ' + window._accessToken;
    var resp = await fetch('/api/narrative-style/upload', { method: 'POST', body: fd, headers: headers });
    var d = await resp.json();
    if (d.error) { info.textContent = '❌ ' + d.error; return; }
    // 将提取的风格填入自定义文本框
    $('st_custom_style').value = d.extracted_style || d.text || '';
    $('st_narrative_style').value = '自定义';
    $('custom_style_section').style.display = 'block';
    info.textContent = '✅ 已从 ' + file.name + ' 提取风格特征';
    updateStylePreview();
  } catch(e) {
    info.textContent = '❌ 上传失败';
  }
  input.value = '';
}

window.onload = function() {
  loadSaves();
  applyTheme();
};

async function handleFileUpload(input) {
  var file = input.files[0];
  if (!file) return;
  var info = $('fileInfo');
  info.textContent = '正在解析 ' + file.name + '...';
  var fd = new FormData();
  fd.append('file', file);
  try {
    // [Bug] 添加认证头，避免被安全中间件拦截
    var headers = {};
    if (window._accessToken) headers['Authorization'] = 'Bearer ' + window._accessToken;
    var resp = await fetch('/api/upload-description', { method: 'POST', body: fd, headers: headers });
    var d = await resp.json();
    if (d.error) { info.textContent = '❌ ' + d.error; return; }
    var wd = $('wd');
    if (wd.value && d.text) {
      if (!confirm('输入框已有内容，是否替换为文件内容？\n\n确定 = 替换\n取消 = 追加到末尾')) {
        wd.value = wd.value + '\n\n' + d.text;
      } else {
        wd.value = d.text;
      }
    } else {
      wd.value = d.text;
    }
    info.textContent = '✅ 已加载 ' + d.filename + ' (' + d.text.length + '字)';
    toast('文件已加载: ' + d.filename, 'success');
  } catch(e) {
    info.textContent = '❌ 上传失败';
  }
  input.value = '';
}

function toggleRightPanel() {
  var panel = $('rightPanel');
  panel.classList.toggle('show');
}