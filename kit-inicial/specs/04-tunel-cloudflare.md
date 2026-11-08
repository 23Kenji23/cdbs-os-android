# Túnel Cloudflare

- Modos (`CDBS_TUNNEL_MODE`): `quick` (endereço temporário `https://....trycloudflare.com`), `named` (endereço fixo, exige token) ou `off`.
- No modo `quick` o endereço é lido da saída do `cloudflared` por expressão regular.
- O endereço muda toda vez que o túnel reinicia; o sistema detecta a troca, atualiza o painel e avisa.
- `CDBS_TUNNEL_AUTOSTART`: o guardião liga o túnel sozinho.
- O painel mostra o estado do túnel (conectado, caindo, desligado) e permite ligar e desligar.
- `CDBS_PUBLIC_BASE` define um endereço fixo próprio (domínio comprado) quando existir.
- Nos testes nunca usar o `cloudflared` real: usar um falso que imprime um endereço de exemplo.
