// [P3-8] 配置字段映射共享模块
// 消除 settings.js 和 alpine-components.js 之间的配置字段重复定义

// 配置区块定义：每个区块的字段名和默认值
var CONFIG_SECTIONS = {
  llm: {
    fields: ['api_key', 'base_url', 'model_name', 'max_tokens'],
    defaults: { api_key: '', base_url: 'https://token-plan-cn.xiaomimimo.com/v1', model_name: 'mimo-V2.5-Pro', max_tokens: 8192 }
  },
  // [2026-08-10] 备用模型（cheap_llm）已移除
  dialogue_llm: {
    fields: ['enabled', 'api_key', 'base_url', 'model_name'],
    defaults: { enabled: false, api_key: '', base_url: '', model_name: '' }
  },
  image: {
    fields: ['api_key', 'base_url', 'model_name'],
    defaults: { api_key: '', base_url: 'https://api.siliconflow.cn/v1/images/generations', model_name: 'Kwai-Kolors/Kolors' }
  },
  embedding: {
    fields: ['api_key', 'base_url', 'model_name'],
    defaults: { api_key: '', base_url: 'https://api.siliconflow.cn/v1', model_name: 'BAAI/bge-m3' }
  },
  ui: {
    fields: ['theme', 'font_size', 'strip_gray_narrative', 'accent_color', 'bg_color', 'text_color', 'panel_bg'],
    defaults: { theme: 'obsidian', font_size: 'medium', strip_gray_narrative: true }
  },
  fixed_prompt: {
    fields: ['content', 'enabled'],
    defaults: { content: '', enabled: true }
  },
  game: {
    fields: ['narrative_style', 'narrative_style_custom', 'narrative_perspective', 'economy_enabled', 'narrative_max_chars', 'action_validation_enabled'],
    defaults: { narrative_style: '真人作者', narrative_style_custom: '', narrative_perspective: 'third', economy_enabled: false, narrative_max_chars: 2000, action_validation_enabled: true }
  }
};

// [P3-8] 从 /api/config 响应构建完整 config 对象（带默认值）
function buildConfigFromResponse(res) {
  var config = {};
  for (var section in CONFIG_SECTIONS) {
    var def = CONFIG_SECTIONS[section];
    config[section] = Object.assign({}, def.defaults, res[section] || {});
  }
  config.npc_info_visibility = res.npc_info_visibility || 'immersive';
  config.game = res.game || { narrative_style: '真人作者' };
  return config;
}

// [P3-8] 从 config 对象构建 /api/full-settings 请求体
function buildFullSettingsBody(config) {
  return {
    llm_api_key: config.llm.api_key,
    llm_base_url: config.llm.base_url,
    llm_model: config.llm.model_name,
    llm_max_tokens: config.llm.max_tokens || 0,
    // [2026-08-10] 备用模型（cheap_llm）已移除：不再提交 cheap_llm_* 字段
    dialogue_llm_enabled: config.dialogue_llm.enabled,
    dialogue_llm_api_key: config.dialogue_llm.enabled ? config.dialogue_llm.api_key : '',
    dialogue_llm_base_url: config.dialogue_llm.enabled ? config.dialogue_llm.base_url : '',
    dialogue_llm_model: config.dialogue_llm.enabled ? config.dialogue_llm.model_name : '',
    image_api_key: config.image.api_key,
    image_base_url: config.image.base_url,
    image_model: config.image.model_name,
    embedding_api_key: config.embedding.api_key,
    embedding_base_url: config.embedding.base_url,
    embedding_model: config.embedding.model_name,
    theme: config.ui.theme,
    font_size: config.ui.font_size,
    fixed_prompt: config.fixed_prompt.content,
    fixed_prompt_enabled: config.fixed_prompt.enabled,
    strip_gray_narrative: config.ui.strip_gray_narrative,
    npc_info_visibility: config.npc_info_visibility,
    economy_enabled: config.game.economy_enabled,
    narrative_max_chars: parseInt(config.game.narrative_max_chars) || 2000,
    // [v1.3] NPC 私密档案开关（普通模式）
    npc_private_facts_enabled: !!(config.features && config.features.npc_private_facts && config.features.npc_private_facts.enabled),
    // [v1.3] 小说模式叙事视角
    novel_perspective: (config.novel_roleplay && config.novel_roleplay.narrative_perspective) || 'third'
  };
}

