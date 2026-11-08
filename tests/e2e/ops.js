// Ponta a ponta no Chromium (tela de celular 390×844) + CDBS 3.0 de verdade iniciado por "bash start.sh" (supervisor),
// com cloudflared e API da Meta DE MENTIRA (tudo em 127.0.0.1 — nada sai para a internet).
// Cobre: login → visão geral → 20 posições (publicar, verificar, pausar → 503 → retomar) → histórico com filtros →
// notificações (preferências, credenciais só-gravação, teste por modelo aprovado, status de entrega pelo webhook) →
// bot (!Status, !Link + lista, !Link 6 + botões SIM → supervisor liga o túnel → resultado real de volta, número
// desconhecido sem resposta, assinatura errada, duplicada, texto com "comando de shell") → sistema (túnel, reiniciar
// servidor) → atualização pelo painel (pacote inválido recusado, instalar versão nova, voltar) → layout → sair.
const { chromium, request } = require('playwright');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const assert = require('assert');
const { acompanhar } = require('./paginas');   // 4.0: abre a página do painel onde cada elemento mora

const ADMIN = 'http://127.0.0.1:28089';
const SITES = 'http://127.0.0.1:28088';
const [SITE_ZIP, APP, WORK, BASEVER, NEXTVER, TUNNEL_URL] = process.argv.slice(2);
const SLUG = 'loja-teste';
const META_LOG = path.join(WORK, 'meta.jsonl');
const SHOTS = path.join(__dirname, 'shots');
// valores FALSOS, só deste teste (a API de mentira confere o token; nada disso existe na Meta)
const TOKEN = 'EAAGfalsoE2Etoken0123456789abcdefABCDEF';
const APP_SECRET = 'segredo-do-app-e2e-0123456789abcdef';
const PHONE_ID = '109876543210987';
const OWNER = '5585999990001';                       // número autorizado (administrador do bot e destinatário)
const STRANGER = '5511988887777';                    // número desconhecido
const problems = [];
let offlineOk = false;                               // durante reinícios/atualizações o painel fica fora por alguns segundos (esperado)
let loggedOut = false;
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `ops30-${name}.png`), fullPage: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ API da Meta de mentira: o que o CDBS mandou
const metaLog = () => (fs.existsSync(META_LOG) ? fs.readFileSync(META_LOG, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []);
const lastN = () => { const all = metaLog(); return all.length ? all[all.length - 1].n : 0; };
async function nextMeta(after, test, what, timeout = 25000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const found = metaLog().find((e) => e.n > after && test(e.body));
    if (found) return found;
    await sleep(250);
  }
  throw new Error(`a API da Meta (de mentira) não recebeu: ${what}`);
}
const textTo = (to, re) => (b) => b.to === to && b.type === 'text' && re.test(b.text.body);
const kindTo = (to, kind) => (b) => b.to === to && b.type === 'interactive' && b.interactive.type === kind;

// ------------------------------------------------------------------ "Meta" chamando o webhook (assinado com a chave do app)
let meta;                                            // contexto HTTP separado: sem os cookies do navegador
let seq = 0;
function inbound(from, content, id) {
  seq += 1;
  return { object: 'whatsapp_business_account', entry: [{ id: 'WABA-E2E', changes: [{ field: 'messages', value: {
    messaging_product: 'whatsapp', metadata: { display_phone_number: '15550001111', phone_number_id: PHONE_ID },
    contacts: [{ profile: { name: 'Teste' }, wa_id: from }],
    messages: [{ from, id: id || `wamid.IN${Date.now()}x${seq}`, timestamp: String(Math.floor(Date.now() / 1000)), ...content }] } }] }] };
}
const text = (body) => ({ type: 'text', text: { body } });
const listReply = (id, title) => ({ type: 'interactive', interactive: { type: 'list_reply', list_reply: { id, title } } });
const buttonReply = (id, title) => ({ type: 'interactive', interactive: { type: 'button_reply', button_reply: { id, title } } });
const statusEvent = (wamid, status) => ({ object: 'whatsapp_business_account', entry: [{ id: 'WABA-E2E', changes: [{ field: 'messages', value: {
  messaging_product: 'whatsapp', metadata: { display_phone_number: '15550001111', phone_number_id: PHONE_ID },
  statuses: [{ id: wamid, status, timestamp: String(Math.floor(Date.now() / 1000)), recipient_id: OWNER }] } }] }] });
