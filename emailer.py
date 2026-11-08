"""CDBS.OS — envio de avisos por E-MAIL (SMTP), além ou no lugar do WhatsApp.

Usa a biblioteca padrão do Python (smtplib), sem nada para instalar. Serve qualquer provedor com SMTP:
  • Gmail: smtp.gmail.com, porta 465 (SSL) — precisa de "senha de app" (com a verificação em duas etapas ligada);
  • outros: os dados SMTP do seu provedor (servidor, porta e tipo de segurança).
Segurança:
  • conexão sempre criptografada (SSL ou STARTTLS) com certificado conferido; sem criptografia só para 127.0.0.1
    (usado nos testes);
  • a senha fica em data/system/secrets.json (0600) ou no .env e nunca aparece em histórico, erro ou tela;
  • "enviado" quer dizer que o SEU servidor de e-mail aceitou a mensagem — não é confirmação de que chegou na caixa.
"""
from __future__ import annotations

import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
SECURITY = ("ssl", "starttls", "none")
TIMEOUT = 20.0
PRESETS = {                                    # o painel oferece estes atalhos
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl"},
}


class EmailError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def valid_email(value: object) -> bool:
    return isinstance(value, str) and bool(EMAIL_RE.fullmatch(value.strip()))


def mask_email(value: object) -> str:
    """'joao.silva@gmail.com' -> 'jo•••@gmail.com' (o suficiente para reconhecer, sem expor o endereço inteiro)."""
    if not isinstance(value, str) or "@" not in value:
        return "•••"
    user, domain = value.rsplit("@", 1)
    return f"{user[:2]}•••@{domain}"


class SMTPSender:
    def __init__(self, host: str, port: int, security: str, user: str, password: str, from_address: str, *, timeout: float = TIMEOUT):
        if not host or not HOST_RE.fullmatch(host):
            raise EmailError("SEM_CONFIG", "Falta o servidor SMTP (ex.: smtp.gmail.com).")
        if security not in SECURITY:
            raise EmailError("CONFIG", "Tipo de segurança inválido (ssl, starttls).")
        if security == "none" and host not in ("127.0.0.1", "localhost"):
            raise EmailError("CONFIG", "E-mail sem criptografia só é permitido para 127.0.0.1. Use SSL ou STARTTLS.")
        if not valid_email(from_address):
            raise EmailError("SEM_CONFIG", "Falta o endereço de envio (remetente).")
        if user and not password:
            raise EmailError("SEM_CONFIG", "Falta a senha do e-mail (no Gmail, a senha de app).")
        self.host, self.port, self.security = host, int(port), security
        self.user, self._password, self.from_address = user, password, from_address.strip()
        self.timeout = timeout

    def __repr__(self) -> str:                                  # nunca mostra a senha, nem em depuração
        return "SMTPSender(<configurado>)"

    def send(self, to: str, subject: str, body: str) -> dict:
        if not valid_email(to):
            raise EmailError("DESTINO_INVALIDO", "Endereço de destino inválido.")
        message = EmailMessage()
        message["From"] = f"CDBS.OS <{self.from_address}>"
        message["To"] = to
        message["Subject"] = subject[:150].replace("\n", " ")
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=self.from_address.rsplit("@", 1)[1])
        message.set_content(body)
        context = ssl.create_default_context()
        try:
            if self.security == "ssl":
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with client:
                client.ehlo()
                if self.security == "starttls":
                    client.starttls(context=context)
                    client.ehlo()
                if self.user:
                    client.login(self.user, self._password)
                refused = client.send_message(message)
        except smtplib.SMTPAuthenticationError:
            raise EmailError("LOGIN_RECUSADO", "O servidor de e-mail recusou o usuário/senha. No Gmail, use uma SENHA DE APP "
                                               "(com a verificação em duas etapas ligada), não a senha normal.") from None
        except smtplib.SMTPRecipientsRefused:
            raise EmailError("DESTINO_RECUSADO", "O servidor de e-mail recusou o endereço de destino.") from None
        except smtplib.SMTPSenderRefused:
            raise EmailError("REMETENTE_RECUSADO", "O servidor de e-mail recusou o remetente (use o mesmo endereço da conta).") from None
        except ssl.SSLError:
            raise EmailError("TLS", "Falha na conexão segura com o servidor de e-mail (porta ou tipo de segurança errados?).") from None
        except smtplib.SMTPNotSupportedError:
            raise EmailError("CONFIG", "O servidor não aceita esse tipo de segurança/login nessa porta.") from None
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPHeloError, smtplib.SMTPDataError):
            raise EmailError("SERVIDOR", "O servidor de e-mail fechou a conexão. O CDBS tenta de novo mais tarde.", retryable=True) from None
        except socket.gaierror:
            raise EmailError("SEM_REDE", "Sem internet (não foi possível achar o servidor de e-mail).", retryable=True) from None
        except (socket.timeout, TimeoutError):
            raise EmailError("TIMEOUT", "O servidor de e-mail não respondeu a tempo.", retryable=True) from None
        except OSError:
            raise EmailError("SEM_REDE", "Falha de rede ao falar com o servidor de e-mail.", retryable=True) from None
        if refused:
            raise EmailError("DESTINO_RECUSADO", "O servidor de e-mail recusou o endereço de destino.")
        return {"message_id": str(message["Message-ID"])[:200], "accepted": True}


def doctor_checks(ctx) -> list:
    import settings as settings_mod                                         # noqa: PLC0415
    cfg = settings_mod.Settings(ctx.data / "system", environ={**ctx.env})
    mail = cfg.get()["email"]
    if not mail["enabled"]:
        return [("e-mail", "envio", "INFO", "avisos por e-mail desligados")]
    ready, why = cfg.email_ready()
    if not ready:
        return [("e-mail", "envio", "AVISO", f"ligado, mas {why}", "Painel → Notificações → E-mail")]
    return [("e-mail", "envio", "OK", f"configurado: {mail['smtp_host']}:{mail['smtp_port']} ({mail['smtp_security']}) para "
             f"{len(mail['recipients'])} endereço(s)")]
