/**
 * [v12+] 小说人物扮演模块
 *
 * 新流程（两阶段处理）：
 *   1. 导入小说 → 快速处理（30秒）→ 角色选择 → 章节选择
 *   2. 选定章节后 → 深度处理该章节前内容（1-3分钟）→ 进入游戏
 *
 * 优势：用户30秒就能看到角色，不用等整本处理完。
 */

// ── 状态管理 ──────────────────────────────────────────────

var NovelRoleplay = {
  state: 'idle',          // idle | uploading | processing | characters | chapters | deep_processing | entering
  progress: 0,
  statusMsg: '',
  characters: [],
  chapters: [],
  selectedCharacter: null,
  selectedChapter: null,  // [v12+] 选定的章节序号
  selectedTimeline: null,  // 保留兼容
  pollTimer: null,
  deepProcessPhase: '',    // deep_processing 时区分 quick / deep
  _locatedCharPosition: null,  // [v12+] 粘贴文字定位后的字符位置（方案C用）
};

// ── 耗时预估 ──────────────────────────────────────────────

/**
 * 根据小说字数预估快速导入耗时（约30秒，与字数无关）。
 * 深度处理时间取决于玩家选择的章节位置。
 */
function updateNovelTimeEstimate(charCount) {
  var estBox = document.getElementById('nrTimeEstimate');
  var estText = document.getElementById('nrTimeEstimateText');
  if (!estBox || !estText) return;

  var level, icon;
  if (charCount < 200000) {
    level = '轻量';
    icon = '⚡';
  } else if (charCount < 500000) {
    level = '中篇';
    icon = '📖';
  } else if (charCount < 1000000) {
    level = '长篇';
    icon = '📚';
  } else {
    level = '超长篇';
    icon = '🏯';
  }

  estText.innerHTML =
    icon + ' <strong>' + level + '</strong> · 约 ' + charCount.toLocaleString() + ' 字<br>' +
    '⏱ 快速导入耗时：<strong style="color:var(--gold)">约30秒</strong>（提取章节结构+主要角色）<br>' +
    '<span style="color:var(--dim);font-size:.9em">深度处理在选定章节后进行，只处理该章节前的内容</span>';
  estBox.style.display = 'block';
}

// ── 页面切换 ──────────────────────────────────────────────

