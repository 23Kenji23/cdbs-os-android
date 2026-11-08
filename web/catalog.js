/* CDBS.OS — ajudante OPCIONAL para ler o catálogo da loja.

   Uso no HTML do seu site (mesmo endereço do site, então não precisa configurar nada):
     <script src="/_cdbs/catalog.js"></script>
     const loja = await CDBS.carregar('nome-da-loja');   // { store, categories, products, updated }

   Você também pode ignorar este arquivo e ler o JSON direto:
     fetch('/_api/nome-da-loja/products.json')

   SEGURANÇA — leia: quem digita nome, descrição e categoria é o CLIENTE. Para colocar esses textos
   na página use SEMPRE textContent (ou escape). Nunca innerHTML com texto do produto, senão um
   cliente mal-intencionado (ou uma conta invadida) consegue injetar script no seu site.
*/
(function (global) {
  'use strict';

  const CDBS = {
    /** Baixa o catálogo. Lança erro se a loja não existir ou o servidor estiver fora do ar. */
    async carregar(loja) {
      const resposta = await fetch(`/_api/${encodeURIComponent(loja)}/products.json`, { cache: 'no-cache' });
      if (!resposta.ok) throw new Error('Catálogo indisponível');
      return resposta.json();
    },

    /** "R$ 89,90" ou "Consulte" quando o cliente deixou o preço em branco. */
    preco(produto) {
      return produto.price_text || 'Consulte';
    },

    /** Link do botão de WhatsApp com a mensagem pronta. Devolve '' se a loja não tem número cadastrado. */
    linkWhatsApp(loja, produto, mensagem) {
      const numero = (loja.store && loja.store.whatsapp) || '';
      if (!numero) return '';
      const texto = mensagem
        || `Olá! Tenho interesse em: ${produto.name}${produto.price_text ? ` (${produto.price_text})` : ''}`;
      return `https://wa.me/${numero}?text=${encodeURIComponent(texto)}`;
    },
  };

  global.CDBS = Object.assign(global.CDBS || {}, CDBS);   // convive com o /_cdbs/admin.js
})(window);
