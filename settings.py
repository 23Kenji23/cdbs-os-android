"""CDBS.OS — configurações do monitoramento, das notificações, do bot e do túnel.

Três fontes, nesta ordem de prioridade:
  1. variáveis de ambiente / `.env` (o que estiver ali VENCE e aparece no painel como "definido no .env");
  2. `data/system/settings.json` — o que você muda pelo painel (nada secreto);
  3. valores padrão.
Segredos (token do WhatsApp, chave do app, token de verificação, token do túnel) ficam em `data/system/secrets.json`
(0600) ou no `.env`. Eles podem ser GRAVADOS pelo painel, mas nunca são devolvidos: o painel só sabe se existem e
os 4 últimos caracteres.
"""
from __future__ import annotations

import copy
import os
import re
import secrets as _secrets
import threading
import time
from pathlib import Path

from sysutil import digits, mask_phone, mask_secret, read_json, write_json_private

DEFAULTS: dict = {
    "monitor": {"enabled": True, "interval_minutes": 30, "public_check": False},
    "notifications": {"enabled": False, "recipients": [], "report_enabled": True, "alerts_enabled": True, "cooldown_minutes": 60},
    "whatsapp": {"template_report": "cdbs_relatorio_sites", "template_alert": "cdbs_alerta_site", "template_language": "pt_BR",
                 "api_version": "v26.0", "report_variables": 12},
    "bot": {"enabled": False, "admins": [], "viewers": [], "interactive": True},
    "tunnel": {"mode": "quick", "autostart": False},
    "email": {"enabled": False, "recipients": [], "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl", "smtp_user": "",
              "from_address": ""},
}
# campo do painel -> variável de ambiente que o substitui
ENV_OVERRIDES = {
    ("monitor", "enabled"): "CDBS_MONITOR_ENABLED",
    ("monitor", "interval_minutes"): "CDBS_MONITOR_INTERVAL_MINUTES",
    ("monitor", "public_check"): "CDBS_MONITOR_PUBLIC_CHECK",
    ("notifications", "enabled"): "CDBS_WHATSAPP_ENABLED",
    ("notifications", "recipients"): "CDBS_WHATSAPP_RECIPIENT",
    ("whatsapp", "template_report"): "CDBS_WHATSAPP_TEMPLATE_REPORT",
    ("whatsapp", "template_alert"): "CDBS_WHATSAPP_TEMPLATE_ALERT",
    ("whatsapp", "template_language"): "CDBS_WHATSAPP_TEMPLATE_LANGUAGE",
    ("whatsapp", "api_version"): "CDBS_WHATSAPP_API_VERSION",
    ("whatsapp", "report_variables"): "CDBS_WHATSAPP_REPORT_VARIABLES",
    ("bot", "enabled"): "CDBS_BOT_ENABLED",
    ("bot", "admins"): "CDBS_BOT_ADMINS",
    ("bot", "viewers"): "CDBS_BOT_VIEWERS",
    ("bot", "interactive"): "CDBS_BOT_INTERACTIVE",
    ("tunnel", "mode"): "CDBS_TUNNEL_MODE",
    ("tunnel", "autostart"): "CDBS_TUNNEL_AUTOSTART",
    ("email", "enabled"): "CDBS_EMAIL_ENABLED",
    ("email", "recipients"): "CDBS_EMAIL_TO",
    ("email", "smtp_host"): "CDBS_SMTP_HOST",
    ("email", "smtp_port"): "CDBS_SMTP_PORT",
    ("email", "smtp_security"): "CDBS_SMTP_SECURITY",
    ("email", "smtp_user"): "CDBS_SMTP_USER",
    ("email", "from_address"): "CDBS_EMAIL_FROM",
}
SECRETS = {
    "whatsapp_phone_number_id": "CDBS_WHATSAPP_PHONE_NUMBER_ID",
    "whatsapp_token": "CDBS_WHATSAPP_ACCESS_TOKEN",
    "whatsapp_app_secret": "CDBS_WHATSAPP_APP_SECRET",
    "whatsapp_verify_token": "CDBS_WHATSAPP_VERIFY_TOKEN",
    "tunnel_token": "CDBS_TUNNEL_TOKEN",
    "email_password": "CDBS_SMTP_PASSWORD",
}
TEMPLATE_RE = re.compile(r"^[a-z0-9_]{1,128}$")
LANG_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
API_VERSION_RE = re.compile(r"^v\d{1,2}\.\d$")
MAX_RECIPIENTS = 5
MAX_BOT_NUMBERS = 10


class SettingsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_phone(value: object, default_country: str = "55") -> str:
    """Número no formato internacional só com dígitos (o que a API do WhatsApp usa): '5585999998888'.
    Aceita '+55 (85) 99999-8888', '85 99999-8888' (assume Brasil) e '005585…'. Recusa o que não for 10–15 dígitos."""
    if not isinstance(value, str):
        raise SettingsError("Número inválido.")
    raw = value.strip()
    number = digits(raw)
    if raw.startswith("00"):
        number = number[2:]
    elif not raw.startswith("+") and len(number) in (10, 11):        # DDD + número, sem o país
        number = default_country + number
    if not 10 <= len(number) <= 15 or number.startswith("0"):
        raise SettingsError(f"Número inválido: use o formato internacional, ex.: +55 85 99999-8888.")
    return number


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "sim", "yes", "on")
    raise SettingsError("Valor inválido (use verdadeiro/falso).")


def _phone_list(value: object, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [part for part in re.split(r"[,;]+", value) if part.strip()]      # vírgula separa números; espaços ficam dentro
    if not isinstance(value, list):
        raise SettingsError("Lista de números inválida.")
    out: list[str] = []
    for item in value:
        number = normalize_phone(item)
        if number not in out:
            out.append(number)
    if len(out) > limit:
        raise SettingsError(f"No máximo {limit} números.")
    return out


VALIDATORS = {
    ("monitor", "enabled"): _bool,
    ("monitor", "public_check"): _bool,
    ("notifications", "enabled"): _bool,
    ("notifications", "report_enabled"): _bool,
    ("notifications", "alerts_enabled"): _bool,
    ("bot", "enabled"): _bool,
    ("bot", "interactive"): _bool,
    ("tunnel", "autostart"): _bool,
    ("email", "enabled"): _bool,
}


def _validate_email(key: str, value: object) -> object:
    import emailer                                                          # noqa: PLC0415
    if key == "recipients":
        if isinstance(value, str):
            value = [part.strip() for part in re.split(r"[,;\s]+", value) if part.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) and emailer.valid_email(v) for v in value):
            raise SettingsError("E-mail de destino inválido (ex.: voce@gmail.com).")
        out = []
        for item in value:
            if item.strip().lower() not in out:
                out.append(item.strip().lower())
        if len(out) > MAX_RECIPIENTS:
            raise SettingsError(f"No máximo {MAX_RECIPIENTS} e-mails.")
        return out
    if key == "smtp_host":
        if not isinstance(value, str) or (value and not emailer.HOST_RE.fullmatch(value.strip())):
            raise SettingsError("Servidor SMTP inválido (ex.: smtp.gmail.com).")
        return value.strip().lower()
    if key == "smtp_port":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError("Porta SMTP inválida.") from None
        if not 1 <= number <= 65535:
            raise SettingsError("Porta SMTP inválida (465 para SSL, 587 para STARTTLS).")
        return number
    if key == "smtp_security":
        if value not in emailer.SECURITY:
            raise SettingsError("Segurança inválida: use ssl (porta 465) ou starttls (porta 587).")
        return value
    if key == "smtp_user":
        if not isinstance(value, str) or len(value) > 254 or any(c.isspace() for c in value.strip()):
            raise SettingsError("Usuário SMTP inválido (normalmente é o seu e-mail).")
        return value.strip()
    if key == "from_address":
        if not isinstance(value, str) or (value.strip() and not emailer.valid_email(value)):
            raise SettingsError("Remetente inválido (normalmente o seu e-mail).")
        return value.strip()
    raise SettingsError(f"Configuração desconhecida: email.{key}")