function _hideAllPages() {
  ['home', 'createWorldPage', 'loadSavePage', 'novelRoleplayPage', 'game'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

var _bjImageList = [];
for (var _i = 1; _i <= 22; _i++) {
  _bjImageList.push('/images/bj/bj' + _i + '.png');
}
for (var _j = 1; _j <= 15; _j++) {
  _bjImageList.push('/images/bj/hj' + _j + '.png');
}

var _lastBjIndex = -1;
function _getRandomBjImage() {
  var idx;
  if (_bjImageList.length <= 1) {
    idx = 0;
  } else {
    do {
      idx = Math.floor(Math.random() * _bjImageList.length);
    } while (idx === _lastBjIndex);
  }
  _lastBjIndex = idx;
  return _bjImageList[idx];
}

function setHomeBackground() {
  BGManager.forceSetHomeBg();
}

function setPageBackground() {
  var url = _getRandomBjImage();
  BGManager.setBackground(url, false);
}

function setGameBackground() {
  if (typeof BGManager === 'undefined') return;
  var theme = (typeof window !== 'undefined' && window._currentThemeName) || 'obsidian';
  var isLightTheme = ['parchment', 'sakura', 'mint', 'ivory'].indexOf(theme) >= 0;
  BGManager.setGradientBg(isLightTheme ? ('theme-' + theme) : 'default', false);
}

function showCreateWorld() {
  _hideAllPages();
  document.getElementById('createWorldPage').style.display = 'block';
  setPageBackground();
}

function showLoadSave() {
  _hideAllPages();
  document.getElementById('loadSavePage').style.display = 'block';
  setPageBackground();
  // 刷新存档列表
  if (typeof refreshSaveList === 'function') {
    refreshSaveList();
  }
}

function showNovelRoleplay() {
  _hideAllPages();
  document.getElementById('novelRoleplayPage').style.display = 'block';
  setPageBackground();
  NovelRoleplay.state = 'idle';
  updateNovelRoleplayUI();
}

function backToHome() {
  _hideAllPages();
  document.getElementById('home').style.display = 'flex';
  // [NovelRoleplay] 回到首页时停止二级页面背景轮换
  if (typeof BGManager !== 'undefined' && BGManager.stopSubpageBgRotation) {
    BGManager.stopSubpageBgRotation();
  }
  setHomeBackground();
  if (NovelRoleplay.pollTimer) {
    clearInterval(NovelRoleplay.pollTimer);
    NovelRoleplay.pollTimer = null;
  }
}

// ── UI 更新 ───────────────────────────────────────────────

function updateNovelRoleplayUI() {
  var uploadSection = document.getElementById('nrUploadSection');
  var processingSection = document.getElementById('nrProcessingSection');
  var charactersSection = document.getElementById('nrCharactersSection');
  var chaptersSection = document.getElementById('nrChaptersSection');
  var timelineSection = document.getElementById('nrTimelineSection');
  var topBackBtn = document.getElementById('nrTopBackBtn');
  var topBackToCharBtn = document.getElementById('nrTopBackToCharBtn');

  // 隐藏所有
  if (uploadSection) uploadSection.style.display = 'none';
  if (processingSection) processingSection.style.display = 'none';
  if (charactersSection) charactersSection.style.display = 'none';
  if (chaptersSection) chaptersSection.style.display = 'none';
  if (timelineSection) timelineSection.style.display = 'none';

  // [v12+] 章节选择页：左上角换成"返回角色选择"，其他页面保持"返回"
  if (NovelRoleplay.state === 'chapters') {
    if (topBackBtn) topBackBtn.style.display = 'none';
    if (topBackToCharBtn) topBackToCharBtn.style.display = 'inline-block';
  } else {
    if (topBackBtn) topBackBtn.style.display = 'inline-block';
    if (topBackToCharBtn) topBackToCharBtn.style.display = 'none';
  }

  switch (NovelRoleplay.state) {
    case 'idle':
      if (uploadSection) uploadSection.style.display = 'block';
      break;
    case 'uploading':
    case 'processing':
    case 'deep_processing':
      if (processingSection) processingSection.style.display = 'block';
      break;
    case 'characters':
      if (charactersSection) charactersSection.style.display = 'block';
      break;
    case 'chapters':
      if (chaptersSection) chaptersSection.style.display = 'block';
      break;
    case 'timeline':
      if (timelineSection) timelineSection.style.display = 'block';
      break;
  }
}

// ── 上传小说 ──────────────────────────────────────────────

function handleNovelFileUpload(input) {
  var file = input.files[0];
  if (!file) return;

  // 检查文件大小（限制50MB）
  if (file.size > 50 * 1024 * 1024) {
    alert('文件太大，请限制在50MB以内');
    return;
  }

  // [v12修复] 用 ArrayBuffer 读取，自动检测编码（UTF-8 / GBK / GB18030）
  // 之前写死 UTF-8 导致 GBK 编码的小说（如起点下载的）会变成乱码，
  // 字数从274万降到38万，章节标题无法识别
  var reader = new FileReader();
  reader.onload = function(e) {
    var buffer = e.target.result;
    var bytes = new Uint8Array(buffer);

    // 自动检测编码：UTF-8 BOM / UTF-8 严格模式 / GBK / GB18030
    var text = null;
    var detectedEncoding = 'utf-8';

    // 1. 尝试 UTF-8（严格模式，有BOM则跳过）
    try {
      var decoder = new TextDecoder('utf-8', { fatal: true });
      text = decoder.decode(bytes);
      // 如果包含 replacement char，说明 UTF-8 解码有问题
      if (text.indexOf('\uFFFD') !== -1) {
        text = null;
      }
    } catch (err) {
      text = null;
    }

    // 2. 尝试 GBK
    if (text === null) {
      try {
        var gbkDecoder = new TextDecoder('gbk', { fatal: true });
        text = gbkDecoder.decode(bytes);
        detectedEncoding = 'gbk';
        if (text.indexOf('\uFFFD') !== -1) {
          text = null;
        }
      } catch (err) {
        text = null;
      }
    }

    // 3. 尝试 GB18030（GBK超集）
    if (text === null) {
      try {
        var gb18030Decoder = new TextDecoder('gb18030', { fatal: false });
        text = gb18030Decoder.decode(bytes);
        detectedEncoding = 'gb18030';
      } catch (err) {
        text = null;
      }
    }

    // 4. 最终回退：UTF-8 宽松模式
    if (text === null) {
      var fallbackDecoder = new TextDecoder('utf-8', { fatal: false });
      text = fallbackDecoder.decode(bytes);
      detectedEncoding = 'utf-8 (fallback)';
    }

    console.log('小说编码检测:', detectedEncoding, '字数:', text.length);

    document.getElementById('nrNovelName').value = file.name.replace(/\.[^.]+$/, '');
    document.getElementById('nrFilePreview').textContent =
      '已加载: ' + file.name + ' (' + text.length.toLocaleString() + ' 字, 编码: ' + detectedEncoding + ')';
    document.getElementById('nrFilePreview').style.color = 'var(--gold)';
    // 保存文本到全局变量
    window._novelText = text;

    // 动态预估处理时长并显示
    updateNovelTimeEstimate(text.length);
  };
  reader.onerror = function() {
    alert('文件读取失败');
  };
  reader.readAsArrayBuffer(file);
}

async function startNovelImport() {
  var text = window._novelText;
  if (!text || text.length < 100) {
    alert('请先选择小说文件（至少100字）');
    return;
  }

  var novelName = document.getElementById('nrNovelName').value || '未命名小说';

  NovelRoleplay.state = 'uploading';
  NovelRoleplay.progress = 0;
  NovelRoleplay.statusMsg = '正在上传...';
  updateNovelRoleplayUI();
  document.getElementById('nrProcessingSection').style.display = 'block';

  try {
    var headers = { 'Content-Type': 'application/json' };
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;

    var resp = await fetch('/api/novel-roleplay/import', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ text: text, novel_name: novelName })
    });

    var data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || '上传失败');
    }

    // 开始轮询进度
    NovelRoleplay.state = 'processing';
    NovelRoleplay.statusMsg = 'AI正在深度理解小说...';
    updateNovelRoleplayUI();
    startPollingStatus();
  } catch (e) {
    alert('导入失败: ' + e.message);
    NovelRoleplay.state = 'idle';
    updateNovelRoleplayUI();
  }
}

