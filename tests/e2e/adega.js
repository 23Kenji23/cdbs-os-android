// Ponta a ponta no Chromium: site da adega + botão escondido + painel do cliente dentro do site.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const assert = require('assert');

const ADMIN = 'http://127.0.0.1:28089';
const SITES = 'http://127.0.0.1:28088';
const SLUG = 'adega-corujao-site-portatil-2';
const SITE = `${SITES}/${SLUG}/`;
const DIR = __dirname;
const SHOTS = path.join(DIR, 'shots');
const ZIP = process.argv[2];
const MEDIA = path.join(process.argv[3], 'data', 'catalogs', SLUG, 'media');
const problems = [];
fs.mkdirSync(SHOTS, { recursive: true });

function watch(page, label) {
  page.on('console', (m) => {
    if (!['error', 'warning'].includes(m.type())) return;
    const url = (m.location() && m.location().url) || '';
    // Esperados: catálogo ainda não ativado (o site mostra a demonstração) e a senha errada de propósito.
    const expected = (m.text().includes('404') && url.endsWith(`/_api/${SLUG}/products.json`))
      || (m.text().includes('401') && url.endsWith('/_painel/api/login'));
    if (expected) console.log(`  (esperado) ${label}: ${m.text()} <- ${url}`);
    else problems.push(`${label} console ${m.type()}: ${m.text()} <- ${url}`);
  });
  page.on('pageerror', (e) => problems.push(`${label} pageerror: ${e.message}`));
}
const shot = (page, name, full = false) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: full });

async function tapLogo(page, times = 5) {
  for (let i = 0; i < times; i += 1) await page.tap('.site-header .brand');
}