def _validate(section: str, key: str, value: object) -> object:
    if (section, key) in VALIDATORS:
        return VALIDATORS[(section, key)](value)
    if (section, key) == ("monitor", "interval_minutes"):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError("Intervalo inválido.") from None
        if not 5 <= number <= 1440:
            raise SettingsError("O intervalo deve ficar entre 5 e 1440 minutos.")
        return number
    if (section, key) == ("notifications", "cooldown_minutes"):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError("Espera entre alertas inválida.") from None
        if not 5 <= number <= 1440:
            raise SettingsError("A espera entre alertas repetidos deve ficar entre 5 e 1440 minutos.")
        return number
    if (section, key) == ("notifications", "recipients"):
        return _phone_list(value, MAX_RECIPIENTS)
    if (section, key) in (("bot", "admins"), ("bot", "viewers")):
        return _phone_list(value, MAX_BOT_NUMBERS)
    if section == "whatsapp" and key in ("template_report", "template_alert"):
        if not isinstance(value, str) or not (value == "" or TEMPLATE_RE.fullmatch(value)):
            raise SettingsError("Nome de modelo inválido (use letras minúsculas, números e _).")
        return value
    if (section, key) == ("whatsapp", "template_language"):
        if not isinstance(value, str) or not LANG_RE.fullmatch(value):
            raise SettingsError("Idioma do modelo inválido (ex.: pt_BR).")
        return value
    if (section, key) == ("whatsapp", "api_version"):
        if not isinstance(value, str) or not API_VERSION_RE.fullmatch(value):
            raise SettingsError("Versão da API inválida (ex.: v26.0).")
        return value
    if (section, key) == ("whatsapp", "report_variables"):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError("Número de variáveis do modelo inválido.") from None
        if number not in (3, 12):
            raise SettingsError("O modelo do relatório tem 12 variáveis (completo) ou 3 (resumido).")
        return number
    if section == "email":
        return _validate_email(key, value)
    if (section, key) == ("tunnel", "mode"):
        if value not in ("quick", "named", "off"):
            raise SettingsError("Modo do túnel inválido (quick, named ou off).")
        return value
    raise SettingsError(f"Configuração desconhecida: {section}.{key}")