// ── 轮询进度 ──────────────────────────────────────────────

function startPollingStatus() {
  if (NovelRoleplay.pollTimer) clearInterval(NovelRoleplay.pollTimer);

  NovelRoleplay.pollTimer = setInterval(async function() {
    try {
      var headers = {};
      if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
      var resp = await fetch('/api/novel-roleplay/status', { headers: headers });
      var data = await resp.json();

      NovelRoleplay.progress = data.progress || 0;
      NovelRoleplay.statusMsg = data.message || '';

      // 更新UI
      var bar = document.getElementById('nrProgressBar');
      var msg = document.getElementById('nrProgressMsg');
      var detail = document.getElementById('nrProgressDetail');
      if (bar) bar.style.width = NovelRoleplay.progress + '%';
      if (msg) msg.textContent = NovelRoleplay.statusMsg;
      if (detail) {
        var parts = [];
        if (data.total_chars) parts.push(data.total_chars.toLocaleString() + ' 字');
        if (data.chunks) parts.push(data.chunks + ' 块');
        if (data.entities) parts.push(data.entities + ' 实体');
        if (data.relations) parts.push(data.relations + ' 关系');
        if (data.key_events) parts.push(data.key_events + ' 关键事件');
        if (data.chapters) parts.push(data.chapters + ' 章节');
        detail.textContent = parts.join(' · ');
      }

      if (data.state === 'done') {
        clearInterval(NovelRoleplay.pollTimer);
        NovelRoleplay.pollTimer = null;

        // [v12+] 根据 deepProcessPhase 决定下一步
        if (NovelRoleplay.deepProcessPhase === 'deep') {
          // 深度处理完成 → 进入游戏
          await enterNovelRoleplayAfterDeepProcess();
        } else {
          // 快速处理完成 → 加载角色列表
          await loadCharacters();
        }
      } else if (data.state === 'error') {
        clearInterval(NovelRoleplay.pollTimer);
        NovelRoleplay.pollTimer = null;
        alert('处理失败: ' + (data.error || data.message));
        NovelRoleplay.state = 'idle';
        NovelRoleplay.deepProcessPhase = '';
        updateNovelRoleplayUI();
      }
    } catch (e) {
      console.error('轮询失败:', e);
    }
  }, 2000);
}

// ── 角色选择 ──────────────────────────────────────────────