async function webhook(payload, secret = APP_SECRET) {
  const body = JSON.stringify(payload);
  const signature = 'sha256=' + crypto.createHmac('sha256', secret).update(body).digest('hex');
  const response = await meta.post(`${SITES}/_whatsapp/webhook`, { data: body, headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': signature } });
  return response.status();
}

// ------------------------------------------------------------------ painel
async function confirm(page, typed = '') {
  await page.waitForSelector('#opConfirmDialog[open]');
  if (typed) {
    assert.equal(await page.isDisabled('#opConfirmOk'), true, 'o botão de confirmar fica travado até digitar a palavra');
    await page.fill('#opConfirmInput', typed.slice(0, -1));
    assert.equal(await page.isDisabled('#opConfirmOk'), true, 'palavra incompleta não libera');
    await page.fill('#opConfirmInput', typed);
  }
  await page.tap('#opConfirmOk');
  await page.waitForSelector('#opConfirmDialog:not([open])', { state: 'attached' });
}
async function clearMsg(page, selector) {
  await page.evaluate((s) => { const n = document.querySelector(s); n.textContent = ''; n.className = 'message'; }, selector);
}
async function waitMsg(page, selector, kind, re, timeout = 30000) {
  await page.waitForFunction(([s, k, src]) => {
    const n = document.querySelector(s);
    return n && n.className.split(' ').includes(k) && new RegExp(src).test(n.textContent);
  }, [selector, kind, re.source], { timeout });
  return page.innerText(selector);
}
const slotText = (page, n) => page.innerText(`#opSlotList li.slot:nth-child(${n})`);
async function refreshUntil(page, test, what, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    await page.tap('#opRefresh');
    await page.waitForFunction(() => document.querySelector('#opRefresh').textContent === 'Atualizar');
    if (await test()) return;
    await sleep(700);
  }
  throw new Error(`não aconteceu a tempo: ${what}`);
}
const secretRow = (page, label) => page.locator('#sysSecrets .op-secret', { hasText: label });
// partes que começam fechadas (credenciais, bot, configurar monitoramento): o usuário toca no título para abrir
async function openDetails(page, inner) {
  const details = page.locator('details.op-details', { has: page.locator(inner) });
  if (!(await details.evaluate((d) => d.open))) await details.locator('summary').tap();
  await page.waitForFunction((s) => document.querySelector(s).closest('details').open, inner);
}
async function saveSecret(page, label, value) {
  await clearMsg(page, '#sysSecretsMsg');
  await secretRow(page, label).locator('input').fill(value);
  await secretRow(page, label).locator('button', { hasText: 'Salvar' }).tap();
  await waitMsg(page, '#sysSecretsMsg', 'success', /salvo/);
  assert.equal(await secretRow(page, label).locator('input').inputValue(), '', 'o campo da credencial é limpo depois de salvar');
}
async function json(ctx, url) {
  const response = await ctx.request.get(url);
  return { status: response.status(), body: await response.text() };
}

(async () => {
  meta = await request.newContext();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const page = acompanhar(await ctx.newPage());
  page.on('console', (m) => {
    if (!['error', 'warning'].includes(m.type())) return;
    const url = (m.location() || {}).url || '';
    const line = `console ${m.type()}: ${m.text()} <- ${url}`;
    const offline = /ERR_CONNECTION_REFUSED|ERR_EMPTY_RESPONSE|ERR_CONNECTION_RESET|Failed to fetch/.test(m.text());
    if (offline && offlineOk) console.log(`  (esperado: servidor reiniciando) ${m.text()}`);
    else if (/status of 4(00|22)/.test(m.text()) && url.endsWith('/api/updates/upload')) console.log(`  (esperado: pacote recusado) ${m.text()}`);
    else if (/status of 401/.test(m.text()) && loggedOut) console.log(`  (esperado: depois de sair) ${m.text()}`);
    else problems.push(line);
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('dialog', (d) => {
    if (d.type() === 'confirm' && d.message().startsWith(`Remover o site “${SLUG}”?`)) { d.accept(); return; }   // confirmação de remoção (2.1)
    problems.push(`diálogo nativo inesperado: ${d.message()}`);
    d.dismiss();
  });

  // ================================================================ 1. login
  await page.goto(ADMIN);
  for (const id of ['#opOverview', '#opSlots', '#opEvents', '#sysNotifications', '#sysSystem']) {
    assert.equal(await page.isHidden(id), true, `${id} não pode aparecer antes do login`);
  }
  await page.fill('#password', 'senha-do-admin-123');
  await page.fill('#confirmPassword', 'senha-do-admin-123');
  await page.tap('#authButton');
  await page.waitForSelector('#dashboard:not([hidden])');
  await page.waitForSelector('#opCards .op-card');
  console.log('1. login: as seções novas (visão geral, posições, notificações, sistema, histórico) só aparecem depois de entrar');

  // ================================================================ 2. visão geral
  await page.waitForFunction(() => document.querySelectorAll('#opCards .op-card').length === 7);
  let cards = await page.innerText('#opCards');
  assert.ok(cards.includes(`Versão ${BASEVER}`), cards);
  assert.ok(cards.includes('supervisor ativo'), `o CDBS foi iniciado por bash start.sh, então o supervisor tem que aparecer ativo:\n${cards}`);
  assert.ok(/nenhum backup validado/.test(cards), cards);
  await shot(page, '1-visao-geral');
  console.log(`2. visão geral: 7 cartões (com Servidores), versão ${BASEVER}, supervisor ativo`);

  // ================================================================ 3. 10 posições + publicar
  await page.waitForSelector('#opSlotList li.slot-free');
  assert.ok((await page.innerText('#opSlotList')).includes('20 posições vagas'), '4.0: as 20 vagas aparecem numa linha só');
  await page.setInputFiles('#siteZip', SITE_ZIP);
  await page.fill('#siteSlug', SLUG);
  await page.tap('#uploadButton');
  await page.waitForSelector('.site-card');
  await refreshUntil(page, async () => (await slotText(page, 1)).includes(SLUG) && (await slotText(page, 1)).includes('Ativo'), 'site publicado ocupar a posição 1 e ser verificado ATIVO');
  let slot1 = await slotText(page, 1);
  assert.ok(/verificado \d\d\/\d\d/.test(slot1) && slot1.includes('link público não testado'), slot1);
  assert.ok((await page.innerText('#opSlotList li.slot-free')).includes('19 posições vagas'));
  await clearMsg(page, '#opSlotsMessage');
  await page.locator('#opSlotList li.slot:nth-child(1) button', { hasText: 'Verificar' }).tap();
  await waitMsg(page, '#opSlotsMessage', 'success', /.+/);
  await shot(page, '2-posicoes');
  console.log('3. publicar pelo painel coloca o site na posição 1, verificado de verdade (🟢 Ativo); "Verificar" pede uma rodada nova');

  // ================================================================ 4. pausar → 503 → retomar
  await page.locator('#opSlotList li.slot:nth-child(1) button', { hasText: 'Pausar' }).tap();
  await page.waitForSelector('#opConfirmDialog[open]');
  assert.ok((await page.innerText('#opConfirmText')).includes('Nada é apagado'));
  await page.tap('#opConfirmCancel');                                    // desistir não muda nada
  await page.waitForSelector('#opConfirmDialog:not([open])', { state: 'attached' });
  assert.equal((await meta.get(`${SITES}/${SLUG}/`)).status(), 200, 'cancelar não pode pausar');
  await page.locator('#opSlotList li.slot:nth-child(1) button', { hasText: 'Pausar' }).tap();
  await confirm(page);
  await waitMsg(page, '#opSlotsMessage', 'success', /pausado/);
  let response = await meta.get(`${SITES}/${SLUG}/`);
  assert.equal(response.status(), 503);
  assert.ok((await response.text()).includes('temporariamente'), 'visitante vê a página de "temporariamente fora do ar"');
  assert.ok(fs.existsSync(path.join(APP, 'data', 'sites', SLUG, 'index.html')), 'pausar não apaga nada');
  await refreshUntil(page, async () => (await slotText(page, 1)).includes('Pausado'), 'posição mostrar Pausado');
  await page.locator('#opSlotList li.slot:nth-child(1) button', { hasText: 'Retomar' }).tap();
  await waitMsg(page, '#opSlotsMessage', 'success', /voltou ao ar/);
  assert.equal((await meta.get(`${SITES}/${SLUG}/`)).status(), 200);
  await refreshUntil(page, async () => (await slotText(page, 1)).includes('Ativo'), 'posição voltar a Ativo depois de retomar');
  console.log('4. pausar pede confirmação (cancelar não muda nada) → visitante recebe 503 "temporariamente fora do ar", arquivos intactos → retomar → 200');

  // ================================================================ 4b. remover guarda os arquivos; recuperar recoloca
  const removedHtml = fs.readFileSync(path.join(APP, 'data', 'sites', SLUG, 'index.html'));
  assert.equal(await page.isHidden('#removedBox'), true, 'sem sites removidos a caixa nem aparece');
  await page.locator('.site-card button', { hasText: 'Remover' }).tap();
  await page.waitForFunction(() => /guardados/.test(document.querySelector('#uploadMessage').textContent));
  assert.equal((await meta.get(`${SITES}/${SLUG}/`)).status(), 404, 'removido sai do ar');
  await page.waitForSelector('#removedBox:not([hidden])');
  await openDetails(page, '#removedList');
  assert.ok((await page.innerText('#removedList')).includes(SLUG));
  await refreshUntil(page, async () => /20 posições vagas/.test(await slotText(page, 1)), 'remover libera a posição 1');
  await page.locator('#removedList li', { hasText: SLUG }).locator('button', { hasText: 'Recuperar' }).tap();
  await waitMsg(page, '#removedMessage', 'success', /voltou ao ar \(posição 1\)/);
  assert.equal((await meta.get(`${SITES}/${SLUG}/`)).status(), 200);
  assert.ok(fs.readFileSync(path.join(APP, 'data', 'sites', SLUG, 'index.html')).equals(removedHtml), 'voltou o mesmo arquivo');
  await page.waitForSelector('.site-card');
  await page.waitForSelector('#removedBox[hidden]', { state: 'attached' });
  await refreshUntil(page, async () => (await slotText(page, 1)).includes(SLUG) && (await slotText(page, 1)).includes('Ativo'), 'site recuperado volta à posição 1, ativo');
  console.log('4b. remover NÃO apaga: o site sai do ar, a posição fica vaga e os arquivos vão para "Sites removidos"; Recuperar recoloca o mesmo site');

  // ================================================================ 5. histórico com filtros
  await page.selectOption('#opEvCategory', 'site');
  await page.fill('#opEvText', SLUG);
  await page.tap('#opEventsFilter button[type="submit"]');
  await page.waitForFunction(() => /Site recuperado/.test(document.querySelector('#opEventList').textContent));
  let events = await page.innerText('#opEventList');
  for (const label of ['Site publicado', 'Site pausado', 'Site retomado', 'Site removido', 'Site recuperado']) assert.ok(events.includes(label), `${label} no histórico:\n${events}`);
  assert.ok(!events.includes('Login no painel'), 'o filtro de categoria tem que esconder as outras');
  await page.fill('#opEvText', 'nao-existe-esse-texto');
  await page.tap('#opEventsFilter button[type="submit"]');
  await page.waitForFunction(() => /Nenhum evento/.test(document.querySelector('#opEventList').textContent));
  await page.selectOption('#opEvCategory', '');
  await page.fill('#opEvText', '');
  console.log('5. histórico: filtro por categoria e texto (publicado/pausado/retomado); filtro sem resultado avisa');

  // ================================================================ 5b. intervalo: 30 min é o padrão, dá para trocar ou desligar
  await openDetails(page, '#opMonitorForm');
  assert.equal(await page.inputValue('#opMonInterval'), '30', 'o padrão é 30 minutos');
  const monitorNow = async () => JSON.parse((await json(ctx, `${ADMIN}/api/settings`)).body).settings.monitor;
  await page.selectOption('#opMonInterval', '60');
  await clearMsg(page, '#opMonitorMsg');
  await page.tap('#opMonitorForm button[type="submit"]');
  await waitMsg(page, '#opMonitorMsg', 'success', /salvo/);
  let mon = await monitorNow();
  assert.deepEqual([mon.enabled, mon.interval_minutes], [true, 60]);
  await page.waitForFunction(() => /a cada 60 min/.test(document.querySelector('#opSlotsInfo').textContent));
  await page.selectOption('#opMonInterval', 'off');
  await clearMsg(page, '#opMonitorMsg');
  await page.tap('#opMonitorForm button[type="submit"]');
  await waitMsg(page, '#opMonitorMsg', 'success', /desligada/);
  mon = await monitorNow();
  assert.equal(mon.enabled, false, 'desligado de verdade');
  await page.waitForFunction(() => /DESLIGADO/.test(document.querySelector('#opSlotsInfo').textContent));
  await page.selectOption('#opMonInterval', '30');
  await clearMsg(page, '#opMonitorMsg');
  await page.tap('#opMonitorForm button[type="submit"]');
  await waitMsg(page, '#opMonitorMsg', 'success', /salvo/);
  mon = await monitorNow();
  assert.deepEqual([mon.enabled, mon.interval_minutes], [true, 30]);
  console.log('5b. intervalo: padrão 30 min; trocado para 1 hora, desligado e voltado para 30 min pelo painel');

  // ================================================================ 6. notificações: preferências, credenciais, teste
  await page.waitForSelector('#sysNotifications:not([hidden])');
  assert.ok((await page.innerText('#sysNotifStatus')).includes('WhatsApp desligado'));
  await page.tap('#sysPreview');
  await page.waitForSelector('#sysPreviewBox:not([hidden])');
  const preview = await page.innerText('#sysPreviewBox');
  assert.ok(preview.includes(`1. 🟢 Ativo — ${SLUG}`) && preview.includes('20. ⚪ Vago') && preview.includes('Resumo: 1 ativo · 19 vagos'), preview);
  await page.check('#sysNotifEnabled');
  await page.fill('#sysRecipients', '+55 85 99999-0001');
  await clearMsg(page, '#sysNotifFormMsg');
  await page.tap('#sysNotifForm button[type="submit"]');
  await waitMsg(page, '#sysNotifFormMsg', 'success', /Preferências salvas/);
  const nowRecipients = await page.innerText('#sysRecipientsNow');
  assert.ok(nowRecipients.includes('0001') && !nowRecipients.includes('99999'), `número mascarado na tela: ${nowRecipients}`);
  assert.equal(await page.inputValue('#sysRecipients'), '');
  await page.waitForFunction(() => /Falta configurar/.test(document.querySelector('#sysNotifStatus').textContent), null, { timeout: 10000 });   // sem credenciais: avisa
  await openDetails(page, '#sysSecrets');
  await saveSecret(page, 'Phone number ID', PHONE_ID);
  await saveSecret(page, 'Token de acesso da API', TOKEN);
  await saveSecret(page, 'App Secret', APP_SECRET);
  await page.locator('#sysSecrets .op-secret', { hasText: 'Verify token' }).locator('button', { hasText: 'Gerar' }).tap();
  await page.waitForFunction(() => (document.querySelector('#sysSecrets .op-token') || {}).textContent.length >= 20);
  const VERIFY = (await page.innerText('#sysSecrets .op-token')).trim();
  await page.waitForFunction(() => /WhatsApp pronto/.test(document.querySelector('#sysNotifStatus').textContent));
  const html = await page.content();
  const settingsJson = await json(ctx, `${ADMIN}/api/settings`);
  for (const secret of [TOKEN, APP_SECRET]) {
    assert.ok(!html.includes(secret), 'credencial nunca volta para a tela');
    assert.ok(!settingsJson.body.includes(secret), 'credencial nunca sai na API do painel');
  }
  assert.ok(!settingsJson.body.includes(VERIFY), 'o token de verificação aparece UMA vez (na hora de gerar) e depois só mascarado');
  assert.ok((await secretRow(page, 'Token de acesso da API').innerText()).includes('configurado'));
  await shot(page, '3-notificacoes');
  // mensagem de teste: fora da janela de 24 h, vai como MODELO aprovado (12 parâmetros de uma linha)
  let mark = lastN();
  await page.tap('#sysTest');
  await confirm(page);
  await waitMsg(page, '#sysNotifMessage', 'success', /fila de envio/);
  const test = await nextMeta(mark, (b) => b.to === OWNER && b.type === 'template', 'modelo da mensagem de teste');
  assert.equal(test.auth_ok, true, 'o CDBS usou o token salvo pelo painel');
  assert.equal(test.body.template.name, 'cdbs_relatorio_sites');
  assert.equal(test.body.template.language.code, 'pt_BR');
  const params = test.body.template.components[0].parameters.map((p) => p.text);
  assert.equal(params.length, 12, 'o modelo do relatório tem 12 variáveis');
  assert.ok(params.every((p) => p && !/[\n\t]| {4}/.test(p)), `parâmetro com quebra de linha/tab/4 espaços é recusado pela Meta (132018): ${JSON.stringify(params)}`);
  assert.ok(params[1].includes(SLUG), `posição 1 no relatório: ${params[1]}`);
  console.log('6. notificações: número mascarado, credenciais só-gravação (nunca voltam na tela nem na API), WhatsApp pronto,');
  console.log('   teste vai como modelo cdbs_relatorio_sites (12 parâmetros de uma linha) com o token salvo');

  // ================================================================ 7. webhook: aperto de mão e status de entrega
  response = await meta.get(`${SITES}/_whatsapp/webhook?hub.mode=subscribe&hub.verify_token=${encodeURIComponent(VERIFY)}&hub.challenge=E2E-12345`);
  assert.equal(response.status(), 200);
  assert.equal(await response.text(), 'E2E-12345');
  assert.equal((await meta.get(`${SITES}/_whatsapp/webhook?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1`)).status(), 403);
  assert.notEqual((await meta.get(`${ADMIN}/_whatsapp/webhook?hub.mode=subscribe&hub.verify_token=${encodeURIComponent(VERIFY)}&hub.challenge=1`)).status(), 200,
    'o webhook só existe no servidor público (o painel nunca é exposto)');
  assert.equal(await webhook(statusEvent(test.wamid, 'delivered'), 'chave-errada'), 401, 'evento com assinatura errada é recusado');
  assert.equal(await webhook(statusEvent(test.wamid, 'delivered')), 200);
  // o webhook responde na hora e o bot aplica o status logo depois (em segundo plano): espera a API confirmar antes de recarregar
  for (let i = 0; i < 40 && !(await json(ctx, `${ADMIN}/api/notifications`)).body.includes('"entregue"'); i += 1) await sleep(250);
  await page.reload();
  await page.waitForSelector('#dashboard:not([hidden])');
  await page.waitForFunction(() => /entregue/.test(document.querySelector('#sysNotifList').textContent), null, { timeout: 15000 });
  console.log('7. webhook: aperto de mão só com o token certo; assinatura errada → 401; "delivered" assinado → painel mostra ✓✓ entregue');

  // ================================================================ 7b. e-mail (SMTP de mentira em 127.0.0.1:28092, definido no .env)
  const MAIL_USER = 'dono.cdbs@gmail.com';
  const MAIL_PASS = 'abcd efgh ijkl mnop';
  const smtpLog = () => { const f = path.join(WORK, 'smtp.jsonl'); return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []; };
  await openDetails(page, '#sysMailForm');
  assert.equal(await page.isDisabled('#sysMailHost'), true, 'servidor definido no .env aparece travado');
  assert.equal(await page.inputValue('#sysMailPreset'), 'outro');
  assert.ok((await page.innerText('#sysNotifStatus')).includes('E-mail desligado'));
  await page.check('#sysMailEnabled');
  await page.fill('#sysMailUser', MAIL_USER);
  await page.fill('#sysMailPassword', MAIL_PASS);
  await clearMsg(page, '#sysMailMsg');
  await page.tap('#sysMailForm button[type="submit"]');
  await waitMsg(page, '#sysMailMsg', 'success', /E-mail salvo/);
  assert.equal(await page.inputValue('#sysMailPassword'), '', 'a senha some do campo depois de salvar');
  assert.ok((await page.innerText('#sysMailToNow')).includes('do•••@gmail.com'), 'sem destino informado, manda para o próprio e-mail (mascarado)');
  await page.waitForFunction(() => /E-mail pronto/.test(document.querySelector('#sysNotifStatus').textContent));
  assert.ok(!(await page.content()).includes(MAIL_PASS) && !(await json(ctx, `${ADMIN}/api/settings`)).body.includes(MAIL_PASS), 'senha nunca volta');
  const waBefore = metaLog().length;
  await page.tap('#sysMailTest');
  await confirm(page);
  await waitMsg(page, '#sysMailMsg', 'success', /Teste na fila/);
  const isTest = (m) => /mensagem de teste/.test(m.data);
  for (let i = 0; i < 60 && !smtpLog().some(isTest); i += 1) await sleep(250);
  const mailSent = smtpLog().find(isTest);
  assert.ok(mailSent, 'o servidor de e-mail recebeu o teste');
  assert.deepEqual(mailSent.to, [MAIL_USER]);
  assert.equal(mailSent.login_ok, true, 'entrou com o usuário e a senha de app salvos');
  assert.ok(/mensagem de teste/.test(mailSent.data) && mailSent.data.includes(SLUG), 'o e-mail traz o relatório');
  await sleep(1000);
  assert.equal(metaLog().length, waBefore, 'teste por e-mail não manda WhatsApp');
  console.log('7b. e-mail: servidor do .env travado no painel; senha de app (com espaços) só-gravação; teste chegou ao servidor SMTP com login certo e o relatório; nenhum WhatsApp');

  // ================================================================ 8. bot pelo WhatsApp
  await openDetails(page, '#sysBotForm');
  await page.check('#sysBotEnabled');
  await page.fill('#sysBotAdmins', '+55 85 99999-0001');
  await page.tap('#sysBotForm button[type="submit"]');
  await page.waitForFunction(() => /Bot pronto/.test(document.querySelector('#sysBotMsg').textContent));
  // número desconhecido: nenhuma resposta
  mark = lastN();
  assert.equal(await webhook(inbound(STRANGER, text('!Status'))), 200);
  // !Status do dono
  const statusId = `wamid.STATUS${Date.now()}`;
  assert.equal(await webhook(inbound(OWNER, text('!Status'), statusId)), 200);
  const status = await nextMeta(mark, textTo(OWNER, /CDBS\.OS .* — status/), 'resposta do !Status');
  assert.ok(status.body.text.body.includes('Posições:') && status.body.text.body.includes('Túnel:'), status.body.text.body);
  // reenvio da Meta (mesmo id): ignorado
  mark = lastN();
  assert.equal(await webhook(inbound(OWNER, text('!Status'), statusId)), 200);
  await sleep(2500);
  assert.equal(metaLog().filter((e) => e.n > mark && e.body.to === OWNER && e.body.type === 'text' && /— status/.test(e.body.text.body)).length, 0, 'mensagem repetida não é processada de novo');
  assert.equal(metaLog().filter((e) => e.body.to === STRANGER).length, 0, 'número desconhecido não recebe NADA');
  // !Link → lista interativa → opção 1
  mark = lastN();
  await webhook(inbound(OWNER, text('!Link')));
  const list = await nextMeta(mark, kindTo(OWNER, 'list'), 'menu !Link em lista interativa');
  const rows = list.body.interactive.action.sections[0].rows;
  assert.equal(rows.length, 8, 'administrador vê as 8 opções');
  assert.ok(rows.every((r) => r.title.length <= 24 && (r.description || '').length <= 72) && list.body.interactive.action.button.length <= 20, 'limites da Meta para listas');
  mark = lastN();
  await webhook(inbound(OWNER, listReply(rows[0].id, rows[0].title)));
  const listing = await nextMeta(mark, textTo(OWNER, /20 posições/), 'opção 1 (listar as 20 posições)');
  assert.ok(listing.body.text.body.includes(`1. 🟢 Ativo — ${SLUG}`) && listing.body.text.body.includes('20. ⚪ Vago'), listing.body.text.body);
  // texto que parece comando de shell: só "opção inválida"
  mark = lastN();
  await webhook(inbound(OWNER, text('!Link 1; rm -rf / && reboot')));
  await nextMeta(mark, textTo(OWNER, /Opção inválida/), 'recusa do texto com comando de shell');
  // !Link 6 → botões SIM/CANCELAR → SIM → supervisor liga o túnel → resultado REAL chega depois
  mark = lastN();
  await webhook(inbound(OWNER, text('!Link 6')));
  const buttons = await nextMeta(mark, kindTo(OWNER, 'button'), 'confirmação com botões');
  assert.ok(buttons.body.interactive.body.text.includes('ligar o túnel'), buttons.body.interactive.body.text);
  const yes = buttons.body.interactive.action.buttons.find((b) => b.reply.title === 'SIM');
  assert.ok(/^confirm:[0-9a-f]{6}:sim$/.test(yes.reply.id), yes.reply.id);
  assert.equal(fs.existsSync(path.join(APP, 'data', 'system', 'tunnel.json')) && JSON.parse(fs.readFileSync(path.join(APP, 'data', 'system', 'tunnel.json'))).state === 'conectado', false,
    'sem o SIM nada acontece');
  mark = lastN();
  await webhook(inbound(OWNER, buttonReply(yes.reply.id, 'SIM')));
  await nextMeta(mark, textTo(OWNER, /Pedido enviado: ligar o túnel/), 'aviso de pedido enviado');
  const result = await nextMeta(mark, textTo(OWNER, /Resultado \(ligar o túnel\)/), 'resultado real do supervisor', 60000);
  assert.ok(result.body.text.body.includes('✓') && result.body.text.body.includes(TUNNEL_URL), result.body.text.body);
  // !Link 2: agora há link público
  mark = lastN();
  await webhook(inbound(OWNER, text('!Link 2')));
  const links = await nextMeta(mark, textTo(OWNER, /Links públicos|Nenhum link/), 'opção 2 (links públicos)');
  assert.ok(links.body.text.body.includes(`${TUNNEL_URL}/${SLUG}/`), links.body.text.body);
  const requestsDir = path.join(APP, 'data', 'system', 'control', 'requests');
  assert.deepEqual(fs.existsSync(requestsDir) ? fs.readdirSync(requestsDir) : [], [], 'nenhum pedido sobrando na fila do supervisor');
  console.log('8. bot: desconhecido sem resposta; !Status; reenvio ignorado; !Link em lista (8 opções, limites da Meta) → opção 1;');
  console.log(`   texto com "rm -rf" = opção inválida; !Link 6 → botões → SIM → supervisor ligou o túnel → "✓ ... ${TUNNEL_URL}"; !Link 2 com o link`);

  // ================================================================ 9. sistema: túnel e reinício do servidor
  await page.reload();
  await page.waitForSelector('#dashboard:not([hidden])');
  await page.waitForFunction(() => /Supervisor ativo/.test(document.querySelector('#sysSupervisor').textContent));
  await page.waitForFunction((u) => document.querySelector('#sysTunnelState').textContent.includes(u), TUNNEL_URL);
  assert.ok((await page.innerText('#sysTunnelState')).includes('conectado'));
  await openDetails(page, '#sysSecrets');
  assert.equal((await page.innerText('#sysWebhookUrl')).trim(), `${TUNNEL_URL}/_whatsapp/webhook`, 'endereço do webhook para cadastrar na Meta');
  cards = await page.innerText('#opCards');
  assert.ok(cards.includes('🟢 conectado') && cards.includes(TUNNEL_URL), cards);
  await shot(page, '4-sistema-tunel');
  await clearMsg(page, '#sysOpMessage');
  await page.tap('#sysTunnelStop');
  await confirm(page);
  await waitMsg(page, '#sysOpMessage', 'success', /Túnel desligado/, 60000);
  await page.waitForFunction(() => /desligado/.test(document.querySelector('#sysTunnelState').textContent));
  const before = JSON.parse((await json(ctx, `${ADMIN}/api/health`)).body);
  offlineOk = true;
  await clearMsg(page, '#sysOpMessage');
  await page.tap('#sysRestart');
  await confirm(page);
  await waitMsg(page, '#sysOpMessage', 'success', /.+/, 90000);
  offlineOk = false;
  const after = JSON.parse((await json(ctx, `${ADMIN}/api/health`)).body);
  assert.notEqual(after.pid, before.pid, 'o servidor foi mesmo reiniciado (processo novo)');
  assert.equal((await json(ctx, `${ADMIN}/api/overview`)).status, 200, 'a sessão do painel continua valendo depois do reinício');
  console.log('9. sistema: túnel conectado com o link e o endereço do webhook; desligar túnel com confirmação;');
  console.log(`   reiniciar servidor pelo painel → processo novo (pid ${before.pid} → ${after.pid}) e a sessão continua`);

  // ================================================================ 10. atualização pelo painel
  const siteBefore = fs.readFileSync(path.join(APP, 'data', 'sites', SLUG, 'index.html'));
  const secretsBefore = fs.readFileSync(path.join(APP, 'data', 'system', 'secrets.json'));
  await page.setInputFiles('#sysUpdateFile', path.join(WORK, 'lixo.zip'));
  await clearMsg(page, '#sysUpdateMsg');
  await page.tap('#sysUpdateForm button[type="submit"]');
  await waitMsg(page, '#sysUpdateMsg', 'error', /.+/);
  await page.setInputFiles('#sysUpdateFile', path.join(WORK, 'invalido.zip'));
  await clearMsg(page, '#sysUpdateMsg');
  await page.tap('#sysUpdateForm button[type="submit"]');
  await waitMsg(page, '#sysUpdateMsg', 'error', /recusado|inválido|não/i);
  if (await page.locator('#sysStaged .op-package.bad').count()) {
    assert.equal(await page.locator('#sysStaged .op-package.bad button', { hasText: 'Instalar' }).isDisabled(), true, 'pacote recusado não pode ser instalado');
    await page.locator('#sysStaged .op-package.bad button', { hasText: 'Descartar' }).tap();
    await page.waitForFunction(() => !document.querySelector('#sysStaged .op-package.bad'));
  }
  await page.setInputFiles('#sysUpdateFile', path.join(WORK, 'next.zip'));
  await clearMsg(page, '#sysUpdateMsg');
  await page.tap('#sysUpdateForm button[type="submit"]');
  await waitMsg(page, '#sysUpdateMsg', 'success', new RegExp(`Pacote ${NEXTVER.replace(/\./g, '\\.')} conferido`));
  await page.waitForSelector('#sysStaged .op-package.ok');
  assert.ok((await page.innerText('#sysStaged')).includes('Passou na validação'));
  await shot(page, '5-atualizacao-conferida');
  offlineOk = true;
  await clearMsg(page, '#sysUpdateMsg');
  await page.locator('#sysStaged .op-package.ok button', { hasText: 'Instalar' }).tap();
  await confirm(page, 'INSTALAR');
  await waitMsg(page, '#sysUpdateMsg', 'success', /.+/, 240000);
  await page.waitForFunction((v) => document.querySelector('#sysVersion').textContent.includes(`Versão instalada: ${v}`), NEXTVER, { timeout: 30000 });
  assert.equal(JSON.parse((await json(ctx, `${ADMIN}/api/health`)).body).version, NEXTVER);
  assert.ok(fs.readFileSync(path.join(APP, 'data', 'sites', SLUG, 'index.html')).equals(siteBefore), 'a atualização não toca nos sites');
  assert.ok(fs.readFileSync(path.join(APP, 'data', 'system', 'secrets.json')).equals(secretsBefore), 'a atualização não toca nas credenciais');
  await page.waitForFunction((v) => [...document.querySelectorAll('#sysSnapshots li')].some((li) => li.textContent.includes(`versão ${v}`)), BASEVER);
  await shot(page, '6-atualizacao-instalada');
  console.log(`10. atualização: arquivo que não é ZIP e ZIP sem CDBS recusados (sem botão de instalar); pacote ${NEXTVER} conferido →`);
  console.log(`    INSTALAR digitado → supervisor instalou e conferiu a saúde → versão ${NEXTVER}; sites e credenciais intactos`);
  await clearMsg(page, '#sysUpdateMsg');
  await page.locator('#sysSnapshots li', { hasText: `versão ${BASEVER}` }).first().locator('button', { hasText: 'Voltar para esta' }).tap();
  await confirm(page, 'VOLTAR');
  await waitMsg(page, '#sysUpdateMsg', 'success', /.+/, 240000);
  await page.waitForFunction((v) => document.querySelector('#sysVersion').textContent.includes(`Versão instalada: ${v}`), BASEVER, { timeout: 30000 });
  offlineOk = false;
  assert.equal(JSON.parse((await json(ctx, `${ADMIN}/api/health`)).body).version, BASEVER);
  const history = await page.innerText('#sysUpdateHistory');
  assert.ok(history.includes(`${BASEVER} → ${NEXTVER}`) && history.includes(`${NEXTVER} → ${BASEVER}`), history);
  assert.equal((await meta.get(`${SITES}/${SLUG}/`)).status(), 200, 'o site continua no ar depois de atualizar e voltar');
  console.log(`11. voltar versão: VOLTAR digitado → versão ${BASEVER} de novo; histórico mostra as duas trocas; site no ar`);

  // ================================================================ 11. histórico do bot e privacidade
  await page.selectOption('#opEvCategory', 'bot');
  await page.selectOption('#opEvResult', 'recusado');
  await page.tap('#opEventsFilter button[type="submit"]');
  await page.waitForFunction(() => /não autorizado/.test(document.querySelector('#opEventList').textContent));
  events = await page.innerText('#opEventList');
  assert.ok(!events.includes(STRANGER) && !events.includes(OWNER), 'números aparecem mascarados no histórico');
  await page.selectOption('#opEvResult', '');
  await page.fill('#opEvText', STRANGER);
  await page.tap('#opEventsFilter button[type="submit"]');
  await page.waitForFunction(() => /Nenhum evento/.test(document.querySelector('#opEventList').textContent));
  console.log('12. histórico do bot: recusa do número desconhecido registrada; números sempre mascarados (busca pelo número inteiro não acha)');

  // ================================================================ 12. segredos fora dos arquivos de registro
  const dataDir = path.join(APP, 'data');
  const leaks = [];
  (function walk(dir) {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      const stat = fs.lstatSync(full);
      if (stat.isDirectory()) { if (!full.includes(path.join('data', 'updates'))) walk(full); continue; }
      if (name === 'secrets.json') continue;                    // o único lugar onde as credenciais ficam (0600)
      const content = fs.readFileSync(full, 'latin1');
      for (const secret of [TOKEN, APP_SECRET, VERIFY, MAIL_PASS]) if (content.includes(secret)) leaks.push(`${full.slice(APP.length)} contém uma credencial`);
    }
  })(dataDir);
  assert.deepEqual(leaks, [], leaks.join('\n'));
  assert.equal((fs.statSync(path.join(dataDir, 'system', 'secrets.json')).mode & 0o777), 0o600, 'secrets.json só para o dono (0600)');
  assert.ok(!fs.readFileSync(META_LOG, 'utf8').includes(TOKEN));
  console.log('13. credenciais só em data/system/secrets.json (0600); nenhum histórico, fila, estado ou registro contém token/chave');

  // ================================================================ 13. layout e saída
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `rolagem horizontal no celular: ${overflow}px`);
  await shot(page, '7-painel-completo');
  console.log('14. sem rolagem horizontal em 390 px com todas as seções abertas');
  // recarregar várias vezes: TODAS as seções têm de carregar sempre (antes da correção, ~1 em 8 recargas ficava sem alguma)
  let missingAfterReload = 0;
  for (let i = 0; i < 15; i += 1) {
    await page.reload();
    await page.waitForSelector('#dashboard:not([hidden])');
    await page.waitForFunction(() => document.querySelectorAll('#opCards .op-card').length === 7 && !!document.querySelector('#sysSupervisor').textContent, null, { timeout: 5000 }).catch(() => {});
    const hidden = await page.evaluate(() => ['#opOverview', '#opSlots', '#opEvents', '#sysNotifications', '#sysSystem', '#bkSection'].filter((s) => document.querySelector(s).hidden));
    if (hidden.length) missingAfterReload += 1;
  }
  assert.equal(missingAfterReload, 0, `${missingAfterReload} de 15 recargas ficaram com seção faltando`);
  console.log('14b. 15 recargas seguidas: todas as seções carregaram em todas');
  loggedOut = true;
  await page.tap('#logoutButton');
  await page.waitForSelector('#authView:not([hidden])');
  for (const id of ['#opOverview', '#opSlots', '#opEvents', '#sysNotifications', '#sysSystem']) assert.equal(await page.isHidden(id), true, `${id} some ao sair`);
  for (const id of ['#opCards', '#opSlotList', '#opEventList', '#sysSecrets']) assert.equal(await page.locator(`${id} *`).count(), 0, `${id} é limpo ao sair`);
  for (const route of ['/api/overview', '/api/settings', '/api/monitor', '/api/notifications', '/api/updates', '/api/tunnel']) {
    assert.equal((await ctx.request.get(`${ADMIN}${route}`)).status(), 401, `${route} exige sessão`);
  }
  for (const route of ['/api/overview', '/api/settings', '/ops.js', '/system.js', '/api/updates']) {
    assert.equal((await meta.get(`${SITES}${route}`)).status(), 404, `${route} não existe no servidor público`);
  }
  const postNoHeader = await ctx.request.post(`${ADMIN}/api/monitor/check`, { data: '{}', headers: { 'Content-Type': 'application/json' } });
  assert.ok([401, 403].includes(postNoHeader.status()), 'ação sem sessão e sem o cabeçalho do painel é recusada');
  console.log('15. sair esconde e limpa tudo; APIs novas exigem sessão e não existem no servidor público');

  await browser.close();
  await meta.dispose();
  if (problems.length) { console.log('PROBLEMAS:\n' + problems.join('\n')); process.exit(2); }
  console.log('E2E do painel 3.0 (supervisor + WhatsApp + atualização): TUDO OK');
})().catch((error) => { console.error('FALHOU:', error.message); process.exit(1); });