class Settings:
    def __init__(self, folder: Path, environ: dict | None = None):
        self.folder = folder
        self.path = folder / "settings.json"
        self.secrets_path = folder / "secrets.json"
        self._environ = environ if environ is not None else os.environ
        self._lock = threading.Lock()

    # ----- leitura
    def _stored(self) -> dict:
        data = read_json(self.path)
        return data if isinstance(data, dict) else {}

    def _env_value(self, section: str, key: str) -> object | None:
        name = ENV_OVERRIDES.get((section, key))
        raw = self._environ.get(name, "") if name else ""
        if not raw.strip():
            return None
        try:
            return _validate(section, key, raw)
        except SettingsError:
            return None                                   # valor ruim no .env: o diagnóstico avisa; aqui vale o do painel

    def get(self) -> dict:
        merged = copy.deepcopy(DEFAULTS)
        stored = self._stored()
        for section, values in merged.items():
            for key in values:
                saved = (stored.get(section) or {}).get(key) if isinstance(stored.get(section), dict) else None
                if saved is not None:
                    try:
                        values[key] = _validate(section, key, saved)
                    except SettingsError:
                        pass
                env = self._env_value(section, key)
                if env is not None:
                    values[key] = env
        return merged

    def locked_by_env(self) -> list[str]:
        return [f"{s}.{k}" for (s, k), name in ENV_OVERRIDES.items() if self._environ.get(name, "").strip()]

    def view(self) -> dict:
        """Para o painel: configurações + o que está travado pelo .env + situação dos segredos (sem valores)."""
        data = self.get()
        data["notifications"]["recipients_view"] = [mask_phone(n) for n in data["notifications"]["recipients"]]
        data["bot"]["admins_view"] = [mask_phone(n) for n in data["bot"]["admins"]]
        data["bot"]["viewers_view"] = [mask_phone(n) for n in data["bot"]["viewers"]]
        for key in ("recipients",):
            data["notifications"].pop(key)
        data["bot"].pop("admins")
        data["bot"].pop("viewers")
        import emailer                                                      # noqa: PLC0415
        data["email"]["recipients_view"] = [emailer.mask_email(a) for a in data["email"].pop("recipients")]
        return {"settings": data, "locked_by_env": self.locked_by_env(), "secrets": self.secrets_view()}

    # ----- escrita
    def update(self, section: str, changes: dict) -> dict:
        if section not in DEFAULTS or not isinstance(changes, dict) or not changes:
            raise SettingsError("Pedido inválido.")
        locked = set(self.locked_by_env())
        clean: dict = {}
        for key, value in changes.items():
            if key not in DEFAULTS[section]:
                raise SettingsError(f"Configuração desconhecida: {section}.{key}")
            if f"{section}.{key}" in locked:
                raise SettingsError(f"{section}.{key} está definido no .env; mude lá (ou apague a linha) para editar pelo painel.", 409)
            clean[key] = _validate(section, key, value)
        with self._lock:
            stored = self._stored()
            current = stored.get(section) if isinstance(stored.get(section), dict) else {}
            current.update(clean)
            stored[section] = current
            stored["updated_at"] = int(time.time())
            write_json_private(self.path, stored)
        return self.get()

    # ----- segredos
    def secret(self, name: str) -> str:
        env_name = SECRETS[name]
        value = self._environ.get(env_name, "").strip()
        if value:
            return value
        stored = read_json(self.secrets_path)
        value = stored.get(name, "") if isinstance(stored, dict) else ""
        return value if isinstance(value, str) else ""

    def secret_source(self, name: str) -> str:
        if self._environ.get(SECRETS[name], "").strip():
            return "env"
        stored = read_json(self.secrets_path)
        return "painel" if isinstance(stored, dict) and stored.get(name) else ""

    def set_secret(self, name: str, value: object) -> None:
        if name not in SECRETS:
            raise SettingsError("Campo desconhecido.")
        if self._environ.get(SECRETS[name], "").strip():
            raise SettingsError("Este valor está definido no .env; mude lá.", 409)
        spaces_ok = name == "email_password"                     # senhas de e-mail podem ter espaço (a de app do Gmail vem em 4 grupos)
        if not isinstance(value, str) or not value.strip() or len(value) > 2048 or (not spaces_ok and any(c.isspace() for c in value.strip())):
            raise SettingsError("Valor inválido (sem espaços, até 2048 caracteres).")
        value = value.strip()
        if name == "whatsapp_phone_number_id" and not value.isdigit():
            raise SettingsError("O identificador do número de envio (Phone number ID) só tem dígitos.")
        with self._lock:
            stored = read_json(self.secrets_path)
            stored = stored if isinstance(stored, dict) else {}
            stored[name] = value
            write_json_private(self.secrets_path, stored)

    def clear_secret(self, name: str) -> None:
        if name not in SECRETS:
            raise SettingsError("Campo desconhecido.")
        with self._lock:
            stored = read_json(self.secrets_path)
            if isinstance(stored, dict) and name in stored:
                stored.pop(name)
                write_json_private(self.secrets_path, stored)

    def generate_verify_token(self) -> str:
        """Token de verificação do webhook: você cola no painel da Meta. Aparece só nesta resposta."""
        token = "cdbs-" + _secrets.token_urlsafe(24)
        self.set_secret("whatsapp_verify_token", token)
        return token

    def secrets_view(self) -> dict:
        out = {}
        for name in SECRETS:
            value = self.secret(name)
            out[name] = {"configured": bool(value), "source": self.secret_source(name),
                         "hint": (value[-4:] if name == "whatsapp_phone_number_id" and len(value) >= 8 else mask_secret(value)) if value else ""}
        return out

    # ----- conveniências
    def email_ready(self) -> tuple[bool, str]:
        mail = self.get()["email"]
        if not mail["smtp_host"]:
            return False, "falta o servidor SMTP"
        if not (mail["from_address"] or mail["smtp_user"]):
            return False, "falta o seu e-mail (usuário/remetente)"
        if mail["smtp_user"] and not self.secret("email_password"):
            return False, "falta a senha do e-mail (no Gmail, a senha de app)"
        if not mail["recipients"]:
            return False, "falta o e-mail de destino"
        return True, ""

    def whatsapp_ready(self) -> tuple[bool, str]:
        if not self.secret("whatsapp_phone_number_id"):
            return False, "falta o identificador do número de envio (Phone number ID)"
        if not self.secret("whatsapp_token"):
            return False, "falta o token de acesso da API"
        return True, ""
