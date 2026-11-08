const $ = (selector) => document.querySelector(selector);
const BASE = '/_painel';   // o painel mora dentro do endereço dos sites
const JSON_HEADERS = { 'Content-Type': 'application/json' };
const PARAMS = new URLSearchParams(location.search);
const WANTED = (PARAMS.get('loja') || '').trim().toLowerCase();   // loja do site que abriu o painel
// "embed": aberto por cima do site pelo botão escondido (/_cdbs/admin.js), dentro de um iframe.
const EMBED = PARAMS.get('embed') === '1' && window.parent !== window;
const state = { products: [], categories: [], store: null, editing: null, hadImage: false };

/* Conversa com o site por baixo (só com o mesmo endereço). */
function tell(tipo) {
  if (EMBED) window.parent.postMessage({ tipo }, location.origin);
}
function changed() {
  tell('cdbs:alterado');
}

function say(element, text, kind = '') {
  element.textContent = text;
  element.className = `message ${kind}`.trim();
}

async function api(url, options = {}) {
  const response = await fetch(BASE + url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && url !== '/api/login') {
    showLogin();
    throw new Error('Sua sessão terminou. Entre novamente.');
  }
  if (!response.ok) throw new Error(data.error || `Erro ${response.status}`);
  return data;
}

function showLogin(notice = '') {
  $('#appView').hidden = true;
  $('#loginView').hidden = false;
  $('#logoutButton').hidden = true;
  $('#viewSite').hidden = true;
  $('#storeName').textContent = 'Painel da loja';
  if (WANTED && !$('#loginSlug').value) $('#loginSlug').value = WANTED;
  // Veio de um site: a loja já é conhecida, o cliente só digita a senha.
  const known = Boolean(WANTED) && $('#loginSlug').value === WANTED;
  $('#slugField').hidden = known;
  $('#slugInfo').hidden = !known;
  $('#slugName').textContent = WANTED;
  $('#loginIntro').textContent = known ? 'Digite a senha que você recebeu.' : 'Use o nome da loja e a senha que você recebeu.';
  say($('#loginMessage'), notice);
  if (!$('#loginView').contains(document.activeElement)) (known ? $('#loginPassword') : $('#loginSlug')).focus({ preventScroll: true });
}

$('#slugChange').addEventListener('click', () => {
  $('#slugInfo').hidden = true;
  $('#slugField').hidden = false;
  $('#loginSlug').value = '';
  $('#loginSlug').focus();
});

function showApp(store) {
  state.store = store;
  $('#loginView').hidden = true;
  $('#appView').hidden = false;
  $('#logoutButton').hidden = false;
  $('#storeName').textContent = store.name || store.slug;
  const view = $('#viewSite');
  view.hidden = EMBED || !store.public_url;   // aberto por cima do site, o site já está ali atrás
  if (store.public_url) view.href = store.public_url;
}

async function load() {
  const data = await api('/api/products');
  state.products = data.products;
  state.categories = data.categories;
  showApp(data.store);
  render();
}

function render() {
  $('#productCount').textContent = String(state.products.length);
  $('#empty').hidden = state.products.length > 0;
  $('#list').replaceChildren(...state.products.map(card));
  $('#categoryList').replaceChildren(...state.categories.map((name) => {
    const option = document.createElement('option');
    option.value = name;
    return option;
  }));
}

function button(text, className, handler) {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = text;
  if (className) element.className = className;
  element.addEventListener('click', handler);
  return element;
}

function card(product) {
  const article = document.createElement('article');
  article.className = 'product';

  let thumb;
  if (product.image_url) {
    thumb = document.createElement('img');
    thumb.src = product.image_url;
    thumb.alt = '';
    thumb.loading = 'lazy';
  } else {
    thumb = document.createElement('div');
    thumb.textContent = '📷';
  }
  thumb.classList.add('thumb');

  const info = document.createElement('div');
  const name = document.createElement('div');
  name.className = 'product-name';
  name.textContent = product.name;
  const price = document.createElement('div');
  price.className = 'product-price';
  price.textContent = product.price_text || 'Preço: consulte';
  const meta = document.createElement('div');
  meta.className = 'product-meta';
  meta.textContent = product.category || 'Sem categoria';
  const status = document.createElement('span');
  status.className = product.available ? 'badge' : 'badge off';
  status.textContent = product.available ? 'Disponível' : 'Esgotado';
  info.append(name, price, meta, status);

  const actions = document.createElement('div');
  actions.className = 'product-actions';
  actions.append(
    button(product.available ? 'Marcar esgotado' : 'Voltou ao estoque', '', () => toggle(product)),
    button('Editar', '', () => openEditor(product)),
    button('Excluir', 'danger', () => remove(product)),
  );

  article.append(thumb, info, actions);
  return article;
}

async function toggle(product) {
  try {
    await api(`/api/products/${product.id}`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ available: !product.available }) });
    changed();
    await load();
    say($('#message'), product.available ? 'Marcado como esgotado.' : 'Marcado como disponível.', 'success');
  } catch (error) {
    say($('#message'), error.message, 'error');
  }
}

async function remove(product) {
  if (!confirm(`Excluir “${product.name}”? Isso não pode ser desfeito.`)) return;
  try {
    await api(`/api/products/${product.id}`, { method: 'DELETE' });
    changed();
    await load();
    say($('#message'), 'Produto excluído.', 'success');
  } catch (error) {
    say($('#message'), error.message, 'error');
  }
}

