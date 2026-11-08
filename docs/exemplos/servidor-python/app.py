"""Exemplo de servidor para o CDBS.OS 4.0 — Python puro (sem instalar nada).

O que ele mostra:
  • ler a porta do CDBS (PORT) e escutar SÓ no celular (HOST = 127.0.0.1);
  • responder a verificação de saúde (/saude), que o guardião usa para saber se está vivo;
  • guardar dados na pasta persistente (CDBS_APP_DATA), que sobrevive a implantações novas;
  • receber dados de um formulário (POST) — algo que um site estático não faz.

Como usar: compacte ESTA pasta em .zip, crie no painel um servidor tipo Python com arquivo de entrada app.py e caminho de
saúde /saude, envie o zip e toque em "Implantar". Ele fica em https://SEU-LINK/NOME/.
"""
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PORT = int(os.environ.get("PORT", "8101"))
HOST = os.environ.get("HOST", "127.0.0.1")
BASE = os.environ.get("CDBS_BASE_PATH", "")              # ex.: /recados — use em links absolutos
DATA = Path(os.environ.get("CDBS_APP_DATA", "dados"))
DATA.mkdir(parents=True, exist_ok=True)
FILE = DATA / "recados.json"
LOCK = threading.Lock()


def load() -> list:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save(items: list) -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FILE)                                  # troca atômica: nunca fica meio gravado


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):                     # aparece no Registro do painel
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send(self, status, body, kind="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/saude":
            return self.send(200, "ok", "text/plain")
        if self.path == "/api/recados":
            return self.send(200, json.dumps(load(), ensure_ascii=False), "application/json; charset=utf-8")
        items = "".join(f"<li>{html.escape(r['texto'])} <small>{time.strftime('%d/%m %H:%M', time.localtime(r['em']))}</small></li>" for r in reversed(load()[-50:]))
        self.send(200, f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Mural de recados</title><body style="font:16px system-ui;max-width:560px;margin:40px auto;padding:0 16px">
<h1>Mural de recados</h1><p>Exemplo de servidor no CDBS: o recado fica guardado no celular.</p>
<form method=post action="{BASE}/recado"><input name=texto maxlength=200 required style="width:70%;padding:8px"> <button>Enviar</button></form>
<ul>{items or '<li>Nenhum recado ainda.</li>'}</ul>""")

    def do_POST(self):
        if self.path != "/recado":
            return self.send(404, "não encontrado", "text/plain")
        size = min(int(self.headers.get("Content-Length") or 0), 4096)
        texto = (parse_qs(self.rfile.read(size).decode("utf-8", "replace")).get("texto") or [""])[0].strip()[:200]
        if texto:
            with LOCK:
                items = load() + [{"texto": texto, "em": int(time.time())}]
                save(items[-500:])
        self.send_response(303)
        self.send_header("Location", "/")                  # o CDBS corrige para /NOME/ sozinho
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    print(f"mural de recados em {HOST}:{PORT} (público em {BASE}/)", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