// [P3-8] 从 DOM 输入框收集配置（供 settings.js 使用）
function collectConfigFromDOM() {
  // [Bug] max_tokens: 不限制时为 0
  var mtEnabled = $('st_maxtok_enabled').checked;
  var mtVal = $('st_maxtok').value;
  var maxTokens = mtEnabled ? 0 : (parseInt(mtVal) || 8192);
  return {
    llm: {
      api_key: $('st_lk').value,
      base_url: $('st_lb').value,
      model_name: $('st_lm').value,
      max_tokens: maxTokens
    },
    // [2026-08-10] 备用模型（cheap_llm）已移除：不再收集，统一走主模型
    dialogue_llm: {
      enabled: $('st_dlg_enabled') ? $('st_dlg_enabled').checked : false,
      api_key: $('st_dlg_lk') ? $('st_dlg_lk').value : '',
      base_url: $('st_dlg_lb') ? $('st_dlg_lb').value : '',
      model_name: $('st_dlg_lm') ? $('st_dlg_lm').value : ''
    },
    image: {
      api_key: $('st_ik').value,
      base_url: $('st_iu').value,
      model_name: $('st_im').value
    },
    embedding: {
      api_key: $('st_ek').value,
      base_url: $('st_eu').value,
      model_name: $('st_em').value
    },
    ui: {
      theme: $('st_th').value,
      font_size: $('st_fs').value,
      strip_gray_narrative: $('st_strip_gray').checked
    },
    fixed_prompt: {
      content: $('st_fp').value,
      enabled: $('st_fpe').checked
    },
    npc_info_visibility: $('st_npc_visibility').value,
    game: {
      narrative_style: $('st_narrative_style') ? $('st_narrative_style').value : '真人作者',
      narrative_style_custom: (function() {
        // [v1.5] 选中自定义N 时，把对应槽位内容同步到 narrative_style_custom
        var s = $('st_narrative_style');
        var sn = s ? s.value : '';
        var m = /^自定义([123])$/.exec(sn);
        if (m) { var ta = $('st_custom_style_' + m[1]); return ta ? ta.value : ''; }
        return '';
      })(),
      economy_enabled: $('st_economy_enabled').checked,
      narrative_max_chars: parseInt($('st_narrative_max_chars').value) || 2000,
      action_validation_enabled: $('st_action_validation') ? $('st_action_validation').checked : false
    },
    novel_roleplay: {
      narrative_perspective: $('st_novel_perspective') ? $('st_novel_perspective').value : 'third'
    },
    // [v1.3] NPC 私密档案开关（普通模式）
    features: {
      npc_private_facts: {
        enabled: $('st_npc_private_facts') ? $('st_npc_private_facts').checked : false
      }
    }
  };
}

// [P3-8] 将 /api/config 响应填充到 DOM 输入框（供 settings.js 使用）
function fillDOMFromConfig(c) {
  $('st_lk').value = c.llm?.api_key || '';
  $('st_lb').value = c.llm?.base_url || '';
  $('st_lm').value = c.llm?.model_name || '';
  // [Bug] max_tokens: 0 或空 = 不限制
  var mt = c.llm?.max_tokens || 0;
  var cb = $('st_maxtok_enabled');
  var inp = $('st_maxtok');
  if (!mt || mt <= 0) {
    cb.checked = true;
    inp.disabled = true;
    inp.value = '';
    inp.placeholder = '由API默认值决定';
  } else {
    cb.checked = false;
    inp.disabled = false;
    inp.value = mt;
  }
  // [2026-08-10] 备用模型（cheap_llm）已移除：不再回填 DOM
  var dlg = c.dialogue_llm || {};
  // [Bugfix 2026-08-09] 对话模型 section 已被 v11 注释屏蔽（HTML 注释内），
  // 元素不存在时跳过，否则 null.checked 抛异常导致 loadSettings 整体中断
  if ($('st_dlg_enabled')) $('st_dlg_enabled').checked = dlg.enabled === true;
  if ($('st_dlg_lk')) $('st_dlg_lk').value = dlg.api_key || '';
  if ($('st_dlg_lb')) $('st_dlg_lb').value = dlg.base_url || '';
  if ($('st_dlg_lm')) $('st_dlg_lm').value = dlg.model_name || '';
  $('st_ik').value = c.image?.api_key || '';
  $('st_iu').value = c.image?.base_url || '';
  $('st_im').value = c.image?.model_name || '';
  $('st_ek').value = c.embedding?.api_key || '';
  $('st_eu').value = c.embedding?.base_url || '';
  $('st_em').value = c.embedding?.model_name || '';
  $('st_th').value = c.ui?.theme || 'obsidian';
  $('st_fs').value = c.ui?.font_size || 'medium';
  $('st_strip_gray').checked = c.ui?.strip_gray_narrative !== false;
  $('st_npc_visibility').value = c.npc_info_visibility || 'immersive';
  var fp = c.fixed_prompt || {};
  $('st_fp').value = fp.content || '';
  $('st_fpe').checked = fp.enabled !== false;
  $('st_economy_enabled').checked = c.game?.economy_enabled === true;
  $('st_narrative_max_chars').value = c.game?.narrative_max_chars || 2000;
  if ($('st_action_validation')) $('st_action_validation').checked = c.game?.action_validation_enabled !== false;
  // [v1.3] NPC 私密档案开关（普通模式）
  if ($('st_npc_private_facts')) {
    $('st_npc_private_facts').checked = !!(c.features && c.features.npc_private_facts && c.features.npc_private_facts.enabled);
  }
  // [v1.3] 小说模式叙事视角（默认 third）
  if ($('st_novel_perspective')) {
    $('st_novel_perspective').value = (c.novel_roleplay && c.novel_roleplay.narrative_perspective) || 'third';
  }
}
