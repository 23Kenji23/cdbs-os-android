"""cloudflared de MENTIRA para os testes do supervisor (nunca abre túnel de verdade, nunca usa a internet).

Imita o que o supervisor usa do cloudflared real:
  • escreve um link https://<nome>.trycloudflare.com na saída (como o Quick Tunnel faz);
  • responde /ready no endereço de --metrics (200 = conectado, 503 = sem conexão).
Controles por variável de ambiente:
  FAKE_CF_URL         link a anunciar (padrão: um nome aleatório)
  FAKE_CF_READY_FILE  se o arquivo existir e contiver "0", /ready responde 503
  FAKE_CF_EXIT_AFTER  sai com código 1 depois de N segundos (simula queda)
  FAKE_CF_ARGS_FILE   grava os argumentos e se havia TUNNEL_TOKEN no ambiente (para conferir o que o supervisor passou)
"""
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    args = sys.argv[1:]
    metrics = args[args.index("--metrics") + 1] if "--metrics" in args else "127.0.0.1:20241"
    host, port = metrics.rsplit(":", 1)
    record = os.environ.get("FAKE_CF_ARGS_FILE")
    if record:
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"args": args, "has_token": bool(os.environ.get("TUNNEL_TOKEN"))}) + "\n")
    ready_file = os.environ.get("FAKE_CF_READY_FILE", "")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def do_GET(self):
            ok = True
            if ready_file and os.path.exists(ready_file):
                with open(ready_file, encoding="utf-8") as handle:
                    ok = handle.read().strip() != "0"
            status = 200 if self.path == "/ready" and ok else 503
            body = json.dumps({"status": status, "readyConnections": 1 if status == 200 else 0}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = os.environ.get("FAKE_CF_URL") or f"https://teste-{secrets.token_hex(3)}-tunel.trycloudflare.com"
    print("2026-10-26T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
    if "--url" in args:
        print("2026-10-26T10:00:01Z INF +--------------------------------------------+", flush=True)
        print(f"2026-10-26T10:00:01Z INF |  {url}  |", flush=True)
    print(f"2026-10-26T10:00:01Z INF Starting metrics server on {metrics}/metrics", flush=True)
    print("2026-10-26T10:00:02Z INF Registered tunnel connection connIndex=0", flush=True)
    exit_after = float(os.environ.get("FAKE_CF_EXIT_AFTER", "0") or 0)
    started = time.time()
    try:
        while True:
            time.sleep(0.1)
            if exit_after and time.time() - started > exit_after:
                print("2026-10-26T10:00:05Z ERR Connection terminated error=\"falha simulada\"", flush=True)
                return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
