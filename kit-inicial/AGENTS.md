# AGENTS.md — como rodar build e testes

Use só estes comandos. Não invente outros.

## Ambiente
- Python 3.10 ou mais novo.
- Biblioteca padrão do Python. Única dependência externa: `cryptography` (backups criptografados).
- Node.js só para checar a sintaxe do JavaScript do painel (`web/*.js`).
- `cloudflared` só na operação real; nos testes use um falso (`tests/fake_cloudflared.py`).
- Alvo final: Android com Termux. O desenvolvimento acontece em Linux.

## Gate (precisa passar para commitar)
```sh
python3 -m unittest discover -s tests -p "test_*.py"
python3 -m compileall -q .
for f in web/*.js client/*.js; do [ -f "$f" ] && node --check "$f"; done
```

## Estrutura esperada
```
*.py        núcleo (servidor, painel, guardião, monitor, notificações, backup, atualizador)
web/        painel administrativo (HTML/CSS/JS puro, sem framework)
client/     painel do cliente (catálogo)
tests/      testes automáticos (um arquivo por setor)
docs/       arquitetura e guias
config/     .env.example (só valores fictícios)
data/       dados em tempo de execução (ignorado pelo git, exceto data/sites/.keep)
```

## Convenções
- Um arquivo de teste por setor: `tests/test_<setor>.py`.
- Segredos só por variável de ambiente ou pelo painel; nunca no repositório.
- Mensagens para o usuário em português do Brasil.
