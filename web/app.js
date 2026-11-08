const authView = document.querySelector('#authView');
const dashboard = document.querySelector('#dashboard');
const authForm = document.querySelector('#authForm');
const authMessage = document.querySelector('#authMessage');
const authTitle = document.querySelector('#authTitle');
const authDescription = document.querySelector('#authDescription');
const authButton = document.querySelector('#authButton');
const authHint = document.querySelector('#authHint');
const confirmGroup = document.querySelector('#confirmGroup');
const confirmPassword = document.querySelector('#confirmPassword');
const uploadForm = document.querySelector('#uploadForm');
const uploadMessage = document.querySelector('#uploadMessage');
const sitesList = document.querySelector('#sitesList');
const emptyState = document.querySelector('#emptyState');
const siteCount = document.querySelector('#siteCount');
const publicNotice = document.querySelector('#publicNotice');
let firstSetup = false;

function message(el, text, kind = '') {
  el.textContent = text;
  el.className = `message ${kind}`.trim();
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Erro ${response.status}`);
  return data;
}

function showAuth(setup) {
  firstSetup = setup;
  if (window.CDBSBackups) window.CDBSBackups.stop();
  if (window.CDBSOps) window.CDBSOps.stop();
  if (window.CDBSSystem) window.CDBSSystem.stop();
  if (window.CDBSApps) window.CDBSApps.stop();
  dashboard.hidden = true;
  document.querySelector('#logoutButton').hidden = true;
  authView.hidden = false;
  confirmGroup.hidden = !setup;
  authTitle.textContent = setup ? 'Criar senha do painel' : 'Entrar no CDBS.OS';
  authDescription.textContent = setup
    ? 'Crie a senha que protege a administração neste celular.'
    : 'Digite sua senha para gerenciar os sites.';
  authButton.textContent = setup ? 'Criar senha e entrar' : 'Entrar';
  authHint.textContent = setup ? 'Use pelo menos 12 caracteres. A senha não é enviada para nenhum serviço externo.' : '';
  document.querySelector('#password').autocomplete = setup ? 'new-password' : 'current-password';
}

function showDashboard() {
  authView.hidden = true;
  dashboard.hidden = false;
  document.querySelector('#logoutButton').hidden = false;
  loadSites();
  loadStores();
  if (window.CDBSBackups) window.CDBSBackups.load();
  if (window.CDBSOps) window.CDBSOps.load();
  if (window.CDBSSystem) window.CDBSSystem.load();
  if (window.CDBSApps) window.CDBSApps.load();
}

async function initialize() {
  try {
    const state = await api('/api/session');
    if (state.authenticated) showDashboard();
    else showAuth(state.setup_required);
  } catch (error) {
    showAuth(true);
    message(authMessage, `Não foi possível iniciar o painel: ${error.message}`, 'error');
  }
}

authForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const password = document.querySelector('#password').value;
  if (firstSetup && password !== confirmPassword.value) {
    message(authMessage, 'As senhas não são iguais.', 'error');
    return;
  }
  authButton.disabled = true;
  message(authMessage, 'Verificando…');
  try {
    await api(firstSetup ? '/api/setup' : '/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password })
    });
    authForm.reset();
    showDashboard();
  } catch (error) {
    message(authMessage, error.message, 'error');
  } finally {
    authButton.disabled = false;
  }
});

function slugFromName(name) {
  return name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
}

document.querySelector('#siteZip').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (file && !document.querySelector('#siteSlug').value) {
    document.querySelector('#siteSlug').value = slugFromName(file.name.replace(/\.zip$/i, ''));
  }
});

uploadForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = document.querySelector('#siteZip').files[0];
  const slug = document.querySelector('#siteSlug').value.trim().toLowerCase();
  if (!file) return message(uploadMessage, 'Escolha um arquivo ZIP.', 'error');
  if (!/^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/.test(slug)) {
    return message(uploadMessage, 'Use letras sem acento, números e hífens no nome.', 'error');
  }
  const button = document.querySelector('#uploadButton');
  button.disabled = true;
  button.textContent = 'Publicando…';
  message(uploadMessage, 'Validando e preparando o site…');
  try {
    await api(`/api/sites?slug=${encodeURIComponent(slug)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file
    });
    uploadForm.reset();
    message(uploadMessage, 'Site publicado no celular.', 'success');
    await loadSites();
    if (window.CDBSBackups) window.CDBSBackups.refresh();
    if (window.CDBSOps) window.CDBSOps.refresh();
  } catch (error) {
    message(uploadMessage, error.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'Publicar site';
  }
});

