/* CDBS.OS — botão ESCONDIDO do painel do cliente (para ele cadastrar os produtos sozinho).

   1) Coloque no HTML do site, antes dos seus scripts:
        <script src="/_cdbs/admin.js" defer></script>

   2) Marque o "ponto secreto" com data-cdbs-admin, por exemplo o logo:
        <a class="logo" href="#topo" data-cdbs-admin>...</a>
      O cliente toca 5 vezes seguidas (em até 3 segundos) nele e o painel abre por cima do site.
      Sem nenhum elemento marcado, o ponto secreto é o canto inferior esquerdo da tela.
      Também abre direto pelo endereço do site com #painel no final:  https://.../loja/#painel

   O visitante comum não vê nada: não existe botão visível e, mesmo que alguém ache, sem a senha
   não entra. O painel fica no MESMO endereço do site (/_painel/), então não há link extra para enviar.

   Ao fechar o painel, se o cliente mudou algum produto, o site é recarregado para mostrar a novidade.
   Para atualizar sem recarregar (sem perder o carrinho), o seu script pode tratar o evento:
        window.addEventListener('cdbs:atualizado', (evento) => { evento.preventDefault(); recarregarProdutos(); });

   O nome da loja é o nome do site no endereço (/NOME/). Se o catálogo tiver outro nome, use:
        <script src="/_cdbs/admin.js" data-loja="nome-do-catalogo" defer></script>
*/
(function (global) {
  'use strict';

  const script = document.currentScript;
  const loja = ((script && script.dataset.loja) || global.location.pathname.split('/').filter(Boolean)[0] || '').toLowerCase();
  const TOQUES = 5;
  const JANELA_MS = 3000;   // os 5 toques precisam caber em 3 segundos
  const CANTO_PX = 56;      // tamanho do canto secreto quando a página não marca nenhum elemento
  const HASH = '#painel';

  let toques = [];
  let camada = null;
  let quadro = null;
  let alterado = false;
  let empurrou = false;     // nós criamos a entrada #painel no histórico (o "voltar" do Android fecha o painel)
  let rolagem = '';
  let socorro = 0;

  function botaoSocorro() {
    if (!camada || camada.querySelector('button')) return;
    const botao = document.createElement('button');
    botao.type = 'button';
    botao.textContent = 'Fechar ✕';
    Object.assign(botao.style, {
      position: 'absolute', top: '10px', right: '10px', padding: '10px 14px', border: '0', borderRadius: '10px',
      background: '#18263a', color: '#fff', font: '700 14px system-ui, sans-serif', cursor: 'pointer',
    });
    botao.addEventListener('click', fechar);
    camada.append(botao);
  }

  function abrir() {
    if (camada || !loja || !document.body) return;
    alterado = false;
    camada = document.createElement('div');
    camada.setAttribute('role', 'dialog');
    camada.setAttribute('aria-modal', 'true');
    camada.setAttribute('aria-label', 'Painel da loja');
    Object.assign(camada.style, {
      position: 'fixed', top: '0', right: '0', bottom: '0', left: '0', zIndex: '2147483647', background: '#f5f8fc',
    });
    quadro = document.createElement('iframe');
    quadro.title = 'Painel da loja';
    quadro.src = `/_painel/?loja=${encodeURIComponent(loja)}&embed=1`;
    Object.assign(quadro.style, { width: '100%', height: '100%', border: '0', display: 'block', background: '#f5f8fc' });
    camada.append(quadro);
    document.body.append(camada);

    rolagem = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';   // o site por baixo não rola junto
    if (global.location.hash !== HASH) {
      global.history.pushState(null, '', HASH);
      empurrou = true;
    }
    // Se o painel não responder (servidor antigo ou fora do ar), aparece um botão para sair.
    socorro = global.setTimeout(botaoSocorro, 6000);
    quadro.focus();
  }

  function fechar() {
    if (!camada) return;
    global.clearTimeout(socorro);
    // Alguém do site tratou a novidade (preventDefault)? Então não precisa recarregar a página.
    const recarregar = alterado && global.dispatchEvent(new CustomEvent('cdbs:atualizado', { cancelable: true }));
    alterado = false;
    camada.remove();
    camada = null;
    quadro = null;
    document.documentElement.style.overflow = rolagem;
    if (recarregar) {
      global.location.replace(global.location.pathname + global.location.search);
      return;
    }
    if (empurrou) {
      empurrou = false;
      global.history.back();
    } else if (global.location.hash === HASH) {
      global.history.replaceState(null, '', global.location.pathname + global.location.search);
    }
  }

  function noPontoSecreto(evento) {
    if (document.querySelector('[data-cdbs-admin]')) {
      return Boolean(evento.target instanceof Element && evento.target.closest('[data-cdbs-admin]'));
    }
    return evento.clientX <= CANTO_PX && evento.clientY >= global.innerHeight - CANTO_PX;
  }

  function contarToque(evento) {
    if (camada || !evento.isPrimary || evento.button > 0) return;
    if (!noPontoSecreto(evento)) {
      toques = [];          // tocar em outro lugar zera a contagem
      return;
    }
    const agora = Date.now();
    toques = toques.filter((momento) => agora - momento < JANELA_MS);
    toques.push(agora);
    if (toques.length >= TOQUES) {
      toques = [];
      global.setTimeout(abrir, 60);   // deixa o clique do próprio logo (ex.: href="#topo") terminar antes
    }
  }

  function iniciar() {
    // Evita o zoom de "toque duplo" no ponto secreto.
    document.querySelectorAll('[data-cdbs-admin]').forEach((elemento) => { elemento.style.touchAction = 'manipulation'; });
    document.addEventListener('pointerup', contarToque, true);
    global.addEventListener('hashchange', () => { if (global.location.hash === HASH) abrir(); });
    global.addEventListener('popstate', () => {
      if (camada && global.location.hash !== HASH) {
        empurrou = false;   // o próprio "voltar" já saiu do #painel
        fechar();
      }
    });
    global.addEventListener('message', (evento) => {
      if (!quadro || evento.source !== quadro.contentWindow || evento.origin !== global.location.origin) return;
      const tipo = evento.data && evento.data.tipo;
      if (tipo === 'cdbs:pronto') global.clearTimeout(socorro);
      else if (tipo === 'cdbs:alterado') alterado = true;
      else if (tipo === 'cdbs:fechar') fechar();
    });
    if (global.location.hash === HASH) abrir();
  }

  global.CDBS = Object.assign(global.CDBS || {}, { loja, painel: { abrir, fechar } });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})(window);
