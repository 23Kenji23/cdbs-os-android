// 4.0 — o painel tem páginas (Início, Sites, Servidores…). Os testes de navegador foram escritos para a página única da
// 3.0; este auxiliar faz o que a pessoa faz: antes de tocar/ler um elemento, abre pelo menu a página onde ele mora.
// Não "mostra tudo": o elemento só fica visível porque a página dele foi aberta de verdade (location.hash → nav.js).
'use strict';

// Do seletor do Playwright, só a parte CSS que dá para achar no documento (o #id do começo, se houver).
function css(selector) {
  if (typeof selector !== 'string') return null;
  const id = selector.match(/^#[\w-]+/);
  if (id) return id[0];
  if (/:has-text|text=|>>|:has\(/.test(selector)) return null;
  return selector;
}

async function abrir(page, selector) {
  const target = css(selector);
  if (!target) return;
  const changed = await page.evaluate((sel) => {
    let node = null;
    try { node = document.querySelector(sel); } catch (error) { return false; }
    const holder = node && node.closest('[data-page]');
    if (!holder || !holder.hidden) return false;
    location.hash = holder.dataset.page;
    return true;
  }, target).catch(() => false);
  if (changed) await page.waitForFunction((sel) => { const n = document.querySelector(sel); const h = n && n.closest('[data-page]'); return !h || !h.hidden; }, target, { timeout: 5000 }).catch(() => {});
}

const ACOES = ['tap', 'click', 'fill', 'selectOption', 'check', 'uncheck', 'setInputFiles', 'innerText', 'textContent', 'isDisabled', 'isChecked',
  'inputValue', 'getAttribute', 'focus', 'press', 'type', 'dblclick', 'hover', 'scrollIntoViewIfNeeded'];

function locatorComPagina(page, locator, base) {
  return new Proxy(locator, {
    get(target, prop) {
      if (prop === '__base') return base;
      const value = target[prop];
      if (typeof value !== 'function') return value;
      if (prop === 'locator' || prop === 'first' || prop === 'last' || prop === 'nth' || prop === 'filter') {
        return (...args) => locatorComPagina(page, value.apply(target, args), base);
      }
      if (ACOES.includes(prop) || prop === 'waitFor') {
        return async (...args) => { await abrir(page, base); return value.apply(target, args); };
      }
      return value.bind(target);
    },
  });
}

function acompanhar(page) {
  for (const name of ACOES) {
    const original = page[name] && page[name].bind(page);
    if (!original) continue;
    page[name] = async (selector, ...rest) => { await abrir(page, selector); return original(selector, ...rest); };
  }
  const wait = page.waitForSelector.bind(page);
  page.waitForSelector = async (selector, options = {}) => {
    if (options.state && options.state !== 'visible') return wait(selector, options);
    const started = Date.now();
    const limit = options.timeout || 30000;
    while (Date.now() - started < limit) {                           // espera o elemento EXISTIR e então abre a página dele
      const exists = await page.evaluate((sel) => { try { return !!document.querySelector(sel); } catch (e) { return true; } }, css(selector) || 'body').catch(() => false);
      if (exists) break;
      await new Promise((r) => setTimeout(r, 100));
    }
    await abrir(page, selector);
    return wait(selector, { ...options, timeout: Math.max(1000, limit - (Date.now() - started)) });
  };
  const locate = page.locator.bind(page);
  page.locator = (selector, options) => {
    // locator('details', { has: locator('#removedList') }): quem diz a página é o elemento de dentro
    const inner = options && options.has && options.has.__base;
    return locatorComPagina(page, locate(selector, options), inner || selector);
  };
  return page;
}

module.exports = { acompanhar, abrir };
