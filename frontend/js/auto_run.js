// 太虚幻境 v1.2 — 自主运行功能
// 让世界自我演化 N 天，围绕主角汇总成小说章节

function openAutoRunPanel() {
  // 重置 UI 到初始表单状态
  $('autoRunForm').style.display = 'block';
  $('autoRunProgress').style.display = 'none';
  $('autoRunResult').style.display = 'none';
  $('autoRunChapterList').style.display = 'none';
  $('autoRunModal').classList.add('on');
}

function closeAutoRun() {
  $('autoRunModal').classList.remove('on');
}

async function startAutoRun() {
  var days = parseInt($('autoRunDays').value, 10);
  if (!days || days < 1) { toast('请输入有效的天数', 'error'); return; }
  if (days > 365) { toast('单次最多 365 天', 'error'); return; }

  // 根据天数估算等待时间
  var estMinutes;
  if (days <= 7) {
    estMinutes = '5-7 分钟';
  } else if (days <= 30) {
    estMinutes = '10-20 分钟';
  } else {
    estMinutes = Math.ceil(days / 2) + ' 分钟左右';
  }

  // 二次确认
  if (!confirm('将让世界自主运行 ' + days + ' 天。\n\n运行期间无法操作游戏，结束后会自动生成一章小说。\n运行前会自动存档（可回滚）。\n\n⏱ 系统情节推演大概需要 ' + estMinutes + '，请耐心等待。\n\n确认开始？')) return;

  // 切换到进度状态
  $('autoRunForm').style.display = 'none';
  $('autoRunResult').style.display = 'none';
  $('autoRunChapterList').style.display = 'none';
  $('autoRunProgress').style.display = 'block';
  $('autoRunProgressText').textContent = '正在自主运行 ' + days + ' 天...';
  $('autoRunProgressHint').textContent = '⏱ 预计需要 ' + estMinutes + '，期间世界持续推演，NPC 自主行动。请耐心等待，不要关闭页面。';

  // 用较长超时（LOD 优化后每天约 3-10 秒；保底 10 分钟，最多 365 天）
  // [Bug] 原 days*60000 在 7 天时为 7 分钟，但浏览器 fetch 实际 5 分钟就断
  // 改为 days*30000（30秒/天）+ 600000 保底（10分钟），避免超时
  var timeout = Math.max(600000, days * 30000);
  var d = await api('POST', '/api/auto-run/start', { days: days, options: {} }, timeout);

  $('autoRunProgress').style.display = 'none';

  if (!d || d.error) {
    toast(d && d.error ? d.error : '自主运行失败', 'error');
    // 失败时回到表单
    $('autoRunForm').style.display = 'block';
    return;
  }

  // 显示结果
  $('autoRunResult').style.display = 'block';
  var stats = $('autoRunStats');
  var aborted = d.aborted ? ' ⚠️ 中途异常' : '';
  var errInfo = d.error ? '（错误：' + d.error + '）' : '';
  stats.innerHTML = '📅 第 ' + d.from_day + ' 天 ~ 第 ' + d.to_day + ' 天（共 ' +
    d.days_advanced + ' 天） · 👥 ' + d.events_count + ' 个事件 · 💬 ' +
    d.interactions_count + ' 次代演对话' + aborted + errInfo;

  var chapterEl = $('autoRunChapter');
  var chapter = d.chapter || '（无章节内容）';
  // 转义 HTML，避免章节内容中的特殊字符破坏布局
  chapterEl.textContent = chapter;

  // [v1.2] 把生成的章节推送到游戏主页面叙事流，让玩家关闭弹窗后也能看到
  if (typeof addSystem === 'function' && typeof addNarrative === 'function') {
    addSystem('⏩ 世界已自主运行 ' + d.days_advanced + ' 天（第 ' + d.from_day + ' ~ ' + d.to_day + ' 天）');
    if (typeof addChapterDivider === 'function') addChapterDivider('📖');
    // 章节内容按段落渲染（addNarrative 会做 sanitizeHTML）
    var paragraphs = chapter.split(/\n+/).filter(function(p) { return p.trim(); });
    paragraphs.forEach(function(p) {
      if (typeof addNarrative === 'function') addNarrative(p);
    });
    if (typeof addChapterDivider === 'function') addChapterDivider('✦');
  }
  // 同步刷新主角状态条（自主运行推进了时间/年龄/状态等）
  try {
    var sd = await api('GET', '/api/state');
    if (sd && sd.state) {
      GS = sd.state;
      if (typeof updateStatus === 'function') updateStatus();
    }
  } catch(e) {}
  toast('自主运行完成，章节已生成', 'success');
}

async function viewAutoRunChapters() {
  var d = await api('GET', '/api/auto-run/chapters');
  if (!d || d.error) { toast(d && d.error ? d.error : '加载失败', 'error'); return; }
  var chapters = d.chapters || [];
  if (!chapters.length) {
    toast('暂无自主运行生成的章节', 'info');
    return;
  }

  // 切换到章节列表视图
  $('autoRunForm').style.display = 'none';
  $('autoRunProgress').style.display = 'none';
  $('autoRunResult').style.display = 'none';
  $('autoRunChapterList').style.display = 'block';

  var html = '<div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:center">' +
    '<h3 style="margin:0">历史章节（' + chapters.length + '）</h3>' +
    '<button onclick="openAutoRunPanel()" style="padding:6px 12px;background:transparent;color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer">← 返回</button>' +
    '</div>';
  html += chapters.map(function(c, i) {
    return '<div style="padding:10px 14px;margin:8px 0;border:1px solid var(--border);border-radius:8px;cursor:pointer;background:rgba(255,255,255,.02)" onclick="readAutoRunChapter(\'' + c.file + '\')">' +
      '<div style="display:flex;justify-content:space-between;align-items:center">' +
        '<b>第 ' + (chapters.length - i) + ' 章</b>' +
        '<span style="color:var(--dim);font-size:.82em">' + (c.created_at || '') + '</span>' +
      '</div>' +
      '<div style="color:var(--dim);font-size:.85em;margin-top:4px">📅 第 ' + c.from_day + ' ~ ' + c.to_day + ' 天（' + c.days + ' 天）</div>' +
      '<div style="color:var(--text);font-size:.88em;margin-top:6px;opacity:.85">' + escHtml(c.preview) + '...</div>' +
    '</div>';
  }).join('');
  $('autoRunChapterList').innerHTML = html;
}

async function readAutoRunChapter(file) {
  var d = await api('GET', '/api/auto-run/chapters/' + file);
  if (!d || d.error) { toast(d && d.error ? d.error : '读取失败', 'error'); return; }
  var data = d.chapter || {};
  // 切换到结果视图显示完整章节
  $('autoRunChapterList').style.display = 'none';
  $('autoRunResult').style.display = 'block';
  $('autoRunStats').innerHTML = '📅 第 ' + (data.from_day || 0) + ' 天 ~ 第 ' + (data.to_day || 0) +
    ' 天（共 ' + (data.days || 0) + ' 天） · 生成于 ' + (data.created_at || '');
  $('autoRunChapter').textContent = data.chapter || '（无内容）';
}