async function loadCharacters() {
  try {
    var headers = {};
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
    var resp = await fetch('/api/novel-roleplay/characters', { headers: headers });
    var data = await resp.json();

    NovelRoleplay.characters = data.characters || [];
    NovelRoleplay.state = 'characters';
    updateNovelRoleplayUI();
    renderCharacters();
  } catch (e) {
    alert('加载角色失败: ' + e.message);
    NovelRoleplay.state = 'idle';
    updateNovelRoleplayUI();
  }
}

function renderCharacters() {
  var container = document.getElementById('nrCharactersList');
  container.innerHTML = '';

  if (NovelRoleplay.characters.length === 0) {
    container.innerHTML = '<p style="color:var(--text-muted)">未检测到角色，请检查小说内容。</p>';
    return;
  }

  NovelRoleplay.characters.forEach(function(char, idx) {
    var card = document.createElement('div');
    card.className = 'nr-character-card';
    card.onclick = function(e) { selectCharacter(idx, e); };

    var importance = char.importance_score || 0;
    var stars = '';
    for (var i = 0; i < 5; i++) {
      stars += i < Math.round(importance / 3) ? '★' : '☆';
    }

    card.innerHTML =
      '<div class="nr-char-name">' + escapeHtml(char.name) + '</div>' +
      '<div class="nr-char-desc">' + escapeHtml(char.description || '暂无描述') + '</div>' +
      '<div class="nr-char-stats">' +
        '<span>提及 ' + (char.mention_count || 0) + ' 次</span>' +
        '<span>关系 ' + (char.relationship_count || 0) + ' 条</span>' +
        '<span class="nr-stars">' + stars + '</span>' +
      '</div>' +
      (char.available_timeline_points ?
        '<div class="nr-char-tl">可用时间节点: ' + char.available_timeline_points + '</div>' : '');

    container.appendChild(card);
  });
}

function selectCharacter(idx, domEvent) {
  var char = NovelRoleplay.characters[idx];
  if (!char) return;

  // 取消之前的选中
  document.querySelectorAll('.nr-character-card').forEach(function(c) {
    c.classList.remove('selected');
  });

  // [v12修复] 用传入的 domEvent 取 currentTarget
  var card = domEvent ? domEvent.currentTarget :
              document.querySelectorAll('.nr-character-card')[idx];
  if (card) card.classList.add('selected');
  NovelRoleplay.selectedCharacter = char.name;

  // 启用下一步按钮
  document.getElementById('nrToTimelineBtn').disabled = false;
}

async function goToChapters() {
  if (!NovelRoleplay.selectedCharacter) {
    alert('请先选择一个角色');
    return;
  }

  try {
    var headers = {};
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
    var resp = await fetch('/api/novel-roleplay/chapters', { headers: headers });
    var data = await resp.json();

    NovelRoleplay.chapters = data.chapters || [];
    NovelRoleplay.state = 'chapters';
    updateNovelRoleplayUI();
    renderChapters();
  } catch (e) {
    alert('加载章节列表失败: ' + e.message);
  }
}

// ── 章节选择 ──────────────────────────────────────────────

