// CDBS.OS 4.0 — navegação por páginas: uma página por vez (Início, Sites, Servidores, Lojas, Backups, Avisos, Sistema,
// Histórico), endereço com #pagina (o botão voltar do Android funciona), e links antigos (#bkSection, #opSlots…) levam à
// página certa. Os módulos de cada seção continuam iguais: aqui só se mostra e esconde a página que os contém.
(() => {
  'use strict';

  const PAGES = ['inicio', 'sites', 'servidores', 'lojas', 'backups', 'avisos', 'sistema', 'historico'];
  const TITLES = { inicio: 'Início', sites: 'Sites', servidores: 'Servidores', lojas: 'Lojas', backups: 'Backups', avisos: 'Avisos', sistema: 'Sistema', historico: 'Histórico' };
  const KEY = 'cdbs:pagina';

  function pageOf(hash) {
    const id = (hash || '').replace(/^#/, '');
    if (PAGES.includes(id)) return { page: id, target: null };
    if (!id) return null;
    const node = document.getElementById(id);
    const holder = node && node.closest('[data-page]');
    return holder ? { page: holder.dataset.page, target: node } : null;
  }

  function show(page, target, focus) {
    for (const node of document.querySelectorAll('[data-page]')) node.hidden = node.dataset.page !== page;
    for (const link of document.querySelectorAll('[data-page-link]')) {
      if (link.dataset.pageLink === page) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    document.title = `CDBS.OS — ${TITLES[page] || 'Painel'}`;
    try { localStorage.setItem(KEY, page); } catch (error) { /* navegador sem armazenamento: tudo bem */ }
    if (target) setTimeout(() => target.scrollIntoView({ block: 'start' }), 0);
    else if (focus) window.scrollTo({ top: 0 });
    if (focus) {
      const heading = document.querySelector(`#pg-${page} .page-head h1`);
      if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    }
    document.dispatchEvent(new CustomEvent('cdbs:page', { detail: { page } }));
  }

  function route(focus) {
    const found = pageOf(location.hash);
    if (found) { show(found.page, found.target, focus); return; }
    let saved = 'inicio';
    try { saved = localStorage.getItem(KEY) || 'inicio'; } catch (error) { /* idem */ }
    show(PAGES.includes(saved) ? saved : 'inicio', null, false);
  }

  window.addEventListener('hashchange', () => route(true));
  document.addEventListener('DOMContentLoaded', () => route(false));

  // Selos no menu: posições com problema, servidores com problema e espaço (vêm da visão geral, desenhada pelo ops.js).
  document.addEventListener('cdbs:overview', (event) => {
    const ov = event.detail || {};
    const set = (page, text, tone) => {
      const badge = document.querySelector(`[data-badge="${page}"]`);
      if (!badge) return;
      badge.hidden = !text;
      badge.textContent = text || '';
      badge.className = `nav-badge ${tone ? `nav-${tone}` : ''}`;
    };
    const counts = (ov.slots && ov.slots.summary && ov.slots.summary.counts) || {};
    const used = ov.slots ? ov.slots.slots.filter((s) => s.slug).length : 0;
    const badSites = (counts.FORA_DO_AR || 0) + (counts.SO_LOCAL || 0);
    set('sites', badSites ? `${badSites} !` : `${used}/${ov.slots ? ov.slots.slots.length : 20}`, badSites ? 'fail' : '');
    if (ov.apps) set('servidores', ov.apps.problems.length ? `${ov.apps.problems.length} !` : `${ov.apps.used}/${ov.apps.slots}`, ov.apps.problems.length ? 'fail' : '');
    const tunnelOk = ov.tunnel && ov.tunnel.state === 'conectado';
    set('sistema', tunnelOk ? '' : '•', tunnelOk ? '' : 'warn');
  });

  window.CDBSNav = { show, route, PAGES };
})();