(async () => {
  const browser = await chromium.launch();

  // ---------- 1. você, no painel admin: publica a adega
  const adminCtx = await browser.newContext({ viewport: { width: 1000, height: 1400 } });
  const admin = require('./paginas').acompanhar(await adminCtx.newPage()); watch(admin, 'admin');
  admin.on('dialog', (d) => d.accept());
  await admin.goto(ADMIN);
  await admin.fill('#password', 'senha-do-admin-123');
  await admin.fill('#confirmPassword', 'senha-do-admin-123');
  await admin.click('#authButton');
  await admin.waitForSelector('#dashboard:not([hidden])');
  await admin.setInputFiles('#siteZip', ZIP);
  await admin.fill('#siteSlug', SLUG);
  await admin.click('#uploadButton');
  await admin.waitForSelector('.site-card');
  console.log('admin: site publicado');

  // ---------- 2. visitante no celular, ANTES do catálogo: vitrine de demonstração igual à original
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const site = await phone.newPage(); watch(site, 'site');
  site.on('dialog', (d) => d.accept());
  await site.goto(SITE);
  await site.waitForSelector('.product-card');
  assert.equal(await site.locator('.product-card').count(), 4);
  assert.equal(await site.locator('.product-tag', { hasText: 'EXEMPLO' }).count(), 4);
  assert.ok(await site.isVisible('.demo-strip'));
  assert.equal((await site.textContent('#whatsNumber')).trim(), '(85) 98198-0171');
  await shot(site, '1-site-demonstracao');
  console.log('site sem catálogo: 4 produtos de exemplo, avisos de demonstração visíveis');

  // ---------- 3. você ativa o catálogo com o MESMO nome do site
  await admin.fill('#storeSlug', SLUG);
  await admin.fill('#storeName', 'Adega Corujão');
  await admin.fill('#storeWhatsapp', '(85) 98888-7777');
  await admin.click('#storeButton');
  await admin.waitForSelector('#credentialBox:not([hidden])');
  const password = (await admin.textContent('#credPassword')).trim();
  assert.match(password, /^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$/);
  assert.ok((await admin.textContent('#credUrl')).includes(`/_painel/?loja=${SLUG}`));
  const panelHref = await admin.getAttribute('#storesList .site-card a', 'href');
  assert.equal(panelHref, `${SITES}/_painel/?loja=${SLUG}`);
  await admin.evaluate(() => { navigator.clipboard.writeText = async (t) => { window.__copiado = t; }; });
  await admin.click('#credCopy');
  const message = await admin.evaluate(() => window.__copiado);
  assert.ok(message.includes('toque 5 vezes seguidas no logo') && message.includes(password), message);
  await shot(admin, '2-admin-catalogo', true);
  console.log('admin: catálogo ativado; mensagem para o cliente explica os 5 toques');

  // ---------- 4. catálogo ativado e vazio: avisos de demonstração somem
  await site.reload();
  await site.waitForSelector('.catalog-message');
  assert.ok((await site.textContent('.catalog-message')).includes('Nenhum produto'));
  assert.ok(!(await site.isVisible('.demo-strip')));
  assert.equal((await site.textContent('#whatsNumber')).trim(), '(85) 98888-7777');
  console.log('site com catálogo vazio: sem produtos de exemplo, número da loja');

  // ---------- 5. o lojista toca 5 vezes no logo -> painel abre por cima do site
  await site.evaluate(() => { window.__semRecarregar = true; });
  await tapLogo(site, 4);
  await site.waitForTimeout(400);
  assert.equal(await site.locator('iframe[title="Painel da loja"]').count(), 0, '4 toques não podem abrir');
  await site.tap('#hero-title');                 // tocar em outro lugar zera a contagem
  await tapLogo(site, 1);
  await site.waitForTimeout(400);
  assert.equal(await site.locator('iframe[title="Painel da loja"]').count(), 0, 'a contagem deveria ter zerado');
  await site.waitForTimeout(3100);               // passou da janela de 3 s: começa do zero
  await tapLogo(site, 5);
  await site.waitForSelector('iframe[title="Painel da loja"]');
  assert.ok(site.url().endsWith('#painel'));
  const frame = site.frameLocator('iframe[title="Painel da loja"]');
  await frame.locator('#loginView:not([hidden])').waitFor();
  assert.ok(await frame.locator('#slugField').isHidden(), 'a loja deveria vir do site, sem campo para digitar');
  assert.equal((await frame.locator('#slugName').textContent()).trim(), SLUG);
  assert.ok(await frame.locator('#closeButton').isVisible());
  await shot(site, '3-painel-login-por-cima-do-site');

  await frame.locator('#loginPassword').fill('SENHA-ERRADA-0000');
  await frame.locator('#loginButton').click();
  await frame.locator('#loginMessage', { hasText: 'incorretos' }).waitFor();
  await frame.locator('#loginPassword').fill(password.toLowerCase().replace(/-/g, ' '));
  await frame.locator('#loginButton').click();
  await frame.locator('#appView:not([hidden])').waitFor();
  assert.ok(await frame.locator('#viewSite').isHidden(), 'dentro do site não precisa de "Ver minha loja"');
  console.log('painel: abriu com 5 toques, login só com a senha');

  // produto 1: foto grande de câmera, preço e categoria; nome com HTML para provar que vira texto
  await frame.locator('#newButton').click();
  await frame.locator('#name').fill('Heineken <img src=x onerror=alert(1)>');
  await frame.locator('#price').fill('12,5');
  await frame.locator('#category').fill('Cervejas');
  await frame.locator('#description').fill('Long neck 330 ml, gelada');
  await frame.locator('#photo').setInputFiles(path.join(DIR, 'foto-grande.jpg'));
  await frame.locator('#saveButton').click();
  await frame.locator('.product').first().waitFor({ timeout: 20000 });
  // produto 2: sem preço ("Consulte"), sem foto
  await frame.locator('#newButton').click();
  await frame.locator('#name').fill('Carvão 5 kg');
  await frame.locator('#category').fill('Extras');
  await frame.locator('#saveButton').click();
  await frame.locator('.product').nth(1).waitFor();
  // produto 3: esgotado
  await frame.locator('#newButton').click();
  await frame.locator('#name').fill('Vinho Tinto Seco');
  await frame.locator('#price').fill('59,90');
  await frame.locator('#category').fill('Vinhos');
  await frame.locator('#available').uncheck();
  await frame.locator('#saveButton').click();
  await frame.locator('.product').nth(2).waitFor();
  await shot(site, '4-painel-produtos');
  const files = fs.readdirSync(MEDIA);
  assert.equal(files.length, 1);
  const size = fs.statSync(path.join(MEDIA, files[0])).size;
  assert.ok(size < 1_800_000, `foto deveria ter sido reduzida, ficou ${size}`);
  console.log(`painel: 3 produtos salvos; foto de 5,9 MB virou ${(size / 1024).toFixed(0)} KB`);

  // fecha pelo botão do painel -> vitrine atualiza sem recarregar a página
  await frame.locator('#closeButton').click();
  await site.waitForSelector('iframe[title="Painel da loja"]', { state: 'detached' });
  await site.waitForFunction(() => document.querySelectorAll('.product-card').length === 3);
  assert.ok(await site.evaluate(() => window.__semRecarregar === true), 'a página não deveria ter recarregado');
  assert.ok(!site.url().endsWith('#painel'));
  assert.equal(await site.locator('img[src="x"]').count(), 0, 'o nome do produto não pode virar HTML');
  assert.ok((await site.textContent('.product-grid')).includes('Heineken <img src=x onerror=alert(1)>'));
  assert.equal(await site.locator('.product-card.is-esgotado .add-button:disabled').count(), 1);
  assert.equal(await site.locator('.product-tag', { hasText: 'EXEMPLO' }).count(), 0);
  const chips = await site.locator('.category-chip').allTextContents();
  assert.deepEqual(chips.map((c) => c.trim()), ['Todos 03', 'Cervejas', 'Extras', 'Vinhos']);
  await site.locator('.product-card img.is-foto').evaluate((img) => img.decode());
  await site.locator('#catalogo').scrollIntoViewIfNeeded();
  await shot(site, '5-site-com-produtos-do-lojista');
  console.log('site: atualizou sozinho ao fechar o painel (sem recarregar), categorias e esgotado certos');

  // pedido pelo WhatsApp com produto com preço + "consulte"
  const addTo = (name) => site.locator('.product-card', { hasText: name }).locator('[data-add]').click();
  await addTo('Heineken');
  await addTo('Carvão');
  await addTo('Heineken');
  const href = decodeURIComponent(await site.getAttribute('#whatsappButton', 'href')).replace(/\u00a0/g, ' ');   // Intl usa espaço especial após R$
  assert.ok(href.startsWith('https://wa.me/5585988887777?text='), href);
  assert.ok(href.includes('Olá, Adega Corujão!') && href.includes('2 × Heineken') && href.includes('R$ 12,50 cada (R$ 25,00)'), href);
  assert.ok(href.includes('Carvão 5 kg — preço a consultar') && href.includes('Total: R$ 25,00 (+ itens a consultar)'), href);
  assert.ok(!href.includes('fictícios'), href);
  await site.locator('#pedido').scrollIntoViewIfNeeded();
  await shot(site, '6-pedido');
  console.log('pedido: mensagem do WhatsApp com a loja real, preço e "a consultar"');

  // ---------- 6. "voltar" do Android fecha o painel sem sair do site
  await tapLogo(site);
  await site.waitForSelector('iframe[title="Painel da loja"]');
  await frame.locator('#appView:not([hidden])').waitFor();     // já logado (sessão de 7 dias)
  // o lojista marca o produto que está no carrinho como esgotado
  await frame.locator('.product', { hasText: 'Heineken' }).locator('button', { hasText: 'Marcar esgotado' }).click();
  await frame.locator('#message', { hasText: 'esgotado' }).waitFor();
  await site.goBack();
  await site.waitForSelector('iframe[title="Painel da loja"]', { state: 'detached' });
  assert.ok(site.url().startsWith(SITE));
  await site.waitForFunction(() => document.querySelector('#toast').textContent.includes('saíram do pedido'));
  assert.equal((await site.textContent('#headerCount')).trim(), '1', 'só o carvão deveria ficar no pedido');
  assert.ok(await site.evaluate(() => window.__semRecarregar === true));
  console.log('voltar: fechou o painel, continuou no site; item esgotado saiu do pedido');

  // ---------- 7. plano B: #painel no endereço abre direto
  const direct = await phone.newPage(); watch(direct, 'direto');
  await direct.goto(`${SITE}#painel`);
  await direct.waitForSelector('iframe[title="Painel da loja"]');
  await direct.frameLocator('iframe[title="Painel da loja"]').locator('#appView:not([hidden])').waitFor();
  console.log('#painel: abre direto');

  // ---------- 8. outro site não consegue embutir o painel
  const other = await browser.newContext();
  const evil = await other.newPage();
  const blocked = [];
  evil.on('console', (m) => blocked.push(m.text()));
  await evil.route('http://evil.test/', (route) => route.fulfill({ contentType: 'text/html', body: `<iframe src="${SITES}/_painel/"></iframe>` }));
  await evil.goto('http://evil.test/');
  await evil.waitForTimeout(800);
  const inner = evil.frames().find((f) => f !== evil.mainFrame());
  const innerText = inner ? await inner.evaluate(() => document.body ? document.body.innerText : '').catch(() => '') : '';
  assert.ok(!innerText.includes('Entrar no painel'), 'o painel não pode abrir dentro de outro site');
  console.log('outro site: painel bloqueado no iframe');

  await browser.close();
  if (problems.length) {
    console.log('PROBLEMAS NO CONSOLE:\n' + problems.join('\n'));
    process.exit(1);
  }
  console.log('TUDO OK');
})().catch((error) => { console.error(error); process.exit(1); });