function renderChapters() {
  var container = document.getElementById('nrChaptersList');
  if (!container) return;
  container.innerHTML = '';

  // [v12+] 自动分章提示
  var hintBox = document.getElementById('nrAutoSplitHint');
  var autoCount = NovelRoleplay.chapters.filter(function(c) { return c.is_auto_split; }).length;
  if (hintBox) {
    if (NovelRoleplay.chapters.length === 0) {
      hintBox.style.display = 'block';
      hintBox.innerHTML = '⚠️ 未检测到章节标题，且自动分章失败。请使用下方"按进度进入"或"粘贴文字定位"。';
    } else if (autoCount === NovelRoleplay.chapters.length) {
      hintBox.style.display = 'block';
      hintBox.innerHTML = 'ℹ️ 本小说未检测到章节标题，已按段落自动分章（共 ' + NovelRoleplay.chapters.length + ' 段）。' +
                          '如不满意，可使用下方"按进度进入"或"粘贴文字定位"。';
    } else if (autoCount > 0) {
      hintBox.style.display = 'block';
      hintBox.innerHTML = 'ℹ️ 部分章节为自动分章（' + autoCount + '/' + NovelRoleplay.chapters.length + '）。';
    } else {
      hintBox.style.display = 'none';
    }
  }

  if (NovelRoleplay.chapters.length === 0) {
    container.innerHTML = '<p style="color:var(--text-muted)">未检测到章节。请使用下方"按进度进入"或"粘贴文字定位"。</p>';
    return;
  }

  NovelRoleplay.chapters.forEach(function(chapter, idx) {
    var node = document.createElement('div');
    node.className = 'nr-chapter-node';
    node.onclick = function(e) { selectChapter(idx, e); };

    // 章节长度格式化
    var lengthKb = Math.round((chapter.length || 0) / 1000);
    var lengthStr = lengthKb > 0 ? lengthKb + 'k' : (chapter.length || 0);

    // 预估处理时间（每10万字约1分钟）
    var cumChars = chapter.char_end || 0;
    var estMin = Math.max(1, Math.ceil(cumChars / 100000));
    var estStr = '约 ' + estMin + ' 分钟';

    // 截取首段摘要
    var preview = (chapter.first_segment || '').substring(0, 80) + '...';

    // [v12+] 自动分章标记
    var autoBadge = chapter.is_auto_split
      ? '<span style="color:var(--dim);font-size:.7em;margin-left:6px;padding:1px 6px;border:1px solid var(--border);border-radius:3px">自动分章</span>'
      : '';

    node.innerHTML =
      '<div class="nr-chap-idx">第' + (idx + 1) + '章</div>' +
      '<div class="nr-chap-content">' +
        '<div class="nr-chap-title">' + escapeHtml(chapter.title || '未命名') + autoBadge + '</div>' +
        '<div class="nr-chap-preview">' + escapeHtml(preview) + '</div>' +
        '<div class="nr-chap-stats">' +
          '<span>' + lengthStr + ' 字</span>' +
          '<span>累计 ' + (cumChars / 10000).toFixed(1) + ' 万字</span>' +
          '<span>处理 ' + estStr + '</span>' +
        '</div>' +
      '</div>';

    container.appendChild(node);
  });

  // 显示已选角色
  var charDisplay = document.getElementById('nrSelectedCharDisplay');
  if (charDisplay) {
    charDisplay.textContent = '扮演角色: ' + NovelRoleplay.selectedCharacter;
  }
}

function selectChapter(idx, domEvent) {
  var chapter = NovelRoleplay.chapters[idx];
  if (!chapter) return;

  // 取消之前的选中
  document.querySelectorAll('.nr-chapter-node').forEach(function(n) {
    n.classList.remove('selected');
  });

  var node = domEvent ? domEvent.currentTarget :
              document.querySelectorAll('.nr-chapter-node')[idx];
  if (node) node.classList.add('selected');

  NovelRoleplay.selectedChapter = chapter.index;

  // 启用"开始深度处理"按钮
  var btn = document.getElementById('nrStartDeepBtn');
  if (btn) {
    btn.disabled = false;
    btn.style.opacity = '1';
  }
}

// [v12+] 从章节页面返回到角色选择页面
function backToCharacters() {
  NovelRoleplay.state = 'characters';
  NovelRoleplay.selectedChapter = null;
  NovelRoleplay._locatedCharPosition = null;  // 清理粘贴定位状态
  var btn = document.getElementById('nrStartDeepBtn');
  if (btn) {
    btn.disabled = true;
    btn.style.opacity = '.5';
  }
  updateNovelRoleplayUI();
}

