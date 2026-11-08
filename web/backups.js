// CDBS.OS — "Central de backups" (painel privado).
// Segurança: tudo que vem do servidor entra na tela por textContent (nunca innerHTML); nenhum segredo é guardado no navegador
// (a senha de recuperação só existe na memória desta página, durante o pedido, e é apagada dos campos em seguida);
// os códigos de confirmação (restaurar, excluir, limpar) vivem só na memória desta página e valem uma única vez.
(() => {
  'use strict';

  const ACTIVE = ['CRIANDO', 'VALIDANDO', 'PENDENTE', 'PREPARANDO', 'APLICANDO', 'VERIFICANDO'];
  const ICON = { CRIANDO: '◔', VALIDANDO: '◔', PENDENTE: '◔', PREPARANDO: '◔', APLICANDO: '◔', VERIFICANDO: '◔', CONCLUIDO: '✓', FALHOU: '✕', INVALIDO: '⚠', REVERTIDO: '↩' };
  const TONE = { CRIANDO: 'wait', VALIDANDO: 'wait', PENDENTE: 'wait', PREPARANDO: 'wait', APLICANDO: 'wait', VERIFICANDO: 'wait', CONCLUIDO: 'ok', FALHOU: 'fail', INVALIDO: 'fail', REVERTIDO: 'warn' };
  const LABEL = { CRIANDO: 'CRIANDO', VALIDANDO: 'VALIDANDO', PENDENTE: 'AGUARDANDO', PREPARANDO: 'PREPARANDO', APLICANDO: 'TROCANDO ARQUIVOS', VERIFICANDO: 'CONFERINDO', CONCLUIDO: 'CONCLUÍDO', FALHOU: 'FALHOU', INVALIDO: 'INVÁLIDO', REVERTIDO: 'REVERTIDO (versão anterior recolocada)' };
  const KIND = { manual: 'Manual', seguranca: 'Segurança (antes de restaurar)', importado: 'Importado', migrado: 'Protegido (cópia de um legado)', restauracao: 'Restauração' };
  const RETENTION_LABEL = { off: 'Manter tudo (padrão)', keep_last: 'Manter só os N mais recentes por loja', keep_days: 'Manter só os últimos N dias' };
  const PASSWORD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';

  const S = { overview: null, slug: '', detail: null, timer: null, busy: false, stopped: true, summary: null, token: 0, running: false, del: null, preview: null, generated: '' };
  const $ = (selector) => document.querySelector(selector);

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

  // Troca o conteúdo de um elemento ignorando null/false (replaceChildren escreveria a palavra "null" na tela).
  function fill(node, ...children) {
    node.replaceChildren(...children.flat(Infinity).filter((child) => child != null && child !== false));
  }

  async function bf(method, path, body, raw, extraHeaders) {
    // X-CDBS-Request: cabeçalho que outro site não consegue enviar (defesa extra além da verificação de origem feita no servidor).
    const options = { method, credentials: 'same-origin', headers: { 'X-CDBS-Request': '1', ...(extraHeaders || {}) } };
    if (raw) {
      options.headers['Content-Type'] = 'application/octet-stream';
      options.body = raw;
    } else if (method !== 'GET') {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body === undefined ? {} : body);
    }
    const response = await fetch('/api/backups' + path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `Erro ${response.status}`);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function say(text, kind = '') {
    const target = $('#bkMessage');
    if (!target) return;
    target.textContent = text;
    target.className = `message ${kind}`.trim();
  }

  function whenText(seconds) {
    if (!seconds) return '—';
    return new Date(seconds * 1000).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function bytes(n) {
    if (n == null) return '—';
    if (n >= 1073741824) return `${(n / 1073741824).toFixed(1)} GB`;
    if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
    if (n === 0) return '0 KB';
    return `${Math.max(1, Math.round(n / 1024))} KB`;
  }

  function span(seconds) {
    if (seconds == null) return '—';
    return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  }

  function badge(state) {
    return el('span', { class: `state state-${TONE[state] || 'off'}`, text: `${ICON[state] || '•'} ${LABEL[state] || state}` });
  }

  function partsText(parts) {
    const names = { site: 'site', catalog: 'catálogo' };
    return ['site', 'catalog'].filter((p) => (parts || []).includes(p)).map((p) => names[p]).join(' + ') || '—';
  }

  function sessionEnded(error) {
    if (error.status === 401) { stop(); return true; }
    return false;
  }

  function errorText(error) {
    const next = error.data && error.data.next_action ? ` ${error.data.next_action}` : '';
    return `${error.message}${next}`;
  }

  function activeAnywhere() {
    const ov = S.overview;
    return !!(ov && ov.stores.some((row) => row.active && ACTIVE.includes(row.active.state)));
  }

  // ------------------------------------------------------------------ carregamento
  async function load() {
    S.stopped = false;
    const section = $('#bkSection');
    if (section) section.hidden = false;
    await refresh();
  }

  async function refresh() {
    if (S.stopped) return;
    const ticket = ++S.token;
    try {
      const overview = await bf('GET', '/overview');
      if (ticket !== S.token || S.stopped) return;
      const wasBusy = S.running;
      S.overview = overview;
      S.running = activeAnywhere();
      if (wasBusy && !S.running && window.CDBSLists) window.CDBSLists.reload().catch(() => {});   // terminou uma operação: site/lojas podem ter mudado
      if (S.slug && !overview.stores.some((row) => row.slug === S.slug)) S.slug = '';
      if (S.slug) {
        const detail = await bf('GET', `/${encodeURIComponent(S.slug)}`);
        if (ticket !== S.token || S.stopped) return;
        S.detail = detail;
      } else {
        S.detail = null;
      }
      render();
      schedule();
    } catch (error) {
      if (!sessionEnded(error)) say(errorText(error), 'error');
    }
  }

  function schedule() {
    clearTimeout(S.timer);
    S.timer = null;
    if (S.stopped) return;
    // Enquanto há uma operação rodando a tela acompanha de perto; fora disso não consulta nada sozinha.
    if (activeAnywhere() || (S.detail && S.detail.active && ACTIVE.includes(S.detail.active.state))) S.timer = setTimeout(refresh, 1500);
  }

  function stop() {
    S.stopped = true;
    S.token += 1;
    clearTimeout(S.timer);
    S.timer = null;
    S.overview = null;
    S.detail = null;
    S.slug = '';
    S.summary = null;
    S.del = null;
    S.preview = null;
    S.generated = '';
    for (const id of ['#bkRestoreDialog', '#bkDeleteDialog', '#bkPasswordDialog']) { const dialog = $(id); if (dialog && dialog.open) dialog.close(); }
    for (const id of ['#bkRestorePassword', '#bkPasswordInput', '#bkConfirmInput', '#bkDeleteInput']) { const input = $(id); if (input) input.value = ''; }
    for (const id of ['#bkAlert', '#bkProtection', '#bkStores', '#bkDetail', '#bkRetention', '#bkStorage', '#bkReminder']) { const node = $(id); if (node) node.replaceChildren(); }
    say('');
    const section = $('#bkSection');
    if (section) section.hidden = true;
  }

  // ------------------------------------------------------------------ senha de recuperação
  function randomPassword() {
    // 4 grupos de 5 caracteres sorteados com o gerador seguro do navegador (sem letras que se confundem): ~116 bits
    const limit = 256 - (256 % PASSWORD_ALPHABET.length);
    const out = [];
    while (out.length < 20) {
      const chunk = new Uint8Array(32);
      crypto.getRandomValues(chunk);
      for (const value of chunk) if (value < limit && out.length < 20) out.push(PASSWORD_ALPHABET[value % PASSWORD_ALPHABET.length]);
    }
    return [0, 5, 10, 15].map((i) => out.slice(i, i + 5).join('')).join('-');
  }

  // Pergunta a senha numa janela. Devolve a senha digitada, '' (se escolheu "só o arquivo") ou null (cancelou).
  function askPassword({ title, text, confirmLabel, skipLabel }) {
    return new Promise((resolve) => {
      const dialog = $('#bkPasswordDialog');
      const input = $('#bkPasswordInput');
      const ok = $('#bkPasswordOk');
      const cancel = $('#bkPasswordCancel');
      const skip = $('#bkPasswordSkip');
      $('#bkPasswordTitle').textContent = title;
      $('#bkPasswordText').textContent = text;
      $('#bkPasswordMsg').textContent = '';
      ok.textContent = confirmLabel || 'Continuar';
      skip.hidden = !skipLabel;
      if (skipLabel) skip.textContent = skipLabel;
      input.value = '';
      ok.disabled = true;
      let finished = false;
      const finish = (value) => {
        if (finished) return;
        finished = true;
        input.removeEventListener('input', onInput);
        input.removeEventListener('keydown', onKey);
        ok.removeEventListener('click', onOk);
        cancel.removeEventListener('click', onCancel);
        skip.removeEventListener('click', onSkip);
        dialog.removeEventListener('cancel', onCancel);
        input.value = '';
        if (dialog.open) dialog.close();
        resolve(value);
      };
      const onInput = () => { ok.disabled = !input.value; };
      const onOk = () => { if (input.value) finish(input.value); };
      const onKey = (event) => { if (event.key === 'Enter') { event.preventDefault(); onOk(); } };
      const onCancel = () => finish(null);
      const onSkip = () => finish('');
      input.addEventListener('input', onInput);
      input.addEventListener('keydown', onKey);
      ok.addEventListener('click', onOk);
      cancel.addEventListener('click', onCancel);
      skip.addEventListener('click', onSkip);
      dialog.addEventListener('cancel', onCancel);
      if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
      input.focus();
    });
  }

  async function setupProtection(button) {
    const password = $('#bkSetupPassword').value;
    const confirm = $('#bkSetupConfirm').value;
    if (!$('#bkSetupAck').checked) { say('Marque a confirmação de que você guardou a senha em local seguro.', 'error'); return; }
    await run(button, 'Ativando…', async () => {
      await bf('POST', '/_protecao/setup', { password, confirm });
      for (const id of ['#bkSetupPassword', '#bkSetupConfirm']) $(id).value = '';
      S.generated = '';
      say('Proteção ativada. Os próximos backups serão criptografados. Guarde a senha: ela não pode ser recuperada.', 'success');
      await refresh();
    });
  }

  // ------------------------------------------------------------------ ações
  async function run(button, label, work) {
    if (S.busy) return;
    S.busy = true;
    const original = button ? button.textContent : '';
    if (button) { button.disabled = true; if (label) button.textContent = label; }
    try {
      await work();
    } catch (error) {
      if (!sessionEnded(error)) say(errorText(error), 'error');
    } finally {
      S.busy = false;
      if (button && button.isConnected) { button.disabled = false; button.textContent = original; }
    }
  }

  async function createBackup(slug, button) {
    if (!protectionReady()) { say(protectionBlockText(), 'error'); return; }
    const password = await askPassword({ title: 'Senha de recuperação', text: `Digite a senha de recuperação para criar um backup protegido de “${slug}”.`, confirmLabel: 'Criar backup' });
    if (password == null) return;
    return run(button, 'Iniciando…', async () => {
      say('Criando o backup, criptografando e conferindo a integridade…');
      const data = await bf('POST', `/${encodeURIComponent(slug)}/create`, { password });
      say(data.deduplicated ? 'Já havia um backup em andamento nesta loja; acompanhe abaixo.' : 'Backup iniciado. Acompanhe o andamento abaixo.', 'success');
      await refresh();
    });
  }

  async function validateBackup(slug, rec, button) {
    let password = '';
    if (rec.protection && rec.protection.state === 'CRIPTOGRAFADO') {
      password = await askPassword({
        title: 'Verificar backup protegido',
        text: 'Com a senha, o CDBS abre o backup inteiro e confere cada arquivo. Sem a senha, só confere o arquivo (tamanho e impressão digital), o que NÃO prova que o conteúdo abre.',
        confirmLabel: 'Verificar tudo', skipLabel: 'Só o arquivo (sem senha)'
      });
      if (password == null) return;
    }
    return run(button, 'Verificando…', async () => {
      const data = await bf('POST', `/${encodeURIComponent(slug)}/${rec.id}/validate`, password ? { password } : {});
      const ok = data.backup.state === 'CONCLUIDO';
      const detail = data.backup.validation && data.backup.validation.message ? ` ${data.backup.validation.message}` : '';
      say(ok ? `Verificação concluída.${detail}` : `Backup inválido: ${data.backup.error_message || 'não confere com o inventário.'}`, ok ? 'success' : 'error');
      await refresh();
    });
  }

  async function protectBackup(slug, rec, button) {
    if (!protectionReady()) { say(protectionBlockText(), 'error'); return; }
    const password = await askPassword({
      title: 'Proteger backup legado',
      text: 'O CDBS cria uma CÓPIA criptografada deste backup. O arquivo original (sem criptografia) é mantido; depois de conferir a cópia, você decide se quer excluí-lo.',
      confirmLabel: 'Criar cópia protegida'
    });
    if (password == null) return;
    return run(button, 'Protegendo…', async () => {
      await bf('POST', `/${encodeURIComponent(slug)}/${rec.id}/protect`, { password });
      say('Cópia protegida iniciada. Acompanhe abaixo.', 'success');
      await refresh();
    });
  }

  async function importBackup(slug, input, button) {
    const file = input.files && input.files[0];
    if (!file) { say('Escolha o arquivo de um backup exportado pelo CDBS (.cdbk, ou .zip de uma versão antiga).', 'error'); return; }
    let headers;
    if (file.name.toLowerCase().endsWith('.cdbk')) {
      const password = await askPassword({ title: 'Importar backup protegido', text: 'Digite a senha de recuperação deste CDBS. O arquivo só abre com a mesma senha com que foi criado.', confirmLabel: 'Importar' });
      if (password == null) return;
      headers = { 'X-CDBS-Senha': password };
    }
    await run(button, 'Enviando…', async () => {
      say('Enviando e conferindo o arquivo…');
      await bf('POST', `/${encodeURIComponent(slug)}/import`, undefined, file, headers);
      input.value = '';
      say('Backup importado e validado. Ele já aparece na lista.', 'success');
      await refresh();
    });
  }

  // ---- restaurar / recuperar loja
  async function openRestore(slug, id, button) {
    await run(button, 'Preparando…', async () => {
      const data = await bf('POST', `/${encodeURIComponent(slug)}/${id}/restore/summary`, {});
      S.summary = data.summary;
      fillDialog(data.summary);
      const dialog = $('#bkRestoreDialog');
      if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    });
  }

  function fillDialog(summary) {
    const body = $('#bkRestoreBody');
    const when = whenText(summary.backup.created_at);
    const recover = summary.mode === 'recuperar_loja';
    $('#bkDialogTitle').textContent = recover ? 'Recuperar loja removida' : 'Restaurar backup';
    $('#bkConfirmBtn').textContent = recover ? 'Recuperar loja agora' : 'Restaurar agora';
    fill(body,
      el('p', {}, 'Loja: ', el('strong', { text: summary.store_name }), ' (', el('code', { text: summary.store }), ')'),
      el('p', {}, 'Backup: ', el('strong', { text: `${summary.backup.short_id} — ${when}` }), ` · ${bytes(summary.backup.size_bytes)} · ${partsText(summary.backup.parts)}`),
      summary.backup.protection ? el('p', { class: 'bk-meta', text: summary.backup.protection.label }) : null,
      el('p', { class: 'bk-warning', text: `⚠ ${summary.warning}` }),
      recover ? el('ul', { class: 'bk-recover' },
        el('li', { text: 'Voltam: arquivos do site, produtos, fotos, nome e WhatsApp da loja.' }),
        el('li', { text: 'NÃO voltam: senha do cliente, sessões abertas e qualquer acesso antigo.' }),
        el('li', { text: 'A loja fica DESATIVADA (site, catálogo e fotos invisíveis ao público; o cliente não consegue entrar) até você gerar uma nova senha em “Catálogo dos clientes”.' })) : null,
      el('p', {}, summary.will_create_safety_backup
        ? 'Será criado um backup de segurança (protegido) do estado atual antes de qualquer troca. Se algo falhar, a versão atual volta sozinha.'
        : 'Esta loja não tem arquivos atuais; não há o que proteger antes da troca.'),
      summary.space && summary.space.enough === false
        ? el('p', { class: 'bk-warning', text: `Espaço livre insuficiente: precisa de ${bytes(summary.space.needed_bytes)} e há ${bytes(summary.space.free_bytes)}. A operação será recusada sem alterar nada.` })
        : el('p', { class: 'hint-text', text: `Espaço necessário: ${bytes(summary.space.needed_bytes)} · livre: ${bytes(summary.space.free_bytes)}.` }),
      el('p', {}, 'Para confirmar, digite o nome da loja: ', el('code', { text: summary.confirm_text }))
    );
    $('#bkConfirmInput').value = '';
    $('#bkRestorePassword').value = '';
    $('#bkPasswordField').hidden = !summary.needs_password;
    $('#bkRecreateField').hidden = !recover;
    $('#bkRecreateAck').checked = false;
    $('#bkConfirmBtn').disabled = true;
    $('#bkDialogMessage').textContent = '';
  }

  function restoreReady() {
    const summary = S.summary;
    if (!summary) return false;
    if ($('#bkConfirmInput').value.trim() !== summary.confirm_text) return false;
    if (summary.needs_password && !$('#bkRestorePassword').value) return false;
    if (summary.mode === 'recuperar_loja' && !$('#bkRecreateAck').checked) return false;
    return true;
  }

  function closeDialog() {
    S.summary = null;
    const dialog = $('#bkRestoreDialog');
    if (dialog.open) dialog.close();
    $('#bkConfirmInput').value = '';
    $('#bkRestorePassword').value = '';
  }

  async function confirmRestore() {
    const summary = S.summary;
    if (!restoreReady()) return;
    const button = $('#bkConfirmBtn');
    await run(button, 'Trabalhando…', async () => {
      const body = { confirm_token: summary.confirm_token, confirm_slug: $('#bkConfirmInput').value.trim() };
      if (summary.needs_password) body.password = $('#bkRestorePassword').value;
      if (summary.mode === 'recuperar_loja') body.confirm_recreate = $('#bkRecreateAck').checked;
      try {
        await bf('POST', `/${encodeURIComponent(summary.store)}/${summary.backup.id}/restore`, body);
      } catch (error) {
        $('#bkDialogMessage').textContent = errorText(error);
        if (error.data && error.data.code === 'SENHA_INCORRETA') $('#bkRestorePassword').value = '';
        throw error;
      }
      closeDialog();
      say(summary.mode === 'recuperar_loja' ? 'Recuperação iniciada. Não feche o aplicativo; o andamento aparece abaixo.' : 'Restauração iniciada. Não feche o aplicativo; o andamento aparece abaixo.', 'success');
      S.slug = summary.store;
      await refresh();
    });
  }

  // ---- excluir backup
  async function openDelete(slug, id, button) {
    await run(button, 'Preparando…', async () => {
      const data = await bf('POST', `/${encodeURIComponent(slug)}/${id}/delete/summary`, {});
      S.del = data.summary;
      const summary = data.summary;
      fill($('#bkDeleteBody'),
        el('p', {}, 'Loja: ', el('code', { text: summary.store }), ' · Backup: ', el('strong', { text: `${summary.backup.short_id} — ${whenText(summary.backup.started_at)}` }), ` · ${bytes(summary.backup.size_bytes)}`),
        summary.backup.protection ? el('p', { class: 'bk-meta', text: summary.backup.protection.label }) : null,
        el('p', { class: 'bk-warning', text: `⚠ ${summary.warning}` }),
        summary.is_last_validated ? el('p', { class: 'bk-warning bk-strong', text: `⚠ ${summary.last_warning}` }) : null,
        el('p', {}, 'Para confirmar, digite o identificador do backup: ', el('code', { text: summary.confirm_text }))
      );
      $('#bkDeleteInput').value = '';
      $('#bkDeleteLastField').hidden = !summary.is_last_validated;
      $('#bkDeleteLastAck').checked = false;
      $('#bkDeleteMsg').textContent = '';
      $('#bkDeleteBtn').disabled = true;
      const dialog = $('#bkDeleteDialog');
      if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    });
  }

  function deleteReady() {
    const summary = S.del;
    if (!summary) return false;
    if ($('#bkDeleteInput').value.trim().toLowerCase() !== summary.confirm_text) return false;
    if (summary.is_last_validated && !$('#bkDeleteLastAck').checked) return false;
    return true;
  }

  function closeDelete() {
    S.del = null;
    const dialog = $('#bkDeleteDialog');
    if (dialog.open) dialog.close();
    $('#bkDeleteInput').value = '';
  }

  async function confirmDelete() {
    const summary = S.del;
    if (!deleteReady()) return;
    await run($('#bkDeleteBtn'), 'Excluindo…', async () => {
      try {
        await bf('POST', `/${encodeURIComponent(summary.store)}/${summary.backup.id}/delete`, {
          confirm_token: summary.confirm_token, confirm_text: $('#bkDeleteInput').value.trim(), confirm_last: summary.is_last_validated ? $('#bkDeleteLastAck').checked : false
        });
      } catch (error) {
        $('#bkDeleteMsg').textContent = errorText(error);
        throw error;
      }
      closeDelete();
      say(`Backup ${summary.backup.short_id} excluído. O site e o catálogo não foram alterados.`, 'success');
      await refresh();
    });
  }

  // ---- retenção
  async function saveRetention(button) {
    const mode = (document.querySelector('input[name="bkRetMode"]:checked') || {}).value || 'off';
    const body = { mode };
    const last = parseInt($('#bkRetLast').value, 10);
    const days = parseInt($('#bkRetDays').value, 10);
    if (Number.isInteger(last)) body.keep_last = last;
    if (Number.isInteger(days)) body.keep_days = days;
    await run(button, 'Salvando…', async () => {
      await bf('POST', '/_retencao', body);
      S.preview = null;
      say('Política salva. Nada é apagado automaticamente; use “Ver o que seria apagado” quando quiser limpar.', 'success');
      await refresh();
    });
  }

  async function previewRetention(button) {
    await run(button, 'Calculando…', async () => {
      const data = await bf('POST', '/_retencao/preview', {});
      S.preview = data.preview;
      renderRetention();
    });
  }

  async function applyRetention(button) {
    const preview = S.preview;
    if (!preview) return;
    const text = $('#bkRetConfirm').value.trim();
    await run(button, 'Apagando…', async () => {
      const data = await bf('POST', '/_retencao/apply', { confirm_token: preview.confirm_token, confirm_text: text });
      S.preview = null;
      const result = data.result;
      say(`${result.deleted.length} backup(s) excluído(s), ${bytes(result.freed_bytes)} liberados.${result.skipped.length ? ` ${result.skipped.length} ficaram para depois (loja ocupada).` : ''}`, 'success');
      await refresh();
    });
  }

  // ------------------------------------------------------------------ desenho
  function protectionReady() {
    const p = S.overview && S.overview.protection;
    return !!(p && p.available && p.configured);
  }

  function protectionBlockText() {
    const p = S.overview && S.overview.protection;
    if (p && !p.available) return 'Criptografia indisponível neste aparelho: novos backups estão bloqueados (não serão criados sem proteção). Veja o aviso no topo da Central.';
    return 'Antes do primeiro backup, defina a senha de recuperação na caixa “Proteção dos backups”.';
  }

  function render() {
    renderAlert();
    renderProtection();
    renderStores();
    renderDetail();
    renderRetention();
    renderStorage();
    renderReminder();
  }

  function renderAlert() {
    const box = $('#bkAlert');
    const rows = S.overview.stores.filter((row) => row.reminder.overdue);
    const legacy = S.overview.storage.legacy_count || 0;
    const items = [];
    if (rows.length) {
      items.push(el('strong', { text: `⚠ ${rows.length === 1 ? '1 loja precisa' : `${rows.length} lojas precisam`} de backup` }),
        el('ul', {}, rows.map((row) => el('li', {}, el('code', { text: row.slug }), ` — ${row.reminder.message}`))),
        el('span', { class: 'hint-text', text: 'É só um lembrete: nada é bloqueado. Ele é registrado uma vez por dia, não a cada vez que você abre o painel.' }));
    }
    if (legacy) {
      items.push(el('strong', { text: `⚠ ${legacy} backup(s) LEGADO(s), sem criptografia` }),
        el('span', { class: 'hint-text', text: 'Foram feitos pela versão anterior e continuam funcionando, mas quem obtiver o arquivo consegue ler. Abra a loja e use “Proteger” em cada um. Nada é convertido ou apagado sozinho.' }));
    }
    if (!items.length) { box.replaceChildren(); box.hidden = true; return; }
    box.hidden = false;
    box.replaceChildren(...items);
  }

  function renderProtection() {
    const box = $('#bkProtection');
    const p = S.overview.protection;
    if (!p.available) {
      fill(box,
        el('strong', { text: '⚠ Criptografia indisponível — novos backups estão BLOQUEADOS' }),
        el('p', { text: p.reason || 'A biblioteca de criptografia não está instalada.' }),
        el('p', { class: 'hint-text', text: 'No Termux: pkg install python-cryptography  (depois reinicie o CDBS). Os backups já existentes continuam listados; os legados ainda podem ser restaurados e baixados. O CDBS não cria backup sem proteção para parecer que funcionou.' }));
      box.className = 'bk-protect bad';
      return;
    }
    if (!p.configured) {
      const password = el('input', { id: 'bkSetupPassword', type: 'password', autocomplete: 'new-password', autocapitalize: 'off', spellcheck: 'false', maxlength: '256', 'aria-label': 'Senha de recuperação' });
      const confirm = el('input', { id: 'bkSetupConfirm', type: 'password', autocomplete: 'new-password', autocapitalize: 'off', spellcheck: 'false', maxlength: '256', 'aria-label': 'Repita a senha' });
      const shown = el('code', { id: 'bkGenerated', class: 'bk-generated', text: S.generated });
      box.className = 'bk-protect warn';
      fill(box,
        el('strong', { text: 'Proteção dos backups: defina a senha de recuperação' }),
        el('p', { text: `Os backups passam a ser criptografados (${p.cipher}, chave derivada da senha com scrypt). A senha NÃO é guardada em lugar nenhum e NÃO pode ser recuperada: sem ela, os backups não abrem — nem para o CDBS, nem para quem o desenvolveu.` }),
        el('p', { class: 'bk-warning', text: '⚠ Guarde a senha agora, num gerenciador de senhas ou anotada em papel, FORA deste celular. Se o celular quebrar e a senha estiver só nele, os backups baixados ficam inúteis.' }),
        el('div', { class: 'field' }, el('label', { for: 'bkSetupPassword', text: `Senha de recuperação (mínimo ${p.min_chars} caracteres)` }), password),
        el('div', { class: 'field' }, el('label', { for: 'bkSetupConfirm', text: 'Repita a senha' }), confirm),
        el('div', { class: 'bk-store-actions' },
          el('button', { class: 'button secondary', type: 'button', onclick: () => { S.generated = randomPassword(); password.value = S.generated; confirm.value = S.generated; shown.textContent = S.generated; } }, 'Gerar senha forte'),
          el('button', { class: 'button secondary', type: 'button', onclick: async () => { try { await navigator.clipboard.writeText(password.value); say('Senha copiada. Cole no seu gerenciador de senhas e limpe a área de transferência depois.', 'success'); } catch (e) { say('Não foi possível copiar sozinho. Selecione o texto e copie.', 'error'); } } }, 'Copiar')),
        shown,
        el('label', { class: 'bk-check' }, el('input', { id: 'bkSetupAck', type: 'checkbox' }), ' Guardei a senha em local seguro e entendo que, se eu a perder, os backups protegidos não poderão ser abertos.'),
        el('button', { class: 'button primary', type: 'button', onclick: (e) => setupProtection(e.currentTarget) }, 'Ativar proteção'));
      return;
    }
    box.className = 'bk-protect ok';
    fill(box,
      el('strong', { text: '🔒 Proteção ativa' }),
      el('p', { class: 'bk-meta', text: `Formato ${p.format} v${p.version} · ${p.cipher} · ${p.kdf} · definida em ${whenText(p.created_at)}.` }),
      el('p', { class: 'hint-text', text: p.warning }));
  }

  function renderStores() {
    const list = $('#bkStores');
    const rows = S.overview.stores;
    if (!rows.length) {
      list.replaceChildren(el('div', { class: 'empty-state compact' }, el('p', { text: 'Nenhum site ou catálogo ainda. Publique um site para poder criar backups.' })));
      return;
    }
    list.replaceChildren(...rows.map((row) => {
      const running = row.active && ACTIVE.includes(row.active.state);
      const selected = row.slug === S.slug;
      const removed = !row.has_catalog && row.backup_count > 0 && !row.has_site;
      return el('article', { class: `bk-store${selected ? ' selected' : ''}${row.reminder.overdue ? ' overdue' : ''}`, 'data-slug': row.slug },
        el('div', { class: 'bk-store-main' },
          el('strong', { text: row.slug }),
          el('span', { class: 'bk-meta', text: `${row.has_site ? 'site' : 'sem site'} · ${row.has_catalog ? 'catálogo' : 'sem catálogo'}` }),
          row.store_pending ? el('span', { class: 'bk-late', text: '⏸ Loja DESATIVADA: recuperada de um backup, aguardando nova senha (invisível ao público).' }) : null,
          removed ? el('span', { class: 'bk-meta', text: 'Loja removida — os backups continuam aqui e permitem recuperá-la.' }) : null,
          el('span', { class: 'bk-meta', text: row.last_validated_at ? `Último backup validado: ${whenText(row.last_validated_at)}` : 'Nenhum backup validado ainda' }),
          el('span', { class: 'bk-meta' }, 'Última tentativa: ', row.last_state ? badge(row.last_state) : '—'),
          el('span', { class: 'bk-meta', text: `${row.backup_count} backup(s) · ${bytes(row.backup_bytes)} guardados (${row.encrypted_count} protegido(s), ${row.legacy_count} legado(s))` }),
          row.reminder.overdue ? el('span', { class: 'bk-late', text: `⚠ ${row.reminder.message}` }) : null,
          running ? el('span', { class: 'bk-meta' }, 'Em andamento: ', badge(row.active.state)) : null
        ),
        el('div', { class: 'bk-store-actions' },
          el('button', { class: 'button primary', type: 'button', disabled: running || !(row.has_site || row.has_catalog), 'data-act': 'create', onclick: (e) => createBackup(row.slug, e.currentTarget) }, 'Criar backup'),
          el('button', { class: 'button secondary', type: 'button', 'data-act': 'open', onclick: () => { S.slug = selected ? '' : row.slug; refresh(); } }, selected ? 'Fechar histórico' : 'Histórico e restauração')
        ));
    }));
  }

  function progressText(rec) {
    const p = rec.progress || {};
    if (p.total) return `${p.done} de ${p.total} arquivos · ${span(rec.duration_s)}`;
    return span(rec.duration_s);
  }

  function renderDetail() {
    const box = $('#bkDetail');
    if (!S.slug || !S.detail) { box.replaceChildren(); box.hidden = true; return; }
    box.hidden = false;
    const detail = S.detail;
    const row = S.overview.stores.find((r) => r.slug === detail.slug) || {};
    const running = detail.active && ACTIVE.includes(detail.active.state);
    const restores = detail.history.filter((r) => r.kind === 'restauracao');
    const fileInput = el('input', { id: 'bkImportFile', type: 'file', accept: '.cdbk,.zip,application/octet-stream,application/zip', 'aria-label': 'Arquivo de backup (.cdbk ou .zip)' });
    const importBtn = el('button', { class: 'button secondary', type: 'button', disabled: running, onclick: (e) => importBackup(detail.slug, fileInput, e.currentTarget) }, 'Importar backup');

    fill(box, [
      el('div', { class: 'panel-heading' }, el('div', {}, el('div', { class: 'eyebrow', text: 'LOJA' }), el('h3', { text: `Backups de ${detail.slug}` }))),
      running ? el('div', { class: 'bk-running', role: 'status' }, badge(detail.active.state), ' ', el('span', { text: `${KIND[detail.active.kind] || detail.active.kind} — ${progressText(detail.active)}` }),
        el('span', { class: 'hint-text', text: ' Não feche o aplicativo até terminar. Só uma operação por loja roda de cada vez.' })) : null,
      detail.backups.length
        ? el('ul', { class: 'bk-list' }, detail.backups.map((rec) => backupItem(detail, rec, restores, running, row)))
        : el('p', { class: 'hint-text', text: 'Esta loja ainda não tem backups. Toque em “Criar backup”.' }),
      el('div', { class: 'bk-import' },
        el('label', { for: 'bkImportFile', text: 'Importar uma cópia exportada antes (.cdbk, ou .zip de uma versão antiga)' }),
        el('div', { class: 'bk-import-row' }, fileInput, importBtn),
        el('small', { class: 'hint-text', text: 'O arquivo é conferido por inteiro e só entra se for um backup íntegro desta mesma loja. Arquivos .cdbk pedem a senha de recuperação.' })),
      restores.length ? el('div', { class: 'bk-history' }, el('strong', { text: 'Últimas restaurações' }),
        el('ul', {}, restores.slice(0, 5).map((r) => el('li', {}, badge(r.state), ` ${whenText(r.started_at)} — backup ${r.source_backup ? String(r.source_backup).slice(0, 8) : '?'}`,
          r.safety_backup ? ` · segurança ${String(r.safety_backup).slice(0, 8)}` : '',
          r.state !== 'CONCLUIDO' && r.error_message ? el('div', { class: 'bk-err', text: `${r.error_message} ${r.next_action || ''}` }) : null,
          r.state === 'CONCLUIDO' && r.next_action ? el('div', { class: 'bk-meta', text: r.next_action }) : null)))) : null
    ]);
  }

  function protectionLine(rec) {
    const p = rec.protection;
    if (!p) return null;
    if (p.state === 'CRIPTOGRAFADO') {
      return el('div', { class: 'bk-prot bk-prot-ok' }, el('span', { class: 'state state-ok', text: '🔒 CRIPTOGRAFADO' }),
        el('span', { class: 'bk-meta', text: ` ${p.format} v${p.version} · ${p.cipher} · precisa da senha de recuperação${p.content_verified_at ? ` · conteúdo aberto e conferido em ${whenText(p.content_verified_at)}` : ' · conteúdo ainda não aberto desde que foi criado'}` }));
    }
    return el('div', { class: 'bk-prot bk-prot-legacy' }, el('span', { class: 'state state-warn', text: '⚠ LEGADO — SEM CRIPTOGRAFIA' }), el('span', { class: 'bk-meta', text: ' Arquivo ZIP comum da versão anterior; quem tiver o arquivo consegue ler.' }));
  }

  function backupItem(detail, rec, restores, running, row) {
    const lastRestore = restores.find((r) => r.source_backup && String(r.source_backup).startsWith(rec.short_id));
    const valid = rec.validation;
    const recover = rec.can_restore && !row.has_catalog && (rec.parts || []).includes('catalog');
    const level = valid && valid.mode ? { completa: 'conteúdo conferido', protegida: 'conteúdo aberto e conferido com a senha', arquivo: 'só o arquivo (sem abrir o conteúdo)' }[valid.mode] : '';
    return el('li', { class: 'bk-item', 'data-id': rec.id },
      el('div', { class: 'bk-item-head' }, el('code', { text: rec.short_id }), badge(rec.state), el('span', { class: 'bk-meta', text: KIND[rec.kind] || rec.kind })),
      protectionLine(rec),
      el('div', { class: 'bk-meta', text: `${whenText(rec.started_at)} · ${bytes(rec.size_bytes)} · ${partsText(rec.parts)} · ${rec.files} arquivos` }),
      el('div', { class: 'bk-meta', text: valid ? `Integridade: ${valid.ok ? '✓ confere' : '✕ NÃO confere'}${level ? ` (${level})` : ''} — ${valid.message}` : 'Integridade: ainda não verificada' }),
      rec.state === 'FALHOU' || rec.state === 'INVALIDO' ? el('div', { class: 'bk-err', text: `${rec.error_message || ''} ${rec.next_action || ''}`.trim() }) : null,
      rec.warnings && rec.warnings.length ? el('div', { class: 'bk-meta', text: `Avisos: ${rec.warnings.join(' · ')}` }) : null,
      lastRestore ? el('div', { class: 'bk-meta' }, 'Última restauração deste backup: ', badge(lastRestore.state), ` ${whenText(lastRestore.started_at)}`) : null,
      rec.exports && rec.exports.length ? el('div', { class: 'bk-meta', text: `Última exportação: ${whenText(rec.exports[rec.exports.length - 1].at)} ${rec.exports[rec.exports.length - 1].ok ? '✓' : '✕ incompleta'}` }) : null,
      el('div', { class: 'bk-item-actions' },
        el('button', { class: 'button secondary', type: 'button', disabled: !rec.can_validate || running, 'data-act': 'validate', onclick: (e) => validateBackup(detail.slug, rec, e.currentTarget) }, 'Verificar integridade'),
        rec.can_protect ? el('button', { class: 'button secondary', type: 'button', disabled: running, 'data-act': 'protect', onclick: (e) => protectBackup(detail.slug, rec, e.currentTarget) }, 'Proteger (criptografar)') : null,
        rec.can_export
          ? el('a', { class: 'button secondary', href: `/api/backups/${encodeURIComponent(detail.slug)}/${rec.id}/download`, download: '' }, 'Baixar cópia')
          : el('button', { class: 'button secondary', type: 'button', disabled: true }, 'Baixar cópia'),
        el('button', { class: 'button danger', type: 'button', disabled: !rec.can_restore || running, 'data-act': 'restore', onclick: (e) => openRestore(detail.slug, rec.id, e.currentTarget) }, recover ? 'Recuperar loja…' : 'Restaurar…'),
        rec.can_delete ? el('button', { class: 'button danger ghost', type: 'button', disabled: running, 'data-act': 'delete', onclick: (e) => openDelete(detail.slug, rec.id, e.currentTarget) }, 'Excluir…') : null
      ));
  }

  function renderRetention() {
    const box = $('#bkRetention');
    if (!S.overview) return;
    const policy = S.overview.retention;
    const mode = policy.mode;
    const radios = policy.options.map((option) => el('label', { class: 'bk-radio' },
      el('input', { type: 'radio', name: 'bkRetMode', value: option.mode, checked: option.mode === mode }),
      el('span', {}, el('strong', { text: option.title }), el('small', { class: 'hint-text', text: option.impact }))));
    const preview = S.preview;
    fill(box,
      el('strong', { text: 'Espaço e retenção de backups' }),
      el('p', { class: 'hint-text', text: `Política atual: ${RETENTION_LABEL[mode] || mode}. Exclusão automática: DESATIVADA — ${policy.auto_note}` }),
      ...radios,
      el('div', { class: 'bk-ret-row' },
        el('label', { for: 'bkRetLast', text: 'N backups' }), el('input', { id: 'bkRetLast', type: 'number', min: '1', max: '1000', value: String(policy.keep_last) }),
        el('label', { for: 'bkRetDays', text: 'N dias' }), el('input', { id: 'bkRetDays', type: 'number', min: '7', max: '3650', value: String(policy.keep_days) })),
      el('div', { class: 'bk-store-actions' },
        el('button', { class: 'button secondary', type: 'button', onclick: (e) => saveRetention(e.currentTarget) }, 'Salvar política'),
        el('button', { class: 'button secondary', type: 'button', onclick: (e) => previewRetention(e.currentTarget) }, 'Ver o que seria apagado')),
      preview ? el('div', { class: 'bk-preview' },
        preview.candidates.length
          ? [el('p', { class: 'bk-warning', text: `⚠ ${preview.candidates.length} backup(s), ${bytes(preview.total_bytes)}. ${preview.warning}` }),
            el('ul', {}, preview.candidates.map((c) => el('li', {}, el('code', { text: c.slug }), ' · ', el('code', { text: c.short_id }), ` · ${whenText(c.started_at)} · ${bytes(c.size_bytes)} · ${c.protection === 'CRIPTOGRAFADO' ? 'protegido' : 'LEGADO'} — ${c.reason}`))),
            el('div', { class: 'field' }, el('label', { for: 'bkRetConfirm', text: `Para apagar, digite ${preview.confirm_text}` }), el('input', { id: 'bkRetConfirm', autocomplete: 'off', autocapitalize: 'characters', spellcheck: 'false', maxlength: '10' })),
            el('button', { class: 'button danger', type: 'button', onclick: (e) => applyRetention(e.currentTarget) }, 'Apagar estes backups')]
          : el('p', { class: 'hint-text', text: 'Nada seria apagado com a política atual.' })) : null
    );
  }

  function renderStorage() {
    const box = $('#bkStorage');
    const st = S.overview.storage;
    fill(box,
      el('strong', { text: 'Espaço neste celular' }),
      el('div', { class: 'bk-bars' },
        el('div', { class: 'bk-bar-row' }, el('span', { text: 'Sites publicados' }), el('b', { text: bytes(st.sites_bytes) })),
        el('div', { class: 'bk-bar-row' }, el('span', { text: 'Catálogos (produtos e fotos)' }), el('b', { text: bytes(st.catalogs_bytes) })),
        el('div', { class: 'bk-bar-row' }, el('span', { text: `Backups (${st.backup_count}: ${st.encrypted_count} protegidos, ${st.legacy_count} legados; fora de qualquer cota)` }), el('b', { text: bytes(st.backup_bytes) })),
        el('div', { class: 'bk-bar-row' }, el('span', { text: 'Espaço livre' }), el('b', { text: st.free_bytes == null ? 'não foi possível medir' : bytes(st.free_bytes) }))),
      el('p', { class: 'hint-text', text: st.policy }),
      el('p', { class: 'hint-text', text: st.site_quota_note }),
      el('p', { class: 'bk-warning', text: '⚠ Backup guardado só neste celular não protege contra perda, dano ou formatação do aparelho. Use “Baixar cópia” e guarde o arquivo (e a senha de recuperação) em outro lugar.' })
    );
  }

  function renderReminder() {
    const box = $('#bkReminder');
    const status = S.overview.reminder_status || {};
    fill(box,
      el('strong', { text: 'Lembrete mensal' }),
      el('p', { class: 'bk-meta', text: status.last_run_at ? `Última verificação: ${whenText(status.last_run_at)} (${status.source === 'inicio' ? 'ao iniciar o CDBS' : status.source === 'periodico' ? 'verificação periódica' : 'ao abrir o painel'}) · ${status.overdue} loja(s) atrasada(s).` : 'Ainda não houve verificação.' }),
      el('p', { class: 'hint-text', text: status.note || '' })
    );
  }

  // ------------------------------------------------------------------ ligação
  function wire() {
    const input = $('#bkConfirmInput');
    if (!input) return;
    const update = () => { $('#bkConfirmBtn').disabled = !restoreReady(); };
    input.addEventListener('input', update);
    $('#bkRestorePassword').addEventListener('input', update);
    $('#bkRecreateAck').addEventListener('change', update);
    $('#bkConfirmBtn').addEventListener('click', confirmRestore);
    $('#bkCancelBtn').addEventListener('click', closeDialog);
    $('#bkRestoreDialog').addEventListener('cancel', () => { S.summary = null; });
    const updateDelete = () => { $('#bkDeleteBtn').disabled = !deleteReady(); };
    $('#bkDeleteInput').addEventListener('input', updateDelete);
    $('#bkDeleteLastAck').addEventListener('change', updateDelete);
    $('#bkDeleteBtn').addEventListener('click', confirmDelete);
    $('#bkDeleteCancel').addEventListener('click', closeDelete);
    $('#bkDeleteDialog').addEventListener('cancel', () => { S.del = null; });
  }

  wire();
  window.CDBSBackups = { load, refresh, stop };
})();
