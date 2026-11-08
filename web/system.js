// CDBS.OS — painel: notificações/WhatsApp, bot, servidor, túnel e atualizações (3.0).
// Credenciais: os campos são só de gravação. O servidor nunca devolve os valores, só "configurado ••••1234".
// Nenhuma credencial fica guardada no navegador; os campos são limpos logo depois de salvar.
(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const ui = () => window.CDBSUI;
  const S = { timer: null, stopped: true, settings: null, updates: null, followingUpdate: false };
  const SECRET_LABELS = {
    whatsapp_phone_number_id: ['Phone number ID (número de envio)', 'só dígitos, está no painel da Meta → WhatsApp → Configuração da API'],
    whatsapp_token: ['Token de acesso da API', 'use um token PERMANENTE de usuário do sistema; o temporário expira em 24 h'],
    whatsapp_app_secret: ['Chave secreta do app (App Secret)', 'Configurações do app → Básico; serve para conferir que os eventos vêm mesmo da Meta'],
    tunnel_token: ['Token do túnel nomeado (Cloudflare)', 'só para o modo de endereço fixo'],
  };
  const DELIVERY_TEXT = {
    pendente: '◔ pendente', aceito: '✓ aceito pela API (ainda sem confirmação de entrega)', enviado: '✓ enviado', entregue: '✓✓ entregue', lido: '✓✓ lido',
    falhou: '✕ falhou', bloqueado: '⚠ bloqueado', agrupado: '≡ agrupado (repetido)', desativado: '— só no histórico local', registrado: '— registrado',
  };
  const STEP_TEXT = {
    VALIDANDO: 'validando o pacote', COPIA_DE_SEGURANCA: 'cópia de segurança do programa atual', PARANDO: 'parando o servidor',
    INSTALANDO: 'copiando os arquivos novos', INICIANDO: 'iniciando a versão nova', VERIFICANDO: 'conferindo a saúde da versão nova',
    CONCLUIDO: '✓ concluída', REVERTENDO: '↩ voltando para a versão anterior', REVERTIDO: '↩ revertida (versão anterior recolocada)',
    FALHOU: '✕ falhou (nada foi alterado)', FALHOU_REVERSAO: '✕ falhou e a volta automática também falhou',
  };

  function csv(numbers) { return (numbers || []).join(', '); }
  function splitList(text) { return text.split(',').map((x) => x.trim()).filter(Boolean); }

  // ------------------------------------------------------------------ notificações
  async function loadSettings() {
    const view = await ui().api('GET', '/api/settings');
    S.settings = view;
    const { notifications: n, whatsapp: w, bot: b, tunnel: t } = view.settings;
    $('#sysNotifEnabled').checked = !!n.enabled;
    $('#sysRecipients').value = '';
    $('#sysRecipientsNow').textContent = n.recipients_view.length ? `Agora: ${csv(n.recipients_view)} (deixe em branco para manter)` : 'Nenhum número configurado.';
    $('#sysReportEnabled').checked = !!n.report_enabled;
    $('#sysAlertsEnabled').checked = !!n.alerts_enabled;
    $('#sysCooldown').value = n.cooldown_minutes;
    $('#sysTplReport').value = w.template_report;
    $('#sysTplReportVars').value = String(w.report_variables);
    $('#sysTplAlert').value = w.template_alert;
    $('#sysTplLang').value = w.template_language;
    $('#sysApiVersion').value = w.api_version;
    $('#sysBotEnabled').checked = !!b.enabled;
    $('#sysBotAdmins').value = '';
    $('#sysBotViewers').value = '';
    $('#sysBotAdminsNow').textContent = b.admins_view.length ? `Agora: ${csv(b.admins_view)}` : 'Ninguém autorizado ainda.';
    $('#sysBotViewersNow').textContent = b.viewers_view.length ? `Agora: ${csv(b.viewers_view)}` : 'Ninguém.';
    $('#sysBotInteractive').checked = !!b.interactive;
    $('#sysTunnelMode').value = t.mode;
    $('#sysTunnelAuto').checked = !!t.autostart;
    const lock = (id, key) => { const node = $(id); node.disabled = view.locked_by_env.includes(key); node.title = node.disabled ? 'Definido no .env' : ''; };
    [['#sysNotifEnabled', 'notifications.enabled'], ['#sysRecipients', 'notifications.recipients'], ['#sysTplReport', 'whatsapp.template_report'],
      ['#sysTplReportVars', 'whatsapp.report_variables'],
      ['#sysTplAlert', 'whatsapp.template_alert'], ['#sysTplLang', 'whatsapp.template_language'], ['#sysApiVersion', 'whatsapp.api_version'],
      ['#sysBotEnabled', 'bot.enabled'], ['#sysBotAdmins', 'bot.admins'], ['#sysBotViewers', 'bot.viewers'], ['#sysBotInteractive', 'bot.interactive'],
      ['#sysTunnelMode', 'tunnel.mode'], ['#sysTunnelAuto', 'tunnel.autostart']].forEach(([id, key]) => lock(id, key));
    const bs = view.bot_status || {};
    ui().say('#sysBotMsg', bs.enabled ? (bs.ready ? '✓ Bot pronto para receber comandos.' : `⚠ Bot ligado, mas: ${(bs.problems || []).join('; ')}`) : 'Bot desligado.', bs.enabled && !bs.ready ? 'error' : '');
    loadMail(view);
    $('#sysWebhookUrl').textContent = view.webhook_url || '—';
    $('#sysWebhookNote').textContent = view.webhook_note || '';
    renderSecrets(view.secrets);
  }

  // ------------------------------------------------------------------ e-mail (SMTP)
  const GMAIL = { host: 'smtp.gmail.com', port: 465, security: 'ssl' };
  function showAdvanced() { $('#sysMailAdvanced').hidden = $('#sysMailPreset').value === 'gmail'; }

  function loadMail(view) {
    const m = view.settings.email;
    const isGmail = !m.smtp_host || (m.smtp_host === GMAIL.host && m.smtp_port === GMAIL.port && m.smtp_security === GMAIL.security);
    $('#sysMailEnabled').checked = !!m.enabled;
    $('#sysMailPreset').value = isGmail ? 'gmail' : 'outro';
    $('#sysMailUser').value = m.smtp_user || '';
    $('#sysMailHost').value = m.smtp_host || '';
    $('#sysMailPort').value = m.smtp_port;
    $('#sysMailSecurity').value = m.smtp_security === 'starttls' ? 'starttls' : 'ssl';
    $('#sysMailFrom').value = m.from_address || '';
    $('#sysMailTo').value = '';
    $('#sysMailToNow').textContent = m.recipients_view.length ? `Agora: ${csv(m.recipients_view)} (deixe em branco para manter)` : 'Nenhum e-mail configurado.';
    $('#sysMailPassword').value = '';
    const locked = (key) => view.locked_by_env.includes(`email.${key}`);
    for (const [id, key] of [['#sysMailEnabled', 'enabled'], ['#sysMailUser', 'smtp_user'], ['#sysMailTo', 'recipients'], ['#sysMailHost', 'smtp_host'],
      ['#sysMailPort', 'smtp_port'], ['#sysMailSecurity', 'smtp_security'], ['#sysMailFrom', 'from_address']]) {
      $(id).disabled = locked(key);
      $(id).title = locked(key) ? 'Definido no .env' : '';
    }
    if (locked('smtp_host')) $('#sysMailPreset').value = 'outro';
    $('#sysMailPreset').disabled = locked('smtp_host');
    const pw = (view.secrets || {}).email_password || {};
    $('#sysMailPassword').disabled = pw.source === 'env';
    $('#sysMailPasswordNow').textContent = pw.configured ? `configurada ${pw.hint || ''} (${pw.source === 'env' ? 'no .env' : 'pelo painel'}) — digite só para trocar` : 'não configurada';
    showAdvanced();
  }

  $('#sysMailPreset').addEventListener('change', showAdvanced);

  $('#sysMailForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const gmail = $('#sysMailPreset').value === 'gmail';
    const wanted = {
      enabled: $('#sysMailEnabled').checked,
      smtp_user: $('#sysMailUser').value.trim(),
      smtp_host: gmail ? GMAIL.host : $('#sysMailHost').value.trim(),
      smtp_port: gmail ? GMAIL.port : Number($('#sysMailPort').value),
      smtp_security: gmail ? GMAIL.security : $('#sysMailSecurity').value,
      from_address: gmail ? '' : $('#sysMailFrom').value.trim(),
    };
    const ids = { enabled: '#sysMailEnabled', smtp_user: '#sysMailUser', smtp_host: '#sysMailHost', smtp_port: '#sysMailPort', smtp_security: '#sysMailSecurity', from_address: '#sysMailFrom' };
    const changes = Object.fromEntries(Object.entries(wanted).filter(([key]) => !$(ids[key]).disabled));   // o que está no .env não é mandado
    const user = wanted.smtp_user || '';
    if (!$('#sysMailTo').disabled && $('#sysMailTo').value.trim()) changes.recipients = $('#sysMailTo').value.split(/[,;\s]+/).filter(Boolean);
    else if (!$('#sysMailTo').disabled && !S.settings.settings.email.recipients_view.length && user) changes.recipients = [user];   // padrão: manda para você mesmo
    try {
      if (Object.keys(changes).length) await ui().api('POST', '/api/settings/email', changes);
      const password = $('#sysMailPassword').value;
      if (password.trim()) await ui().api('POST', '/api/secrets', { name: 'email_password', value: password });
      ui().say('#sysMailMsg', 'E-mail salvo. Use "Enviar e-mail de teste" para conferir.', 'success');
      await loadSettings();
      await loadNotifications();
    } catch (error) { ui().say('#sysMailMsg', error.message, 'error'); }
  });

  $('#sysMailTest').addEventListener('click', async (event) => {
    if (!await ui().confirmDialog({ title: 'Enviar e-mail de teste?', text: 'Manda o relatório atual para os e-mails configurados.', okLabel: 'Enviar teste' })) return;
    await ui().busy(event.target, 'Enviando…', async () => {
      try {
        const data = await ui().api('POST', '/api/notifications/test', { confirm: true, channel: 'email' });
        ui().say('#sysMailMsg', data.event.status === 'pendente' ? 'Teste na fila. O resultado aparece em "Últimas notificações" em alguns segundos.' : `Não enviado: ${data.event.note}`,
          data.event.status === 'pendente' ? 'success' : 'error');
        setTimeout(loadNotifications, 4000);
        setTimeout(loadNotifications, 12000);
      } catch (error) { ui().say('#sysMailMsg', error.message, 'error'); }
    });
  });

  function renderSecrets(secrets) {
    const { el, fill } = ui();
    const rows = Object.entries(SECRET_LABELS).map(([name, [label, hint]]) => {
      const info = secrets[name] || {};
      const input = el('input', { type: name === 'whatsapp_phone_number_id' ? 'text' : 'password', autocomplete: 'off', spellcheck: 'false', 'aria-label': label,
        placeholder: info.configured ? 'configurado — digite para trocar' : 'não configurado' });
      const save = el('button', { class: 'button secondary', type: 'button', text: 'Salvar' });
      const clear = el('button', { class: 'button ghost', type: 'button', text: 'Apagar', disabled: !info.configured || info.source === 'env' });
      const status = info.configured ? `configurado ${info.hint || ''} (${info.source === 'env' ? 'no .env' : 'pelo painel'})` : 'não configurado';
      save.addEventListener('click', () => ui().busy(save, 'Salvando…', async () => {
        try {
          const data = await ui().api('POST', '/api/secrets', { name, value: input.value });
          input.value = '';
          renderSecrets(data.secrets);
          ui().say('#sysSecretsMsg', `${label}: salvo.`, 'success');
          loadNotifications();
        } catch (error) { ui().say('#sysSecretsMsg', error.message, 'error'); }
      }));
      clear.addEventListener('click', async () => {
        if (!await ui().confirmDialog({ title: `Apagar ${label}?`, text: 'O envio pelo WhatsApp para de funcionar até você salvar de novo.', okLabel: 'Apagar' })) return;
        try {
          const data = await ui().api('DELETE', `/api/secrets/${name}`);
          renderSecrets(data.secrets);
          ui().say('#sysSecretsMsg', `${label}: apagado.`, 'success');
        } catch (error) { ui().say('#sysSecretsMsg', error.message, 'error'); }
      });
      return el('div', { class: 'op-secret' }, el('label', { text: label }), el('small', { text: `${status} · ${hint}` }), el('div', { class: 'bk-import-row' }, input, save, clear));
    });
    const verify = secrets.whatsapp_verify_token || {};
    const gen = el('button', { class: 'button secondary', type: 'button', text: verify.configured ? 'Gerar outro' : 'Gerar' });
    const shown = el('code', { class: 'op-token' });
    gen.addEventListener('click', () => ui().busy(gen, 'Gerando…', async () => {
      try {
        const data = await ui().api('POST', '/api/secrets/verify-token', {});
        shown.textContent = data.verify_token;
        ui().say('#sysSecretsMsg', data.note, 'success');
      } catch (error) { ui().say('#sysSecretsMsg', error.message, 'error'); }
    }));
    rows.push(el('div', { class: 'op-secret' }, el('label', { text: 'Token de verificação do webhook (Verify token)' }),
      el('small', { text: `${verify.configured ? `configurado ${verify.hint || ''}` : 'não configurado'} · você cola este valor no painel da Meta, junto com o endereço do webhook` }),
      el('div', { class: 'bk-import-row' }, gen, shown)));
    fill($('#sysSecrets'), rows);
  }

  async function loadNotifications() {
    const { el, fill, whenText } = ui();
    const data = await ui().api('GET', '/api/notifications');
    const status = data.status;
    const box = $('#sysNotifStatus');
    box.className = `bk-protect ${status.state === 'pronto' ? 'ok' : status.state === 'desativado' ? '' : 'warn'}`;
    fill(box, el('p', {}, el('strong', { text: { pronto: '✓ WhatsApp pronto', desativado: 'WhatsApp desligado', aguardando_configuracao: '⚠ Falta configurar', ultimo_envio_falhou: '⚠ O último envio falhou' }[status.state] || status.state }),
      ` — ${status.text}`),
      status.email ? el('p', {}, el('strong', { text: { pronto: '✓ E-mail pronto', desativado: 'E-mail desligado', aguardando_configuracao: '⚠ E-mail: falta configurar', ultimo_envio_falhou: '⚠ E-mail: o último envio falhou' }[status.email.state] || status.email.state }),
        ` — ${status.email.text}`) : null,
      status.last_sent ? el('p', { class: 'hint', text: `Último envio aceito pela API: ${whenText(status.last_sent.at)} (${status.last_sent.title}). "Aceito" quer dizer que a Meta recebeu o pedido; a confirmação de entrega chega pelo webhook, se ele estiver configurado.` }) : null);
    fill($('#sysNotifList'), data.items.length ? data.items.map((item) => el('li', { class: 'op-event' },
      el('span', { class: 'op-ev-time', text: whenText(item.at) }),
      el('span', { class: 'op-ev-main' }, el('strong', { text: item.title }), ` · ${item.kind}`,
        el('span', { class: 'op-ev-detail', text: ` — ${item.deliveries.length ? item.deliveries.map((d) => `${d.to}: ${DELIVERY_TEXT[d.status] || d.status}${d.error ? ` (${d.error})` : ''}`).join('; ') : (DELIVERY_TEXT[item.status] || item.status) + (item.note ? ` — ${item.note}` : '')}` }))))
      : el('li', { class: 'empty-state compact', text: 'Nenhuma notificação ainda.' }));
  }

  $('#sysPreview').addEventListener('click', (event) => ui().busy(event.target, 'Montando…', async () => {
    try {
      const data = await ui().api('GET', '/api/notifications/preview');
      const box = $('#sysPreviewBox');
      box.textContent = data.text;
      box.hidden = false;
    } catch (error) { ui().say('#sysNotifMessage', error.message, 'error'); }
  }));

  $('#sysTest').addEventListener('click', async (event) => {
    if (!await ui().confirmDialog({ title: 'Enviar mensagem de teste?', text: 'Envia o relatório atual para os números configurados. Pela regra da Meta, fora da conversa de 24 h ele vai como modelo aprovado (pode ser cobrado).', okLabel: 'Enviar teste' })) return;
    await ui().busy(event.target, 'Enviando…', async () => {
      try {
        const data = await ui().api('POST', '/api/notifications/test', { confirm: true });
        ui().say('#sysNotifMessage', data.event.status === 'pendente' ? 'Teste na fila de envio. O resultado aparece em "Últimas notificações".' : `Não enviado: ${data.event.note}`, data.event.status === 'pendente' ? 'success' : 'error');
        setTimeout(loadNotifications, 4000);
      } catch (error) { ui().say('#sysNotifMessage', error.message, 'error'); }
    });
  });

  $('#sysNotifForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const changes = { report_enabled: $('#sysReportEnabled').checked, alerts_enabled: $('#sysAlertsEnabled').checked, cooldown_minutes: Number($('#sysCooldown').value) };
    if (!$('#sysNotifEnabled').disabled) changes.enabled = $('#sysNotifEnabled').checked;
    if (!$('#sysRecipients').disabled && $('#sysRecipients').value.trim()) changes.recipients = splitList($('#sysRecipients').value);
    const wa = {};
    for (const [id, key] of [['#sysTplReport', 'template_report'], ['#sysTplAlert', 'template_alert'], ['#sysTplLang', 'template_language'], ['#sysApiVersion', 'api_version']]) {
      if (!$(id).disabled) wa[key] = $(id).value.trim();
    }
    if (!$('#sysTplReportVars').disabled) wa.report_variables = Number($('#sysTplReportVars').value);
    try {
      await ui().api('POST', '/api/settings/notifications', changes);
      if (Object.keys(wa).length) await ui().api('POST', '/api/settings/whatsapp', wa);
      ui().say('#sysNotifFormMsg', 'Preferências salvas.', 'success');
      await loadSettings();
      await loadNotifications();
    } catch (error) { ui().say('#sysNotifFormMsg', error.message, 'error'); }
  });

  $('#sysBotForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const changes = {};
    if (!$('#sysBotEnabled').disabled) changes.enabled = $('#sysBotEnabled').checked;
    if (!$('#sysBotInteractive').disabled) changes.interactive = $('#sysBotInteractive').checked;
    if (!$('#sysBotAdmins').disabled && $('#sysBotAdmins').value.trim()) changes.admins = splitList($('#sysBotAdmins').value);
    if (!$('#sysBotViewers').disabled && $('#sysBotViewers').value.trim()) changes.viewers = splitList($('#sysBotViewers').value);
    try {
      await ui().api('POST', '/api/settings/bot', changes);
      ui().say('#sysBotMsg', 'Bot salvo.', 'success');
      await loadSettings();
    } catch (error) { ui().say('#sysBotMsg', error.message, 'error'); }
  });

  // ------------------------------------------------------------------ servidor e túnel
  async function loadSystem() {
    const { el, fill, TUNNEL_TEXT } = ui();
    const [tunnelInfo, ov] = await Promise.all([ui().api('GET', '/api/tunnel'), ui().api('GET', '/api/overview')]);
    const sup = $('#sysSupervisor');
    sup.className = `bk-protect ${tunnelInfo.supervisor ? 'ok' : 'warn'}`;
    fill(sup, el('p', {}, el('strong', { text: tunnelInfo.supervisor ? '✓ Supervisor ativo' : '⚠ Supervisor ausente' }),
      tunnelInfo.supervisor ? ' — ele reinicia o servidor se cair, cuida do túnel e instala atualizações.' : ' — o CDBS foi iniciado sem o bash start.sh, então reiniciar, túnel e atualizações pelo painel ficam indisponíveis.'));
    const server = ov.supervisor.server || {};
    $('#sysServerState').textContent = tunnelInfo.supervisor ? `${server.state || 'rodando'}${server.message ? ` — ${server.message}` : ''}` : 'rodando (sem supervisor)';
    const tunnel = tunnelInfo.tunnel;
    $('#sysTunnelState').textContent = `${TUNNEL_TEXT[tunnel.state] || tunnel.state}${tunnel.url ? ` — ${tunnel.url}` : ''}${tunnel.message ? ` — ${tunnel.message}` : ''}`;
    for (const id of ['#sysRestart', '#sysTunnelStart', '#sysTunnelRestart', '#sysTunnelStop']) $(id).disabled = !tunnelInfo.supervisor;
  }

  async function supervisorAction(button, path, confirmText, label) {
    if (confirmText && !await ui().confirmDialog({ title: label, text: confirmText, okLabel: label })) return;
    await ui().busy(button, 'Aguardando…', async () => {
      try {
        const data = await ui().api('POST', path, { confirm: true });
        ui().say('#sysOpMessage', 'Pedido enviado ao supervisor…');
        const result = await ui().followRequest(data.request, (r) => ui().say('#sysOpMessage', r.message || 'Em andamento…'));
        ui().say('#sysOpMessage', result.message, result.status === 'ok' ? 'success' : 'error');
      } catch (error) { ui().say('#sysOpMessage', error.message, 'error'); }
      await loadSystem().catch(() => {});
      if (window.CDBSOps) window.CDBSOps.refresh();
    });
  }

  $('#sysRestart').addEventListener('click', (e) => supervisorAction(e.target, '/api/system/restart', 'O painel e os sites ficam fora do ar por alguns segundos enquanto o servidor reinicia.', 'Reiniciar servidor'));
  $('#sysTunnelStart').addEventListener('click', (e) => supervisorAction(e.target, '/api/tunnel/start', '', 'Ligar túnel'));
  $('#sysTunnelRestart').addEventListener('click', (e) => supervisorAction(e.target, '/api/tunnel/restart', 'Os links públicos param por alguns segundos. No Quick Tunnel o endereço MUDA (o novo aparece aqui e vai por WhatsApp, se configurado).', 'Reiniciar túnel'));
  $('#sysTunnelStop').addEventListener('click', (e) => supervisorAction(e.target, '/api/tunnel/stop', 'Os sites continuam no celular, mas ninguém de fora consegue abrir até você ligar de novo.', 'Desligar túnel'));

  $('#sysTunnelForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const changes = {};
    if (!$('#sysTunnelMode').disabled) changes.mode = $('#sysTunnelMode').value;
    if (!$('#sysTunnelAuto').disabled) changes.autostart = $('#sysTunnelAuto').checked;
    try {
      await ui().api('POST', '/api/settings/tunnel', changes);
      ui().say('#sysOpMessage', 'Túnel salvo. O modo novo vale na próxima vez que o túnel ligar.', 'success');
    } catch (error) { ui().say('#sysOpMessage', error.message, 'error'); }
  });

  // ------------------------------------------------------------------ atualizações
  function checksList(report) {
    const { el } = ui();
    return el('ul', { class: 'op-checks' }, (report.checks || []).map((c) => el('li', { class: c.ok ? 'ok' : 'bad', text: `${c.ok ? '✓' : '✕'} ${c.name}: ${c.message}` })));
  }

  function renderJournal(journal) {
    const { el, fill, whenText } = ui();
    const box = $('#sysUpdateJournal');
    if (!journal) { box.hidden = true; return; }
    box.hidden = false;
    fill(box, el('p', {}, el('strong', { text: `${journal.from_version} → ${journal.to_version}: ` }), STEP_TEXT[journal.step] || journal.step),
      journal.message ? el('p', { class: 'hint', text: journal.message }) : null,
      el('ol', { class: 'op-steps' }, (journal.steps || []).map((s) => el('li', { text: `${whenText(s.at)} — ${STEP_TEXT[s.step] || s.step}` }))));
  }

  async function loadUpdates() {
    const { el, fill, whenText } = ui();
    const data = await ui().api('GET', '/api/updates');
    S.updates = data;
    $('#sysVersion').textContent = `Versão instalada: ${data.current.version}${data.current.released ? ` (${data.current.released})` : ''}${data.supervisor ? '' : ' · para instalar pelo painel, inicie o CDBS com bash start.sh (supervisor)'}`;
    renderJournal(data.journal);
    fill($('#sysStaged'), data.staged.map((report) => {
      const install = el('button', { class: 'button primary', type: 'button', text: 'Instalar', disabled: !report.ok || !data.supervisor });
      const discard = el('button', { class: 'button ghost', type: 'button', text: 'Descartar' });
      install.addEventListener('click', () => installPackage(install, report));
      discard.addEventListener('click', () => ui().busy(discard, 'Descartando…', async () => {
        try { await ui().api('DELETE', `/api/updates/${report.id}`); await loadUpdates(); } catch (error) { ui().say('#sysUpdateMsg', error.message, 'error'); }
      }));
      return el('div', { class: `op-package ${report.ok ? 'ok' : 'bad'}` },
        el('p', {}, el('strong', { text: `Pacote ${report.version || '(sem versão)'}` }), ` · recebido ${whenText(report.received_at)} · ${report.files} arquivos · ${report.relation} que a instalada (${report.current_version})`),
        report.ok ? el('p', { class: 'bk-strong', text: '✓ Passou na validação.' }) : el('p', { class: 'bk-err', text: `✕ Recusado: ${(report.problems || []).join('; ')}` }),
        checksList(report), el('div', { class: 'op-row-actions' }, install, discard));
    }));
    fill($('#sysSnapshots'), data.snapshots.length ? data.snapshots.map((snap) => {
      const back = el('button', { class: 'button secondary', type: 'button', text: 'Voltar para esta', disabled: !data.supervisor });
      back.addEventListener('click', () => rollback(back, snap));
      return el('li', { class: 'op-event' }, el('span', { class: 'op-ev-time', text: whenText(snap.created_at) }),
        el('span', { class: 'op-ev-main' }, el('strong', { text: `versão ${snap.version}` }), ` · ${snap.files} arquivos · ${snap.reason || ''}`), back);
    }) : el('li', { class: 'empty-state compact', text: 'Nenhuma cópia guardada ainda (a primeira é feita na primeira atualização pelo painel).' }));
    fill($('#sysUpdateHistory'), data.history.length ? data.history.map((h) => el('li', { class: 'op-event' },
      el('span', { class: 'op-ev-time', text: whenText(h.finished_at) }),
      el('span', { class: 'op-ev-main' }, el('strong', { text: `${h.from_version} → ${h.to_version}` }), ` · ${STEP_TEXT[h.step] || h.step}`, h.message ? el('span', { class: 'op-ev-detail', text: ` — ${h.message}` }) : null)))
      : el('li', { class: 'empty-state compact', text: 'Nenhuma atualização pelo painel ainda.' }));
  }

  async function installPackage(button, report) {
    const older = report.relation !== 'mais nova';
    const answer = await ui().confirmDialog({
      title: `Instalar a versão ${report.version}?`,
      text: 'O CDBS faz uma cópia conferida do programa atual, para o servidor, instala, inicia e confere a saúde. Se algo falhar, volta sozinho para a versão atual. Seus sites, catálogos, senhas, backups e o .env não são tocados. O painel fica fora do ar por alguns segundos.',
      okLabel: 'Instalar', typed: 'INSTALAR', extra: older ? `O pacote é ${report.relation} que a instalada: confirmo que quero ${report.relation === 'igual' ? 'reinstalar' : 'voltar para uma versão mais antiga'}.` : '' });
    if (!answer) return;
    if (older && !answer.extra) { ui().say('#sysUpdateMsg', 'Marque a confirmação de versão igual/mais antiga para continuar.', 'error'); return; }
    await followUpdate(button, '/api/updates/' + report.id + '/install', { confirm_text: 'INSTALAR', allow_same_or_older: older });
  }

  async function rollback(button, snap) {
    const answer = await ui().confirmDialog({ title: `Voltar para a versão ${snap.version}?`, text: 'Mesmo processo de uma atualização: cópia do programa atual, troca, verificação de saúde e volta automática se falhar. Os dados não são tocados.', okLabel: 'Voltar versão', typed: 'VOLTAR' });
    if (!answer) return;
    await followUpdate(button, '/api/updates/rollback', { snapshot: snap.id, confirm_text: 'VOLTAR' });
  }

  async function followUpdate(button, path, body) {
    await ui().busy(button, 'Instalando…', async () => {
      try {
        S.followingUpdate = true;
        const data = await ui().api('POST', path, body);
        ui().say('#sysUpdateMsg', 'Pedido enviado ao supervisor. Não feche o painel; ele volta sozinho depois do reinício.');
        const result = await ui().followRequest(data.request, (r) => {
          ui().say('#sysUpdateMsg', r.message || 'Em andamento…');
          ui().api('GET', '/api/updates').then((u) => renderJournal(u.journal)).catch(() => {});
        }, 300);
        ui().say('#sysUpdateMsg', result.message, result.status === 'ok' ? 'success' : 'error');
      } catch (error) {
        ui().say('#sysUpdateMsg', error.message, 'error');
      } finally {
        S.followingUpdate = false;
      }
      await loadUpdates().catch(() => {});
    });
  }

  $('#sysUpdateForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const file = $('#sysUpdateFile').files[0];
    if (!file) { ui().say('#sysUpdateMsg', 'Escolha o arquivo .zip do pacote.', 'error'); return; }
    const button = event.target.querySelector('button');
    await ui().busy(button, 'Conferindo…', async () => {
      try {
        const data = await ui().api('POST', '/api/updates/upload', undefined, file);
        $('#sysUpdateForm').reset();
        ui().say('#sysUpdateMsg', data.package.ok ? `Pacote ${data.package.version} conferido: pode instalar.` : `Pacote recusado: ${data.package.problems.join('; ')}`, data.package.ok ? 'success' : 'error');
        await loadUpdates();
      } catch (error) { ui().say('#sysUpdateMsg', error.message, 'error'); }
    });
  });

  // ------------------------------------------------------------------ ciclo
  async function load() {
    S.stopped = false;
    for (const id of ['#sysNotifications', '#sysSystem']) $(id).hidden = false;
    await Promise.allSettled([loadSettings(), loadNotifications(), loadSystem(), loadUpdates()]);
    clearInterval(S.timer);
    S.timer = setInterval(() => {
      if (document.visibilityState !== 'visible' || S.followingUpdate) return;
      loadNotifications().catch(() => {});
      loadSystem().catch(() => {});
    }, 30000);
  }

  function stop() {
    S.stopped = true;
    clearInterval(S.timer);
    for (const id of ['#sysNotifications', '#sysSystem']) { const node = $(id); if (node) node.hidden = true; }
    const box = $('#sysPreviewBox');
    if (box) { box.textContent = ''; box.hidden = true; }
    const secrets = $('#sysSecrets');
    if (secrets) secrets.replaceChildren();
  }

  window.CDBSSystem = { load, stop };
})();
