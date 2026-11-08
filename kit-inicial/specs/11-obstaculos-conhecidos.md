# Obstáculos conhecidos e como tratar

1. **Android suspende processos em segundo plano** (Doze/App Standby). Tratar: `termux-wake-lock`, aparelho na tomada, sem otimização de bateria; documentar que não há garantia 24 h.
2. **Quick Tunnel muda de endereço** ao reiniciar. Tratar: detectar, atualizar painel, avisar; oferecer `CDBS_PUBLIC_BASE` com domínio próprio.
3. **Webhook da Meta precisa de endereço fixo.** Tratar: avisar quando o endereço muda e orientar a atualizar o webhook.
4. **Segredo vazando para log ou zip.** Tratar: mascaramento, `.gitignore`, `.env.example` só com ficção.
5. **Caminhos do Termux diferem do Linux** (`/data/data/com.termux/...`). Tratar: nunca fixar caminho absoluto; usar variáveis.
6. **Processo órfão** depois de queda do guardião. Tratar: instância única e limpeza no início.
7. **Falta de teste em Android real.** Tratar: testes simulados e uma lista explícita do que falta validar no aparelho.
