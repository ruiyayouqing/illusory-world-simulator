/* [v12] NPC行动智能推演 前端逻辑 */

function openNpcPrediction() {
  var modal = document.getElementById('npcPredictionModal');
  if (modal) modal.style.display = 'flex';
}

function closeNpcPrediction() {
  var modal = document.getElementById('npcPredictionModal');
  if (modal) modal.style.display = 'none';
}

async function startNpcPrediction() {
  var sourceMode = document.getElementById('pred_source_mode').value;
  var maxNpcs = parseInt(document.getElementById('pred_max_npcs').value) || 50;
  var btn = document.getElementById('pred_start_btn');
  var progress = document.getElementById('pred_progress');
  var progressText = document.getElementById('pred_progress_text');
  var progressBar = document.getElementById('pred_progress_bar');

  btn.disabled = true;
  btn.textContent = '推演中...';
  progress.style.display = 'block';
  progressBar.style.width = '0%';
  progressText.textContent = '正在启动推演...';

  try {
    var r = await api('POST', '/api/npc-prediction/start', {
      source_mode: sourceMode,
      max_npcs: maxNpcs,
    });

    if (r.error) {
      progressText.textContent = '错误: ' + r.error;
      btn.disabled = false;
      btn.textContent = '开始推演';
      return;
    }

    progressBar.style.width = '100%';
    progressText.textContent = '推演完成!';
    renderPredictionReport(r.report);
  } catch (e) {
    progressText.textContent = '请求失败: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = '开始推演';
}

async function loadPredictionReport() {
  try {
    var r = await api('GET', '/api/npc-prediction/report');
    if (r.error) {
      document.getElementById('pred_report_content').innerHTML =
        '<div style="color:var(--dim);text-align:center;padding:20px">暂无推演报告，请先执行推演</div>';
      return;
    }
    renderPredictionReport(r.report);
  } catch (e) {
    document.getElementById('pred_report_content').innerHTML =
      '<div style="color:#ff4444;text-align:center;padding:20px">加载失败: ' + e.message + '</div>';
  }
}

function renderPredictionReport(report) {
  var container = document.getElementById('pred_report_content');
  if (!container || !report) return;

  var html = '';

  html += '<div style="margin-bottom:16px;padding:12px;background:rgba(201,169,110,.08);border:1px solid rgba(201,169,110,.2);border-radius:8px">';
  html += '<div style="font-weight:600;color:var(--gold);margin-bottom:8px">' + escHtml(report.title) + '</div>';
  html += '<div style="font-size:.85em;color:var(--dim);white-space:pre-line">' + escHtml(report.summary) + '</div>';
  html += '<div style="font-size:.8em;color:var(--dim);margin-top:8px">';
  html += 'NPC数量: <b>' + report.total_npcs + '</b> | ';
  html += '一致性: <b>' + Math.round(report.cross_validation_score * 100) + '%</b> | ';
  html += '生成时间: ' + escHtml(report.generated_at);
  html += '</div>';
  html += '</div>';

  if (report.cross_validation_notes) {
    html += '<div style="margin-bottom:16px;padding:10px;background:rgba(100,150,255,.06);border:1px solid rgba(100,150,255,.2);border-radius:6px;font-size:.85em">';
    html += '<div style="font-weight:600;margin-bottom:4px">一致性校验</div>';
    html += '<div style="white-space:pre-line;color:var(--text)">' + escHtml(report.cross_validation_notes) + '</div>';
    html += '</div>';
  }

  html += '<div id="pred_apply_section" style="margin-bottom:12px;text-align:right">';
  html += '<button class="btn" style="font-size:.85em;padding:6px 16px;width:auto" onclick="applyNpcPredictions()">确认应用到游戏</button>';
  html += '</div>';

  var predictions = report.predictions || [];
  if (predictions.length === 0) {
    html += '<div style="color:var(--dim);text-align:center;padding:20px">无推演结果</div>';
  } else {
    var EVENT_CN = {
      marriage: '结婚', first_child: '初为人父/母', child_birth: '孩子出生',
      career_advance: '升职', start_business: '创业', relocate: '搬家',
      retire: '退休', illness: '生病', accident: '意外',
      death_illness: '病逝', death_old_age: '寿终正寝',
      imprisonment: '入狱', wealth_change: '财富变化',
      leave_home: '离家', join_faction: '加入势力',
    };

    predictions.forEach(function(p) {
      var events = p.events || [];
      var eventsHtml = events.length > 0 ? events.map(function(e) {
        var cn = EVENT_CN[e.type] || e.type;
        return '<span style="display:inline-block;padding:2px 8px;margin:2px;background:rgba(201,169,110,.1);border:1px solid rgba(201,169,110,.2);border-radius:4px;font-size:.8em">' + escHtml(cn) + '(第' + e.day + '天)</span>';
      }).join('') : '<span style="color:var(--dim);font-size:.8em">无变化</span>';

      var confColor = p.confidence > 0.5 ? 'var(--gold)' : '#ff6666';
      html += '<div style="padding:10px;margin-bottom:8px;background:rgba(201,169,110,.04);border:1px solid rgba(201,169,110,.12);border-radius:6px">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">';
      html += '<span style="font-weight:600">' + escHtml(p.name) + '</span>';
      html += '<span style="font-size:.75em;color:' + confColor + '">置信度: ' + Math.round(p.confidence * 100) + '%</span>';
      html += '</div>';
      html += '<div style="margin-bottom:6px">' + eventsHtml + '</div>';
      if (p.narrative) {
        html += '<div style="font-size:.85em;color:var(--text);line-height:1.5">' + escHtml(p.narrative) + '</div>';
      }
      html += '</div>';
    });
  }

  container.innerHTML = html;
}

async function applyNpcPredictions() {
  var btn = document.querySelector('#pred_apply_section .btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '应用中...';
  }

  try {
    var r = await api('POST', '/api/npc-prediction/apply', { npc_ids: [] });
    if (r.error) {
      alert('应用失败: ' + r.error);
    } else {
      alert('成功应用 ' + r.applied + ' 个NPC的状态更新' + (r.skipped > 0 ? '，跳过 ' + r.skipped + ' 个' : ''));
    }
  } catch (e) {
    alert('请求失败: ' + e.message);
  }

  if (btn) {
    btn.disabled = false;
    btn.textContent = '确认应用到游戏';
  }
}
