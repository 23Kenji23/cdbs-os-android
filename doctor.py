"""CDBS.OS — diagnóstico: `python3 cdbs.py doctor` (Parte 1.5).

Por padrão SÓ LÊ: não altera dados, não liga nem desliga nada e não mostra senha, token nem chave.
Cada verificação devolve OK / AVISO / FALHA / INFO, uma explicação e, quando há problema, o que fazer.

Opções:
  --json        saída em JSON (para guardar ou comparar)
  --offline     pula os testes de internet (DNS/conexão)
  --corrigir    aplica SÓ correções seguras (criar pastas que faltam, ajustar permissões, pôr de lado um arquivo de
                configuração corrompido — renomeado, nunca apagado) e diz exatamente o que fez

Código de saída: 0 sem FALHA · 1 com alguma FALHA · 2 erro do próprio diagnóstico.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config
import versioninfo
from sysutil import fmt_local, read_json

OK, AVISO, FALHA, INFO = "OK", "AVISO", "FALHA", "INFO"
PROVIDERS = ("site_monitor", "apps", "notify", "whatsapp", "emailer", "supervisor", "updater", "bot")   # módulos que acrescentam verificações próprias


@dataclass
class Check:
    area: str
    name: str
    status: str
    message: str
    action: str = ""


@dataclass
class Context:
    root: Path
    data: Path
    env: dict
    env_warnings: list
    offline: bool = False
    fix: bool = False
    now: float = field(default_factory=time.time)
    fixes: list = field(default_factory=list)

    @property
    def site_port(self) -> int:
        return _port(self.env.get("CDBS_SITE_PORT") or os.environ.get("CDBS_SITE_PORT"), 8088)

    @property
    def admin_port(self) -> int:
        return _port(self.env.get("CDBS_ADMIN_PORT") or os.environ.get("CDBS_ADMIN_PORT"), 8089)

    def http_get(self, url: str, timeout: float = 3.0) -> tuple[int, bytes]:
        """GET simples, sem seguir para outros hosts. Devolve (status, corpo) ou (0, b'') se não conectou."""
        req = urllib.request.Request(url, headers={"User-Agent": "cdbs-doctor"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:      # noqa: S310 — só URLs montadas aqui
                return response.status, response.read(65536)
        except urllib.error.HTTPError as error:
            return error.code, b""
        except (urllib.error.URLError, OSError, ValueError):
            return 0, b""


def _port(value: object, default: int) -> int:
    try:
        port = int(str(value).strip()) if value not in (None, "") else default
    except ValueError:
        return -1
    return port if 1024 <= port <= 65535 else -1


def _is_termux() -> bool:
    return bool(os.environ.get("TERMUX_VERSION")) or os.environ.get("PREFIX", "").startswith("/data/data/com.termux")


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)    # noqa: S603 — comandos fixos
        return done.returncode, (done.stdout or done.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


# --------------------------------------------------------------------------- verificações básicas

def check_environment(ctx: Context) -> list[Check]:
    out = []
    version = sys.version_info
    py = f"Python {version.major}.{version.minor}.{version.micro}"
    if version < (3, 10):
        out.append(Check("ambiente", "python", FALHA, f"{py}: muito antigo.", "Atualize: pkg upgrade python"))
    else:
        out.append(Check("ambiente", "python", OK, py))
    out.append(Check("ambiente", "sistema", INFO, f"{'Termux/Android' if _is_termux() else platform.system()} ({platform.machine()})"))
    try:
        import cryptography                                                          # noqa: PLC0415
        out.append(Check("ambiente", "criptografia", OK, f"biblioteca cryptography {cryptography.__version__}"))
    except Exception:                                                                 # noqa: BLE001
        out.append(Check("ambiente", "criptografia", FALHA, "biblioteca cryptography ausente: backups novos ficam bloqueados.",
                         "pkg install python-cryptography -y"))
    import hashlib                                                                    # noqa: PLC0415
    out.append(Check("ambiente", "scrypt", OK if hasattr(hashlib, "scrypt") else FALHA,
                     "hashlib.scrypt disponível" if hasattr(hashlib, "scrypt") else "este Python não tem hashlib.scrypt",
                     "" if hasattr(hashlib, "scrypt") else "pkg upgrade python"))
    try:
        import ssl                                                                    # noqa: PLC0415
        out.append(Check("ambiente", "ssl", OK, f"{ssl.OPENSSL_VERSION}"))
    except Exception:                                                                 # noqa: BLE001
        out.append(Check("ambiente", "ssl", AVISO, "módulo ssl indisponível: sem HTTPS (WhatsApp e teste público)", "pkg install openssl"))
    return out


def check_commands(ctx: Context) -> list[Check]:
    out = []
    if shutil.which("cloudflared"):
        code, text = _run(["cloudflared", "--version"])
        out.append(Check("comandos", "cloudflared", OK if code == 0 else AVISO, (text.splitlines() or ["cloudflared"])[0][:80]))
    else:
        out.append(Check("comandos", "cloudflared", AVISO, "cloudflared não encontrado: sem link público.", "pkg install cloudflared -y"))
    for name, why, fix in (("termux-wake-lock", "ajuda a manter o Termux acordado", "vem com o Termux; no Linux comum não existe"),
                           ("unzip", "usado pelo atualizar.sh", "pkg install unzip -y")):
        found = shutil.which(name)
        out.append(Check("comandos", name, OK if found else (AVISO if _is_termux() or name == "unzip" else INFO),
                         f"{name} {'disponível' if found else 'ausente'} ({why})", "" if found else fix))
    if shutil.which("termux-battery-status"):
        code, text = _run(["termux-battery-status"], 8)
        try:
            info = json.loads(text) if code == 0 else {}
        except ValueError:
            info = {}
        if info:
            level, plugged = info.get("percentage"), str(info.get("plugged", "")).upper()
            status = AVISO if isinstance(level, (int, float)) and level < 30 and "UNPLUGGED" in plugged else OK
            out.append(Check("android", "bateria", status, f"bateria {level}% ({'na tomada' if 'UNPLUGGED' not in plugged else 'fora da tomada'})",
                             "Deixe o celular na tomada: sem bateria o CDBS para e não consegue avisar." if status == AVISO else ""))
    elif _is_termux():
        out.append(Check("android", "bateria", INFO, "estado da bateria indisponível (precisa do app Termux:API + pkg install termux-api)"))
    if _is_termux():
        out.append(Check("android", "segundo plano", INFO,
                         "O Android pode pausar ou encerrar o Termux com a tela desligada. Use termux-wake-lock, deixe na tomada e "
                         "tire o Termux da economia de bateria (Configurações → Apps → Termux → Bateria → Sem restrições). "
                         "Isso ajuda, mas não garante funcionamento 24 h."))
    return out


def check_program(ctx: Context) -> list[Check]:
    info = versioninfo.load(ctx.root)
    if not info["valid"]:
        return [Check("programa", "versão", AVISO, "VERSION.json ausente ou inválido: não dá para conferir os arquivos do programa.",
                      "Reinstale a partir do ZIP oficial (bash atualizar.sh <zip>).")]
    result = versioninfo.verify(ctx.root, info)
    out = [Check("programa", "versão", OK, f"{info['name']} {info['version']} ({info['released'] or 'sem data'})")]
    if result["ok"]:
        out.append(Check("programa", "integridade", OK, f"{result['checked']} arquivos conferidos (SHA-256)"))
    else:
        parts = []
        if result["missing"]:
            parts.append(f"faltando: {', '.join(result['missing'][:5])}")
        if result["changed"]:
            parts.append(f"alterados: {', '.join(result['changed'][:5])}")
        out.append(Check("programa", "integridade", FALHA if result["missing"] else AVISO, "; ".join(parts),
                         "Se você não editou esses arquivos de propósito, reinstale com bash atualizar.sh <zip>."))
    return out


def check_config(ctx: Context) -> list[Check]:
    out = []
    env_path = ctx.root / ".env"
    if env_path.exists():
        out.append(Check("configuração", ".env", OK, ".env encontrado"))
        mode = _mode(env_path)
        if mode is not None and mode & 0o077 and any(ctx.env.get(k) for k in config.SECRET_KEYS):
            out.append(Check("configuração", "permissão .env", AVISO, f".env tem tokens e permissão {mode:o} (outros usuários podem ler).",
                             "chmod 600 .env  (ou rode o diagnóstico com --corrigir)"))
            if ctx.fix:
                try:
                    os.chmod(env_path, 0o600)
                    ctx.fixes.append("permissão do .env ajustada para 600")
                except OSError:
                    pass
    else:
        out.append(Check("configuração", ".env", INFO, ".env não existe: usando portas padrão 8088 (sites) e 8089 (painel).",
                         "Para personalizar: cp config/.env.example .env"))
    for warning in ctx.env_warnings[:10]:
        out.append(Check("configuração", ".env", AVISO, warning))
    site, admin = ctx.site_port, ctx.admin_port
    if site < 0 or admin < 0:
        out.append(Check("configuração", "portas", FALHA, "porta inválida no .env (use 1024 a 65535).", "Corrija CDBS_SITE_PORT/CDBS_ADMIN_PORT no .env"))
    elif site == admin:
        out.append(Check("configuração", "portas", FALHA, "a porta dos sites e a do painel são iguais.", "Use 8088 para sites e 8089 para o painel."))
    else:
        out.append(Check("configuração", "portas", OK, f"sites {site} (vai para o túnel) · painel {admin} (só local)"))
    base = (ctx.env.get("CDBS_PUBLIC_BASE") or "").strip()
    if base and not base.startswith("https://"):
        out.append(Check("configuração", "endereço público", FALHA, "CDBS_PUBLIC_BASE precisa começar com https://", "Corrija no .env ou deixe vazio."))
    elif base:
        out.append(Check("configuração", "endereço público", OK, f"endereço fixo configurado: {base[:80]}"))
    else:
        out.append(Check("configuração", "endereço público", INFO, "sem endereço fixo: o link vem do túnel temporário (muda a cada reinício do túnel)."))
    return out


SENSITIVE_FILES = ("admin.json", "secret.key", "admin-sessions.json", "system/secrets.json", "backups/protection.json")
PRIVATE_DIRS = ("backups", "system", "monitoring", "notifications", "updates", "restore-tmp", "removidos")


def check_data(ctx: Context) -> list[Check]:
    out = []
    data = ctx.data
    if not data.exists():
        if ctx.fix:
            (data / "sites").mkdir(parents=True, exist_ok=True)
            ctx.fixes.append("pasta data/sites criada")
        out.append(Check("dados", "pasta de dados", AVISO if not ctx.fix else OK,
                         "data/ ainda não existe (é criada no primeiro início)." if not ctx.fix else "data/ criada agora"))
        return out
    probe = data / f".doctor-{os.getpid()}.tmp"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        out.append(Check("dados", "gravação", OK, "data/ aceita gravação"))
    except OSError:
        out.append(Check("dados", "gravação", FALHA, "não consigo gravar em data/ (permissão ou disco cheio).",
                         "Confira o espaço livre e as permissões da pasta do CDBS."))
    loose = []
    for rel in SENSITIVE_FILES:
        path = data / rel
        mode = _mode(path)
        if mode is not None and mode & 0o077:
            loose.append(f"{rel} ({mode:o})")
            if ctx.fix:
                try:
                    os.chmod(path, 0o600)
                    ctx.fixes.append(f"permissão de data/{rel} ajustada para 600")
                except OSError:
                    pass
    for rel in PRIVATE_DIRS:
        path = data / rel
        mode = _mode(path)
        if mode is not None and mode & 0o077:
            loose.append(f"{rel}/ ({mode:o})")
            if ctx.fix:
                try:
                    os.chmod(path, 0o700)
                    ctx.fixes.append(f"permissão de data/{rel}/ ajustada para 700")
                except OSError:
                    pass
    if loose:
        out.append(Check("dados", "permissões", AVISO if not ctx.fix else OK,
                         ("arquivos/pastas privados com permissão aberta: " + ", ".join(loose[:6])) if not ctx.fix else "permissões ajustadas",
                         "" if ctx.fix else "Rode: python3 cdbs.py doctor --corrigir"))
    else:
        out.append(Check("dados", "permissões", OK, "arquivos de senha, chave e backups estão privados"))
    sites = [p for p in (data / "sites").iterdir() if p.is_dir() and not p.name.startswith(".")] if (data / "sites").is_dir() else []
    no_index = [p.name for p in sites if not (p / "index.html").is_file()]
    out.append(Check("dados", "sites", OK if not no_index else AVISO, f"{len(sites)} site(s) publicado(s)"
                     + (f"; sem index.html: {', '.join(no_index[:5])}" if no_index else ""),
                     "Um site sem index.html não abre: republique o ZIP ou restaure um backup." if no_index else ""))
    removed = data / "removidos" / "sites"
    kept = [p for p in removed.iterdir() if p.is_dir() and (p / "site").is_dir()] if removed.is_dir() else []
    if kept:
        kept_bytes = sum(f.stat().st_size for p in kept for f in (p / "site").rglob("*") if f.is_file() and not f.is_symlink())
        out.append(Check("dados", "sites removidos (guardados)", INFO, f"{len(kept)} cópia(s), {kept_bytes // (1024 * 1024)} MB — nunca são apagadas sozinhas",
                         "Para liberar espaço: painel → Seus sites → Sites removidos → Apagar de vez."))
    try:
        usage = shutil.disk_usage(data)
        free_mb = usage.free // (1024 * 1024)
        status = FALHA if free_mb < 100 else AVISO if free_mb < 500 else OK
        out.append(Check("dados", "espaço livre", status, f"{free_mb} MB livres de {usage.total // (1024 * 1024)} MB",
                         "Libere espaço: apague arquivos do celular ou backups antigos (Central de backups → Excluir)." if status != OK else ""))
    except OSError:
        out.append(Check("dados", "espaço livre", AVISO, "não foi possível medir o espaço livre"))
    backups = data / "backups"
    if backups.is_dir():
        total = sum(p.stat().st_size for p in backups.rglob("*") if p.is_file())
        protected = (backups / "protection.json").is_file()
        out.append(Check("backups", "senha de recuperação", OK if protected else AVISO,
                         "definida (o valor não é guardado)" if protected else "ainda não definida: não dá para criar backups.",
                         "" if protected else "No painel: Central de backups → Proteção dos backups."))
        out.append(Check("backups", "espaço dos backups", INFO, f"{total // (1024 * 1024)} MB em data/backups"))
        out.extend(_backup_age(ctx, backups))
    else:
        out.append(Check("backups", "backups", AVISO, "nenhum backup ainda.", "Faça o primeiro: Central de backups → Criar backup."))
    return out


def _backup_age(ctx: Context, backups: Path) -> list[Check]:
    overdue, never = [], []
    for folder in sorted(p for p in backups.iterdir() if p.is_dir()):
        last = 0
        for rec_path in folder.glob("*.json"):
            if rec_path.name == "state.json":
                continue
            rec = read_json(rec_path)
            if isinstance(rec, dict) and rec.get("state") == "CONCLUIDO" and rec.get("kind") in ("manual", "seguranca", "importado", "migrado"):
                last = max(last, int(rec.get("finished_at") or rec.get("started_at") or 0))
        if not last:
            never.append(folder.name)
        elif ctx.now - last > 30 * 86400:
            overdue.append(folder.name)
    if not overdue and not never:
        return [Check("backups", "atualizados", OK, "todas as lojas com backup validado nos últimos 30 dias")]
    parts = []
    if overdue:
        parts.append("há mais de 30 dias: " + ", ".join(overdue[:5]))
    if never:
        parts.append("sem backup validado: " + ", ".join(never[:5]))
    return [Check("backups", "atualizados", AVISO, "; ".join(parts), "Central de backups → Criar backup.")]


def check_processes(ctx: Context) -> list[Check]:
    out = []
    status, body = ctx.http_get(f"http://127.0.0.1:{ctx.admin_port}/api/health")
    running = status == 200
    if running:
        try:
            info = json.loads(body)
        except ValueError:
            info = {}
        out.append(Check("processos", "painel (8089)", OK, f"respondendo · versão {info.get('version', '?')} · ativo desde {fmt_local(info.get('started_at'))}"))
    elif status:
        out.append(Check("processos", "painel (8089)", AVISO, f"algo respondeu na porta {ctx.admin_port} com HTTP {status}, mas não parece o CDBS 3.",
                         "Outro programa (ou uma versão antiga do CDBS) está usando a porta."))
    else:
        out.append(Check("processos", "painel (8089)", AVISO, f"nada respondendo em 127.0.0.1:{ctx.admin_port}: o CDBS está parado.",
                         "Inicie com: bash start.sh"))
    status, _ = ctx.http_get(f"http://127.0.0.1:{ctx.site_port}/_cdbs/ping")
    out.append(Check("processos", "sites (8088)", OK if status == 200 else AVISO,
                     "servidor dos sites respondendo" if status == 200 else f"servidor dos sites não respondeu em 127.0.0.1:{ctx.site_port}",
                     "" if status == 200 else "Inicie com: bash start.sh"))
    beat = read_json(ctx.data / "system" / "cdbs-heartbeat.json")
    if isinstance(beat, dict) and beat.get("started_at"):
        if beat.get("clean") is False and not running:
            out.append(Check("processos", "último encerramento", AVISO,
                             f"o CDBS parou SEM desligar normalmente; último sinal de vida em {fmt_local(beat.get('beat_at'))}.",
                             "Causas prováveis: o Android encerrou o Termux, o celular reiniciou ou ficou sem bateria. "
                             "Use termux-wake-lock, deixe na tomada e tire o Termux da economia de bateria."))
        elif beat.get("clean") is True:
            out.append(Check("processos", "último encerramento", OK, f"último desligamento foi normal ({fmt_local(beat.get('stopped_at'))})"))
    return out


def check_connectivity(ctx: Context) -> list[Check]:
    if ctx.offline:
        return [Check("internet", "conectividade", INFO, "testes de internet pulados (--offline)")]
    out = []
    for host, why in (("graph.facebook.com", "API do WhatsApp"), ("region1.v2.argotunnel.com", "túnel Cloudflare")):
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            resolved = True
        except OSError:
            resolved = False
        if not resolved:
            out.append(Check("internet", host, AVISO, f"não resolvi {host} ({why}): sem internet ou DNS com problema.",
                             "Confira o Wi-Fi/dados móveis. Se o resto da internet funciona, troque o DNS privado do Android para automático."))
            continue
        try:
            with socket.create_connection((host, 443), timeout=4):
                out.append(Check("internet", host, OK, f"conexão OK ({why})"))
        except OSError:
            out.append(Check("internet", host, AVISO, f"DNS OK, mas a conexão com {host} falhou ({why}).", "Rede instável ou bloqueada."))
    return out


BASE_CHECKS = (check_environment, check_commands, check_program, check_config, check_data, check_processes, check_connectivity)


def run(root: Path, *, offline: bool = False, fix: bool = False, data: Path | None = None) -> dict:
    env, warnings = config.parse_env(root / ".env")
    ctx = Context(root=root, data=data or root / "data", env=env, env_warnings=warnings, offline=offline, fix=fix)
    checks: list[Check] = []
    for step in BASE_CHECKS:
        try:
            checks.extend(step(ctx))
        except Exception as exc:                                                     # noqa: BLE001
            checks.append(Check("diagnóstico", step.__name__, AVISO, f"verificação não concluída ({type(exc).__name__})"))
    for name in PROVIDERS:
        try:
            module = importlib.import_module(name)
        except Exception:                                                            # noqa: BLE001 — módulo ausente nesta versão
            continue
        provider = getattr(module, "doctor_checks", None)
        if provider is None:
            continue
        try:
            for item in provider(ctx):
                checks.append(Check(*item) if isinstance(item, tuple) else item)
        except Exception as exc:                                                     # noqa: BLE001
            checks.append(Check("diagnóstico", name, AVISO, f"verificação de {name} não concluída ({type(exc).__name__})"))
    counts = {s: sum(1 for c in checks if c.status == s) for s in (OK, AVISO, FALHA, INFO)}
    return {"generated_at": int(time.time()), "root": "(pasta do CDBS)", "counts": counts, "fixes": ctx.fixes,
            "checks": [asdict(c) for c in checks]}


def render_text(report: dict) -> str:
    lines = [f"== CDBS.OS — diagnóstico ({fmt_local(report['generated_at'])})"]
    area = None
    marks = {OK: "[OK]   ", AVISO: "[AVISO]", FALHA: "[FALHA]", INFO: "[INFO] "}
    for item in report["checks"]:
        if item["area"] != area:
            area = item["area"]
            lines.append(f"-- {area}")
        lines.append(f"  {marks.get(item['status'], '[?]    ')} {item['name']}: {item['message']}")
        if item["action"] and item["status"] in (AVISO, FALHA):
            lines.append(f"           → o que fazer: {item['action']}")
    if report["fixes"]:
        lines.append("-- correções aplicadas (--corrigir)")
        lines.extend(f"  • {fix}" for fix in report["fixes"])
    c = report["counts"]
    lines.append(f"== resumo: {c[OK]} OK · {c[AVISO]} aviso(s) · {c[FALHA]} falha(s) · {c[INFO]} informação(ões)")
    lines.append("   (este relatório não contém senhas, tokens nem chaves; pode mandar quando pedir ajuda)")
    return "\n".join(lines)


def main(argv: list[str], root: Path) -> int:
    known = {"--json", "--offline", "--corrigir"}
    unknown = [a for a in argv if a not in known]
    if unknown:
        print(f"Opção desconhecida: {unknown[0]}. Use: python3 cdbs.py doctor [--json] [--offline] [--corrigir]", file=sys.stderr)
        return 2
    try:
        report = run(root, offline="--offline" in argv, fix="--corrigir" in argv)
    except Exception as exc:                                                         # noqa: BLE001
        print(f"O diagnóstico falhou: {type(exc).__name__}", file=sys.stderr)
        return 2
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_text(report))
    return 1 if report["counts"][FALHA] else 0
