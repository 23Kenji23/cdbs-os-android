# Guardião (supervisor)

- Um processo único (`supervisor.py`) inicia e vigia o servidor de sites e o túnel.
- Se um processo cair, reinicia com **espera crescente** entre tentativas e **limite** de reinícios; ao estourar o limite, registra e avisa.
- **Instância única:** uma segunda cópia não pode subir. Processos órfãos de execuções anteriores são encerrados.
- Encerra tudo de forma limpa com Ctrl+C.
- `start.sh` confere Python 3.10+, arquivos obrigatórios e a biblioteca de criptografia; tem modo `--verificar` que não inicia nada.
- No Termux, pede `termux-wake-lock` para o Android não adormecer o processo (ajuda, não garante).