function makeSiteCard(site) {
  const card = document.createElement('article');
  card.className = 'site-card';
  const main = document.createElement('div');
  main.className = 'site-main';
  const title = document.createElement('div');
  title.className = 'site-title';
  title.textContent = site.slug;
  const address = document.createElement('div');
  address.className = 'site-address';
  address.textContent = site.public_url || `Local neste celular: ${site.local_url}`;
  main.append(title, address);
  const actions = document.createElement('div');
  actions.className = 'site-actions';
  const open = document.createElement('a');
  open.className = 'button secondary';
  open.href = site.public_url || site.local_url;
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  open.textContent = site.public_url ? 'Abrir site' : 'Abrir local';
  actions.append(open);
  if (site.public_url) {
    const copy = document.createElement('button');
    copy.className = 'button secondary';
    copy.type = 'button';
    copy.textContent = 'Copiar link';
    copy.addEventListener('click', async () => {
      await navigator.clipboard.writeText(site.public_url);
      copy.textContent = 'Copiado';
      setTimeout(() => { copy.textContent = 'Copiar link'; }, 1400);
    });
    actions.append(copy);
  }
  const remove = document.createElement('button');
  remove.className = 'button danger';
  remove.type = 'button';
  remove.textContent = 'Remover';
  remove.addEventListener('click', async () => {
    if (!confirm(`Remover o site “${site.slug}”? Ele sai do ar na hora. Os arquivos ficam guardados em “Sites removidos” (dá para recuperar ou apagar de vez depois).`)) return;
    try {
      const result = await api(`/api/sites/${encodeURIComponent(site.slug)}`, { method: 'DELETE' });
      await loadSites();
      if (window.CDBSBackups) window.CDBSBackups.refresh();
      if (window.CDBSOps) { window.CDBSOps.refresh(); window.CDBSOps.loadRemoved(); }
      message(uploadMessage, result.kept ? 'Site removido. Os arquivos ficaram guardados em “Sites removidos”.' : 'Site removido.', 'success');
    } catch (error) {
      message(uploadMessage, error.message, 'error');
    }
  });
  actions.append(remove);
  card.append(main, actions);
  return card;
}

async function loadSites() {
  try {
    const data = await api('/api/sites');
    sitesList.replaceChildren(...data.sites.map(makeSiteCard));
    emptyState.hidden = data.sites.length > 0;
    siteCount.textContent = String(data.sites.length);
    document.querySelector('#siteNames').replaceChildren(...data.sites.map((site) => {
      const option = document.createElement('option');
      option.value = site.slug;
      return option;
    }));
    publicNotice.textContent = data.public_configured
      ? 'Os links acima usam o endereço HTTPS configurado.'
      : 'Os sites estão prontos localmente. Para clientes, falta configurar um endereço público HTTPS.';
  } catch (error) {
    if (error.message.includes('Entre novamente')) showAuth(false);
    else message(uploadMessage, error.message, 'error');
  }
}

document.querySelector('#logoutButton')?.addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); }
  finally { showAuth(false); }
});

/* ---------------------------------------------------------- catálogo dos clientes */

const storeForm = document.querySelector('#storeForm');
const storeMessage = document.querySelector('#storeMessage');
const storesList = document.querySelector('#storesList');
const credentialBox = document.querySelector('#credentialBox');
let publicBase = '';
let localBase = '';
let credentialText = '';

/* O painel do cliente fica dentro do endereço dos sites: <endereço>/_painel/?loja=NOME. */
function panelLink(slug, base = publicBase) {
  return `${base}/_painel/?loja=${encodeURIComponent(slug)}`;
}

