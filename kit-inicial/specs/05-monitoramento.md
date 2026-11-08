# Monitoramento

- Verifica as 10 posições em intervalo configurável (`CDBS_MONITOR_INTERVAL_MINUTES`).
- Duas checagens: **local** (o servidor responde neste aparelho) e **pública** (abre pelo endereço do túnel, opcional, `CDBS_MONITOR_PUBLIC_CHECK`).
- Estados: `ativo`, `só no aparelho`, `fora do ar`, `inconclusivo`, `pausado`, `vago`.
- `inconclusivo` é usado quando não dá para saber (sem internet, túnel desligado). Nunca marcar "fora do ar" por palpite.
- Só notifica quando o estado **muda**, para não repetir alerta a cada checagem.
- O estado persiste em disco e sobrevive a reinício.