// [v12+] 备选入口：展开/收起"按进度 / 粘贴文字"区域
function toggleNrAltEntry() {
  var body = document.getElementById('nrAltEntryBody');
  var btn = document.getElementById('nrAltToggleBtn');
  var section = document.getElementById('nrAltEntrySection');
  if (!body || !btn) return;
  if (body.style.display === 'none') {
    body.style.display = 'block';
    btn.textContent = '收起 ▲';
    setTimeout(function() {
      if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 100);
  } else {
    body.style.display = 'none';
    btn.textContent = '展开 ▼';
  }
}

// [v12+] 方案B：按进度（百分比/字数）启动深度处理
async function startDeepProcessByProgress() {
  var inputEl = document.getElementById('nrProgressInput');
  var unitEl = document.getElementById('nrProgressUnit');
  if (!inputEl || !unitEl) return;

  var val = parseFloat(inputEl.value);
  if (!val || val <= 0) {
    alert('请输入有效的进度数值');
    return;
  }

  var unit = unitEl.value;
  var charPosition;
  var totalChars = (window._novelText || '').length || 0;

  if (unit === 'percent') {
    if (val > 100) {
      alert('百分比不能超过100');
      return;
    }
    charPosition = Math.floor(totalChars * val / 100);
  } else {
    // 字数
    if (val > totalChars) {
      alert('字数不能超过全书总字数 ' + totalChars.toLocaleString());
      return;
    }
    charPosition = Math.floor(val);
  }

  var rangeDesc = unit === 'percent' ? (val + '%') : (charPosition.toLocaleString() + ' 字');
  if (!confirm('将深度处理到全书 ' + rangeDesc + ' 的位置（约 ' + charPosition.toLocaleString() + ' 字），预计1-3分钟。是否继续？')) {
    return;
  }

  await _startDeepProcessCommon({ char_position: charPosition });
}

// [v12+] 方案C-1：粘贴文字 → 查找位置并预览
async function locateTextAndPreview() {
  var inputEl = document.getElementById('nrSnippetInput');
  var resultEl = document.getElementById('nrLocateResult');
  var startBtn = document.getElementById('nrStartBySnippetBtn');
  if (!inputEl || !resultEl) return;

  var snippet = inputEl.value.trim();
  if (snippet.length < 20) {
    resultEl.textContent = '请至少粘贴20字';
    resultEl.style.color = 'var(--accent-red)';
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.style.display = 'none';
    }
    return;
  }

  resultEl.textContent = '查找中...';
  resultEl.style.color = 'var(--dim)';
  if (startBtn) startBtn.style.display = 'none';

  try {
    var headers = { 'Content-Type': 'application/json' };
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;

    var resp = await fetch('/api/novel-roleplay/locate-text', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ snippet: snippet })
    });
    var data = await resp.json();

    if (!resp.ok) {
      throw new Error(data.detail || '查找失败');
    }

    if (data.found) {
      var percent = data.progress_percent || 0;
      var matched = data.matched_text || '';
      resultEl.innerHTML =
        '✅ 找到！位置: ' + (data.char_position || 0).toLocaleString() +
        ' / ' + (data.total_chars || 0).toLocaleString() +
        ' (进度 ' + percent + '%)<br>' +
        '<span style="color:var(--dim)">匹配: "' + escapeHtml(matched.substring(0, 60)) + '..."</span>';
      resultEl.style.color = 'var(--accent-green)';

      // 启用"确认并深度处理"按钮
      if (startBtn) {
        startBtn.disabled = false;
        startBtn.style.display = 'block';
        NovelRoleplay._locatedCharPosition = data.char_position;
      }
    } else {
      resultEl.textContent = '❌ ' + (data.error || '未找到这段文字');
      resultEl.style.color = 'var(--accent-red)';
      if (startBtn) {
        startBtn.disabled = true;
        startBtn.style.display = 'none';
      }
    }
  } catch (e) {
    resultEl.textContent = '❌ ' + e.message;
    resultEl.style.color = 'var(--accent-red)';
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.style.display = 'none';
    }
  }
}

// [v12+] 方案C-2：定位成功后，启动深度处理
async function startDeepProcessBySnippet() {
  var pos = NovelRoleplay._locatedCharPosition;
  if (pos === undefined || pos === null || pos < 0) {
    alert('请先点击"查找位置"定位文字');
    return;
  }
  await _startDeepProcessCommon({ char_position: pos });
}

// [v12+] 深度处理公共逻辑（章节/进度/文字定位共用）
async function _startDeepProcessCommon(payload) {
  try {
    var headers = { 'Content-Type': 'application/json' };
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;

    // 默认补 character_name
    if (!payload.character_name) {
      payload.character_name = NovelRoleplay.selectedCharacter || '';
    }
    // 默认 chapter_index = -1（用 char_position）
    if (payload.char_position !== undefined && payload.chapter_index === undefined) {
      payload.chapter_index = -1;
    }

    var resp = await fetch('/api/novel-roleplay/deep-process', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });
    var data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || '启动深度处理失败');
    }

    // 切换到处理中状态
    NovelRoleplay.state = 'deep_processing';
    NovelRoleplay.deepProcessPhase = 'deep';
    NovelRoleplay.progress = 0;
    NovelRoleplay.statusMsg = '深度处理中...';
    updateNovelRoleplayUI();
    document.getElementById('nrProcessingSection').style.display = 'block';

    // 复用轮询逻辑
    startPollingStatus();
  } catch (e) {
    alert('启动深度处理失败: ' + e.message);
  }
}