function showCredential(info) {
  const direct = publicBase ? panelLink(info.slug) : `[ENDEREÇO DO SITE]/_painel/?loja=${info.slug}`;
  document.querySelector('#credUrl').textContent = direct;
  document.querySelector('#credSlug').textContent = info.slug;
  document.querySelector('#credPassword').textContent = info.password;
  credentialText = [
    'Olá! O painel para você cadastrar seus produtos já está pronto.',
    '',
    'Como entrar: abra o seu site e toque 5 vezes seguidas no logo. O painel abre por cima do site.',
    `Ou use o link direto: ${direct}`,
    `Loja: ${info.slug}`,
    `Senha: ${info.password}`,
    '',
    'Guarde a senha. Para adicionar um produto: toque em “Adicionar produto”, tire a foto, coloque nome e preço e salve. Ao fechar o painel, o produto já aparece no site.',
  ].join('\n');
  credentialBox.hidden = false;
  credentialBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

document.querySelector('#credCopy').addEventListener('click', async (event) => {
  await navigator.clipboard.writeText(credentialText);
  event.target.textContent = 'Copiado';
  setTimeout(() => { event.target.textContent = 'Copiar mensagem para o cliente'; }, 1400);
});

function makeStoreCard(store) {
  const card = document.createElement('article');
  card.className = 'site-card';
  const main = document.createElement('div');
  main.className = 'site-main';
  const title = document.createElement('div');
  title.className = 'site-title';
  title.textContent = `${store.slug} — ${store.name}`;
  const address = document.createElement('div');
  address.className = 'site-address';
  const count = `${store.products} produto${store.products === 1 ? '' : 's'}`;
  address.textContent = `${count}${store.site_published ? '' : ' · site ainda não publicado'} · seu HTML lê /_api/${store.slug}/products.json`;
  main.append(title, address);
  if (store.pending) {
    const pending = document.createElement('div');
    pending.className = 'bk-warning';
    pending.textContent = '⏸ Loja DESATIVADA: foi recuperada de um backup. Site, catálogo e fotos estão invisíveis ao público e o cliente não consegue entrar. Clique em “Gerar senha e ativar” para reativar.';
    main.append(pending);
  }

  const actions = document.createElement('div');
  actions.className = 'site-actions';

  const edit = document.createElement('button');
  edit.className = 'button secondary';
  edit.type = 'button';
  edit.textContent = 'Dados';
  edit.addEventListener('click', async () => {
    const name = prompt('Nome da loja:', store.name);
    if (name === null) return;
    const whatsapp = prompt('WhatsApp (com DDD):', store.whatsapp);
    if (whatsapp === null) return;
    try {
      await api(`/api/stores/${encodeURIComponent(store.slug)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, whatsapp }),
      });
      message(storeMessage, 'Dados atualizados.', 'success');
      await loadStores();
    } catch (error) {
      message(storeMessage, error.message, 'error');
    }
  });

  const reset = document.createElement('button');
  reset.className = 'button secondary';
  reset.type = 'button';
  reset.textContent = store.pending ? 'Gerar senha e ativar' : 'Nova senha';
  reset.addEventListener('click', async () => {
    const question = store.pending
      ? `Ativar “${store.slug}”? Será gerada uma senha nova e a loja volta a aparecer ao público.`
      : `Gerar nova senha para “${store.slug}”? A senha atual e os acessos abertos deixam de funcionar.`;
    if (!confirm(question)) return;
    try {
      const info = await api(`/api/stores/${encodeURIComponent(store.slug)}/password`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      showCredential(info);
      message(storeMessage, store.pending ? 'Loja ativada com a senha nova. Envie ao cliente.' : 'Nova senha gerada. Envie ao cliente.', 'success');
      await loadStores();
    } catch (error) {
      message(storeMessage, error.message, 'error');
    }
  });

  const remove = document.createElement('button');
  remove.className = 'button danger';
  remove.type = 'button';
  remove.textContent = 'Remover';
  remove.addEventListener('click', async () => {
    if (!confirm(`Remover o catálogo “${store.slug}”? Apaga TODOS os produtos e fotos do cliente e a senha dele. Os backups desta loja continuam guardados e permitem recuperar o conteúdo (a loja voltaria desativada, sem a senha antiga).`)) return;
    try {
      await api(`/api/stores/${encodeURIComponent(store.slug)}`, { method: 'DELETE' });
      message(storeMessage, 'Catálogo removido.', 'success');
      await loadStores();
    } catch (error) {
      message(storeMessage, error.message, 'error');
    }
  });

  const open = document.createElement('a');
  open.className = 'button secondary';
  open.href = panelLink(store.slug, publicBase || localBase);
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  open.textContent = 'Painel do cliente';

  actions.append(open, edit, reset, remove);
  card.append(main, actions);
  return card;
}

async function loadStores() {
  try {
    const data = await api('/api/stores');
    publicBase = data.public_base || '';
    localBase = data.local_base || '';
    storesList.replaceChildren(...data.stores.map(makeStoreCard));
    document.querySelector('#storeCount').textContent = String(data.stores.length);
    document.querySelector('#clientNotice').textContent = publicBase
      ? `O cliente entra pelo próprio site (5 toques no logo, se o HTML tiver o botão escondido) ou em ${publicBase}/_painel/?loja=NOME.`
      : 'O painel do cliente usa o MESMO túnel dos sites: não precisa abrir outro. Com o túnel rodando, ele entra pelo próprio site (5 toques no logo) ou em https://SEU-ENDEREÇO/_painel/?loja=NOME. O botão “Painel do cliente” abre a versão local, neste celular, para você testar.';
  } catch (error) {
    if (error.message.includes('Entre novamente')) showAuth(false);
    else message(storeMessage, error.message, 'error');
  }
}

storeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const slug = document.querySelector('#storeSlug').value.trim().toLowerCase();
  if (!/^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/.test(slug)) {
    return message(storeMessage, 'Use letras sem acento, números e hífens no nome.', 'error');
  }
  const button = document.querySelector('#storeButton');
  button.disabled = true;
  message(storeMessage, 'Criando o catálogo…');
  try {
    const info = await api('/api/stores', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        slug, name: document.querySelector('#storeName').value, whatsapp: document.querySelector('#storeWhatsapp').value,
      }),
    });
    storeForm.reset();
    await loadStores();
    showCredential(info);
    message(storeMessage, 'Catálogo ativado. Envie o acesso ao cliente.', 'success');
  } catch (error) {
    message(storeMessage, error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

// Só começa depois que TODOS os scripts do painel rodaram (backups.js, ops.js e system.js também são "defer").
// Antes, se a resposta de /api/session chegasse antes deles, as seções novas ficavam sem carregar até atualizar a página.
if (document.readyState === 'complete') initialize();
else document.addEventListener('DOMContentLoaded', initialize, { once: true });

// A Central de backups pede para recarregar as listas quando uma restauração/recuperação termina (site e lojas mudam por baixo).
window.CDBSLists = { reload: () => Promise.all([loadSites(), loadStores()]) };
// Sessão expirada em qualquer parte do painel (401): volta para a tela de entrada.
window.CDBSAuth = () => showAuth(false);
