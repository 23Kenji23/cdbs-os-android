// CDBS.OS — painel operacional: visão geral, posições de site e histórico de eventos (3.0; 20 posições e servidores desde a 4.0).
// Segurança: tudo que vem do servidor entra na tela por textContent (nunca innerHTML). Ações que tiram algo do ar
// pedem confirmação e o botão fica travado até a resposta; o painel só mostra "pronto" quando o servidor confirma.
(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const STATE_TONE = { ATIVO: 'ok', SO_LOCAL: 'warn', FORA_DO_AR: 'fail', INCONCLUSIVO: 'warn', PAUSADO: 'off', VAGO: 'off' };
  const TUNNEL_TEXT = {
    conectado: '🟢 conectado', iniciando: '◔ conectando…', desconectado: '🟠 desconectado (internet?)', reiniciando: '◔ reiniciando…',
    desligado: '⚪ desligado', parado_por_falhas: '🔴 parado (falhou várias vezes)', indisponivel: '🔴 cloudflared não instalado', desconhecido: '❔ desconhecido',
  };
  const ACTION_TEXT = {
    servico_iniciado: 'CDBS iniciado', servico_parado: 'CDBS parado', encerramento_inesperado: 'Encerramento inesperado', inicio_falhou: 'Falha ao iniciar',
    supervisor_iniciado: 'Supervisor iniciado', supervisor_parado: 'Supervisor parado', servidor_reiniciado: 'Servidor caiu e foi reiniciado',
    servidor_travado: 'Servidor travou e foi reiniciado', servidor_parado_por_falhas: 'Servidor parado por falhas seguidas', servidor_reiniciado_a_pedido: 'Servidor reiniciado a pedido',
    site_publicado: 'Site publicado', site_removido: 'Site removido', site_pausado: 'Site pausado', site_retomado: 'Site retomado',
    loja_criada: 'Loja criada', loja_editada: 'Loja editada', loja_removida: 'Loja removida', loja_ativada: 'Loja ativada', senha_loja_trocada: 'Senha de loja trocada',
    backup_concluido: 'Backup concluído', backup_falhou: 'Backup falhou', backup_invalido: 'Backup inválido', backup_excluido: 'Backup excluído',
    restauracao_concluida: 'Restauração concluída', restauracao_revertida: 'Restauração desfeita', restauracao_falhou: 'Restauração falhou',
    atualizacao_iniciada: 'Atualização iniciada', atualizacao_concluida: 'Atualização concluída', atualizacao_revertida: 'Atualização revertida',
    atualizacao_falhou: 'Atualização falhou', pacote_recebido: 'Pacote de atualização recebido',
    verificacao: 'Verificação das posições', verificar_agora: 'Verificar agora', posicao_atribuida: 'Posição atribuída', posicao_liberada: 'Posição liberada',
    site_excedente: 'Site fora das posições (todas ocupadas)', tunel_ligado: 'Túnel ligado', tunel_desligado: 'Túnel desligado', tunel_caiu: 'Túnel caiu',
    tunel_conectado: 'Túnel conectado', tunel_link_novo: 'Novo link público', tunel_parado_por_falhas: 'Túnel parado por falhas',
    login_painel: 'Login no painel', senha_painel_criada: 'Senha do painel criada', credencial_salva: 'Credencial salva', credencial_apagada: 'Credencial apagada',
    configuracao_alterada: 'Configuração alterada', teste_pedido: 'Teste de notificação', comando_bot: 'Comando do bot', comando_recusado: 'Comando recusado',
    acao_bot: 'Ação pelo bot', webhook_recusado: 'Evento do WhatsApp recusado', webhook_verificado: 'Webhook verificado pela Meta',
    site_recuperado: 'Site recuperado', site_apagado: 'Cópia de site apagada de vez',
    app_criado: 'Servidor criado', app_configurado: 'Servidor configurado', app_variaveis: 'Variáveis de servidor', app_codigo_recebido: 'Código de servidor recebido',
    app_implantado: 'Servidor implantado', app_implantacao_revertida: 'Implantação desfeita (versão anterior voltou)', app_implantacao_falhou: 'Implantação falhou',
    app_versao_voltada: 'Servidor voltou de versão', app_ligado: 'Servidor ligado', app_desligado: 'Servidor desligado', app_caiu: 'Servidor caiu (reiniciando)',
    app_no_ar: 'Servidor voltou', app_parado_por_falhas: 'Servidor parado por falhas seguidas', app_removido: 'Servidor removido',
  };
  const S = { timer: null, stopped: true, slots: null, busy: false };

  // ------------------------------------------------------------------ utilidades compartilhadas (system.js usa também)
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === false || value == null) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, value);
    }
    for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child.nodeType ? child : document.createTextNode(String(child)));
    return node;
  }

  function fill(node, ...children) {
    if (node) node.replaceChildren(...children.flat(Infinity).filter((child) => child != null && child !== false));
  }

  async function api(method, path, body, raw) {
    const options = { method, credentials: 'same-origin', headers: { 'X-CDBS-Request': '1' } };
    if (raw) {
      options.headers['Content-Type'] = 'application/zip';
      options.body = raw;
    } else if (method !== 'GET') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body === undefined ? {} : body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (networkError) {
      const error = new Error('O CDBS não respondeu (pode estar reiniciando).');
      error.offline = true;
      throw error;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `Erro ${response.status}`);
      error.status = response.status;
      error.data = data;
      if (response.status === 401 && window.CDBSAuth) window.CDBSAuth();
      throw error;
    }
    return data;
  }

  function whenText(seconds) {
    if (!seconds) return '—';
    return new Date(seconds * 1000).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function say(target, text, kind = '') {
    const node = typeof target === 'string' ? $(target) : target;
    if (!node) return;
    node.textContent = text;
    node.className = `message ${kind}`.trim();
  }

  // Diálogo de confirmação: devolve true/false. Com `typed`, só libera depois de digitar a palavra exata.
  function confirmDialog({ title, text, okLabel = 'Confirmar', typed = '', extra = '' }) {
    const dialog = $('#opConfirmDialog');
    return new Promise((resolve) => {
      $('#opConfirmTitle').textContent = title;
      $('#opConfirmText').textContent = text;
      const field = $('#opConfirmField');
      const input = $('#opConfirmInput');
      const extraBox = $('#opConfirmExtraBox');
      field.hidden = !typed;
      input.value = '';
      $('#opConfirmLabel').textContent = typed ? `Digite ${typed} para confirmar` : '';
      $('#opConfirmExtra').hidden = !extra;
      $('#opConfirmExtraText').textContent = extra;
      extraBox.checked = false;
      const ok = $('#opConfirmOk');
      ok.textContent = okLabel;
      const update = () => { ok.disabled = !!typed && input.value.trim().toUpperCase() !== typed; };
      update();
      const finish = (value) => {
        input.removeEventListener('input', update);
        ok.onclick = null;
        $('#opConfirmCancel').onclick = null;
        dialog.onclose = null;
        if (dialog.open) dialog.close();
        resolve(value ? { ok: true, extra: extraBox.checked } : false);
      };
      input.addEventListener('input', update);
      ok.onclick = () => finish(true);
      $('#opConfirmCancel').onclick = () => finish(false);
      dialog.onclose = () => finish(false);
      dialog.showModal();
      if (typed) input.focus();
    });
  }

  // Botão travado enquanto a ação roda (sem cliques repetidos); devolve o que a ação devolver.
  async function busy(button, label, action) {
    if (button.disabled) return undefined;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
      return await action();
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  // Acompanha um pedido feito ao supervisor até o resultado REAL (ou desistir depois de `limit` segundos).
  async function followRequest(rid, onUpdate, limit = 180) {
    const started = Date.now();
    let offlineSeen = false;
    while (Date.now() - started < limit * 1000) {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        const result = await api('GET', `/api/control/${rid}`);
        if (offlineSeen && onUpdate) onUpdate({ status: 'em_andamento', message: 'O CDBS voltou; conferindo o resultado…' });
        if (result.status && !['aguardando', 'em_andamento'].includes(result.status)) return result;
        if (onUpdate) onUpdate(result);
      } catch (error) {
        if (error.offline) {
          offlineSeen = true;
          if (onUpdate) onUpdate({ status: 'em_andamento', message: 'Servidor reiniciando… (é normal o painel ficar fora por alguns segundos)' });
          continue;
        }
        throw error;
      }
    }
    return { status: 'sem_resposta', message: 'Sem resposta final do supervisor ainda. Veja o histórico em alguns minutos.' };
  }

  window.CDBSUI = { el, fill, api, whenText, say, confirmDialog, busy, followRequest, TUNNEL_TEXT, ACTION_TEXT };

  // ------------------------------------------------------------------ visão geral
  function card(title, main, sub, tone) {
    return el('article', { class: `op-card ${tone ? `op-${tone}` : ''}` }, el('h3', { text: title }), el('p', { class: 'op-main', text: main }), sub ? el('p', { class: 'op-subtext', text: sub }) : null);
  }

  function renderOverview(ov) {
    const counts = ov.slots.summary.counts;
    const bad = (counts.FORA_DO_AR || 0) + (counts.SO_LOCAL || 0);
    const tunnel = ov.tunnel;
    const notif = ov.notifications;
    const cards = [
      card('Sistema', `Versão ${ov.version}`, `no ar desde ${whenText(ov.started_at)} · supervisor ${ov.supervisor.running ? 'ativo' : 'AUSENTE (inicie com bash start.sh)'}`, ov.supervisor.running ? 'ok' : 'warn'),
      card(`Sites (${ov.slots.slots.filter((s) => s.slug).length} de ${ov.slots.slots.length} posições)`, ov.slots.summary.text, ov.slots.last_round ? `última verificação ${whenText(ov.slots.last_round.started_at)}` : 'ainda sem verificação', bad ? 'fail' : 'ok'),
      ov.apps ? card(`Servidores (${ov.apps.used} de ${ov.apps.slots})`, ov.apps.text, ov.apps.ready ? (ov.apps.problems.length ? `com problema: ${ov.apps.problems.join(', ')}` : 'guardião cuidando') : 'reinicie o CDBS (bash start.sh) para ativar',
        ov.apps.problems.length ? 'fail' : ov.apps.ready ? (ov.apps.used ? 'ok' : '') : 'warn') : null,
      card('Túnel', TUNNEL_TEXT[tunnel.state] || tunnel.state, tunnel.url || ov.public_base || (tunnel.managed ? 'sem link público agora' : tunnel.message), tunnel.state === 'conectado' ? 'ok' : 'warn'),
      card('Último backup', ov.last_backup ? `${ov.last_backup.slug} · ${whenText(ov.last_backup.at)}` : 'nenhum backup validado', ov.last_backup && ov.last_backup.encrypted ? 'criptografado' : '', ov.last_backup ? 'ok' : 'warn'),
      card('Notificações', `WhatsApp: ${notif.text}` + (notif.email ? ` · E-mail: ${notif.email.text}` : ''),
        notif.last_sent ? `último envio aceito ${whenText(notif.last_sent.at)}` : 'nenhum envio ainda',
        notif.state === 'pronto' || (notif.email && notif.email.state === 'pronto') ? 'ok'
          : notif.state === 'desativado' && (!notif.email || notif.email.state === 'desativado') ? '' : 'warn'),
      card('Atualização', ov.update ? `${ov.update.from_version} → ${ov.update.to_version}` : 'nenhuma pelo painel', ov.update ? (ov.update.message || ov.update.step) : '', ov.update && ['FALHOU_REVERSAO', 'FALHOU'].includes(ov.update.step) ? 'fail' : ''),
    ];
    fill($('#opCards'), cards);
    document.dispatchEvent(new CustomEvent('cdbs:overview', { detail: ov }));   // selos do menu (nav.js)
    const problems = ov.recent_problems || [];
    fill($('#opProblems'), problems.length ? [el('h3', { class: 'op-sub', text: 'Problemas recentes' }), el('ul', { class: 'op-events' }, problems.map(eventRow))] : null);
  }

  // ------------------------------------------------------------------ posições de site
  function stateBadge(item) {
    return el('span', { class: `state state-${STATE_TONE[item.state] || 'off'}`, text: item.label || item.state });
  }

  function slotRow(item, extras) {
    const actions = el('div', { class: 'site-actions' });
    if (item.slug) {
      const url = item.public_url || item.local_url;
      actions.append(el('a', { class: 'button secondary', href: url, target: '_blank', rel: 'noopener noreferrer', text: item.public_url ? 'Abrir' : 'Abrir local' }));
      const check = el('button', { class: 'button secondary', type: 'button', text: 'Verificar' });
      check.addEventListener('click', () => busy(check, 'Verificando…', () => checkNow(item.slot)));
      const pause = el('button', { class: item.paused ? 'button secondary' : 'button danger', type: 'button', text: item.paused ? 'Retomar' : 'Pausar' });
      pause.addEventListener('click', () => busy(pause, item.paused ? 'Retomando…' : 'Pausando…', () => togglePause(item)));
      const release = el('button', { class: 'button ghost', type: 'button', text: 'Liberar posição' });
      release.addEventListener('click', () => busy(release, 'Liberando…', () => releaseSlot(item)));
      actions.append(check, pause, release);
    } else if (extras.length) {
      const select = el('select', { 'aria-label': `Site para a posição ${item.slot}` }, extras.map((slug) => el('option', { value: slug, text: slug })));
      const assign = el('button', { class: 'button secondary', type: 'button', text: 'Colocar aqui' });
      assign.addEventListener('click', () => busy(assign, 'Salvando…', () => assignSlot(item.slot, select.value)));
      actions.append(select, assign);
    }
    const lines = [];
    if (item.slug) {
      lines.push(item.checked_at ? `verificado ${whenText(item.checked_at)}` : 'ainda não verificado');
      if (item.last_success_at) lines.push(`último OK ${whenText(item.last_success_at)}`);
      if (item.public && item.public.result === 'nao_verificado') lines.push('link público não testado');
    }
    return el('li', { class: `slot slot-${STATE_TONE[item.state] || 'off'}` },
      el('div', { class: 'slot-num', text: String(item.slot) }),
      el('div', { class: 'slot-main' },
        el('div', { class: 'slot-head' }, stateBadge(item), el('strong', { text: item.slug || 'Vago' })),
        item.reason ? el('div', { class: 'bk-err', text: item.reason }) : null,
        lines.length ? el('div', { class: 'bk-meta', text: lines.join(' · ') }) : null),
      actions);
  }

  function renderSlots(view) {
    S.slots = view;
    const extras = view.extras || [];
    // 4.0: com 20 posições, as vagas viram UMA linha (a não ser que haja site esperando posição: aí cada vaga oferece "Colocar aqui")
    const free = view.slots.filter((item) => !item.slug);
    const rows = view.slots.filter((item) => item.slug || extras.length).map((item) => slotRow(item, extras));
    if (free.length && !extras.length) {
      rows.push(el('li', { class: 'slot slot-off slot-free' }, el('div', { class: 'slot-num', text: String(free.length) }),
        el('div', { class: 'slot-main' }, el('strong', { text: `${free.length} posições vagas` }),
          el('div', { class: 'bk-meta', text: `Nº ${free.map((item) => item.slot).join(', ')} · um site novo entra sozinho na primeira vaga` }))));
    }
    fill($('#opSlotList'), rows);
    const parts = [`Relatório a cada ${view.interval_minutes} min`, view.enabled ? 'monitoramento ligado' : 'monitoramento DESLIGADO',
      view.public_check ? `teste pelo link público ligado${view.public_base ? '' : ' (sem link público agora)'}` : 'teste só no aparelho (link público não testado)'];
    if (view.next_at) parts.push(`próxima rodada ~${new Date(view.next_at * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`);
    if (view.running) parts.push('verificação em andamento…');
    $('#opSlotsInfo').textContent = parts.join(' · ');
    const warn = $('#opSlotsWarn');
    const notes = [];
    if (extras.length) notes.push(`Fora das 10 posições (sem monitoramento): ${extras.join(', ')}. Libere uma posição para incluir.`);
    for (const w of view.warnings || []) notes.push(w);
    warn.hidden = !notes.length;
    warn.textContent = notes.join(' ');
  }

  async function checkNow(slot) {
    try {
      const result = await api('POST', '/api/monitor/check', slot ? { slot } : {});
      say('#opSlotsMessage', result.message, result.started ? 'success' : 'error');
      setTimeout(refresh, 2500);
      setTimeout(refresh, 7000);
    } catch (error) {
      say('#opSlotsMessage', error.message, 'error');
    }
  }

  async function togglePause(item) {
    if (!item.paused) {
      const answer = await confirmDialog({ title: `Pausar ${item.slug}?`, text: 'O site sai do ar para os visitantes (eles veem "temporariamente fora do ar"). Nada é apagado e você pode retomar quando quiser.', okLabel: 'Pausar site' });
      if (!answer) return;
    }
    try {
      await api('POST', `/api/sites/${encodeURIComponent(item.slug)}/${item.paused ? 'resume' : 'pause'}`, item.paused ? {} : { confirm: true });
      say('#opSlotsMessage', item.paused ? `${item.slug} voltou ao ar.` : `${item.slug} pausado.`, 'success');
      await refresh();
    } catch (error) {
      say('#opSlotsMessage', error.message, 'error');
    }
  }

  async function releaseSlot(item) {
    const answer = await confirmDialog({ title: `Liberar a posição ${item.slot}?`, text: `${item.slug} sai do monitoramento (o site continua publicado). As outras posições não mudam de número.`, okLabel: 'Liberar' });
    if (!answer) return;
    try {
      await api('POST', '/api/monitor/slots', { slot: item.slot, slug: null });
      say('#opSlotsMessage', `Posição ${item.slot} liberada.`, 'success');
      await refresh();
    } catch (error) {
      say('#opSlotsMessage', error.message, 'error');
    }
  }

  async function assignSlot(slot, slug) {
    try {
      await api('POST', '/api/monitor/slots', { slot, slug });
      say('#opSlotsMessage', `${slug} agora está na posição ${slot}.`, 'success');
      await refresh();
    } catch (error) {
      say('#opSlotsMessage', error.message, 'error');
    }
  }

  // ------------------------------------------------------------------ histórico
  function eventRow(entry) {
    const tone = { ok: 'ok', falha: 'fail', recusado: 'fail', aviso: 'warn', info: 'off' }[entry.result] || 'off';
    const label = ACTION_TEXT[entry.action] || entry.action.replace(/_/g, ' ');
    return el('li', { class: 'op-event' },
      el('span', { class: 'op-ev-time', text: whenText(entry.at) }),
      el('span', { class: `state state-${tone}`, text: entry.result }),
      el('span', { class: 'op-ev-main' }, el('strong', { text: label }), entry.target ? ` · ${entry.target}` : '', entry.detail ? el('span', { class: 'op-ev-detail', text: ` — ${entry.detail}` }) : null,
        entry.actor ? el('span', { class: 'op-ev-actor', text: ` (${entry.actor})` }) : null));
  }

  async function loadEvents() {
    const params = new URLSearchParams();
    if ($('#opEvCategory').value) params.set('category', $('#opEvCategory').value);
    if ($('#opEvResult').value) params.set('result', $('#opEvResult').value);
    if ($('#opEvText').value.trim()) params.set('q', $('#opEvText').value.trim());
    params.set('limit', '80');
    const data = await api('GET', `/api/events?${params}`);
    const cat = $('#opEvCategory');
    if (cat.options.length <= 1) {
      for (const [value, text] of Object.entries(data.categories)) cat.append(el('option', { value, text }));
      for (const value of data.results) $('#opEvResult').append(el('option', { value, text: value }));
    }
    fill($('#opEventList'), data.events.length ? data.events.map(eventRow) : el('li', { class: 'empty-state compact', text: 'Nenhum evento com esses filtros.' }));
  }

  // ------------------------------------------------------------------ sites removidos (guardados)
  const bytesText = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1).replace('.', ',')} MB` : `${Math.max(1, Math.round((n || 0) / 1024))} KB`);

  function removedRow(item) {
    const restore = el('button', { class: 'button secondary', type: 'button', text: 'Recuperar', disabled: item.published_now,
      title: item.published_now ? 'Já existe um site publicado com esse nome' : '' });
    restore.addEventListener('click', () => busy(restore, 'Recuperando…', async () => {
      try {
        const result = await api('POST', `/api/removed-sites/${item.id}/restore`, {});
        say('#removedMessage', `${result.slug} voltou ao ar${result.slot ? ` (posição ${result.slot})` : ''}.`, 'success');
        if (window.CDBSLists) await window.CDBSLists.reload();
        await loadRemoved();
        await refresh();
      } catch (error) { say('#removedMessage', error.message, 'error'); }
    }));
    const purge = el('button', { class: 'button ghost', type: 'button', text: 'Apagar de vez' });
    purge.addEventListener('click', async () => {
      const answer = await confirmDialog({ title: `Apagar de vez a cópia de ${item.slug}?`,
        text: 'Os arquivos guardados deste site são apagados para sempre e não dá para desfazer. Os backups da Central de backups não são afetados.',
        okLabel: 'Apagar de vez', typed: 'APAGAR' });
      if (!answer) return;
      await busy(purge, 'Apagando…', async () => {
        try {
          await api('DELETE', `/api/removed-sites/${item.id}`, { confirm_text: 'APAGAR' });
          say('#removedMessage', `Cópia de ${item.slug} apagada.`, 'success');
          await loadRemoved();
        } catch (error) { say('#removedMessage', error.message, 'error'); }
      });
    });
    return el('li', { class: 'op-event' },
      el('span', { class: 'op-ev-time', text: whenText(item.removed_at) }),
      el('span', { class: 'op-ev-main' }, el('strong', { text: item.slug }), ` · ${item.files} arquivo${item.files === 1 ? '' : 's'} · ${bytesText(item.bytes)}`,
        item.published_now ? el('span', { class: 'op-ev-detail', text: ' — já existe um site publicado com esse nome' }) : null),
      el('div', { class: 'op-row-actions' }, restore, purge));
  }

  async function loadRemoved() {
    if (S.stopped) return;
    try {
      const data = await api('GET', '/api/removed-sites');
      $('#removedBox').hidden = !data.items.length;
      $('#removedSummary').textContent = `Sites removidos (guardados) — ${data.items.length}${data.items.length ? ` · ${bytesText(data.total_bytes)}` : ''}`;
      fill($('#removedList'), data.items.map(removedRow));
    } catch (error) {
      if (!error.offline) say('#removedMessage', error.message, 'error');
    }
  }

  // ------------------------------------------------------------------ monitoramento: configurações
  async function loadMonitorSettings() {
    const view = await api('GET', '/api/settings');
    const mon = view.settings.monitor;
    const select = $('#opMonInterval');
    const value = mon.enabled ? String(mon.interval_minutes) : 'off';
    if (![...select.options].some((o) => o.value === value)) {              // intervalo diferente vindo do .env ou de antes
      select.append(el('option', { value, text: `a cada ${value} minutos` }));
    }
    select.value = value;
    $('#opMonPublic').checked = !!mon.public_check;
    const locked = view.locked_by_env.includes('monitor.enabled') || view.locked_by_env.includes('monitor.interval_minutes');
    select.disabled = locked;
    select.title = locked ? 'Definido no .env' : '';
    $('#opMonPublic').disabled = view.locked_by_env.includes('monitor.public_check');
    $('#opMonPublic').title = $('#opMonPublic').disabled ? 'Definido no .env' : '';
  }

  $('#opMonitorForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const changes = {};
    const choice = $('#opMonInterval').value;
    if (!$('#opMonInterval').disabled) {
      changes.enabled = choice !== 'off';
      if (choice !== 'off') changes.interval_minutes = Number(choice);
    }
    if (!$('#opMonPublic').disabled) changes.public_check = $('#opMonPublic').checked;
    try {
      await api('POST', '/api/settings/monitor', changes);
      say('#opMonitorMsg', choice === 'off' ? 'Verificação automática desligada: as posições só são verificadas quando você pedir.' : 'Monitoramento salvo. Vale a partir da próxima rodada.', 'success');
      await refresh();
    } catch (error) {
      say('#opMonitorMsg', error.message, 'error');
    }
  });

  // ------------------------------------------------------------------ ciclo
  async function refresh() {
    if (S.stopped) return;
    try {
      const ov = await api('GET', '/api/overview');
      renderOverview(ov);
      const view = await api('GET', '/api/monitor');
      renderSlots(view);
    } catch (error) {
      if (!error.offline) say('#opSlotsMessage', error.message, 'error');
    }
  }

  async function load() {
    S.stopped = false;
    for (const id of ['#opOverview', '#opSlots', '#opEvents']) $(id).hidden = false;
    await refresh();
    try { await loadEvents(); } catch (error) { /* aparece na próxima tentativa */ }
    try { await loadMonitorSettings(); } catch (error) { /* idem */ }
    await loadRemoved();
    clearInterval(S.timer);
    S.timer = setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 20000);
  }

  function stop() {
    S.stopped = true;
    clearInterval(S.timer);
    for (const id of ['#opOverview', '#opSlots', '#opEvents']) { const node = $(id); if (node) node.hidden = true; }
    fill($('#opCards'));
    fill($('#opSlotList'));
    fill($('#opEventList'));
    fill($('#removedList'));
    const removed = $('#removedBox');
    if (removed) removed.hidden = true;
  }

  $('#opRefresh').addEventListener('click', (event) => busy(event.target, 'Atualizando…', async () => { await refresh(); await loadEvents(); }));
  $('#opCheckAll').addEventListener('click', (event) => busy(event.target, 'Verificando…', () => checkNow(null)));
  $('#opEventsFilter').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await loadEvents(); } catch (error) { fill($('#opEventList'), el('li', { class: 'bk-err', text: error.message })); }
  });

  window.CDBSOps = { load, stop, refresh, loadEvents, loadRemoved };
})();
