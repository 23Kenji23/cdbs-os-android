"""CDBS.OS — leitura do arquivo `.env` (KEY=VALUE), sem executar nada.

Usado pelo servidor, pelo supervisor e pelo diagnóstico. Só chaves conhecidas são aceitas; o resto é ignorado e
listado como aviso. Valores secretos (tokens) nunca são impressos por este módulo.
"""
from __future__ import annotations

import os
from pathlib import Path

KNOWN_KEYS = {
    # portas e endereço público (desde a 1ª versão)
    "CDBS_SITE_PORT", "CDBS_ADMIN_PORT", "CDBS_PUBLIC_BASE",
    # monitoramento das posições de site
    "CDBS_MONITOR_ENABLED", "CDBS_MONITOR_INTERVAL_MINUTES", "CDBS_MONITOR_PUBLIC_CHECK", "CDBS_MONITOR_SLOTS",
    # WhatsApp (API oficial — WhatsApp Business Platform / Cloud API)
    "CDBS_WHATSAPP_ENABLED", "CDBS_WHATSAPP_PHONE_NUMBER_ID", "CDBS_WHATSAPP_ACCESS_TOKEN", "CDBS_WHATSAPP_APP_SECRET",
    "CDBS_WHATSAPP_VERIFY_TOKEN", "CDBS_WHATSAPP_RECIPIENT", "CDBS_WHATSAPP_TEMPLATE_NAME", "CDBS_WHATSAPP_TEMPLATE_REPORT",
    "CDBS_WHATSAPP_TEMPLATE_ALERT", "CDBS_WHATSAPP_TEMPLATE_LANGUAGE", "CDBS_WHATSAPP_API_VERSION", "CDBS_WHATSAPP_API_BASE",
    "CDBS_WHATSAPP_REPORT_VARIABLES",
    # bot de comandos
    "CDBS_BOT_ENABLED", "CDBS_BOT_ADMINS", "CDBS_BOT_VIEWERS", "CDBS_BOT_INTERACTIVE",
    # túnel Cloudflare gerenciado pelo supervisor
    "CDBS_TUNNEL_MODE", "CDBS_TUNNEL_TOKEN", "CDBS_TUNNEL_AUTOSTART", "CDBS_TUNNEL_METRICS_PORT",
    # avisos por e-mail (SMTP)
    "CDBS_EMAIL_ENABLED", "CDBS_EMAIL_TO", "CDBS_EMAIL_FROM", "CDBS_SMTP_HOST", "CDBS_SMTP_PORT", "CDBS_SMTP_SECURITY",
    "CDBS_SMTP_USER", "CDBS_SMTP_PASSWORD",
    # servidores (apps), 4.0: porta da 1ª vaga (as 5 vagas usam esta e as 4 seguintes) e caminho do node, se não estiver no PATH
    "CDBS_APP_PORT_BASE", "CDBS_NODE_BIN",
}
SECRET_KEYS = {"CDBS_WHATSAPP_ACCESS_TOKEN", "CDBS_WHATSAPP_APP_SECRET", "CDBS_WHATSAPP_VERIFY_TOKEN", "CDBS_TUNNEL_TOKEN", "CDBS_SMTP_PASSWORD"}


def parse_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """Devolve (valores conhecidos, avisos). Linhas inválidas e chaves desconhecidas viram aviso, nunca erro."""
    values: dict[str, str] = {}
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return values, warnings
    except (OSError, UnicodeDecodeError):
        return values, [".env ilegível (codificação ou permissão)"]
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            warnings.append(f".env linha {number}: sem '=' (ignorada)")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in KNOWN_KEYS:
            warnings.append(f".env linha {number}: chave desconhecida {key[:40]} (ignorada)")
            continue
        values[key] = value
    return values, warnings


def load_into_environ(root: Path) -> list[str]:
    """Como antes: o ambiente do processo vence o .env (setdefault)."""
    values, warnings = parse_env(root / ".env")
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return warnings


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "sim", "yes", "on")


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)
