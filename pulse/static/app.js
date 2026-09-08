'use strict';
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (n) => new Intl.NumberFormat('zh-CN').format(n || 0);
  const token = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : num(n);
  const time = (n) => n >= 3600 ? `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m` : n >= 60 ? `${Math.floor(n / 60)}m ${Math.floor(n % 60)}s` : `${Math.floor(n)}s`;
  const clockTime = (ts) => ts == null ? '未知' : new Date(ts*1000).toLocaleString('zh-CN',{timeZone:data.timezone,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  const timingNote = (item) => item.incompleteTurns ? `<p class="cp-muted cp-gap">${item.incompleteTurns} 个回合记录不完整，仅统计可确认的时间区间；缺少结束记录时计至最后执行活动。</p>` : '';
  const names = {running:'运行中 · 日志观测',waiting:'等待输入 · 日志观测',unknown:'状态未确认',ended:'回合已结束',interrupted:'已中断',error:'执行异常'};
  const titles = {overview:'总览',projects:'项目',usage:'用量',report:'日报',settings:'设置',project:'项目详情',task:'任务详情'};
  let data, busy = false, failed = false, search = '', filter = 'all', timer, reportBody = '', reportKey = '', quotaMessage = '';
  const route = () => {const [page, id] = location.hash.slice(1).split('/'); let decoded=''; try {decoded=decodeURIComponent(id || '');} catch {} return {page:titles[page] ? page : 'overview', id:decoded};};
  const link = (type, id, label, cls='cp-task-name') => `<a class="${cls}" href="#${type}/${encodeURIComponent(id)}">${esc(label)}</a>`;
  const empty = (label) => `<div class="cp-empty">${esc(label)}</div>`;
  const badge = (t) => `<span class="cp-state ${esc(t.status)}">${esc(names[t.status] || names.unknown)}</span>`;
  const progress = (p, name, idle=false) => `<div class="cp-project-progress ${p.known ? '' : 'is-unknown'}${failed ? ' is-stale' : ''}"><div class="cp-progress-caption"><span>${failed ? '上次计划进度' : '计划步骤'}</span><span class="cp-progress-value">${p.known ? p.percent + '%' : idle ? '当前无运行任务' : '进度未知'}</span></div><div class="cp-progress-track" role="progressbar" aria-label="${esc(name)}计划进度" aria-valuemin="0" aria-valuemax="100" ${p.known ? `aria-valuenow="${p.percent}"` : 'aria-valuetext="进度未知"'}><div class="cp-project-fill" style="width:${p.known ? p.percent : 0}%"></div></div><div class="cp-progress-note">${p.known ? `已完成 ${p.completed} / ${p.total} 步` : idle ? '回合结束与项目验收分别判断' : '未取得完整有效计划，不估算百分比'}</div></div>`;
  const taskRows = (tasks) => tasks.length ? `<div class="cp-table-head"><span>任务 / 项目</span><span>状态与计划</span><span>回合累计</span><span>Token</span></div>${tasks.map(t => `<div class="cp-task-row"><div>${link('task',t.id,t.title)}<div class="cp-task-project">${esc(t.projectName)}${t.child ? ' · 子代理' : ''}</div></div><div>${badge(t)}<div class="cp-step">${t.progress.known ? `${t.progress.completed} / ${t.progress.total} 步` : '进度未知'}</div></div><span class="cp-number">${time(t.duration)}${t.incompleteTurns ? '<small class="cp-timing-note">含不完整记录</small>' : ''}</span><span class="cp-number" title="${num(t.usage.total_tokens)}">${token(t.usage.total_tokens)}</span></div>`).join('')}` : empty('此范围内没有任务记录');
  function metrics() {
    const m = data.metrics;
    return `<div class="cp-metrics"><div class="cp-metric"><div class="cp-metric-label">${failed ? '上次运行项目' : '当前运行项目'}</div><div class="cp-value cp-green">${m.runningProjects}<small>个</small></div><div class="cp-metric-foot">${failed ? '连接断开 · 上次观测' : '按最近日志观测'}</div></div><div class="cp-metric"><div class="cp-metric-label">当日运行过的项目</div><div class="cp-value">${m.projects}<small>个</small></div><div class="cp-metric-foot">${m.mainTasks} 个主任务 · ${m.childTasks} 个子代理</div></div><div class="cp-metric"><div class="cp-metric-label">当日运行时长</div><div class="cp-value cp-duration">${time(m.wallTime)}</div><div class="cp-metric-foot">并行累计 ${time(m.duration)}${m.incompleteTurns ? ` · ${m.incompleteTurns} 回合记录不完整` : ''}</div></div><div class="cp-metric"><div class="cp-metric-label">当日 Token</div><div class="cp-value" title="${num(m.usage.total_tokens)}">${token(m.usage.total_tokens)}</div><div class="cp-metric-foot">输入 ${token(m.usage.input_tokens)} · 输出 ${token(m.usage.output_tokens)}</div></div></div>`;
  }
  function windowLabel(minutes) {
    if (minutes == null) return '周期未提供';
    return minutes % 1440 === 0 ? `${num(minutes / 1440)} 天窗口` : minutes % 60 === 0 ? `${num(minutes / 60)} 小时窗口` : `${num(minutes)} 分钟窗口`;
  }
  function resetLabel(window) {
    if (window.resetsAt == null) return '重置时间未提供';
    const seconds = window.resetsAt - Date.now() / 1000;
    if (seconds <= 0) return `${clockTime(window.resetsAt)} · 已到重置时间，等待更新`;
    const minutes = Math.ceil(seconds / 60);
    const remaining = minutes >= 1440 ? `${Math.floor(minutes / 1440)} 天 ${Math.floor(minutes % 1440 / 60)} 小时` : minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`;
    return `${clockTime(window.resetsAt)} 重置 · 约 ${remaining}后`;
  }
  function quotaPanel(compact = false) {
    const q = data.quota;
    const stale = failed || q.status === 'stale';
    const status = failed ? '连接中断' : q.status === 'ready' ? '官方已连接' : q.status === 'stale' ? '等待更新' : q.status === 'loading' ? '连接中' : '暂不可用';
    const windows = q.buckets.flatMap(bucket => (bucket.windows.length ? bucket.windows : [{remainingPercent:null,usedPercent:null,windowDurationMins:null,resetsAt:null}]).map(w => ({bucket, window:w})));
    const cards = windows.map(({bucket, window:w}) => `<article class="cp-quota-window ${stale ? 'is-stale' : ''}"><div class="cp-quota-label"><strong>${esc(bucket.name)}</strong><span>${windowLabel(w.windowDurationMins)}</span></div><div class="cp-quota-value">${w.remainingPercent == null ? '未提供' : `${num(w.remainingPercent)}<small>%</small>`}<span>${stale ? '上次剩余' : '剩余'}</span></div><div class="cp-progress-track cp-quota-track ${w.remainingPercent != null && w.remainingPercent <= 20 ? 'is-low' : ''}" role="progressbar" aria-label="${esc(bucket.name)} ${windowLabel(w.windowDurationMins)}剩余额度" aria-valuemin="0" aria-valuemax="100" ${w.remainingPercent == null ? 'aria-valuetext="额度未提供"' : `aria-valuenow="${w.remainingPercent}"`}><div class="cp-project-fill" style="width:${w.remainingPercent ?? 0}%"></div></div><p class="cp-muted cp-quota-reset">${resetLabel(w)}</p>${bucket.spendControlReached || bucket.limitReached ? '<p class="cp-quota-warning">官方提示已触及使用限制</p>' : ''}</article>`).join('');
    const credits = compact ? '' : q.buckets.filter(b=>b.credits && (b.credits.balance != null || b.credits.unlimited === true)).map(b=>`<span>${esc(b.name)} 额外点数：${b.credits.unlimited === true ? '不限量' : esc(b.credits.balance)}</span>`).join('');
    return `<section class="cp-panel cp-quota" aria-label="账号剩余额度"><div class="cp-panel-top"><div><h2>账号剩余额度 <span class="cp-quota-status ${q.status === 'ready' && !failed ? 'cp-green' : ''}">${status}</span></h2><p class="cp-muted cp-quota-scope">当前 CLI 登录账号 · 所有设备共享 · 不随统计日期切换</p></div><div class="cp-actions">${compact ? '<a href="#usage" class="cp-text-link">额度详情 →</a>' : ''}${q.enabled ? `<button class="cp-button" id="refresh-quota" ${q.refreshing || q.retryAfterSeconds > 0 ? 'disabled' : ''}>${q.refreshing ? '正在查询…' : q.retryAfterSeconds > 0 ? `${q.retryAfterSeconds} 秒后可刷新` : '刷新额度'}</button>` : ''}</div></div>${q.reason ? `<p class="cp-quota-notice" role="status">${esc(q.reason)}</p>` : ''}${q.available ? `<div class="cp-quota-grid">${cards}</div>` : '<div class="cp-empty">剩余额度未知</div>'}<div class="cp-quota-foot"><span>${q.available && q.updatedAt != null ? `更新于 ${clockTime(q.updatedAt)}` : q.lastAttemptAt != null ? `尝试于 ${clockTime(q.lastAttemptAt)}` : '尚未取得额度数据'}${q.enabled ? ` · 每 ${q.refreshSeconds} 秒自动查询` : ''}</span>${q.resetCreditsAvailable != null ? `<span>可用额度重置次数：${num(q.resetCreditsAvailable)}</span>` : ''}${credits}</div>${compact ? '' : '<p class="cp-muted cp-quota-explain">剩余比例来自官方账号额度。未提供的窗口不补算，额外点数与订阅额度分开显示；本机 Token 不能换算订阅剩余额度。</p>'}<p class="cp-muted cp-quota-message" id="quota-message" role="status">${esc(quotaMessage)}</p></section>`;
  }
  function overview() {
    const running = data.projects.filter(p=>p.running || p.waiting);
    const attention = data.tasks.filter(t=>['waiting','error','unknown'].includes(t.status));
    return `${metrics()}${quotaPanel(true)}${attention.length ? `<div class="cp-attention">${attention.length} 个任务需要关注或确认状态 <button id="show-attention">查看任务</button></div>` : ''}<section class="cp-panel"><div class="cp-panel-top"><h2>${failed ? '上次运行项目' : '当前运行项目'}</h2><a href="#projects" class="cp-text-link">全部项目 →</a></div>${running.length ? `<div class="cp-running-grid">${running.map(p=>`<article class="cp-running-item"><div>${link('project',p.id,p.name)}<div class="cp-running-stage">${p.running} 个运行任务${p.waiting ? ` · ${p.waiting} 个等待` : ''}</div></div>${progress(p.progress,p.name)}</article>`).join('')}</div>` : empty('当前没有最近日志支持的运行任务')}</section><section class="cp-panel"><div class="cp-panel-top"><h2>任务活动 <span class="cp-muted">${data.tasks.length}</span></h2><div class="cp-tabs">${[['all','全部'],['running','运行中'],['attention','待关注']].map(([id,label])=>`<button data-filter="${id}" aria-pressed="${filter===id}">${label}</button>`).join('')}</div></div><div id="task-rows">${taskRows(filteredTasks())}</div></section>${timeline()}`;
  }
  function filteredTasks() {
    return data.tasks.filter(t=>filter==='all' || (filter==='running' ? t.status==='running' : ['waiting','error','unknown'].includes(t.status)));
  }
  function timeline() {
    const active = data.projects.filter(p=>p.activeToday).slice(0,8);
    if (!active.length) return '';
    const totals = Math.max(...active.map(p=>p.wallTime),1);
    return `<section class="cp-panel cp-pad"><h2>当日项目运行时长</h2>${active.map(p=>`<div class="cp-stat-line">${link('project',p.id,p.name)}<span>${time(p.wallTime)}</span></div><div class="cp-bar"><span style="width:${p.wallTime/totals*100}%"></span></div>`).join('')}<p class="cp-muted cp-gap">运行时长按日志区间计算，同一时间只计一次，含回合内的模型、工具与等待时间。不同项目可能并行，项目时长不可直接相加。${data.metrics.incompleteTurns ? ` ${data.metrics.incompleteTurns} 个回合记录不完整，已排除无记录的尾部空档。` : ''}</p></section>`;
  }
  function projectRows() {
    const projects = data.projects.filter(p=>p.name.toLowerCase().includes(search.toLowerCase()));
    return projects.length ? projects.map(p=>`<div class="cp-project-row"><div>${link('project',p.id,p.name)}<div class="cp-task-project">${p.tasks.length} 个任务 · ${p.running} 运行 · ${p.waiting} 等待</div></div>${progress(p.progress,p.name,!p.running&&!p.waiting&&!p.unknown)}<span class="cp-number" title="并行累计 ${time(p.duration)}">${time(p.wallTime)}<small class="cp-timing-note">运行时长${p.incompleteTurns ? ' · 记录不完整' : ''}</small></span><span class="cp-number">${token(p.usage.total_tokens)}</span></div>`).join('') : empty('没有匹配的项目');
  }
  function projects() {
    return `<section class="cp-panel"><div class="cp-panel-top"><h2>项目列表</h2><input class="cp-input cp-search" id="search" aria-label="搜索项目" placeholder="搜索项目" value="${esc(search)}"></div><div id="project-rows">${projectRows()}</div></section>`;
  }
  function project(id) {
    const p=data.projects.find(x=>x.id===id);
    if (!p) return `<a href="#projects" class="cp-text-link">← 返回项目</a>${empty('此项目在当前日期没有记录，请切换日期。')}`;
    return `<a href="#projects" class="cp-text-link">← 返回项目</a><div class="cp-detail-title"><h2>${esc(p.name)}</h2><p class="cp-muted">${p.tasks.length} 个任务 · 运行 ${time(p.wallTime)} · 并行累计 ${time(p.duration)} · ${num(p.usage.total_tokens)} Token</p></div><section class="cp-panel cp-pad cp-project-summary">${progress(p.progress,p.name,!p.running&&!p.waiting&&!p.unknown)}<p class="cp-muted cp-gap">项目进度汇总当前主任务的计划步骤；任一任务计划缺失时显示未知。</p>${timingNote(p)}</section><section class="cp-panel"><div class="cp-panel-top"><h2>项目任务</h2></div>${taskRows(data.tasks.filter(t=>p.tasks.includes(t.id)))}</section>`;
  }
  function task(id) {
    const t=data.tasks.find(x=>x.id===id);
    if (!t) return `<a href="#overview" class="cp-text-link">← 返回总览</a>${empty('此任务在当前日期没有记录。')}`;
    const plan=t.plan;
    return `${link('project',t.projectId,'← 返回项目','cp-text-link')}<div class="cp-detail-title"><h2>${esc(t.title)}</h2>${badge(t)}<p class="cp-muted cp-gap">${esc(t.model)} · ${t.child?'子代理':'主任务'} · 最后活动 ${new Date(t.last*1000).toLocaleString('zh-CN',{timeZone:data.timezone,hour12:false})}</p></div><div class="cp-details-grid"><div class="cp-detail-cell">当日回合累计<strong>${time(t.duration)}</strong></div><div class="cp-detail-cell">当日 Token<strong>${num(t.usage.total_tokens)}</strong></div></div>${timingNote(t)}<section class="cp-panel cp-pad cp-gap"><h2>最近计划</h2>${progress(t.progress,t.title)}${plan ? plan.map((s,i)=>`<div class="cp-plan-row"><span class="cp-plan-mark ${s.status==='completed'?'done':s.status==='pending'?'':'current'}">${s.status==='completed'?'✓':i+1}</span><span>${esc(s.step)}</span><span class="cp-muted">${s.status==='completed'?'已完成':s.status==='pending'?'待开始':'进行中'}</span></div>`).join('') : '<p class="cp-muted cp-gap">该任务尚未记录结构化计划。</p>'}</section><section class="cp-panel cp-pad"><h2>当日回合</h2>${t.turns.map(r=>`<div class="cp-turn-row"><span>${clockTime(r.start)}<small class="cp-timing-note">${r.timing==='incomplete' ? '记录不完整 · 最后活动' : r.timing==='live' ? '暂计至' : '结束于'} ${clockTime(r.observedEnd)}</small></span><span>${esc(names[r.status] || names.unknown)}</span><span>${time(r.seconds)}</span></div>`).join('') || empty('只有用量记录，缺少回合时间')}</section><details class="cp-panel cp-pad"><summary>数据归属</summary><p class="cp-muted cp-gap cp-wrap">工作目录：${esc(t.cwd)}<br>任务 ID：${esc(t.id)}</p></details>`;
  }
  function usage() {
    const u=data.metrics.usage;
    return `${quotaPanel()}<div class="cp-two"><section class="cp-panel cp-pad"><h2>Token 构成</h2><div class="cp-value">${num(u.total_tokens)}</div><div class="cp-stack"><span style="width:${u.total_tokens?u.input_tokens/u.total_tokens*100:0}%;background:var(--cp-green)"></span><span style="flex:1;background:var(--cp-blue)"></span></div>${[['输入','input_tokens'],['其中缓存输入','cached_input_tokens'],['输出','output_tokens'],['其中推理输出','reasoning_output_tokens'],['缓存写入（来源字段）','cache_write_input_tokens']].map(([label,key])=>`<div class="cp-stat-line"><span>${label}</span><span>${num(u[key])}</span></div>`).join('')}<p class="cp-muted cp-gap">缓存输入已包含在输入中，推理输出已包含在输出中。</p></section><section class="cp-panel cp-pad"><h2>当日用量范围</h2><p class="cp-gap">${esc(data.date)} · ${esc(data.timezone)}</p><p class="cp-muted cp-gap">以下 Token 来自此设备的任务记录，按响应去重；不包含其他设备或仅在云端运行的任务。</p><p class="cp-muted cp-gap">金额估算未启用。上方账号额度是查询时的当前值，与所选日期的本机 Token 分开统计。</p></section></div><div class="cp-two"><section class="cp-panel cp-pad"><h2>按项目</h2>${data.projects.filter(p=>p.usage.total_tokens).map(p=>`<div class="cp-stat-line">${link('project',p.id,p.name)}<span>${token(p.usage.total_tokens)}</span></div><div class="cp-bar"><span style="width:${p.usage.total_tokens/Math.max(u.total_tokens,1)*100}%"></span></div>`).join('') || empty('当日暂无 token 记录')}</section><section class="cp-panel cp-pad"><h2>按模型</h2>${data.models.map(m=>`<div class="cp-stat-line"><span>${esc(m.model)}</span><span>${token(m.usage.total_tokens)}</span></div><div class="cp-bar"><span style="width:${m.usage.total_tokens/Math.max(u.total_tokens,1)*100}%"></span></div>`).join('') || empty('当日暂无模型记录')}</section></div><p class="cp-muted">旧格式响应 ${data.source.legacyResponses} 条；内部审批与记忆任务不计入项目用量。此统计不覆盖其他设备。</p>`;
  }
  function reportPage() {
    return `<section class="cp-panel cp-pad"><div class="cp-spaced"><h2>当日工作记录</h2><div class="cp-actions"><button class="cp-button" id="copy-report">复制日报</button><a class="cp-button cp-primary" href="/api/report?date=${encodeURIComponent(data.date)}" download>下载 Markdown</a></div></div><textarea id="report-text" class="cp-input cp-report" aria-label="日报内容" readonly>${esc(reportBody)}</textarea><span id="report-message" role="status" class="cp-muted"></span></section>`;
  }
  function settings() {
    const s=data.settings;
    return `<div class="cp-two"><section class="cp-panel cp-pad"><h2>显示偏好</h2><form id="settings-form" class="cp-form cp-gap"><label>刷新间隔（秒）<input class="cp-input" name="refreshSeconds" type="number" min="2" max="60" required value="${s.refreshSeconds}"></label><label>状态确认窗口（秒）<input class="cp-input" name="staleSeconds" type="number" min="30" max="1800" required value="${s.staleSeconds}"><span class="cp-muted">超过此时间没有日志，运行状态转为未确认。静默不代表卡住。</span></label><label>外观<select class="cp-input" name="theme">${[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([v,label])=>`<option value="${v}" ${s.theme===v?'selected':''}>${label}</option>`).join('')}</select></label><button type="submit" class="cp-button cp-primary">保存偏好</button><p id="save-message" role="status" class="cp-muted"></p></form></section><section class="cp-panel cp-pad"><h2>数据连接</h2><dl class="cp-diagnostics"><dt>采集方式</dt><dd>本机日志增量读取</dd><dt>账号额度</dt><dd>官方接口 · ${data.quota.enabled ? "每 60 秒查询" : "已关闭"}</dd><dt>历史范围</dt><dd>最近 ${data.source.days} 天 · ${esc(data.timezone)}</dd><dt>已索引</dt><dd>${data.source.indexed} / ${data.source.total} 个日志</dd><dt>排除内部任务</dt><dd>${data.source.excluded} 个</dd><dt>解析或重置提示</dt><dd>${data.source.warnings} 条</dd><dt>Codex 数据目录</dt><dd>${esc(data.source.codexHome)}</dd><dt>看板数据目录</dt><dd>${esc(data.source.dataDir)}</dd></dl><p class="cp-muted cp-gap">仅监听 127.0.0.1。任务日志只读，偏好和统计缓存保存在看板自己的目录。额度通过本机 Codex CLI 查询官方服务，凭据由 Codex 管理，额度数据仅驻留内存。</p></section></div>`;
  }
  function render() {
    if (!data) return;
    const r=route();
    $('#page-title').textContent=titles[r.page];
    $('#page-subtitle').textContent=r.page==='overview' ? `${data.date} · ${data.timezone}` : r.page==='settings' ? '本机连接与显示偏好' : r.page==='usage' ? `当前账号额度 · ${data.date} 的本机用量` : `${data.date} 的任务记录`;
    document.querySelectorAll('[data-nav]').forEach(a=>{const selected=a.dataset.nav===(['task','project'].includes(r.page)?'projects':r.page); if(selected)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    $('#content').innerHTML=({overview,projects,usage,report:reportPage,settings,project:()=>project(r.id),task:()=>task(r.id)})[r.page]();
    $('#content').setAttribute('aria-busy','false');
    bind();
    if(r.page==='report') loadReport();
  }
  async function loadReport(force=false) {
    const key=data.date;
    if(reportKey===key && !force) return;
    try {
      const response=await fetch(`/api/report?date=${encodeURIComponent(key)}`);
      if(!response.ok) throw Error('日报读取失败');
      reportBody=await response.text();reportKey=key;
      if($('#report-text')) $('#report-text').value=reportBody;
    } catch(e) {if($('#report-message')) $('#report-message').textContent=e.message;}
  }
  function bind() {
    $('#refresh-quota')?.addEventListener('click',async e=>{
      e.currentTarget.disabled=true;
      quotaMessage='';
      try {
        const response=await fetch('/api/quota/refresh',{method:'POST',headers:{'X-Pulse-Request':'1'}});
        const result=await response.json();
        if(!response.ok) throw Error(result.error || '额度刷新失败');
        data.quota=result.quota;
      } catch {quotaMessage='额度刷新失败，请检查本机服务连接后重试。';}
      render();
    });
    document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;render();}));
    $('#show-attention')?.addEventListener('click',()=>{filter='attention';render();$('#task-rows').scrollIntoView({block:'nearest'});});
    $('#search')?.addEventListener('input',e=>{search=e.target.value;$('#project-rows').innerHTML=projectRows();});
    $('#settings-form')?.addEventListener('input',()=>{$('#save-message').textContent='';});
    $('#settings-form')?.addEventListener('submit',async e=>{
      e.preventDefault();
      if(!e.target.reportValidity()) return;
      const form=new FormData(e.target);
      const value={refreshSeconds:Number(form.get('refreshSeconds')),staleSeconds:Number(form.get('staleSeconds')),theme:form.get('theme')};
      try {
        const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Pulse-Request':'1'},body:JSON.stringify(value)});
        const body=await response.json(); if(!response.ok)throw Error(body.error);
        data.settings=body;applyTheme(body.theme);schedule();
        $('#save-message').textContent='已保存，重启后仍然有效。';
      } catch(error) {$('#save-message').textContent=error.message;}
    });
    $('#copy-report')?.addEventListener('click',async()=>{
      try {await navigator.clipboard.writeText($('#report-text').value);$('#report-message').textContent='已复制日报';}
      catch {$('#report-text').focus();$('#report-text').select();$('#report-message').textContent='已选中日报，可按 ⌘C / Ctrl+C 复制';}
    });
  }
  function applyTheme(theme) {document.documentElement.style.colorScheme=theme==='system'?'light dark':theme;}
  function schedule() {clearTimeout(timer);timer=setTimeout(()=>load(),(data?.settings.refreshSeconds || 5)*1000);}
  async function load(manual=false) {
    if(busy) return;
    busy=true;
    $('#refresh').disabled=true;
    try {
      const selected=$('#date').value;
      const response=await fetch('/api/snapshot'+(selected?`?date=${encodeURIComponent(selected)}`:''));
      const result=await response.json();
      if(!response.ok) throw Error(result.error || '采集器暂时不可用');
      data=result;failed=false;
      $('#version').textContent=data.version;
      $('#date').value=data.date;$('#date').min=data.minDate;$('#date').max=data.today;
      $('#error').hidden=data.source.phase==='ready' && !data.source.errors.length;
      $('#error').textContent=data.source.errors.join('；') || (data.source.phase==='loading'?'正在建立索引…':'');
      $('#connection').textContent=data.source.phase==='ready'?'● 本机已连接 · 日志观测':'● '+(data.source.phase==='loading'?'正在索引':'连接待恢复');
      $('#connection').className=data.source.phase==='ready'?'cp-green':'cp-amber';
      $('#scope').textContent=`任务统计：此设备 · 最近 ${data.source.days} 天可查 · 账号额度：${data.quota.status==='ready'?'已连接':data.quota.status==='stale'?'待更新':'暂不可用'}`;
      $('#updated').textContent='刷新于 '+new Date(data.generatedAt*1000).toLocaleTimeString('zh-CN',{hour12:false});
      applyTheme(data.settings.theme);
      // Preserve the user's draft, search cursor and report selection during polling.
      if(manual || !['settings','report'].includes(route().page) && document.activeElement?.id!=='search') render();
      if(!$('#content').children.length) render();
      if(manual && route().page==='report') await loadReport(true);
    } catch(e) {
      failed=true;$('#connection').textContent='● 采集器已断开';$('#connection').className='cp-amber';
      $('#error').hidden=false;$('#error').textContent=(e.message==='Failed to fetch'?'无法连接本机采集器':e.message)+'。保留上次观测数据；连接恢复后自动刷新。';
      if(data && !['settings','report'].includes(route().page))render();
      else if(!data)$('#content').innerHTML=empty('暂时无法连接本机服务，请确认 Codex Pulse 已启动后点击刷新。');
    } finally {busy=false;$('#refresh').disabled=false;schedule();}
  }
  $('#refresh').addEventListener('click',()=>load(true));
  $('#date').addEventListener('change',()=>{reportKey='';load(true);});
  window.addEventListener('hashchange',render);
  load(true);
})();