// ── 深度处理（选定章节后） ──────────────────────────────────

async function startDeepProcess() {
  if (NovelRoleplay.selectedChapter === null) {
    alert('请先选择一个章节');
    return;
  }

  if (!confirm('将深度处理该章节前的内容（预计1-3分钟），处理完成后自动进入游戏。是否继续？')) {
    return;
  }

  await _startDeepProcessCommon({ chapter_index: NovelRoleplay.selectedChapter });
}

// [v12+] 深度处理完成后，调用 enter API 进入游戏
async function enterNovelRoleplayAfterDeepProcess() {
  // 获取API配置
  var config = window._gameConfig || {};
  var apiKey = config.api_key || '';
  var baseUrl = config.base_url || '';
  var modelName = config.model_name || '';

  if (!apiKey) {
    var wdInput = document.getElementById('nrApiKey');
    if (wdInput) apiKey = wdInput.value;
  }

  try {
    var headers = { 'Content-Type': 'application/json' };
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;

    // 深度处理完成后，自动选择最后一个关键事件作为 timeline_id
    // （因为深度处理是基于章节构建的时间轴）
    var resp = await fetch('/api/novel-roleplay/timeline', { headers: headers });
    var tlData = await resp.json();
    var keyEvents = tlData.key_events || [];

    var timelineId = '';
    if (keyEvents.length > 0) {
      // 选最后一个事件（即选定章节附近的时间点）
      timelineId = keyEvents[keyEvents.length - 1].time_id;
    } else {
      // 如果没有关键事件，使用选定章节的 index 作为 timeline_id
      timelineId = 'chapter_' + NovelRoleplay.selectedChapter;
    }

    var enterResp = await fetch('/api/novel-roleplay/enter', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({
        character_name: NovelRoleplay.selectedCharacter,
        timeline_id: timelineId,
        api_key: apiKey,
        base_url: baseUrl,
        model_name: modelName,
      })
    });

    var data = await enterResp.json();
    if (!enterResp.ok) {
      throw new Error(data.detail || '进入游戏失败');
    }

    // [Bug] 先设置 GS 全局状态，再切换界面（与 create/load 流程一致）
    if (data.game_state) {
      GS = data.game_state;
    }

    // [Bug] 标记小说角色扮演模式，showGame 中据此禁用背景轮换
    window._isNovelRoleplay = true;

    // 切换到游戏界面
    document.getElementById('novelRoleplayPage').style.display = 'none';
    if (typeof showGame === 'function') {
      showGame();
    } else {
      document.getElementById('game').style.display = 'block';
    }

    // [NovelRoleplay] 显示剧情介绍（前情提要 + 当前处境）
    if (data.intro && typeof addNarrative === 'function') {
      addNarrative('【前情提要 · 进入小说世界】', true);
      addNarrative(data.intro, false, false);
    }

    // 更新状态栏显示
    if (typeof updateStatus === 'function') {
      updateStatus();
    }

    if (typeof toast === 'function') {
      toast('已进入小说世界！扮演角色: ' + NovelRoleplay.selectedCharacter, 'success');
    }
  } catch (e) {
    alert('进入游戏失败: ' + e.message);
    NovelRoleplay.state = 'chapters';
    NovelRoleplay.deepProcessPhase = '';
    updateNovelRoleplayUI();
  }
}

async function goToTimeline() {
  if (!NovelRoleplay.selectedCharacter) {
    alert('请先选择一个角色');
    return;
  }

  try {
    var headers = {};
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;
    var resp = await fetch('/api/novel-roleplay/timeline', { headers: headers });
    var data = await resp.json();

    NovelRoleplay.state = 'timeline';
    updateNovelRoleplayUI();
    renderTimeline(data.key_events || [], data.full_timeline || []);
  } catch (e) {
    alert('加载时间轴失败: ' + e.message);
  }
}

// ── 时间轴选择（兼容保留） ────────────────────────────────────

