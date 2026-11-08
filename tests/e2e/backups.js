// Ponta a ponta no Chromium (tela de celular) + CDBS de verdade — Evolução 2.1:
// proteção por senha → backup criptografado → baixar → restaurar com senha → importar → legado + proteger → excluir → retenção →
// recuperar loja removida (desativada até nova senha) → backup corrompido → sair.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const assert = require('assert');
const { execFileSync } = require('child_process');

const ADMIN = 'http://127.0.0.1:28089';
const SITES = 'http://127.0.0.1:28088';
const SLUG = 'loja-teste';
const ZIP = process.argv[2];
const APP = process.argv[3];                       // pasta do app de teste (para quebrar/conferir arquivos no disco)
const SHOTS = path.join(__dirname, 'shots');
const RECOVERY = 'senha-de-recuperacao-e2e-1';
const problems = [];
fs.mkdirSync(SHOTS, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `bk21-${name}.png`), fullPage: true });
const siteFile = path.join(APP, 'data', 'sites', SLUG, 'index.html');
const backupsDir = path.join(APP, 'data', 'backups', SLUG);
const files = (ext) => (fs.existsSync(backupsDir) ? fs.readdirSync(backupsDir).filter((f) => f.endsWith(ext)) : []);

async function answerPassword(page, password, button = '#bkPasswordOk') {
  await page.waitForSelector('#bkPasswordDialog[open]');
  await page.fill('#bkPasswordInput', password);
  await page.tap(button);
  await page.waitForSelector('#bkPasswordDialog:not([open])', { state: 'attached' });
}
const waitMsg = (page, kind) => page.waitForFunction((k) => document.querySelector('#bkMessage').className.includes(k), kind, { timeout: 20000 });
// espera nenhuma ação em andamento na Central (botões “Verificando…”, “Enviando…” etc. = ocupados); a tela ignora toques enquanto uma ação roda
const idle = async (page) => { await page.waitForTimeout(500); await page.waitForFunction(() => ![...document.querySelectorAll('#bkSection button')].some((b) => /^(Iniciando|Verificando|Protegendo|Enviando|Preparando|Calculando|Apagando|Salvando|Ativando|Trabalhando|Excluindo)…$/.test(b.textContent)), null, { timeout: 60000 }); };
const items = (page) => page.locator('#bkDetail .bk-item');

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, acceptDownloads: true });
  const page = require('./paginas').acompanhar(await ctx.newPage());
  page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) problems.push(`console ${m.type()}: ${m.text()} <- ${(m.location() || {}).url}`); });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('dialog', (d) => d.accept());            // confirm() do painel (remover loja, gerar senha)

  await page.goto(ADMIN);
  assert.equal(await page.isHidden('#bkSection'), true, 'a Central de backups não pode aparecer antes do login');
  await page.fill('#password', 'senha-do-admin-123');
  await page.fill('#confirmPassword', 'senha-do-admin-123');
  await page.tap('#authButton');
  await page.waitForSelector('#dashboard:not([hidden])');
  await page.setInputFiles('#siteZip', ZIP);
  await page.fill('#siteSlug', SLUG);
  await page.tap('#uploadButton');
  await page.waitForSelector('.site-card');
  await page.fill('#storeSlug', SLUG);
  await page.fill('#storeName', 'Loja Teste');
  await page.tap('#storeButton');
  await page.waitForSelector('#credentialBox:not([hidden])');
  const clientPassword = (await page.innerText('#credPassword')).trim();
  console.log('1. login, site e catálogo criados');

  // ---- proteção: ainda não configurada → backup bloqueado e explicado
  await page.waitForSelector('#bkStores .bk-store');
  let text = await page.innerText('#bkProtection');
  assert.ok(text.includes('defina a senha de recuperação') && text.includes('NÃO pode ser recuperada') && text.includes('FORA deste celular'), text);
  await page.tap('#bkStores [data-act="create"]');
  await waitMsg(page, 'error');
  assert.ok((await page.innerText('#bkMessage')).includes('senha de recuperação'), 'deve explicar que falta definir a senha');
  assert.equal(files('.cdbk').length + files('.zip').length, 0, 'nenhum backup pode nascer sem a proteção');
  await shot(page, '1-protecao');
  console.log('2. sem senha de recuperação o backup é bloqueado com explicação (nada é criado sem proteção)');

  // senha fraca / diferente / sem confirmação
  await page.fill('#bkSetupPassword', 'curta');
  await page.fill('#bkSetupConfirm', 'curta');
  await page.check('#bkSetupAck');
  await page.tap('#bkProtection button:has-text("Ativar proteção")');
  await waitMsg(page, 'error');
  assert.ok((await page.innerText('#bkMessage')).length > 10);
  await page.tap('#bkProtection button:has-text("Gerar senha forte")');
  const generated = await page.inputValue('#bkSetupPassword');
  assert.match(generated, /^[A-Za-z0-9]{5}(-[A-Za-z0-9]{5}){3}$/, generated);
  assert.equal(await page.inputValue('#bkSetupConfirm'), generated);
  assert.equal((await page.innerText('#bkGenerated')).trim(), generated, 'a senha gerada precisa estar visível para ser anotada');
  await page.uncheck('#bkSetupAck');
  await page.tap('#bkProtection button:has-text("Ativar proteção")');
  await waitMsg(page, 'error');
  assert.ok((await page.innerText('#bkMessage')).includes('Marque a confirmação'), 'exige confirmar que guardou a senha');
  await page.fill('#bkSetupPassword', RECOVERY);
  await page.fill('#bkSetupConfirm', RECOVERY);
  await page.check('#bkSetupAck');
  await page.tap('#bkProtection button:has-text("Ativar proteção")');
  await page.waitForFunction(() => document.querySelector('#bkProtection').textContent.includes('Proteção ativa'), null, { timeout: 15000 });
  text = await page.innerText('#bkProtection');
  assert.ok(text.includes('CDBK') && text.includes('AES-256-GCM') && text.includes('scrypt'), text);
  assert.equal(await page.locator('#bkSetupPassword').count(), 0, 'o campo da senha some depois de ativar');
  assert.ok(!(await page.content()).includes(RECOVERY), 'a senha não pode ficar na página');
  assert.ok(!fs.readFileSync(path.join(APP, 'data', 'backups', 'protection.json'), 'utf8').includes(RECOVERY), 'a senha não pode ser gravada');
  await shot(page, '2-protecao-ativa');
  console.log('3. senha forte gerada, confirmação obrigatória, proteção ativa mostrando formato/algoritmo; senha não fica na página nem no disco');

  // ---- backup criptografado
  await page.tap('#bkStores [data-act="create"]');
  await answerPassword(page, 'senha-errada-qualquer-1');
  await waitMsg(page, 'error');
  assert.ok((await page.innerText('#bkMessage')).toLowerCase().includes('incorreta'));
  assert.equal(files('.cdbk').length, 0, 'senha errada não cria backup');
  await page.tap('#bkStores [data-act="create"]');
  await answerPassword(page, RECOVERY);
  await page.waitForFunction(() => document.querySelector('#bkStores').textContent.includes('CONCLUÍDO'), null, { timeout: 40000 });
  text = await page.innerText('#bkStores');
  assert.ok(text.includes('1 protegido(s), 0 legado(s)'), text);
  await page.tap('#bkStores [data-act="open"]');
  await page.waitForSelector('#bkDetail .bk-item');
  let item = await page.innerText('#bkDetail .bk-item');
  assert.ok(item.includes('CRIPTOGRAFADO') && item.includes('AES-256-GCM') && item.includes('CDBK v1') && item.includes('precisa da senha'), item);
  assert.ok(item.includes('✓ confere') && item.includes('site + catálogo') && item.includes('Manual'), item);
  { const all = await page.innerText('#bkSection'); const leak = all.match(/.{0,60}(\bnull\b|undefined|\[object).{0,60}/s); assert.ok(!leak, 'texto "null/undefined" vazou para a tela: ' + (leak && leak[0])); }
  const cdbk = files('.cdbk');
  assert.equal(cdbk.length, 1);
  assert.equal(files('.zip').length, 0, 'não pode sobrar ZIP em claro');
  const raw = fs.readFileSync(path.join(backupsDir, cdbk[0]));
  assert.equal(raw.subarray(0, 6).toString('latin1'), 'CDBSBK');
  assert.ok(!raw.toString('latin1').includes('Loja teste') && !raw.toString('latin1').includes('index.html') && !raw.toString('latin1').includes('PK\u0003\u0004'), 'conteúdo em claro dentro do backup');
  await shot(page, '3-backup-criptografado')
  console.log('4. senha errada não cria nada; senha certa cria backup CRIPTOGRAFADO (AES-256-GCM, CDBK v1); no disco não há texto legível');

  // ---- baixar
  const [download] = await Promise.all([page.waitForEvent('download'), page.tap('#bkDetail .bk-item a:has-text("Baixar cópia")')]);
  const saved = path.join(SHOTS, 'baixado.cdbk');
  await download.saveAs(saved);
  const downloaded = fs.readFileSync(saved);
  assert.equal(downloaded.subarray(0, 6).toString('latin1'), 'CDBSBK');
  assert.match(download.suggestedFilename(), /^cdbs-backup-loja-teste-\d{8}-\d{4}-[0-9a-f]{8}\.cdbk$/);
  assert.ok(!downloaded.toString('latin1').includes('senha-do-admin') && !downloaded.toString('latin1').includes(clientPassword));
  console.log('5. "Baixar cópia" entrega o arquivo .cdbk protegido, com nome limpo');

  // ---- verificar: nível arquivo x nível conteúdo
  await page.tap('#bkDetail .bk-item button:has-text("Verificar integridade")');
  await answerPassword(page, '', '#bkPasswordSkip');
  await page.waitForFunction(() => document.querySelector('#bkDetail .bk-item').textContent.includes('só o arquivo'), null, { timeout: 20000 }).catch(() => {});
  item = await page.innerText('#bkDetail .bk-item');
  assert.ok(item.includes('só o arquivo'), 'sem senha deve dizer que só o arquivo foi conferido: ' + item);
  await page.tap('#bkDetail .bk-item button:has-text("Verificar integridade")');
  await answerPassword(page, RECOVERY);
  await idle(page);
  assert.ok((await page.innerText('#bkDetail .bk-item')).includes('conteúdo aberto e conferido com a senha'));
  console.log('6. verificar sem senha avisa que só o arquivo foi conferido; com senha abre e confere o conteúdo');

  // ---- estraga o site e restaura (senha na janela)
  const original = fs.readFileSync(siteFile, 'utf8');
  fs.writeFileSync(siteFile, '<h1>QUEBRADO</h1>');
  await page.tap('#bkDetail .bk-item button:has-text("Restaurar")');
  await page.waitForSelector('#bkRestoreDialog[open]', { timeout: 10000 }).catch(async (e) => { console.log('MSG:', await page.innerText('#bkMessage')); throw e; });
  assert.equal(await page.isDisabled('#bkConfirmBtn'), true);
  assert.equal(await page.isVisible('#bkRestorePassword'), true, 'backup protegido pede a senha');
  assert.equal(await page.isVisible('#bkRecreateAck'), false, 'restauração normal não pede confirmação de recriar loja');
  const dlg = await page.innerText('#bkRestoreDialog');
  assert.ok(dlg.includes('SUBSTITUÍDOS') && dlg.includes('backup de segurança') && dlg.includes('Loja Teste'), dlg);
  await page.fill('#bkConfirmInput', 'outra-loja');
  await page.fill('#bkRestorePassword', RECOVERY);
  assert.equal(await page.isDisabled('#bkConfirmBtn'), true, 'nome errado não libera o botão');
  await page.fill('#bkConfirmInput', SLUG);
  await page.fill('#bkRestorePassword', '');
  assert.equal(await page.isDisabled('#bkConfirmBtn'), true, 'sem senha não libera o botão');
  await shot(page, '4-confirmacao');
  await page.tap('#bkCancelBtn');
  await page.waitForSelector('#bkRestoreDialog:not([open])', { state: 'attached' });
  assert.equal(fs.readFileSync(siteFile, 'utf8'), '<h1>QUEBRADO</h1>', 'cancelar não pode mudar nada');

  await page.tap('#bkDetail .bk-item button:has-text("Restaurar")');
  await page.waitForSelector('#bkRestoreDialog[open]');
  await page.fill('#bkConfirmInput', SLUG);
  await page.fill('#bkRestorePassword', 'senha-errada-qualquer-1');
  await page.tap('#bkConfirmBtn');
  await page.waitForFunction(() => document.querySelector('#bkDialogMessage').textContent.length > 5, null, { timeout: 15000 });
  assert.ok((await page.innerText('#bkDialogMessage')).toLowerCase().includes('incorreta'));
  assert.equal(await page.evaluate(() => document.querySelector('#bkRestoreDialog').open), true, 'senha errada não fecha a janela');
  assert.equal(fs.readFileSync(siteFile, 'utf8'), '<h1>QUEBRADO</h1>', 'senha errada não pode mudar nada');
  await page.fill('#bkRestorePassword', RECOVERY);
  await page.tap('#bkConfirmBtn');
  await page.waitForSelector('#bkRestoreDialog:not([open])', { state: 'attached' });
  await page.waitForFunction(() => { const h = document.querySelector('.bk-history'); return h && h.textContent.includes('CONCLUÍDO'); }, null, { timeout: 60000 });
  assert.equal(fs.readFileSync(siteFile, 'utf8'), original, 'site não voltou ao conteúdo do backup');
  assert.equal((await ctx.request.get(`${SITES}/${SLUG}/`)).status(), 200);
  const detail = await page.innerText('#bkDetail');
  assert.ok(detail.includes('Segurança (antes de restaurar)'), 'o backup de segurança deve aparecer na lista');
  assert.equal(files('.zip').length, 0, 'o backup de segurança também é criptografado');
  await shot(page, '5-restaurado');
  console.log('7. restauração pede a senha; senha errada não muda nada nem fecha a janela; certa restaura; backup de segurança também é criptografado');

  // ---- importar o .cdbk baixado
  const before = await items(page).count();
  await page.setInputFiles('#bkImportFile', saved);
  await page.tap('#bkDetail .bk-import button');
  await answerPassword(page, RECOVERY);
  await page.waitForFunction((n) => document.querySelectorAll('#bkDetail .bk-item').length === n + 1, before, { timeout: 30000 });
  assert.ok((await page.innerText('#bkDetail')).includes('Importado'));
  console.log('8. importar a cópia baixada (com a senha) cria um backup "Importado" já validado');

  fs.writeFileSync(path.join(SHOTS, 'lixo.zip'), 'isto nao e um backup');
  await page.setInputFiles('#bkImportFile', path.join(SHOTS, 'lixo.zip'));
  await page.tap('#bkDetail .bk-import button');
  await waitMsg(page, 'error');
  assert.ok((await page.innerText('#bkMessage')).length > 10);
  console.log('9. arquivo que não é backup é recusado com mensagem clara');

  // ---- legado (como o da Evolução 2): aviso, nada convertido sozinho, proteger cria cópia
  execFileSync('python3', ['-I', '-c', `
import sys; sys.path.insert(0, '.')
import backup
from pathlib import Path
svc = backup.BackupService(Path('data'), Path('data/sites'), Path('data/catalogs'))
rec, _ = svc.start_backup('${SLUG}', plaintext=True)
svc.wait(rec['id'])
`], { cwd: APP });
  await page.tap('#bkStores [data-act="open"]');       // fecha
  await page.waitForSelector('#bkDetail', { state: 'hidden' });
  await page.tap('#bkStores [data-act="open"]');       // reabre (recarrega)
  await page.waitForSelector('#bkDetail .bk-item');
  await page.waitForFunction(() => document.querySelector('#bkAlert').textContent.includes('LEGADO'), null, { timeout: 15000 });
  assert.ok((await page.innerText('#bkAlert')).includes('sem criptografia'));
  const legacyCard = page.locator('#bkDetail .bk-item', { hasText: /SEM CRIPTOGRAFIA/ });
  assert.equal(await legacyCard.count(), 1);
  assert.ok((await legacyCard.innerText()).includes('SEM CRIPTOGRAFIA'));
  const zipBefore = fs.readFileSync(path.join(backupsDir, files('.zip')[0]));
  await legacyCard.locator('button:has-text("Proteger")').tap();
  await answerPassword(page, RECOVERY);
  await page.waitForFunction(() => document.querySelector('#bkDetail').textContent.includes('Protegido (cópia de um legado)'), null, { timeout: 40000 });
  assert.equal(files('.zip').length, 1, 'o legado original continua lá');
  assert.ok(zipBefore.equals(fs.readFileSync(path.join(backupsDir, files('.zip')[0]))), 'o legado não pode ser alterado');
  await shot(page, '6-legado');
  console.log('10. backup LEGADO aparece com aviso; "Proteger" cria cópia criptografada e mantém o original intacto');

  // ---- excluir (identificador exato; nada de apagar sem digitar)
  const legacyBtn = page.locator('#bkDetail .bk-item', { hasText: /SEM CRIPTOGRAFIA/ }).locator('button:has-text("Excluir")');
  await legacyBtn.tap();
  await page.waitForSelector('#bkDeleteDialog[open]');
  const shortId = (await page.locator('#bkDeleteBody code').last().innerText()).trim();
  assert.match(shortId, /^[0-9a-f]{8}$/);
  assert.ok((await page.innerText('#bkDeleteDialog')).includes('APAGADO') );
  assert.equal(await page.isDisabled('#bkDeleteBtn'), true);
  await page.fill('#bkDeleteInput', 'errado');
  assert.equal(await page.isDisabled('#bkDeleteBtn'), true);
  await page.tap('#bkDeleteCancel');
  await page.waitForSelector('#bkDeleteDialog:not([open])', { state: 'attached' });
  assert.equal(files('.zip').length, 1, 'cancelar não apaga');
  await legacyBtn.tap();
  await page.waitForSelector('#bkDeleteDialog[open]');
  await page.fill('#bkDeleteInput', shortId);
  await page.tap('#bkDeleteBtn');
  await page.waitForSelector('#bkDeleteDialog:not([open])', { state: 'attached' });
  await page.waitForFunction(() => !document.querySelector('#bkDetail').textContent.includes('LEGADO'), null, { timeout: 15000 });
  assert.equal(files('.zip').length, 0);
  assert.equal(fs.readFileSync(siteFile, 'utf8'), original, 'excluir um backup não toca no site');
  console.log('11. exclusão exige digitar o identificador; cancelar não apaga; confirmada apaga só aquele arquivo');

  // ---- retenção: preview sem apagar, confirmação por palavra
  await page.locator('#bkRetention input[value="keep_last"]').check();
  await page.fill('#bkRetLast', '1');
  await page.tap('#bkRetention button:has-text("Salvar política")');
  await waitMsg(page, 'success');
  assert.ok((await page.innerText('#bkRetention')).includes('DESATIVADA'), 'deve dizer que a exclusão automática está desativada');
  const totalBefore = files('.cdbk').length;
  await page.tap('#bkRetention button:has-text("Ver o que seria apagado")');
  await page.waitForSelector('#bkRetention .bk-preview li');
  assert.equal(files('.cdbk').length, totalBefore, 'a prévia não apaga nada');
  const candidates = await page.locator('#bkRetention .bk-preview li').count();
  assert.ok(candidates >= 1 && candidates < totalBefore, `candidatos=${candidates} de ${totalBefore}`);
  await page.fill('#bkRetConfirm', 'apagar');
  await page.tap('#bkRetention button:has-text("Apagar estes backups")');
  await waitMsg(page, 'error');
  assert.equal(files('.cdbk').length, totalBefore, 'palavra errada não apaga');
  await page.fill('#bkRetConfirm', 'APAGAR');
  await page.tap('#bkRetention button:has-text("Apagar estes backups")');
  await page.waitForFunction((n) => document.querySelector('#bkMessage').textContent.includes('excluído'), null, { timeout: 20000 });
  assert.equal(files('.cdbk').length, totalBefore - candidates);
  assert.ok(files('.cdbk').length >= 1, 'o último backup validado nunca é apagado pela retenção');
  await shot(page, '7-retencao');
  console.log('12. retenção: prévia não apaga; palavra errada não apaga; APAGAR remove só os listados e preserva o mais recente');

  // ---- último backup validado: aviso extra
  await page.waitForSelector('#bkDetail .bk-item');
  const deletable = page.locator('#bkDetail .bk-item:has(button:has-text("Excluir"))');
  while ((await deletable.count()) > 1) {
    await deletable.last().locator('button:has-text("Excluir")').tap();
    await page.waitForSelector('#bkDeleteDialog[open]');
    const id = (await page.locator('#bkDeleteBody code').last().innerText()).trim();
    await page.fill('#bkDeleteInput', id);
    await page.tap('#bkDeleteBtn');
    await page.waitForSelector('#bkDeleteDialog:not([open])', { state: 'attached' });
    await idle(page);
  }
  await deletable.first().locator('button:has-text("Excluir")').tap();
  await page.waitForSelector('#bkDeleteDialog[open]');
  assert.ok((await page.innerText('#bkDeleteDialog')).includes('ÚLTIMO backup validado'));
  const lastId = (await page.locator('#bkDeleteBody code').last().innerText()).trim();
  await page.fill('#bkDeleteInput', lastId);
  assert.equal(await page.isDisabled('#bkDeleteBtn'), true, 'o último validado exige a confirmação extra');
  await page.tap('#bkDeleteCancel');
  await page.waitForSelector('#bkDeleteDialog:not([open])', { state: 'attached' });
  assert.equal(files('.cdbk').length, 1);
  console.log('13. o último backup validado exige confirmação extra (e cancelar mantém)');

  // ---- recuperar loja removida
  await page.tap('#logoutButton');
  await page.waitForSelector('#authView:not([hidden])');
  await page.fill('#password', 'senha-do-admin-123');
  await page.tap('#authButton');
  await page.waitForSelector('#dashboard:not([hidden])');
  await page.waitForSelector('#storesList .site-card');
  // um produto para conferir depois
  const login = await ctx.request.post(`${SITES}/_painel/api/login`, { data: { slug: SLUG, password: clientPassword }, headers: { Origin: SITES } });
  assert.equal(login.status(), 200);
  const prod = await ctx.request.post(`${SITES}/_painel/api/products`, { data: { name: 'Camisa Teste', price: '10,00', category: 'Geral' }, headers: { Origin: SITES } });
  assert.equal(prod.status(), 201, await prod.text());
  await page.reload();
  await page.waitForSelector('#bkStores .bk-store');
  await page.tap('#bkStores [data-act="create"]');                      // backup com o produto
  await answerPassword(page, RECOVERY);
  await page.waitForFunction(() => document.querySelector('#bkStores').textContent.includes('CONCLUÍDO'), null, { timeout: 40000 });
  await page.waitForTimeout(500);
  // remove catálogo e site pelos botões do painel
  await page.locator('#storesList .site-card button:has-text("Remover")').first().tap();
  await page.waitForFunction(() => !document.querySelector('#storesList').textContent.includes('loja-teste —'), null, { timeout: 10000 }).catch(() => {});
  await page.locator('#sitesList .site-card button:has-text("Remover")').first().tap().catch(() => {});
  await page.waitForTimeout(800);
  await page.reload();
  await page.waitForSelector('#bkStores .bk-store');
  text = await page.innerText('#bkStores');
  assert.ok(text.includes('sem catálogo') && text.includes('Loja removida'), text);
  assert.equal((await ctx.request.get(`${SITES}/${SLUG}/`)).status(), 404);
  await page.tap('#bkStores [data-act="open"]');
  await page.waitForSelector('#bkDetail .bk-item');
  await page.locator('#bkDetail .bk-item button:has-text("Recuperar loja")').first().tap();
  await page.waitForSelector('#bkRestoreDialog[open]');
  const rdlg = await page.innerText('#bkRestoreDialog');
  assert.ok(rdlg.includes('Recuperar loja removida') && rdlg.includes('NÃO voltam: senha do cliente') && rdlg.includes('DESATIVADA'), rdlg);
  await page.fill('#bkConfirmInput', SLUG);
  await page.fill('#bkRestorePassword', RECOVERY);
  assert.equal(await page.isDisabled('#bkConfirmBtn'), true, 'sem marcar que entende, não recupera');
  await shot(page, '8-recuperar');
  await page.check('#bkRecreateAck');
  assert.equal(await page.isDisabled('#bkConfirmBtn'), false);
  await page.tap('#bkConfirmBtn');
  await page.waitForSelector('#bkRestoreDialog:not([open])', { state: 'attached' });
  await page.waitForFunction(() => document.querySelector('#bkStores').textContent.includes('DESATIVADA'), null, { timeout: 60000 });
  await idle(page);
  assert.equal((await ctx.request.get(`${SITES}/${SLUG}/`)).status(), 404, 'loja recuperada fica invisível ao público');
  assert.equal((await ctx.request.get(`${SITES}/_api/${SLUG}/products.json`)).status(), 404);
  const oldLogin = await ctx.request.post(`${SITES}/_painel/api/login`, { data: { slug: SLUG, password: clientPassword }, headers: { Origin: SITES } });
  assert.equal(oldLogin.status(), 401, 'a senha antiga não pode voltar');
  const card = page.locator('#storesList .site-card', { hasText: SLUG });
  assert.ok((await card.innerText()).includes('DESATIVADA') && (await card.innerText()).includes('1 produto'));
  await shot(page, '9-loja-desativada');
  await card.locator('button:has-text("Gerar senha e ativar")').tap();
  await page.waitForSelector('#credentialBox:not([hidden])');
  const newPassword = (await page.innerText('#credPassword')).trim();
  assert.notEqual(newPassword, clientPassword);
  assert.equal((await ctx.request.get(`${SITES}/${SLUG}/`)).status(), 200);
  assert.equal((await ctx.request.get(`${SITES}/_api/${SLUG}/products.json`)).status(), 200);
  assert.ok((await (await ctx.request.get(`${SITES}/_api/${SLUG}/products.json`)).text()).includes('Camisa Teste'));
  assert.equal((await ctx.request.post(`${SITES}/_painel/api/login`, { data: { slug: SLUG, password: newPassword }, headers: { Origin: SITES } })).status(), 200);
  assert.equal((await ctx.request.post(`${SITES}/_painel/api/login`, { data: { slug: SLUG, password: clientPassword }, headers: { Origin: SITES } })).status(), 401);
  console.log('14. loja removida → "Recuperar loja": volta desativada (404 público, sem login); nova senha ativa; produto e site voltaram; senha antiga nunca funciona');

  // ---- lembrete e espaço
  text = await page.innerText('#bkReminder');
  assert.ok(text.includes('Última verificação') && text.includes('ao iniciar o CDBS') && text.includes('Não envia notificação do Android'), text);
  text = await page.innerText('#bkStorage');
  assert.ok(text.includes('Sites publicados') && text.includes('Catálogos') && text.includes('Backups (') && text.includes('Espaço livre'), text);
  console.log('15. lembrete mostra a última verificação e não promete notificação; espaço separa sites, catálogos e backups');

  // ---- backup corrompido no disco: verificar acusa e bloqueia restauração
  if (await page.isHidden('#bkDetail')) await page.tap('#bkStores [data-act="open"]');
  await page.waitForSelector('#bkDetail .bk-item');
  const fileNow = files('.cdbk')[0];
  const victimId = fileNow.replace('.cdbk', '');
  const buf = fs.readFileSync(path.join(backupsDir, fileNow));
  buf[Math.floor(buf.length / 2)] ^= 0x01;                                // um único bit alterado
  fs.writeFileSync(path.join(backupsDir, fileNow), buf);
  await page.tap(`#bkDetail [data-id="${victimId}"] button:has-text("Verificar integridade")`);
  await answerPassword(page, '', '#bkPasswordSkip');
  await page.waitForFunction((id) => { const n = document.querySelector(`[data-id="${id}"]`); return n && n.textContent.includes('INVÁLIDO'); }, victimId, { timeout: 20000 });
  assert.equal(await page.isDisabled(`#bkDetail [data-id="${victimId}"] button:has-text("Restaurar")`), true);
  assert.equal(await page.locator(`#bkDetail [data-id="${victimId}"] a:has-text("Baixar cópia")`).count(), 0, 'backup inválido não pode ser exportado');
  await shot(page, '10-invalido');
  console.log('16. um único bit alterado no arquivo → INVÁLIDO; Restaurar travado e sem link de download');

  // ---- layout e saída
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `rolagem horizontal no celular: ${overflow}px`);
  console.log('17. sem rolagem horizontal em 390px');
  await page.tap('#logoutButton');
  await page.waitForSelector('#authView:not([hidden])');
  assert.equal(await page.isHidden('#bkSection'), true);
  assert.equal(await page.locator('#bkStores *').count(), 0);
  assert.equal(await page.locator('#bkProtection *').count(), 0);
  assert.equal((await ctx.request.get(`${ADMIN}/api/backups/overview`)).status(), 401);
  assert.equal((await ctx.request.get(`${ADMIN}/api/backups/${SLUG}/${victimId}/download`)).status(), 401);
  assert.equal((await ctx.request.get(`${SITES}/api/backups/overview`)).status(), 404);
  assert.equal((await ctx.request.get(`${SITES}/backups.js`)).status(), 404);
  console.log('18. sair esconde e limpa a Central; API de backups exige sessão e não existe no servidor público');

  await browser.close();
  // 400 só é esperado no teste proposital de senha fraca (etapa 3) — em qualquer outra rota continua sendo falha.
  const real = problems.filter((p) => !/Failed to load resource.*(401|404|422|409|403|429)/.test(p) && !/Failed to load resource.*\b400\b.*\/api\/backups\/_protecao\/setup$/.test(p));
  if (real.length) { console.log('PROBLEMAS:\n' + real.join('\n')); process.exit(2); }
  console.log('E2E de backups 2.1: TUDO OK');
})().catch((error) => { console.error('FALHOU:', error.message); process.exit(1); });
