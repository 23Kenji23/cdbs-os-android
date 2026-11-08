"""Servidor de E-MAIL (SMTP) DE MENTIRA — só para o teste no navegador, só em 127.0.0.1, sem criptografia.

Uso: python3 -I fake_smtp.py PORTA ARQUIVO_DE_REGISTRO USUARIO SENHA
Confere usuário/senha (AUTH PLAIN) e grava cada mensagem recebida como uma linha JSON (destinatários e texto).
A senha nunca é gravada: o registro só diz se o login conferiu.
"""
import base64
import json
import socket
import sys
import threading

PORT, LOG, USER, PASSWORD = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
LOCK = threading.Lock()


def session(conn):
    f = conn.makefile("rb")
    send = lambda line: conn.sendall(line.encode() + b"\r\n")   # noqa: E731
    send("220 fake ESMTP")
    mail, logged = {"to": []}, False
    try:
        while True:
            raw = f.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            cmd = line[:4].upper()
            if cmd in ("EHLO", "HELO"):
                send("250-fake")
                send("250-AUTH PLAIN LOGIN")
                send("250 OK")
            elif cmd == "AUTH":
                parts = line.split()
                if len(parts) >= 3 and parts[1].upper() == "PLAIN":
                    _, user, password = base64.b64decode(parts[2]).decode().split("\0")
                    logged = (user, password) == (USER, PASSWORD)
                send("235 ok" if logged else "535 5.7.8 credenciais recusadas")
            elif cmd == "MAIL":
                mail = {"to": [], "login_ok": logged}
                send("250 ok")
            elif cmd == "RCPT":
                mail["to"].append(line.split("<", 1)[1].split(">", 1)[0])
                send("250 ok")
            elif cmd == "DATA":
                send("354 manda")
                body = []
                while True:
                    chunk = f.readline().decode("utf-8", "replace")
                    if chunk.rstrip("\r\n") == ".":
                        break
                    body.append(chunk)
                mail["data"] = "".join(body)
                with LOCK, open(LOG, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(mail, ensure_ascii=False) + "\n")
                send("250 guardado")
            elif cmd == "QUIT":
                send("221 tchau")
                return
            else:
                send("250 ok")
    finally:
        conn.close()


server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", PORT))
server.listen(5)
print(f"SMTP de mentira em 127.0.0.1:{PORT}", flush=True)
while True:
    connection, _ = server.accept()
    threading.Thread(target=session, args=(connection,), daemon=True).start()