function renderTimeline(keyEvents, fullTimeline) {
  // 兼容：未传参时从 NovelRoleplay.keyEvents 读取
  if (!keyEvents) keyEvents = NovelRoleplay.keyEvents || [];
  NovelRoleplay.keyEvents = keyEvents;

  var container = document.getElementById('nrTimelineList');
  if (!container) return;
  container.innerHTML = '';

  if (keyEvents.length === 0) {
    container.innerHTML = '<p style="color:var(--text-muted)">未检测到关键事件。</p>';
    return;
  }

  keyEvents.forEach(function(tlEvent, idx) {
    var node = document.createElement('div');
    node.className = 'nr-timeline-node';
    node.onclick = function(e) { selectTimeline(idx, e); };

    var importance = tlEvent.importance || 5;
    var dots = '';
    for (var i = 0; i < 10; i++) {
      dots += i < importance ? '●' : '○';
    }

    node.innerHTML =
      '<div class="nr-tl-marker">' + dots + '</div>' +
      '<div class="nr-tl-content">' +
        '<div class="nr-tl-chapter">' + escapeHtml(tlEvent.chapter || '') + '</div>' +
        '<div class="nr-tl-desc">' + escapeHtml(tlEvent.description || '') + '</div>' +
        '<div class="nr-tl-importance">重要性: ' + importance.toFixed(1) + '</div>' +
      '</div>';

    container.appendChild(node);
  });

  var charDisplay = document.getElementById('nrSelectedCharDisplay');
  if (charDisplay) {
    charDisplay.textContent = '扮演角色: ' + NovelRoleplay.selectedCharacter;
  }
}

function selectTimeline(idx, domEvent) {
  var tlEvent = NovelRoleplay.keyEvents[idx];
  if (!tlEvent) return;

  document.querySelectorAll('.nr-timeline-node').forEach(function(n) {
    n.classList.remove('selected');
  });

  var node = domEvent ? domEvent.currentTarget :
              document.querySelectorAll('.nr-timeline-node')[idx];
  if (node) node.classList.add('selected');

  NovelRoleplay.selectedTimeline = tlEvent.time_id;

  var btn = document.getElementById('nrEnterGameBtn');
  if (btn) btn.disabled = false;
}

// ── 进入游戏（直接从时间轴进入，兼容保留） ────────────────────

async function enterNovelRoleplay() {
  if (!NovelRoleplay.selectedCharacter || !NovelRoleplay.selectedTimeline) {
    alert('请选择角色和时间节点');
    return;
  }

  var config = window._gameConfig || {};
  var apiKey = config.api_key || '';
  var baseUrl = config.base_url || '';
  var modelName = config.model_name || '';

  if (!apiKey) {
    var wdInput = document.getElementById('nrApiKey');
    if (wdInput) apiKey = wdInput.value;
  }

  var btn = document.getElementById('nrEnterGameBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '正在进入...';
  }

  try {
    var headers = { 'Content-Type': 'application/json' };
    if (window.ACCESS_TOKEN) headers['Authorization'] = 'Bearer ' + window.ACCESS_TOKEN;

    var resp = await fetch('/api/novel-roleplay/enter', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({
        character_name: NovelRoleplay.selectedCharacter,
        timeline_id: NovelRoleplay.selectedTimeline,
        api_key: apiKey,
        base_url: baseUrl,
        model_name: modelName,
      })
    });

    var data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || '进入游戏失败');
    }

    // [Bug] 先设置 GS 全局状态，再切换界面（与 create/load 流程一致）
    if (data.game_state) {
      GS = data.game_state;
    }

    // [Bug] 标记小说角色扮演模式，showGame 中据此禁用背景轮换
    window._isNovelRoleplay = true;

    document.getElementById('novelRoleplayPage').style.display = 'none';
    if (typeof showGame === 'function') {
      showGame();
    } else {
      document.getElementById('game').style.display = 'block';
    }

    // [NovelRoleplay] 显示剧情介绍（前情提要 + 当前处境）
    if (data.intro && typeof addNarrative === 'function') {
      addNarrative('【前情提要 · 进入小说世界】', true);
      addNarrative(data.intro, false, false);
    }

    // 更新状态栏显示
    if (typeof updateStatus === 'function') {
      updateStatus();
    }

    if (typeof toast === 'function') {
      toast('已进入小说世界！扮演角色: ' + NovelRoleplay.selectedCharacter, 'success');
    }
  } catch (e) {
    alert('进入游戏失败: ' + e.message);
    if (btn) {
      btn.disabled = false;
      btn.textContent = '✨ 进入小说世界';
    }
  }
}

// ── 工具函数 ──────────────────────────────────────────────

function escapeHtml(text) {
  if (!text) return '';
  var div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
