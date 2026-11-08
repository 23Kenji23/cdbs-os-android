"""CDBS.OS — integração com o WhatsApp pela API OFICIAL (WhatsApp Business Platform / Cloud API).

Nada de WhatsApp Web, QR code ou biblioteca não oficial. O que este módulo faz:
  • enviar mensagem de texto, de modelo (template), lista interativa e botões, com prazo curto e erros traduzidos;
  • conferir a assinatura `X-Hub-Signature-256` dos eventos recebidos (HMAC-SHA256 do corpo com a chave do app);
  • responder ao aperto de mão (`hub.challenge`) quando a Meta cadastra o webhook;
  • transformar o JSON recebido em mensagens e atualizações de entrega.
O token de acesso NUNCA aparece em mensagem de erro, registro ou resposta.

Referências: developers.facebook.com/documentation/business-messaging/whatsapp (mensagens, modelos, webhooks, preços).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sysutil import scrub

DEFAULT_BASE = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"
TIMEOUT = 10.0
MAX_TEXT = 4000                                   # a API aceita até 4096 no texto
PARAM_MAX = 200

# código de erro da Meta -> (código nosso, pode tentar de novo?, explicação + o que fazer)
META_ERRORS = {
    190: ("TOKEN_INVALIDO", False, "O token de acesso expirou ou é inválido. Gere um token permanente (usuário do sistema) no painel da Meta e salve de novo."),
    10: ("SEM_PERMISSAO", False, "O app não tem permissão para enviar por este número (whatsapp_business_messaging)."),
    200: ("SEM_PERMISSAO", False, "O app não tem permissão para enviar por este número (whatsapp_business_messaging)."),
    100: ("PARAMETRO_INVALIDO", False, "A API recusou o pedido (parâmetro inválido). Confira o Phone number ID e o número de destino."),
    4: ("LIMITE", True, "Limite de chamadas da API atingido. O CDBS tenta de novo mais tarde."),
    80007: ("LIMITE", True, "Limite de chamadas da conta atingido. O CDBS tenta de novo mais tarde."),
    130429: ("LIMITE", True, "Limite de mensagens por segundo atingido. O CDBS tenta de novo mais tarde."),
    131056: ("LIMITE", True, "Muitas mensagens para o mesmo número em pouco tempo. O CDBS tenta de novo mais tarde."),
    131047: ("JANELA_FECHADA", False, "Passaram mais de 24 h desde a última mensagem desse número: só dá para enviar um MODELO aprovado."),
    131026: ("NAO_ENTREGAVEL", False, "Não foi possível entregar para esse número (não tem WhatsApp, ou não aceitou os termos)."),
    131030: ("DESTINATARIO_NAO_PERMITIDO", False, "Esse número não está na lista de destinatários permitidos do número de teste da Meta."),
    131009: ("PARAMETRO_INVALIDO", False, "Um valor da mensagem é inválido."),
    131051: ("TIPO_NAO_SUPORTADO", False, "Tipo de mensagem não suportado."),
    131000: ("ERRO_META", True, "Erro temporário na Meta. O CDBS tenta de novo mais tarde."),
    131016: ("ERRO_META", True, "Serviço da Meta sobrecarregado. O CDBS tenta de novo mais tarde."),
    132000: ("MODELO_PARAMETROS", False, "O número de parâmetros não bate com o modelo aprovado."),
    132001: ("MODELO_INEXISTENTE", False, "O modelo não existe (ou ainda não foi aprovado) nesse idioma. Confira o nome e o idioma."),
    132005: ("MODELO_TEXTO_LONGO", False, "O texto do modelo preenchido ficou longo demais."),
    132007: ("MODELO_POLITICA", False, "O conteúdo viola a política de modelos da Meta."),
    132012: ("MODELO_PARAMETROS", False, "O formato dos parâmetros não bate com o modelo."),
    132015: ("MODELO_PAUSADO", False, "O modelo foi pausado pela Meta por baixa qualidade."),
    132016: ("MODELO_DESATIVADO", False, "O modelo foi desativado pela Meta."),
    132018: ("MODELO_PARAMETROS", False, "Um parâmetro tem quebra de linha, tabulação ou espaços demais (o CDBS limpa isso; avise se repetir)."),
    133010: ("NUMERO_NAO_REGISTRADO", False, "O número de envio não está registrado na Cloud API."),
}


class WhatsAppError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int = 0, meta_code: int | None = None):
        super().__init__(scrub(message, 300))
        self.code, self.retryable, self.http_status, self.meta_code = code, retryable, http_status, meta_code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable, "http_status": self.http_status, "meta_code": self.meta_code}


def clean_param(text: object) -> str:
    """A API recusa parâmetro de modelo com quebra de linha, tabulação ou mais de 4 espaços seguidos (erro 132018)."""
    value = re.sub(r"[\r\n\t]+", " · ", str(text or ""))
    value = re.sub(r" {4,}", "   ", value).strip()
    return (value or "—")[:PARAM_MAX]


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex HMAC-SHA256(corpo cru, chave do app)>. Comparação em tempo constante."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:].strip().lower())


def handshake(params: dict, verify_token: str) -> str | None:
    """GET de verificação do webhook: devolve o hub.challenge (texto puro) só se o token conferir."""
    mode = (params.get("hub.mode") or [""])[0]
    token = (params.get("hub.verify_token") or [""])[0]
    challenge = (params.get("hub.challenge") or [""])[0]
    if mode != "subscribe" or not verify_token or not hmac.compare_digest(token.encode(), verify_token.encode()):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", challenge):
        return None
    return challenge


@dataclass
class Inbound:
    kind: str                               # "message" ou "status"
    sender: str = ""                        # wa_id de quem mandou (dígitos)
    message_id: str = ""
    timestamp: int = 0
    msg_type: str = ""
    text: str = ""
    reply_id: str = ""                      # id da linha da lista / do botão escolhido
    reply_title: str = ""
    status: str = ""                        # sent / delivered / read / failed
    recipient: str = ""
    error: dict = field(default_factory=dict)
    phone_number_id: str = ""


def parse_webhook(payload: object) -> list[Inbound]:
    out: list[Inbound] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            phone_id = str((value.get("metadata") or {}).get("phone_number_id", ""))
            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                item = Inbound(kind="message", sender=re.sub(r"\D", "", str(msg.get("from", "")))[:15], message_id=str(msg.get("id", ""))[:200],
                               timestamp=int(str(msg.get("timestamp", "0")).strip() or 0) if str(msg.get("timestamp", "0")).strip().isdigit() else 0,
                               msg_type=str(msg.get("type", ""))[:20], phone_number_id=phone_id)
                if item.msg_type == "text":
                    item.text = str((msg.get("text") or {}).get("body", ""))[:1000]
                elif item.msg_type == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("list_reply") or inter.get("button_reply") or {}
                    item.reply_id, item.reply_title = str(reply.get("id", ""))[:200], str(reply.get("title", ""))[:60]
                elif item.msg_type == "button":                          # resposta a botão de MODELO
                    button = msg.get("button") or {}
                    item.reply_id, item.reply_title = str(button.get("payload", ""))[:200], str(button.get("text", ""))[:60]
                out.append(item)
            for status in value.get("statuses") or []:
                if not isinstance(status, dict):
                    continue
                errors = status.get("errors") or []
                out.append(Inbound(kind="status", message_id=str(status.get("id", ""))[:200], status=str(status.get("status", ""))[:20],
                                   recipient=re.sub(r"\D", "", str(status.get("recipient_id", "")))[:15],
                                   timestamp=int(status.get("timestamp", 0) or 0) if str(status.get("timestamp", "0")).isdigit() else 0,
                                   error={"code": errors[0].get("code"), "title": scrub(errors[0].get("title", ""), 120)} if errors and isinstance(errors[0], dict) else {},
                                   phone_number_id=phone_id))
    return out


def _validate_base(base: str) -> str:
    parts = urlsplit(base)
    local = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        raise WhatsAppError("CONFIG", "Endereço da API inválido (precisa ser https).")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise WhatsAppError("CONFIG", "Endereço da API inválido.")
    return base.rstrip("/")


class CloudAPI:
    def __init__(self, phone_number_id: str, token: str, *, api_version: str = DEFAULT_VERSION, base: str = DEFAULT_BASE,
                 timeout: float = TIMEOUT):
        if not phone_number_id or not phone_number_id.isdigit():
            raise WhatsAppError("SEM_CONFIG", "Falta o identificador do número de envio (Phone number ID).")
        if not token:
            raise WhatsAppError("SEM_CONFIG", "Falta o token de acesso da API.")
        if not re.fullmatch(r"v\d{1,2}\.\d", api_version):
            raise WhatsAppError("CONFIG", "Versão da API inválida.")
        self.url = f"{_validate_base(base)}/{api_version}/{phone_number_id}/messages"
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}) if urlsplit(base).hostname in ("127.0.0.1", "localhost")
                                                   else urllib.request.ProxyHandler())

    def __repr__(self) -> str:                                  # nunca mostra o token, nem em depuração
        return "CloudAPI(<configurado>)"

    # ----- tipos de mensagem
    def send_text(self, to: str, body: str) -> dict:
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
                           "text": {"preview_url": False, "body": body[:MAX_TEXT]}})

    def send_template(self, to: str, name: str, language: str, params: list[str]) -> dict:
        components = [{"type": "body", "parameters": [{"type": "text", "text": clean_param(p)} for p in params]}] if params else []
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "template",
                           "template": {"name": name, "language": {"code": language}, "components": components}})

    def send_list(self, to: str, body: str, button: str, rows: list[tuple[str, str, str]], *, header: str = "", footer: str = "") -> dict:
        interactive: dict = {"type": "list", "body": {"text": body[:4096]},
                             "action": {"button": button[:20], "sections": [{"title": "Opções"[:24], "rows": [
                                 {"id": rid[:200], "title": title[:24], **({"description": desc[:72]} if desc else {})} for rid, title, desc in rows[:10]]}]}}
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "interactive", "interactive": interactive})

    def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "interactive",
                           "interactive": {"type": "button", "body": {"text": body[:1024]},
                                           "action": {"buttons": [{"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}} for bid, title in buttons[:3]]}}})

    # ----- transporte
    def _post(self, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url, data=data, method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}", "User-Agent": "CDBS.OS"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:      # noqa: S310 — URL fixa da API configurada
                body = response.read(65536)
                status = response.status
        except urllib.error.HTTPError as error:
            raise self._http_error(error.code, error.read(65536)) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise WhatsAppError("TIMEOUT", "A API do WhatsApp não respondeu a tempo.", retryable=True) from None
            if isinstance(error.reason, socket.gaierror):
                raise WhatsAppError("SEM_REDE", "Sem internet (não foi possível resolver o endereço da API).", retryable=True) from None
            raise WhatsAppError("SEM_REDE", "Falha de rede ao falar com a API do WhatsApp.", retryable=True) from None
        except (socket.timeout, TimeoutError):
            raise WhatsAppError("TIMEOUT", "A API do WhatsApp não respondeu a tempo.", retryable=True) from None
        except OSError:
            raise WhatsAppError("SEM_REDE", "Falha de rede ao falar com a API do WhatsApp.", retryable=True) from None
        try:
            parsed = json.loads(body or b"{}")
        except ValueError:
            raise WhatsAppError("RESPOSTA_INVALIDA", "A API respondeu algo que não é JSON.", retryable=True, http_status=status) from None
        messages = parsed.get("messages") if isinstance(parsed, dict) else None
        if not messages or not isinstance(messages, list) or not isinstance(messages[0], dict) or not messages[0].get("id"):
            raise WhatsAppError("RESPOSTA_INVALIDA", "A API não devolveu o identificador da mensagem.", retryable=False, http_status=status)
        return {"message_id": str(messages[0]["id"])[:200], "accepted": True, "status": messages[0].get("message_status", "accepted")}

    @staticmethod
    def _http_error(status: int, body: bytes) -> WhatsAppError:
        try:
            info = json.loads(body or b"{}").get("error") or {}
        except (ValueError, AttributeError):
            info = {}
        code = info.get("code") if isinstance(info, dict) else None
        if isinstance(code, int) and code in META_ERRORS:
            ours, retry, text = META_ERRORS[code]
            return WhatsAppError(ours, text, retryable=retry, http_status=status, meta_code=code)
        if status >= 500:
            return WhatsAppError("ERRO_META", f"A API do WhatsApp falhou (HTTP {status}). O CDBS tenta de novo mais tarde.", retryable=True, http_status=status, meta_code=code)
        if status == 429:
            return WhatsAppError("LIMITE", "Limite de chamadas atingido. O CDBS tenta de novo mais tarde.", retryable=True, http_status=status, meta_code=code)
        if status in (401, 403):
            return WhatsAppError("TOKEN_INVALIDO", "A API recusou o token de acesso (HTTP %d)." % status, http_status=status, meta_code=code)
        return WhatsAppError("ERRO_API", f"A API recusou o envio (HTTP {status}{f', código {code}' if code else ''}).", http_status=status, meta_code=code)


def doctor_checks(ctx) -> list:
    """Sem mostrar valor nenhum: só se está configurado e coerente."""
    import settings as settings_mod                                         # noqa: PLC0415
    cfg = settings_mod.Settings(ctx.data / "system", environ={**ctx.env})
    data = cfg.get()
    out = []
    ready, why = cfg.whatsapp_ready()
    enabled = data["notifications"]["enabled"]
    if not enabled:
        out.append(("whatsapp", "envio", "INFO", "notificações por WhatsApp desligadas (o monitoramento local funciona igual)"))
    elif not ready:
        out.append(("whatsapp", "envio", "AVISO", f"ligado, mas {why}", "Painel → Notificações → Credenciais do WhatsApp"))
    elif not data["notifications"]["recipients"]:
        out.append(("whatsapp", "envio", "AVISO", "ligado, mas sem número de destino", "Painel → Notificações → Destinatários"))
    else:
        out.append(("whatsapp", "envio", "OK", f"configurado para {len(data['notifications']['recipients'])} destinatário(s); modelos: "
                    f"{data['whatsapp']['template_report'] or '—'} / {data['whatsapp']['template_alert'] or '—'}"))
    if data["bot"]["enabled"]:
        if not cfg.secret("whatsapp_app_secret"):
            out.append(("whatsapp", "bot", "FALHA", "bot ligado sem a chave do app (App Secret): os eventos não podem ser conferidos e são recusados",
                        "Painel → Notificações → Credenciais → App Secret"))
        elif not data["bot"]["admins"] and not data["bot"]["viewers"]:
            out.append(("whatsapp", "bot", "AVISO", "bot ligado sem nenhum número autorizado (ninguém consegue usar)", "Painel → Bot → números autorizados"))
        else:
            out.append(("whatsapp", "bot", "OK", f"bot ligado · {len(data['bot']['admins'])} administrador(es) · {len(data['bot']['viewers'])} só consulta"))
    return out
