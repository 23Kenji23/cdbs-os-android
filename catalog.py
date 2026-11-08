"""Catálogo de produtos por loja: dados, validação, fotos e sessões do cliente.

Cada loja vive em <base>/<nome>/:
    store.json     nome, WhatsApp, hash da senha do cliente e "epoch" das sessões
    products.json  produtos (o mais novo primeiro)
    media/         fotos, com nomes aleatórios que nunca se repetem

Só biblioteca padrão. Este módulo não abre portas nem fala HTTP; quem faz isso é o cdbs.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")  # mesma regra dos nomes de site
PRODUCT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{8}\.(?:jpg|png|webp)$")
MAX_PRODUCTS = 200
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_STORE_BYTES = 60 * 1024 * 1024
PBKDF2_ROUNDS = 310_000
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I/L: fácil de digitar no celular
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class CatalogError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- validação

def normalize_password(value: str) -> str:
    """Ignora espaços, hífens e maiúsculas/minúsculas: 'k7m2 qx9p-...' e 'K7M2QX9P...' são iguais."""
    return re.sub(r"[\s-]", "", value or "").upper()


def new_password() -> str:
    raw = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", normalize_password(password).encode("utf-8"), salt, PBKDF2_ROUNDS)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": PBKDF2_ROUNDS}


def _check_password(record: dict, password: str) -> bool:
    try:
        digest = hashlib.pbkdf2_hmac("sha256", normalize_password(password).encode("utf-8"),
                                     bytes.fromhex(record["salt"]), int(record["rounds"]))
        return hmac.compare_digest(digest.hex(), record["hash"])
    except (KeyError, TypeError, ValueError):
        return False


def _text(data: dict, key: str, label: str, limit: int, *, required: bool = False, multiline: bool = False) -> str:
    value = data.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CatalogError(f"{label[0].upper()}{label[1:]} é inválido.")
    value = _CONTROL_RE.sub("", value).strip()
    if multiline:
        value = re.sub(r"\n{3,}", "\n\n", value.replace("\r\n", "\n").replace("\r", "\n"))
    else:
        value = re.sub(r"\s+", " ", value)
    if required and not value:
        raise CatalogError(f"Informe {label}.")
    if len(value) > limit:
        raise CatalogError(f"{label[0].upper()}{label[1:]} pode ter até {limit} caracteres.")
    return value


def parse_price(value: object) -> int | None:
    """Aceita 89,90 / 89.90 / 1.234,56 / R$ 89,90 / 1.234. Vazio significa 'sem preço' (consulte)."""
    if value is None or isinstance(value, bool):
        if value is None:
            return None
        raise CatalogError("Preço inválido. Use algo como 89,90.")
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().replace("R$", "").replace(" ", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
            text = text.replace(".", "")
    else:
        raise CatalogError("Preço inválido. Use algo como 89,90.")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise CatalogError("Preço inválido. Use algo como 89,90.") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("999999.99"):
        raise CatalogError("Preço inválido. Use um valor entre 0 e 999.999,99.")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_price(cents: int | None) -> str:
    if cents is None:
        return ""
    return f"R$ {cents // 100:,}".replace(",", ".") + f",{cents % 100:02d}"


def normalize_whatsapp(value: object) -> str:
    """Guarda só dígitos com o código do país (55). Vazio é permitido."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CatalogError("WhatsApp inválido. Use DDD + número, ex.: (85) 99999-9999.")
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if len(digits) in (10, 11):
        digits = "55" + digits
    if not 12 <= len(digits) <= 15:
        raise CatalogError("WhatsApp inválido. Use DDD + número, ex.: (85) 99999-9999.")
    return digits


def _image_extension(payload: bytes) -> str | None:
    """Decide o formato pelos primeiros bytes; o nome e o tipo enviados pelo cliente são ignorados."""
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


