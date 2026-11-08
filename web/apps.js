// CDBS.OS 4.0 — página Servidores (aplicativos Python/Node.js) e bloco Armazenamento.
// Segurança: tudo que vem do servidor entra por textContent (CDBSUI.el), nunca innerHTML. Ações que tiram algo do ar pedem
// confirmação; o resultado mostrado é o REAL, devolvido pelo guardião (followRequest), nunca presumido.
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const UI = () => window.CDBSUI;
  const TONE = { rodando: 'ok', iniciando: 'wait', implantando: 'wait', reiniciando: 'warn', parado: 'off', sem_codigo: 'off',
    parado_por_falhas: 'fail', indisponivel: 'fail' };
  const S = { timer: null, view: null, openLogs: new Set(), staged: new Set(), stopped: true };

  const mb = (bytes) => (bytes == null ? '—' : bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);
  const say = (sel, text, tone) => { const n = $(sel); if (n) { n.textContent = text || ''; n.className = `message ${tone || ''}`; } };

  function since(ts) {
    if (!ts) return '—';
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 90) return `${s} s`;
    if (s < 5400) return `${Math.round(s / 60)} min`;
    if (s < 172800) return `${Math.round(s / 3600)} h`;
    return `${Math.round(s / 86400)} dias`;
  }

  // ------------------------------------------------------------------ ações (sempre pelo guardião)
  async function act(app, action, button) {
    const { api, confirmDialog, busy, followRequest } = UI();
    const texts = {
      stop: ['Desligar servidor', `O endereço /${app.name}/ passa a responder "fora do ar" até você ligar de novo.`, 'Desligar'],
      restart: ['Reiniciar servidor', `O endereço /${app.name}/ fica fora do ar por alguns segundos.`, 'Reiniciar'],
      rollback: ['Voltar versão anterior', `A versão anterior de ${app.name} volta a rodar. A atual fica guardada (dá para voltar de novo).`, 'Voltar versão'],
      remove: ['Remover servidor', `${app.name} é desligado e sai do painel. Os arquivos ficam guardados em data/removidos/apps/ (nada é apagado).`, 'Remover'],
    };
    const body = {};
    if (texts[action]) {
      const [title, text, ok] = texts[action];
      const answer = await confirmDialog({ title, text, okLabel: ok, typed: action === 'remove' ? app.name.toUpperCase() : '' });
      if (!answer) return;
      if (action === 'remove') body.confirm_text = app.name; else body.confirm = true;
    }
    const labels = { start: 'Ligando…', stop: 'Desligando…', restart: 'Reiniciando…', deploy: 'Implantando…', rollback: 'Voltando…', remove: 'Removendo…' };
    await busy(button, labels[action] || 'Aguarde…', async () => {
      try {
        const { request } = await api('POST', `/api/apps/${app.name}/${action}`, body);
        say('#appsMessage', `${app.name}: pedido enviado ao guardião…`, '');
        const result = await followRequest(request, (r) => say('#appsMessage', `${app.name}: ${r.message || 'em andamento…'}`, ''), 120);
        say('#appsMessage', `${app.name}: ${result.message}`, result.status === 'ok' ? 'success' : 'error');
        if (action === 'deploy') S.staged.delete(app.name);
      } catch (error) {
        say('#appsMessage', `${app.name}: ${error.message}`, 'error');
      }
      await refresh();
      if (window.CDBSOps) window.CDBSOps.refresh();          // selos do menu e visão geral
    });
  }

  async function upload(app, input, button, msg) {
    const { api, busy } = UI();
    const file = input.files && input.files[0];
    if (!file) { msg.textContent = 'Escolha o arquivo .zip primeiro.'; msg.className = 'message error'; return; }
    await busy(button, 'Enviando…', async () => {
      try {
        const { staged } = await api('POST', `/api/apps/${app.name}/upload`, undefined, await file.arrayBuffer());
        S.staged.add(app.name);
        msg.textContent = `Recebido e conferido: ${staged.files} arquivos (${mb(staged.bytes)}). Agora toque em "Implantar".`;
        msg.className = 'message success';
        input.value = '';
      } catch (error) {
        msg.textContent = error.message; msg.className = 'message error';
      }
      await refresh();
    });
  }

  async function loadLogs(app, box) {
    try {
      const data = await UI().api('GET', `/api/apps/${app.name}/logs?lines=200`);
      box.textContent = data.lines.length ? data.lines.join('\n') : '(nenhuma saída ainda — o registro aparece quando o servidor roda)';
      box.scrollTop = box.scrollHeight;
    } catch (error) {
      box.textContent = error.message;
    }
  }

  // ------------------------------------------------------------------ cartão de um servidor
  function appCard(app, ready) {
    const { el } = UI();
    const tone = TONE[app.state] || 'off';
    const button = (text, cls, action, disabled) => {
      const b = el('button', { class: `button ${cls}`, type: 'button', text, disabled: disabled || !ready });
      b.addEventListener('click', () => act(app, action, b));
      return b;
    };
    const running = app.state === 'rodando' || app.state === 'iniciando' || app.state === 'reiniciando';
    const actions = el('div', { class: 'op-row-actions' });
    if (running) actions.append(button('Reiniciar', 'secondary', 'restart'), button('Desligar', 'danger', 'stop'));
    else actions.append(button(app.state === 'parado_por_falhas' ? 'Tentar de novo' : 'Ligar', 'primary', 'start', !app.has_code));
    if (app.has_staged) actions.append(button('Implantar código novo', 'primary', 'deploy'));
    if (app.has_previous) actions.append(button('Voltar versão anterior', 'secondary', 'rollback'));

    const link = app.public_url || app.local_url;
    const facts = [
      ['Endereço', app.public_url ? app.public_url : `${app.local_url} (sem link público agora)`],
      ['Tipo', `${app.runtime_label} · ${app.entry}`],
      ['Porta no celular', `127.0.0.1:${app.port}`],
      ['No ar há', app.state === 'rodando' ? since(app.started_at) : '—'],
      ['Memória', app.rss_kb ? mb(app.rss_kb * 1024) : '—'],
      ['CPU', app.cpu_pct != null ? `${app.cpu_pct}%` : '—'],
      ['Reinícios (15 min)', String(app.restarts_recent || 0)],
      ['Implantado', app.deployed_at ? UI().whenText(app.deployed_at) : 'nunca'],
    ];

    // envio do código
    const file = el('input', { type: 'file', accept: '.zip,application/zip', 'aria-label': `Código de ${app.name} (.zip)` });
    const upMsg = el('p', { class: 'message', role: 'status' });
    const upBtn = el('button', { class: 'button secondary', type: 'button', text: 'Enviar e conferir' });
    upBtn.addEventListener('click', () => upload(app, file, upBtn, upMsg));
    const uploadBox = el('details', { class: 'op-details', open: !app.has_code && !app.has_staged },
      el('summary', { text: app.has_code ? 'Enviar código novo (.zip)' : 'Enviar o código (.zip) — primeiro passo' }),
      el('p', { class: 'hint-text', text: `ZIP com ${app.entry} dentro (pode estar numa pasta). Para Node.js, inclua node_modules ou só use bibliotecas que já vêm no Node. Nada roda até você tocar em "Implantar".` }),
      el('div', { class: 'bk-import-row' }, file, upBtn), upMsg);

    // registro
    const logBox = el('pre', { class: 'op-pre app-log', tabindex: '0', 'aria-label': `Registro de ${app.name}` });
    const logDetails = el('details', { class: 'op-details', open: S.openLogs.has(app.name) },
      el('summary', { text: 'Registro (saída do programa)' }), logBox);
    const reload = el('button', { class: 'button ghost', type: 'button', text: 'Atualizar registro' });
    reload.addEventListener('click', () => loadLogs(app, logBox));
    logDetails.append(reload);
    logDetails.addEventListener('toggle', () => {
      if (logDetails.open) { S.openLogs.add(app.name); loadLogs(app, logBox); } else S.openLogs.delete(app.name);
    });
    if (logDetails.open) loadLogs(app, logBox);

    // variáveis
    const envMsg = el('p', { class: 'message', role: 'status' });
    const keyIn = el('input', { maxlength: '64', autocomplete: 'off', spellcheck: 'false', placeholder: 'NOME_DA_VARIAVEL', 'aria-label': 'Nome da variável' });
    const valIn = el('input', { type: 'password', autocomplete: 'new-password', spellcheck: 'false', placeholder: 'valor (não aparece de novo)', 'aria-label': 'Valor da variável' });
    const save = el('button', { class: 'button secondary', type: 'button', text: 'Salvar variável' });
    save.addEventListener('click', () => UI().busy(save, 'Salvando…', async () => {
      try {
        await UI().api('POST', `/api/apps/${app.name}/env`, { set: { [keyIn.value.trim().toUpperCase()]: valIn.value } });
        envMsg.textContent = 'Salva. Reinicie o servidor para ele ler o valor novo.'; envMsg.className = 'message success';
        keyIn.value = ''; valIn.value = '';
        await refresh();
      } catch (error) { envMsg.textContent = error.message; envMsg.className = 'message error'; }
    }));
    const keys = el('ul', { class: 'op-events' }, app.env_keys.map((key) => {
      const del = el('button', { class: 'button ghost', type: 'button', text: 'Apagar' });
      del.addEventListener('click', () => UI().busy(del, '…', async () => {
        try { await UI().api('POST', `/api/apps/${app.name}/env`, { remove: [key] }); await refresh(); } catch (error) { envMsg.textContent = error.message; envMsg.className = 'message error'; }
      }));
      return el('li', { class: 'op-event' }, el('code', { text: key }), el('span', { class: 'op-ev-detail', text: 'configurada ••••' }), del);
    }));
    const envBox = el('details', { class: 'op-details' }, el('summary', { text: `Variáveis de ambiente (${app.env_keys.length})` }),
      el('p', { class: 'hint-text', text: 'Senhas e chaves do seu programa (ex.: API_KEY). São gravadas no celular e nunca mostradas de volta. PORT, HOST e CDBS_* são definidas pelo CDBS.' }),
      keys, el('div', { class: 'bk-import-row' }, keyIn, valIn, save), envMsg);

    // configuração
    const cfgMsg = el('p', { class: 'message', role: 'status' });
    const entry = el('input', { value: app.entry, maxlength: '120', 'aria-label': 'Arquivo de entrada' });
    const health = el('input', { value: app.health_path || '', maxlength: '120', placeholder: '/saude', 'aria-label': 'Caminho de saúde' });
    const strip = el('input', { type: 'checkbox', checked: app.strip_prefix !== false });
    const maxBody = el('input', { type: 'number', min: '1', max: '100', value: String(app.max_body_mb || 25), 'aria-label': 'Limite de envio em MB' });
    const timeout = el('input', { type: 'number', min: '5', max: '300', value: String(app.timeout_s || 60), 'aria-label': 'Tempo máximo de resposta em segundos' });
    const saveCfg = el('button', { class: 'button secondary', type: 'button', text: 'Salvar configuração' });
    saveCfg.addEventListener('click', () => UI().busy(saveCfg, 'Salvando…', async () => {
      try {
        const res = await UI().api('POST', `/api/apps/${app.name}/config`, { entry: entry.value.trim(), health_path: health.value.trim(), strip_prefix: strip.checked,
          max_body_mb: Number(maxBody.value), timeout_s: Number(timeout.value) });
        cfgMsg.textContent = res.note; cfgMsg.className = 'message success';
        await refresh();
      } catch (error) { cfgMsg.textContent = error.message; cfgMsg.className = 'message error'; }
    }));
    const cfgBox = el('details', { class: 'op-details' }, el('summary', { text: 'Configuração' }),
      el('div', { class: 'op-form' },
        el('div', { class: 'field' }, el('label', { text: 'Arquivo que inicia o programa' }), entry),
        el('div', { class: 'field' }, el('label', { text: 'Caminho de saúde (deve responder 200; vazio = só a porta)' }), health),
        el('label', { class: 'bk-check' }, strip, ` Tirar /${app.name} do caminho antes de repassar (o programa acha que mora em "/")`),
        el('div', { class: 'field' }, el('label', { text: 'Tamanho máximo de envio por pedido (MB)' }), maxBody),
        el('div', { class: 'field' }, el('label', { text: 'Tempo máximo de resposta (segundos)' }), timeout),
        saveCfg, cfgMsg));

    const remove = el('button', { class: 'button ghost danger-text', type: 'button', text: 'Remover servidor…', disabled: !ready });
    remove.addEventListener('click', () => act(app, 'remove', remove));

    return el('article', { class: `app-card app-${tone}`, 'data-app': app.name },
      el('header', { class: 'app-head' },
        el('span', { class: 'slot-num', text: String(app.slot) }),
        el('div', { class: 'app-title' },
          el('div', { class: 'slot-head' }, el('span', { class: `state state-${tone}`, text: app.label }), el('strong', { text: app.name })),
          app.description ? el('div', { class: 'bk-meta', text: app.description }) : null),
        link && app.state === 'rodando' ? el('a', { class: 'button secondary', href: link, target: '_blank', rel: 'noopener noreferrer', text: 'Abrir' }) : null),
      app.message ? el('div', { class: app.state === 'parado_por_falhas' ? 'bk-err' : 'bk-meta', text: app.message }) : null,
      !app.has_code && !app.has_staged ? el('div', { class: 'bk-warning', text: 'Sem código ainda: envie o .zip abaixo e depois toque em "Implantar código novo".' }) : null,
      el('dl', { class: 'app-facts' }, facts.map(([k, v]) => el('div', {}, el('dt', { text: k }), el('dd', { text: v })))),
      actions, uploadBox, logDetails, envBox, cfgBox, el('div', { class: 'app-foot' }, remove));
  }

  function render(view) {
    const { el, fill } = UI();
    S.view = view;
    $('#appsCount').textContent = `${view.apps.length}/${view.slots}`;
    const note = $('#appsNote');
    note.hidden = !view.note;
    note.textContent = view.note || '';
    // mantém aberto o que a pessoa abriu (o painel atualiza sozinho)
    const open = new Map([...document.querySelectorAll('#appsList [data-app] details')].map((d, i) => [`${d.closest('[data-app]').dataset.app}:${i}`, d.open]));
    fill($('#appsList'), view.apps.length ? view.apps.map((app) => appCard(app, view.supervisor_ready)) : el('div', { class: 'empty-state compact' },
      el('strong', { text: 'Nenhum servidor ainda' }), el('p', { text: `Você tem ${view.slots} vagas. Crie o primeiro logo abaixo.` })));
    [...document.querySelectorAll('#appsList [data-app] details')].forEach((d, i) => {
      const key = `${d.closest('[data-app]').dataset.app}:${i}`;
      if (open.has(key) && !d.matches('[open]') && open.get(key)) d.open = true;
    });
    const box = $('#appsCreateBox');
    if (!view.apps.length) box.open = true;
    const form = $('#appsCreateForm');
    const full = !view.free_slots.length;
    for (const node of form.querySelectorAll('input,select,button')) node.disabled = full;
    if (full) say('#appsCreateMsg', `As ${view.slots} vagas estão ocupadas. Remova um servidor para criar outro.`, '');
  }

  // ------------------------------------------------------------------ armazenamento
  function renderStorage(rep) {
    const { el, fill } = UI();
    const dev = rep.device || {};
    const pct = dev.total ? Math.round((dev.used / dev.total) * 100) : 0;
    const cdbsPct = dev.total ? Math.max(0.5, (rep.cdbs_total / dev.total) * 100) : 0;
    const used = el('span', { class: 'st-used' });
    const mine = el('span', { class: 'st-cdbs' });
    used.style.width = `${pct}%`;                 // CSSOM: permitido pela CSP do painel (style-src 'self'), ao contrário de style="…"
    mine.style.width = `${cdbsPct}%`;
    fill($('#stBar'), used, mine);
    $('#stSummary').textContent = dev.total
      ? `${mb(dev.free)} livres de ${mb(dev.total)} (${pct}% usado no celular). O CDBS ocupa ${mb(rep.cdbs_total)}. Medido ${UI().whenText(rep.measured_at)}.`
      : 'Não foi possível medir o espaço do celular.';
    const names = { sites: 'Sites', servidores: 'Servidores', backups: 'Backups', lojas: 'Lojas (fotos e produtos)', removidos: 'Removidos (guardados)',
      atualizacoes: 'Atualizações (cópias do programa)', sistema: 'Sistema e registros', monitoramento: 'Monitoramento', notificacoes: 'Notificações' };
    fill($('#stParts'), Object.entries(rep.parts).sort((a, b) => b[1] - a[1]).map(([k, v]) => el('div', { class: 'bk-bar-row' }, el('span', { text: names[k] || k }), el('strong', { text: mb(v) }))));
    const sites = Object.entries(rep.sites || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => el('div', { class: 'bk-bar-row' }, el('span', { text: `site ${k}` }), el('span', { text: mb(v) })));
    const apps = Object.entries(rep.apps || {}).map(([k, v]) => el('div', { class: 'bk-bar-row' }, el('span', { text: `servidor ${k}` }),
      el('span', { text: `${mb(v.atual)} código · ${mb(v.dados)} dados · ${mb(v.anterior)} versão anterior · ${mb(v.logs)} registro` })));
    fill($('#stDetail'), sites.length || apps.length ? [...sites, ...apps] : el('p', { class: 'hint-text', text: 'Nada publicado ainda.' }));
  }

  async function loadStorage(fresh) {
    try { renderStorage(await UI().api('GET', `/api/storage${fresh === true ? '?fresh=1' : ''}`)); $('#stStorage').hidden = false; } catch (error) { /* sessão expirada: app.js cuida */ }
  }

  // ------------------------------------------------------------------ ciclo
  async function refresh() {
    try { render(await UI().api('GET', '/api/apps')); $('#appsSection').hidden = false; } catch (error) { say('#appsMessage', error.message, 'error'); }
  }

  async function create(event) {
    event.preventDefault();
    const button = event.submitter || $('#appsCreateForm button');
    await UI().busy(button, 'Criando…', async () => {
      try {
        const { app } = await UI().api('POST', '/api/apps', { name: $('#appName').value.trim().toLowerCase(), runtime: $('#appRuntime').value,
          entry: $('#appEntry').value.trim(), health_path: $('#appHealth').value.trim(), description: $('#appDesc').value.trim() });
        say('#appsCreateMsg', `Servidor ${app.name} criado na vaga ${app.slot} (porta ${app.port}). Agora envie o código.`, 'success');
        $('#appsCreateForm').reset();
        $('#appsCreateBox').open = false;
        await refresh();
      } catch (error) { say('#appsCreateMsg', error.message, 'error'); }
    });
  }

  function onPage(event) {
    const page = event.detail && event.detail.page;
    if (S.stopped) return;
    if (page === 'servidores') refresh();
    if (page === 'inicio') loadStorage();
  }

  function load() {
    S.stopped = false;
    refresh();
    loadStorage();
    clearInterval(S.timer);
    S.timer = setInterval(() => {
      const visible = document.querySelector('#pg-servidores:not([hidden])');
      if (visible && !document.hidden && !document.querySelector('#appsList :focus')) refresh();
    }, 10000);
  }

  function stop() {
    S.stopped = true;
    clearInterval(S.timer);
    for (const id of ['#appsSection', '#stStorage']) { const n = $(id); if (n) n.hidden = true; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('#appsCreateForm').addEventListener('submit', create);
    $('#stRefresh').addEventListener('click', () => UI().busy($('#stRefresh'), 'Medindo…', () => loadStorage(true)));
    const entry = $('#appEntry');
    $('#appRuntime').addEventListener('change', () => { entry.placeholder = $('#appRuntime').value === 'node' ? 'server.js' : 'app.py'; });
  });
  document.addEventListener('cdbs:page', onPage);

  window.CDBSApps = { load, stop, refresh, loadStorage };
})();
