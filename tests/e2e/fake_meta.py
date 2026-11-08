"""API da Meta (WhatsApp Cloud API) DE MENTIRA — só para o teste no navegador. Nada sai para a internet.

Uso: python3 -I fake_meta.py PORTA ARQUIVO_DE_REGISTRO TOKEN_ESPERADO

  • POST /<versão>/<phone_number_id>/messages com "Authorization: Bearer <token certo>" → 200 com um wamid novo
    (o mesmo formato da Meta: {"messaging_product", "contacts", "messages": [{"id": ...}]}).
  • Token diferente → 401 com o erro 190 (o que a Meta devolve para token inválido/expirado).
  • Cada pedido vira uma linha JSON no registro: número de ordem, caminho, se o token conferiu, o corpo e o wamid
    devolvido. O token em si NUNCA é gravado — o teste confere só "auth_ok".
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG, TOKEN = int(sys.argv[1]), sys.argv[2], sys.argv[3]
LOCK = threading.Lock()
COUNT = [0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_invalido": raw[:200].decode("utf-8", "replace")}
        auth_ok = self.headers.get("Authorization") == f"Bearer {TOKEN}"
        path_ok = bool(re.fullmatch(r"/v\d{1,2}\.\d/\d+/messages", self.path))
        with LOCK:
            COUNT[0] += 1
            wamid = f"wamid.E2E{COUNT[0]:04d}" if auth_ok and path_ok else ""
            with open(LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"n": COUNT[0], "path": self.path, "auth_ok": auth_ok, "body": body, "wamid": wamid}, ensure_ascii=False) + "\n")
        if not auth_ok:
            self._send(401, {"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}})
        elif not path_ok:
            self._send(404, {"error": {"message": "Unknown path", "type": "OAuthException", "code": 100}})
        else:
            to = str(body.get("to", ""))
            self._send(200, {"messaging_product": "whatsapp", "contacts": [{"input": to, "wa_id": to}], "messages": [{"id": wamid}]})


server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
print(f"API da Meta de mentira em http://127.0.0.1:{PORT}", flush=True)
server.serve_forever()