def _read_json(path: Path, default: object = None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default  # type: ignore[return-value]
        raise CatalogError("Loja não encontrada.", 404) from None
    except (OSError, ValueError):
        raise CatalogError("Os dados desta loja estão ilegíveis. Avise o administrador.", 500) from None


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


# ------------------------------------------------------------------ catálogo

class Catalog:
    _lock = threading.RLock()  # um só para o processo: várias instâncias não se atropelam

    def __init__(self, base: Path, secret_file: Path):
        self.base = Path(base)
        self.secret_file = Path(secret_file)

    # ----- lojas
    def _dir(self, slug: object) -> Path:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise CatalogError("Nome de loja inválido. Use letras sem acento, números e hífens.")
        return self.base / slug

    def _load_store(self, slug: object) -> dict:
        return _read_json(self._dir(slug) / "store.json")

    def exists(self, slug: object) -> bool:
        try:
            return (self._dir(slug) / "store.json").is_file()
        except CatalogError:
            return False

    def _load_products(self, slug: str) -> dict:
        data = _read_json(self._dir(slug) / "products.json", {"products": [], "rev": 0})
        data.setdefault("products", [])
        data.setdefault("rev", 0)
        return data

    def _save_products(self, slug: str, data: dict) -> None:
        data["rev"] = int(data.get("rev", 0)) + 1
        data["updated"] = int(time.time())
        _write_json(self._dir(slug) / "products.json", data)

    def list_stores(self) -> list[dict]:
        stores: list[dict] = []
        if not self.base.is_dir():
            return stores
        for folder in sorted(self.base.iterdir(), key=lambda item: item.name):
            if not (folder.is_dir() and SLUG_RE.fullmatch(folder.name) and (folder / "store.json").is_file()):
                continue
            try:
                store = _read_json(folder / "store.json")
                count = len(self._load_products(folder.name)["products"])
            except CatalogError:
                continue
            stores.append({"slug": folder.name, "name": store.get("name", ""),
                           "whatsapp": store.get("whatsapp", ""), "products": count, "pending": bool(store.get("pending"))})
        return stores

    def get_store(self, slug: str) -> dict:
        store = self._load_store(slug)
        return {"slug": slug, "name": store.get("name", ""), "whatsapp": store.get("whatsapp", ""), "pending": bool(store.get("pending"))}

    def is_pending(self, slug: object) -> bool:
        """Loja recuperada de um backup: existe, mas está DESATIVADA (sem senha, invisível ao público) até receber uma nova senha."""
        try:
            return bool(self._load_store(slug).get("pending"))
        except CatalogError:
            return False

    def create_store(self, slug: object, name: object, whatsapp: object) -> str:
        """Cria a loja e devolve a senha do cliente. Ela só existe em texto puro neste momento."""
        with self._lock:
            folder = self._dir(slug)
            if (folder / "store.json").exists():
                raise CatalogError("Já existe um catálogo com esse nome.", 409)
            clean_name = _text({"name": name}, "name", "o nome da loja", 60, required=True)
            clean_whatsapp = normalize_whatsapp(whatsapp)
            password = new_password()
            (folder / "media").mkdir(parents=True, exist_ok=True)
            _write_json(folder / "store.json", {
                "slug": slug, "name": clean_name, "whatsapp": clean_whatsapp,
                "password": _hash_password(password), "epoch": 1, "created": int(time.time()),
            })
            self._save_products(slug, {"products": [], "rev": 0})  # type: ignore[arg-type]
            return password

    def update_store(self, slug: str, data: dict) -> None:
        with self._lock:
            store = self._load_store(slug)
            if "name" in data:
                store["name"] = _text(data, "name", "o nome da loja", 60, required=True)
            if "whatsapp" in data:
                store["whatsapp"] = normalize_whatsapp(data["whatsapp"])
            _write_json(self._dir(slug) / "store.json", store)

    def reset_password(self, slug: str) -> str:
        """Nova senha; sessões abertas deixam de valer (o 'epoch' muda)."""
        with self._lock:
            store = self._load_store(slug)
            password = new_password()
            store["password"] = _hash_password(password)
            store["epoch"] = int(store.get("epoch", 1)) + 1
            store.pop("pending", None)                      # gerar a senha é o que ATIVA uma loja recuperada
            _write_json(self._dir(slug) / "store.json", store)
            return password

    def delete_store(self, slug: str) -> None:
        with self._lock:
            folder = self._dir(slug)
            if not (folder / "store.json").is_file():
                raise CatalogError("Loja não encontrada.", 404)
            shutil.rmtree(folder)

    def verify_login(self, slug: object, password: str) -> bool:
        try:
            store = self._load_store(slug)
        except CatalogError:
            # Gasta o mesmo tempo mesmo quando a loja não existe, para não revelar quais existem.
            hashlib.pbkdf2_hmac("sha256", normalize_password(password).encode("utf-8"), b"\0" * 16, PBKDF2_ROUNDS)
            return False
        if store.get("pending") or not store.get("password"):
            hashlib.pbkdf2_hmac("sha256", normalize_password(password).encode("utf-8"), b"\0" * 16, PBKDF2_ROUNDS)   # mesmo tempo; nunca entra
            return False
        return _check_password(store.get("password", {}), password)

    # ----- sessões (cookie assinado: sobrevive a reinício e dispensa guardar sessões em disco)
    def _secret(self) -> bytes:
        with self._lock:
            try:
                data = self.secret_file.read_bytes()
            except FileNotFoundError:
                data = b""
            if len(data) < 32:
                data = secrets.token_bytes(32)
                self.secret_file.parent.mkdir(parents=True, exist_ok=True)
                temp = self.secret_file.with_name(self.secret_file.name + ".tmp")
                temp.write_bytes(data)
                os.chmod(temp, 0o600)
                os.replace(temp, self.secret_file)
            return data

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def make_session(self, slug: str) -> str:
        epoch = int(self._load_store(slug).get("epoch", 1))
        payload = f"{slug}.{int(time.time()) + SESSION_SECONDS}.{epoch}"
        return f"{payload}.{self._sign(payload)}"

    def read_session(self, cookie: object) -> str | None:
        try:
            slug, expiry, epoch, mac = str(cookie).split(".")
            if not hmac.compare_digest(mac.encode("utf-8"), self._sign(f"{slug}.{expiry}.{epoch}").encode("utf-8")):
                return None
            store = self._load_store(slug)
            if store.get("pending") or int(expiry) < time.time() or int(epoch) != int(store.get("epoch", 1)):
                return None
        except (ValueError, CatalogError):
            return None
        return slug

    # ----- produtos
    @staticmethod
    def _fields(data: dict, partial: bool) -> dict:
        out: dict = {}
        if not partial or "name" in data:
            out["name"] = _text(data, "name", "o nome", 80, required=True)
        if not partial or "description" in data:
            out["description"] = _text(data, "description", "a descrição", 500, multiline=True)
        if not partial or "category" in data:
            out["category"] = _text(data, "category", "a categoria", 40)
        if not partial or "price" in data:
            out["price_cents"] = parse_price(data.get("price"))
        if "available" in data:
            if not isinstance(data["available"], bool):
                raise CatalogError("Disponibilidade inválida.")
            out["available"] = data["available"]
        elif not partial:
            out["available"] = True
        return out

    @staticmethod
    def _find(doc: dict, product_id: object) -> dict:
        if isinstance(product_id, str) and PRODUCT_ID_RE.fullmatch(product_id):
            for product in doc["products"]:
                if product.get("id") == product_id:
                    return product
        raise CatalogError("Produto não encontrado.", 404)

    def products(self, slug: str) -> list[dict]:
        self._load_store(slug)
        return self._load_products(slug)["products"]

    def add_product(self, slug: str, data: dict) -> dict:
        with self._lock:
            self._load_store(slug)
            doc = self._load_products(slug)
            if len(doc["products"]) >= MAX_PRODUCTS:
                raise CatalogError(f"Limite de {MAX_PRODUCTS} produtos atingido. Apague algum para cadastrar outro.", 409)
            product = {"id": secrets.token_hex(6), "image": None, "created": int(time.time()), **self._fields(data, False)}
            doc["products"].insert(0, product)
            self._save_products(slug, doc)
            return product

    def update_product(self, slug: str, product_id: str, data: dict) -> dict:
        with self._lock:
            self._load_store(slug)
            doc = self._load_products(slug)
            product = self._find(doc, product_id)
            product.update(self._fields(data, True))
            self._save_products(slug, doc)
            return product

    def delete_product(self, slug: str, product_id: str) -> None:
        with self._lock:
            self._load_store(slug)
            doc = self._load_products(slug)
            product = self._find(doc, product_id)
            doc["products"].remove(product)
            self._save_products(slug, doc)
            self._unlink_image(slug, product.get("image"))

    def _unlink_image(self, slug: str, name: object) -> None:
        if isinstance(name, str) and IMAGE_NAME_RE.fullmatch(name):
            (self._dir(slug) / "media" / name).unlink(missing_ok=True)

    def set_image(self, slug: str, product_id: str, payload: bytes) -> dict:
        if not payload:
            raise CatalogError("Nenhuma foto enviada.")
        if len(payload) > MAX_IMAGE_BYTES:
            raise CatalogError("A foto pode ter até 2 MB.", 413)
        extension = _image_extension(payload)
        if not extension:
            raise CatalogError("Envie uma foto JPG, PNG ou WebP.")
        with self._lock:
            self._load_store(slug)
            doc = self._load_products(slug)
            product = self._find(doc, product_id)
            media = self._dir(slug) / "media"
            media.mkdir(parents=True, exist_ok=True)
            used = sum(item.stat().st_size for item in media.iterdir() if item.is_file())
            old = product.get("image")
            if isinstance(old, str) and IMAGE_NAME_RE.fullmatch(old) and (media / old).is_file():
                used -= (media / old).stat().st_size
            if used + len(payload) > MAX_STORE_BYTES:
                raise CatalogError("O espaço de fotos desta loja acabou. Apague fotos de produtos antigos.", 413)
            name = f"{product_id}-{secrets.token_hex(4)}.{extension}"
            temp = media / f".{name}.tmp"
            temp.write_bytes(payload)
            os.replace(temp, media / name)
            product["image"] = name
            self._save_products(slug, doc)
            self._unlink_image(slug, old)
            return product

    def remove_image(self, slug: str, product_id: str) -> dict:
        with self._lock:
            self._load_store(slug)
            doc = self._load_products(slug)
            product = self._find(doc, product_id)
            old, product["image"] = product.get("image"), None
            self._save_products(slug, doc)
            self._unlink_image(slug, old)
            return product

    def media_path(self, slug: object, name: object) -> Path | None:
        try:
            folder = self._dir(slug)
        except CatalogError:
            return None
        if not isinstance(name, str) or not IMAGE_NAME_RE.fullmatch(name):
            return None
        if self.is_pending(slug):
            return None
        path = folder / "media" / name
        return path if path.is_file() else None

    # ----- visões (o que sai para o painel do cliente e para a API pública)
    @staticmethod
    def panel_view(product: dict) -> dict:
        cents = product.get("price_cents")
        return {
            "id": product["id"], "name": product.get("name", ""), "description": product.get("description", ""),
            "category": product.get("category", ""), "available": bool(product.get("available", True)),
            "price_cents": cents, "price_text": format_price(cents),
            "price_input": f"{cents // 100},{cents % 100:02d}" if cents is not None else "",
            "image": product.get("image"),
        }

    def public_data(self, slug: str) -> dict:
        store = self._load_store(slug)
        if store.get("pending"):
            raise CatalogError("Catálogo não encontrado.", 404)
        doc = self._load_products(slug)
        items = []
        for product in doc["products"]:
            view = self.panel_view(product)
            view.pop("price_input")
            view["image"] = f"/_media/{slug}/{view['image']}" if view["image"] else None
            items.append(view)
        categories = sorted({item["category"] for item in items if item["category"]}, key=str.lower)
        whatsapp = store.get("whatsapp", "")
        updated = datetime.fromtimestamp(int(doc.get("updated", 0)), timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "store": {"slug": slug, "name": store.get("name", ""), "whatsapp": whatsapp,
                      "whatsapp_url": f"https://wa.me/{whatsapp}" if whatsapp else ""},
            "categories": categories, "products": items, "updated": updated,
        }