/* ---------------------------------------------------------------- editor */

function setPreview(url) {
  const preview = $('#photoPreview');
  preview.hidden = !url;
  if (url) preview.src = url;
  else preview.removeAttribute('src');
}

function openEditor(product = null) {
  state.editing = product ? product.id : null;
  state.hadImage = Boolean(product && product.image_url);
  $('#editorTitle').textContent = product ? 'Editar produto' : 'Novo produto';
  $('#name').value = product ? product.name : '';
  $('#price').value = product ? product.price_input : '';
  $('#category').value = product ? product.category : '';
  $('#description').value = product ? product.description : '';
  $('#available').checked = product ? product.available : true;
  $('#photo').value = '';
  setPreview(product ? product.image_url : null);
  $('#removePhoto').hidden = !state.hadImage;
  say($('#editorMessage'), '', 'error');
  $('#editor').showModal();
}

$('#newButton').addEventListener('click', () => openEditor());
$('#cancelButton').addEventListener('click', () => $('#editor').close());

$('#photo').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (file) setPreview(URL.createObjectURL(file));
});

$('#removePhoto').addEventListener('click', async () => {
  if ($('#photo').files[0]) {            // só desistiu da foto nova
    $('#photo').value = '';
    setPreview(state.hadImage ? (state.products.find((item) => item.id === state.editing) || {}).image_url : null);
    $('#removePhoto').hidden = !state.hadImage;
    return;
  }
  if (!state.editing || !confirm('Remover a foto deste produto?')) return;
  try {
    await api(`/api/products/${state.editing}/image`, { method: 'DELETE' });
    changed();
    state.hadImage = false;
    setPreview(null);
    $('#removePhoto').hidden = true;
    await load();
  } catch (error) {
    say($('#editorMessage'), error.message, 'error');
  }
});

/* Reduz a foto no próprio celular: fotos de câmera pesam 3–8 MB, o site só precisa de ~200 KB. */
async function shrink(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    try { await image.decode(); } catch { throw new Error('Não consegui abrir essa foto. Use uma imagem JPG ou PNG.'); }
    for (const [side, quality] of [[1200, 0.82], [1000, 0.72], [800, 0.65], [640, 0.6]]) {
      const scale = Math.min(1, side / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const context = canvas.getContext('2d');
      context.fillStyle = '#fff';   // PNG transparente não vira fundo preto
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
      if (blob && blob.size <= 1_800_000) return blob;
    }
    throw new Error('A foto é grande demais. Tente outra.');
  } finally {
    URL.revokeObjectURL(url);
  }
}

$('#editorForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const save = $('#saveButton');
  save.disabled = true;
  say($('#editorMessage'), 'Salvando…');
  let saved = false;
  try {
    const body = JSON.stringify({
      name: $('#name').value, price: $('#price').value, category: $('#category').value,
      description: $('#description').value, available: $('#available').checked,
    });
    const result = state.editing
      ? await api(`/api/products/${state.editing}`, { method: 'PUT', headers: JSON_HEADERS, body })
      : await api('/api/products', { method: 'POST', headers: JSON_HEADERS, body });
    saved = true;
    changed();
    const file = $('#photo').files[0];
    if (file) {
      say($('#editorMessage'), 'Enviando a foto…');
      const photo = await shrink(file);
      await api(`/api/products/${result.product.id}/image`, { method: 'PUT', headers: { 'Content-Type': 'image/jpeg' }, body: photo });
    }
    $('#editor').close();
    await load();
    say($('#message'), 'Produto salvo. Já está na sua loja.', 'success');
  } catch (error) {
    if (saved) {                         // o texto foi salvo, só a foto falhou
      state.editing = null;
      await load().catch(() => {});
      $('#editor').close();
      say($('#message'), `Produto salvo, mas a foto não foi enviada: ${error.message} Toque em Editar para tentar de novo.`, 'error');
    } else {
      say($('#editorMessage'), error.message, 'error');
    }
  } finally {
    save.disabled = false;
  }
});

/* ----------------------------------------------------------------- login */

$('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#loginButton');
  button.disabled = true;
  say($('#loginMessage'), 'Entrando…');
  try {
    await api('/api/login', {
      method: 'POST', headers: JSON_HEADERS,
      body: JSON.stringify({ slug: $('#loginSlug').value, password: $('#loginPassword').value }),
    });
    $('#loginPassword').value = '';
    say($('#loginMessage'), '');
    await load();
  } catch (error) {
    say($('#loginMessage'), error.message, 'error');
  } finally {
    button.disabled = false;
  }
});

$('#logoutButton').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST', headers: JSON_HEADERS, body: '{}' }); }
  finally { showLogin(); }
});

if (EMBED) {
  document.body.classList.add('embed');
  $('#closeButton').hidden = false;
  $('#closeButton').addEventListener('click', () => tell('cdbs:fechar'));
}

(async function start() {
  try {
    const session = await api('/api/session');
    if (session.authenticated && WANTED && session.store.slug !== WANTED) {
      // Conectado em outra loja (ex.: você testando duas lojas no mesmo celular).
      showLogin(`Você estava conectado em “${session.store.slug}”. Digite a senha desta loja.`);
    } else if (session.authenticated) {
      await load();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin();
    say($('#loginMessage'), `Não foi possível abrir o painel: ${error.message}`, 'error');
  } finally {
    tell('cdbs:pronto');
  }
})();
