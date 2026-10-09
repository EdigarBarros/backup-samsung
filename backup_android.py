#!/usr/bin/env python3
"""
Backup Android - backup completo de qualquer celular Android pelo cabo USB.

O que faz:
  * copia TODOS os arquivos da memória interna (fotos, vídeos, WhatsApp,
    downloads, documentos, áudios...) e do cartão SD, de forma retomável;
  * exporta contatos (.vcf + .csv), SMS (.json, .xml e conversas em .txt),
    registro de chamadas (.csv), agenda (.ics) e lista de apps;
  * envia o backup para o Google Drive (via rclone);
  * organiza fotos, contatos e agenda num formato que o iPhone importa.

Requisitos: Python 3.8+ e o ADB (Android Platform Tools). O próprio programa
consegue baixar o ADB e o rclone com o comando "instalar".

Uso rápido:
  python backup_android.py              -> menu interativo
  python backup_android.py backup       -> backup completo
  python backup_android.py --help       -> todos os comandos
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
TOOLS_DIR = HERE / "ferramentas"
INTERNAL = "/storage/emulated/0"

# Pastas que o Android não deixa ler sem root (ou que são só cache).
PRUNE_PATHS = ["Android/data", "Android/obb"]
PRUNE_NAMES = [".thumbnails"]

# Atalhos para "--somente": categoria -> pastas da memória interna.
FILE_CATEGORIES: Dict[str, List[str]] = {
    "fotos": ["DCIM", "Pictures", "Movies"],
    "whatsapp": [
        "Android/media/com.whatsapp",
        "Android/media/com.whatsapp.w4b",
        "WhatsApp",
        "WhatsApp Business",
    ],
    "telegram": ["Telegram", "Android/media/org.telegram.messenger"],
    "downloads": ["Download"],
    "documentos": ["Documents"],
    "audios": ["Music", "Recordings", "Podcasts", "Audiobooks", "Ringtones"],
}
DATA_PARTS = ["contatos", "sms", "chamadas", "calendario", "apps"]
ALL_PARTS = ["arquivos", "sd"] + DATA_PARTS

MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".dng", ".bmp",
    ".mp4", ".mov", ".3gp", ".mkv", ".avi", ".webm", ".m4v",
}

NOT_BACKED_UP = [
    "Dados internos dos apps (logins, progresso de jogos, configurações): o Android "
    "bloqueia o acesso sem root. Use o backup próprio de cada app.",
    "Histórico de conversas do WhatsApp em formato legível: o arquivo msgstore.db.crypt "
    "é criptografado e só restaura em Android. Para iPhone use o 'Mover para iOS'.",
    "MMS (mensagens com foto do SMS) e mensagens RCS/Chat.",
    "Senhas salvas, contas de bancos e apps autenticadores (2FA).",
    "Samsung Notes, Samsung Pass e Saúde: ficam fora da área acessível; "
    "sincronize/exporte pelos próprios apps.",
    "Pasta Android/data e Android/obb (bloqueadas a partir do Android 11).",
]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"


def ask_yes(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    suffix = " [S/n] " if default else " [s/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer[0] in ("s", "y")


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """Nome de arquivo válido em Windows, macOS e Linux."""
    clean = _BAD_CHARS.sub("_", name).rstrip(" .")
    if not clean:
        clean = "_"
    if clean.split(".")[0].upper() in _WIN_RESERVED:
        clean = "_" + clean
    return clean


def local_path_for(dest: Path, rel: str) -> Path:
    return dest.joinpath(*[sanitize_name(p) for p in rel.split("/") if p])


def dir_size(path: Path) -> Tuple[int, int]:
    total = count = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return count, total


def fmt_ms(ms: Optional[str]) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


class Progress:
    """Linha de progresso que se atualiza no lugar."""

    def __init__(self, total_files: int, total_bytes: int):
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.files = 0
        self.bytes = 0
        self.start = time.time()
        self._last = 0.0

    def update(self, files: int, nbytes: int, force: bool = False) -> None:
        self.files += files
        self.bytes += nbytes
        now = time.time()
        if not force and now - self._last < 0.5:
            return
        self._last = now
        elapsed = max(now - self.start, 0.001)
        speed = self.bytes / elapsed
        pct = 100 * self.bytes / self.total_bytes if self.total_bytes else 100
        eta = (self.total_bytes - self.bytes) / speed if speed > 0 else 0
        line = (
            f"\r  [{pct:5.1f}%] {self.files}/{self.total_files} arquivos | "
            f"{human_size(self.bytes)} de {human_size(self.total_bytes)} | "
            f"{human_size(speed)}/s | faltam ~{human_time(eta)}   "
        )
        sys.stdout.write(line)
        sys.stdout.flush()

    def finish(self) -> None:
        self.update(0, 0, force=True)
        sys.stdout.write("\n")


# --------------------------------------------------------------------------
# ADB
# --------------------------------------------------------------------------
class AdbError(RuntimeError):
    pass


def exe_name(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def find_adb() -> Optional[str]:
    env = os.environ.get("ADB")
    if env:
        return env
    local = TOOLS_DIR / "platform-tools" / exe_name("adb")
    if local.exists():
        return str(local)
    return shutil.which("adb")


@dataclass
class Device:
    serial: str
    state: str
    props: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.props.get("model", self.serial).replace("_", " ")


def parse_devices(output: str) -> List[Device]:
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        devices.append(Device(parts[0], parts[1], props))
    return devices


class Adb:
    def __init__(self, exe: str, serial: Optional[str] = None):
        self.exe = exe
        self.serial = serial

    def _cmd(self, args: Iterable[str]) -> List[str]:
        base = [self.exe]
        if self.serial:
            base += ["-s", self.serial]
        return base + list(args)

    def run(self, args: Iterable[str], check: bool = True, timeout: Optional[float] = None):
        proc = subprocess.run(
            self._cmd(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        out = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        err = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
        if check and proc.returncode != 0:
            raise AdbError((err or out).strip() or f"adb retornou {proc.returncode}")
        return proc.returncode, out, err

    def shell(self, command: str, timeout: Optional[float] = None) -> str:
        _rc, out, _err = self.run(["shell", command], check=False, timeout=timeout)
        return out

    def getprop(self, name: str) -> str:
        return self.shell(f"getprop {name}").strip()


def connect(serial: Optional[str] = None, interactive: bool = True) -> Adb:
    exe = find_adb()
    if not exe:
        raise AdbError(
            "ADB não encontrado. Rode:  python backup_android.py instalar\n"
            "  (ou baixe em https://developer.android.com/tools/releases/platform-tools)"
        )
    proc = subprocess.run([exe, "devices", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    devices = parse_devices(proc.stdout.decode("utf-8", "replace"))
    if serial:
        devices = [d for d in devices if d.serial == serial]
    if not devices:
        raise AdbError(
            "Nenhum celular encontrado. Confira:\n"
            "  1. Cabo USB de DADOS conectado (alguns cabos só carregam).\n"
            "  2. Opções do desenvolvedor ativadas: Configurações > Sobre o telefone >\n"
            "     Informações do software > toque 7x em 'Número de compilação'.\n"
            "  3. Configurações > Opções do desenvolvedor > 'Depuração USB' LIGADA.\n"
            "  4. No Windows, se ainda não aparecer, instale o driver USB do fabricante\n"
            "     (Samsung: 'Samsung Android USB Driver')."
        )
    unauthorized = [d for d in devices if d.state == "unauthorized"]
    ready = [d for d in devices if d.state == "device"]
    if not ready:
        if unauthorized:
            raise AdbError(
                "O celular está conectado mas não autorizou este computador.\n"
                "  Desbloqueie a tela e toque em 'Permitir' na janela 'Permitir depuração USB?'\n"
                "  (marque 'Sempre permitir deste computador'). Depois rode de novo."
            )
        raise AdbError(f"Celular em estado '{devices[0].state}'. Desconecte e conecte o cabo de novo.")
    device = ready[0]
    if len(ready) > 1:
        print("Mais de um celular conectado:")
        for i, d in enumerate(ready, 1):
            print(f"  {i}. {d.label} ({d.serial})")
        if interactive:
            choice = input("Qual usar? [1] ").strip() or "1"
            device = ready[int(choice) - 1]
        else:
            print(f"Usando o primeiro ({device.serial}). Use --serial para escolher.")
    return Adb(exe, device.serial)


def device_info(adb: Adb) -> Dict[str, str]:
    props = {
        "fabricante": "ro.product.manufacturer",
        "marca": "ro.product.brand",
        "modelo": "ro.product.model",
        "nome": "ro.product.device",
        "android": "ro.build.version.release",
        "sdk": "ro.build.version.sdk",
    }
    info = {k: adb.getprop(v) for k, v in props.items()}
    info["serial"] = adb.serial or ""
    return info


def storage_summary(adb: Adb) -> str:
    out = adb.shell(f"df -h {INTERNAL} 2>/dev/null").strip().splitlines()
    return out[-1] if out else ""


# --------------------------------------------------------------------------
# Arquivos
# --------------------------------------------------------------------------
@dataclass
class RemoteFile:
    path: str
    size: int
    mtime: int


def build_find_command(roots: List[str]) -> str:
    q = shlex.quote
    prune = [f"-path {q(INTERNAL + '/' + p)}" for p in PRUNE_PATHS]
    prune += [f"-name {q(n)}" for n in PRUNE_NAMES]
    return (
        f"find {' '.join(q(r) for r in roots)} \\( {' -o '.join(prune)} \\) -prune "
        f"-o -type f -exec stat -c '%s %Y %n' {{}} + 2>/dev/null"
    )


_STAT_LINE = re.compile(r"^(\d+) (\d+) (/.+)$")


def parse_stat_output(output: str) -> List[RemoteFile]:
    files = []
    for line in output.splitlines():
        m = _STAT_LINE.match(line)
        if m:
            files.append(RemoteFile(m.group(3), int(m.group(1)), int(m.group(2))))
    return files


def list_remote_files(adb: Adb, roots: List[str]) -> List[RemoteFile]:
    files = parse_stat_output(adb.shell(build_find_command(roots)))
    # Itens na lixeira da Galeria/Google Fotos começam com ".trashed-".
    return [f for f in files if not f.path.rsplit("/", 1)[-1].startswith(".trashed-")]


def list_sd_cards(adb: Adb) -> List[str]:
    out = adb.shell("ls /storage 2>/dev/null")
    return [
        f"/storage/{name}"
        for name in out.split()
        if re.fullmatch(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}", name)
    ]


@dataclass
class PullResult:
    total: int = 0
    copied: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    bytes_total: int = 0
    errors: List[str] = field(default_factory=list)


def _batches(items: List[Tuple[RemoteFile, Path]], max_files=64, max_chars=6000, max_bytes=256 << 20):
    batch: List[Tuple[RemoteFile, Path]] = []
    chars = nbytes = 0
    for item in items:
        rf, lp = item
        batchable = lp.name == rf.path.rsplit("/", 1)[-1]
        if not batchable:
            if batch:
                yield batch
                batch, chars, nbytes = [], 0, 0
            yield [item]
            continue
        if batch and (
            lp.parent != batch[0][1].parent
            or len(batch) >= max_files
            or chars + len(rf.path) > max_chars
            or nbytes + rf.size > max_bytes
        ):
            yield batch
            batch, chars, nbytes = [], 0, 0
        batch.append(item)
        chars += len(rf.path) + 3
        nbytes += rf.size
    if batch:
        yield batch


def _size_ok(lp: Path, size: int) -> bool:
    try:
        return lp.stat().st_size == size
    except OSError:
        return False


def pull_files(adb: Adb, files: List[RemoteFile], root: str, dest: Path) -> PullResult:
    res = PullResult(total=len(files), bytes_total=sum(f.size for f in files))
    todo: List[Tuple[RemoteFile, Path]] = []
    for rf in files:
        rel = rf.path[len(root):].lstrip("/")
        lp = local_path_for(dest, rel)
        if _size_ok(lp, rf.size):
            res.skipped += 1
        else:
            todo.append((rf, lp))
    if res.skipped:
        print(f"  {res.skipped} arquivos já estavam copiados (backup retomado).")
    if not todo:
        return res

    need = sum(rf.size for rf, _ in todo)
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free
    print(f"  A copiar: {len(todo)} arquivos ({human_size(need)}). Livre no PC: {human_size(free)}.")
    if need > free - (512 << 20):
        print("  ATENÇÃO: o espaço livre no computador parece insuficiente.")
        if not ask_yes("  Continuar mesmo assim (copia até encher)?", default=False):
            res.errors.append("Backup interrompido por falta de espaço no computador.")
            return res

    todo.sort(key=lambda x: (str(x[1].parent), x[1].name))
    progress = Progress(len(todo), need)
    for batch in _batches(todo):
        target_dir = batch[0][1].parent
        target_dir.mkdir(parents=True, exist_ok=True)
        if len(batch) == 1:
            args = ["pull", "-a", batch[0][0].path, str(batch[0][1])]
        else:
            args = ["pull", "-a"] + [rf.path for rf, _ in batch] + [str(target_dir)]
        rc, _out, err = adb.run(args, check=False)
        if rc != 0 and len(batch) > 1:
            # Repete um por um para descobrir qual falhou.
            for rf, lp in batch:
                if not _size_ok(lp, rf.size):
                    adb.run(["pull", "-a", rf.path, str(lp)], check=False)
        for rf, lp in batch:
            if _size_ok(lp, rf.size):
                res.copied += 1
                res.bytes_copied += rf.size
            else:
                msg = err.strip().splitlines()[-1] if err.strip() else "falha ao copiar"
                res.errors.append(f"{rf.path}: {msg}")
        progress.update(len(batch), sum(rf.size for rf, _ in batch))
    progress.finish()
    return res


# --------------------------------------------------------------------------
# Provedores de conteúdo (contatos, SMS, chamadas, agenda)
# --------------------------------------------------------------------------
_ROW_RE = re.compile(r"^Row: \d+ ", re.M)


def parse_content_rows(text: str, columns: List[str]) -> List[Dict[str, Optional[str]]]:
    """Interpreta a saída de `adb shell content query`.

    Cada registro começa com 'Row: N ' e os valores podem ter quebras de linha,
    por isso separamos pelos nomes de coluna conhecidos.
    """
    text = text.replace("\r\n", "\n")
    names = sorted(columns, key=len, reverse=True)
    splitter = re.compile(r"(?:^|, )(" + "|".join(re.escape(c) for c in names) + r")=")
    rows = []
    for chunk in _ROW_RE.split(text)[1:]:
        if chunk.endswith("\n"):
            chunk = chunk[:-1]
        parts = splitter.split(chunk)
        row: Dict[str, Optional[str]] = {}
        for key, value in zip(parts[1::2], parts[2::2]):
            row[key] = None if value == "NULL" else value
        rows.append(row)
    return rows


class ProviderDenied(RuntimeError):
    pass


def content_query(adb: Adb, uri: str, columns: List[str]) -> List[Dict[str, Optional[str]]]:
    out = adb.shell(f"content query --uri {uri} --projection {':'.join(columns)}")
    head = out[:600]
    if "Permission Denial" in head or "SecurityException" in head or head.startswith("Error"):
        raise ProviderDenied(head.strip().splitlines()[0] if head.strip() else "acesso negado")
    if "No result found" in head:
        return []
    return parse_content_rows(out, columns)


# ---- Contatos -------------------------------------------------------------
CONTACT_COLUMNS = ["contact_id", "mimetype", "data1", "data2", "data3", "data4"]
MT = "vnd.android.cursor.item/"
PHONE_TYPES = {"1": "HOME", "2": "CELL", "3": "WORK", "4": "WORK,FAX", "5": "HOME,FAX",
               "6": "PAGER", "7": "OTHER", "12": "MAIN"}
EMAIL_TYPES = {"1": "HOME", "2": "WORK"}


def build_contacts(rows: List[Dict[str, Optional[str]]]) -> List[dict]:
    contacts: Dict[str, dict] = {}
    for r in rows:
        cid = r.get("contact_id")
        mime = r.get("mimetype") or ""
        d1 = (r.get("data1") or "").strip()
        if not cid or not d1:
            continue
        c = contacts.setdefault(cid, {
            "nome": "", "primeiro_nome": "", "sobrenome": "", "telefones": [], "emails": [],
            "enderecos": [], "empresa": "", "cargo": "", "apelido": "", "aniversario": "",
            "nota": "",
        })
        if mime == MT + "name":
            c["nome"] = c["nome"] or d1
            c["primeiro_nome"] = c["primeiro_nome"] or (r.get("data2") or "")
            c["sobrenome"] = c["sobrenome"] or (r.get("data3") or "")
        elif mime == MT + "phone_v2":
            digits = re.sub(r"\D", "", d1)
            if digits and all(re.sub(r"\D", "", p[0])[-9:] != digits[-9:] for p in c["telefones"]):
                c["telefones"].append((d1, PHONE_TYPES.get(r.get("data2") or "", "CELL")))
        elif mime == MT + "email_v2":
            if d1.lower() not in (e[0].lower() for e in c["emails"]):
                c["emails"].append((d1, EMAIL_TYPES.get(r.get("data2") or "", "HOME")))
        elif mime == MT + "postal-address_v2":
            if d1 not in c["enderecos"]:
                c["enderecos"].append(d1)
        elif mime == MT + "organization":
            c["empresa"] = c["empresa"] or d1
            c["cargo"] = c["cargo"] or (r.get("data4") or "")
        elif mime == MT + "nickname":
            c["apelido"] = c["apelido"] or d1
        elif mime == MT + "contact_event" and r.get("data2") == "3":
            c["aniversario"] = d1
        elif mime == MT + "note":
            c["nota"] = c["nota"] or d1
    result = []
    for c in contacts.values():
        if not c["nome"]:
            c["nome"] = c["empresa"] or (c["telefones"][0][0] if c["telefones"] else "") or (
                c["emails"][0][0] if c["emails"] else "")
        if c["nome"]:
            result.append(c)
    result.sort(key=lambda c: c["nome"].lower())
    return result


def vcard_escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))


def to_vcard(c: dict) -> str:
    e = vcard_escape
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if c["primeiro_nome"] or c["sobrenome"]:
        lines.append(f"N:{e(c['sobrenome'])};{e(c['primeiro_nome'])};;;")
    else:
        lines.append(f"N:;{e(c['nome'])};;;")
    lines.append(f"FN:{e(c['nome'])}")
    for num, kind in c["telefones"]:
        lines.append(f"TEL;TYPE={kind}:{num}")
    for mail, kind in c["emails"]:
        lines.append(f"EMAIL;TYPE=INTERNET,{kind}:{mail}")
    for addr in c["enderecos"]:
        lines.append(f"ADR;TYPE=HOME:;;{e(addr)};;;;")
    if c["empresa"]:
        lines.append(f"ORG:{e(c['empresa'])}")
    if c["cargo"]:
        lines.append(f"TITLE:{e(c['cargo'])}")
    if c["apelido"]:
        lines.append(f"NICKNAME:{e(c['apelido'])}")
    if c["aniversario"]:
        lines.append(f"BDAY:{c['aniversario']}")
    if c["nota"]:
        lines.append(f"NOTE:{e(c['nota'])}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def export_contacts(adb: Adb, outdir: Path) -> str:
    rows = content_query(adb, "content://com.android.contacts/data", CONTACT_COLUMNS)
    contacts = build_contacts(rows)
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "contatos.vcf", "w", encoding="utf-8", newline="") as f:
        for c in contacts:
            f.write(to_vcard(c))
    with open(outdir / "contatos.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Nome", "Telefones", "E-mails", "Empresa", "Aniversário", "Endereços", "Nota"])
        for c in contacts:
            w.writerow([c["nome"], " | ".join(p[0] for p in c["telefones"]),
                        " | ".join(m[0] for m in c["emails"]), c["empresa"], c["aniversario"],
                        " | ".join(c["enderecos"]), c["nota"]])
    return f"{len(contacts)} contatos"


# ---- SMS ------------------------------------------------------------------
SMS_COLUMNS = ["_id", "thread_id", "address", "date", "date_sent", "type", "read", "body"]
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def xml_attr(value: Optional[str]) -> str:
    v = _XML_BAD.sub("", value if value is not None else "null")
    v = (v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
         .replace('"', "&quot;").replace("\n", "&#10;").replace("\r", "&#13;"))
    return f'"{v}"'


def sms_to_xml(messages: List[dict]) -> str:
    """Formato do app 'SMS Backup & Restore' (restaura em qualquer Android)."""
    out = io.StringIO()
    out.write("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
    out.write(f'<smses count="{len(messages)}">\n')
    for m in messages:
        attrs = {
            "protocol": "0", "address": m.get("address") or "", "date": m.get("date") or "0",
            "type": m.get("type") or "1", "subject": None, "body": m.get("body") or "",
            "toa": None, "sc_toa": None, "service_center": None, "read": m.get("read") or "1",
            "status": "-1", "locked": "0", "date_sent": m.get("date_sent") or "0",
            "readable_date": fmt_ms(m.get("date")), "contact_name": "(Unknown)",
        }
        out.write("  <sms " + " ".join(f"{k}={xml_attr(v)}" for k, v in attrs.items()) + " />\n")
    out.write("</smses>\n")
    return out.getvalue()


def export_sms(adb: Adb, outdir: Path) -> str:
    messages = content_query(adb, "content://sms", SMS_COLUMNS)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "sms.json").write_text(json.dumps(messages, ensure_ascii=False, indent=1), "utf-8")
    (outdir / "sms_backup_restore.xml").write_text(sms_to_xml(messages), "utf-8")
    convs: Dict[str, List[dict]] = {}
    for m in messages:
        convs.setdefault(m.get("address") or "desconhecido", []).append(m)
    conv_dir = outdir / "conversas"
    conv_dir.mkdir(exist_ok=True)
    for address, msgs in convs.items():
        msgs.sort(key=lambda m: int(m.get("date") or 0))
        with open(conv_dir / (sanitize_name(address)[:80] + ".txt"), "w", encoding="utf-8") as f:
            f.write(f"Conversa com {address}\n{'=' * 40}\n")
            for m in msgs:
                who = "Eu" if m.get("type") == "2" else address
                f.write(f"[{fmt_ms(m.get('date'))}] {who}: {m.get('body') or ''}\n")
    return f"{len(messages)} SMS em {len(convs)} conversas"


# ---- Chamadas -------------------------------------------------------------
CALL_COLUMNS = ["number", "name", "date", "duration", "type"]
CALL_TYPES = {"1": "Recebida", "2": "Feita", "3": "Perdida", "4": "Correio de voz",
              "5": "Rejeitada", "6": "Bloqueada"}


def export_calls(adb: Adb, outdir: Path) -> str:
    calls = content_query(adb, "content://call_log/calls", CALL_COLUMNS)
    calls.sort(key=lambda c: int(c.get("date") or 0), reverse=True)
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "chamadas.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data", "Número", "Nome", "Tipo", "Duração (s)"])
        for c in calls:
            w.writerow([fmt_ms(c.get("date")), c.get("number") or "", c.get("name") or "",
                        CALL_TYPES.get(c.get("type") or "", c.get("type") or ""),
                        c.get("duration") or "0"])
    return f"{len(calls)} chamadas"


# ---- Agenda ---------------------------------------------------------------
EVENT_COLUMNS = ["_id", "title", "description", "eventLocation", "dtstart", "dtend",
                 "allDay", "rrule", "duration", "deleted"]


def _ics_text(value: Optional[str]) -> str:
    return vcard_escape(value or "")


def _ics_time(ms: Optional[str], all_day: bool) -> Optional[str]:
    try:
        t = dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return t.strftime("%Y%m%d") if all_day else t.strftime("%Y%m%dT%H%M%SZ")


def events_to_ics(events: List[dict]) -> Tuple[str, int]:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//backup-android//PT-BR", "CALSCALE:GREGORIAN"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    count = 0
    for ev in events:
        if ev.get("deleted") == "1":
            continue
        all_day = ev.get("allDay") == "1"
        start = _ics_time(ev.get("dtstart"), all_day)
        if not start:
            continue
        prefix = ";VALUE=DATE" if all_day else ""
        lines += ["BEGIN:VEVENT", f"UID:android-{ev.get('_id')}@backup-android",
                  f"DTSTAMP:{stamp}", f"DTSTART{prefix}:{start}"]
        end = _ics_time(ev.get("dtend"), all_day)
        if end:
            lines.append(f"DTEND{prefix}:{end}")
        elif ev.get("duration"):
            dur = ev["duration"]
            m = re.fullmatch(r"P(\d+)S", dur)
            lines.append(f"DURATION:{'PT' + m.group(1) + 'S' if m else dur}")
        if ev.get("rrule"):
            lines.append(f"RRULE:{ev['rrule']}")
        lines.append(f"SUMMARY:{_ics_text(ev.get('title')) or '(sem título)'}")
        if ev.get("eventLocation"):
            lines.append(f"LOCATION:{_ics_text(ev.get('eventLocation'))}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_ics_text(ev.get('description'))}")
        lines.append("END:VEVENT")
        count += 1
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n", count


def export_calendar(adb: Adb, outdir: Path) -> str:
    events = content_query(adb, "content://com.android.calendar/events", EVENT_COLUMNS)
    ics, count = events_to_ics(events)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "calendario.ics").write_text(ics, "utf-8")
    return f"{count} eventos"


# ---- Apps -----------------------------------------------------------------
def export_apps(adb: Adb, outdir: Path, with_apks: bool = False) -> str:
    out = adb.shell("pm list packages -3")
    pkgs = sorted(line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("package:"))
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "apps_instalados.txt", "w", encoding="utf-8") as f:
        f.write("Apps instalados por você (procure os equivalentes na App Store do iPhone)\n\n")
        for p in pkgs:
            f.write(f"{p}\n    https://play.google.com/store/apps/details?id={p}\n")
    msg = f"{len(pkgs)} apps listados"
    if with_apks:
        ok = 0
        for p in pkgs:
            paths = [l.split(":", 1)[1].strip() for l in adb.shell(f"pm path {p}").splitlines()
                     if l.startswith("package:")]
            target = outdir / "apks" / sanitize_name(p)
            target.mkdir(parents=True, exist_ok=True)
            if all(adb.run(["pull", ap, str(target)], check=False)[0] == 0 for ap in paths):
                ok += 1
        msg += f", {ok} APKs copiados"
    return msg


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
def default_backup_dir(info: Dict[str, str]) -> Path:
    name = sanitize_name(f"Backup_{info.get('fabricante') or 'android'}_{info.get('modelo') or 'celular'}")
    return Path.cwd() / name.replace(" ", "_")


def parse_selection(value: Optional[str]) -> Tuple[set, List[str]]:
    """Retorna (partes, pastas). Pastas vazias = memória interna inteira."""
    if not value:
        return set(ALL_PARTS), []
    parts, folders = set(), []
    for item in (v.strip().lower() for v in value.split(",") if v.strip()):
        if item in FILE_CATEGORIES:
            parts.add("arquivos")
            folders += FILE_CATEGORIES[item]
        elif item in ALL_PARTS:
            parts.add(item)
        else:
            valid = ", ".join(list(FILE_CATEGORIES) + ALL_PARTS)
            raise SystemExit(f"Opção desconhecida em --somente: '{item}'. Válidas: {valid}")
    return parts, folders


def cmd_check(args) -> int:
    adb = connect(args.serial, interactive=not args.sim)
    info = device_info(adb)
    print(f"Conectado: {info['fabricante']} {info['modelo']} (Android {info['android']})")
    df = storage_summary(adb)
    if df:
        print(f"Armazenamento: {df}")
    sd = list_sd_cards(adb)
    print(f"Cartão SD: {', '.join(sd) if sd else 'nenhum'}")
    try:
        content_query(adb, "content://com.android.contacts/data", ["contact_id"])
        print("Acesso a contatos/SMS pelo cabo: OK")
    except ProviderDenied:
        print("Acesso a contatos/SMS pelo cabo: BLOQUEADO neste aparelho (veja o README).")
    return 0


def cmd_backup(args) -> int:
    parts, folders = parse_selection(args.somente)
    adb = connect(args.serial, interactive=not args.sim)
    info = device_info(adb)
    dest = Path(args.destino).expanduser().resolve() if args.destino else default_backup_dir(info)
    dest.mkdir(parents=True, exist_ok=True)
    print(f"\nCelular: {info['fabricante']} {info['modelo']} (Android {info['android']})")
    print(f"Backup em: {dest}\n")
    print("Dica: deixe o celular carregando e com a tela desbloqueada durante o backup.\n")

    started = dt.datetime.now()
    report: Dict[str, object] = {"versao": VERSION, "inicio": started.isoformat(timespec="seconds"),
                                 "aparelho": info, "itens": {}, "erros": []}
    errors: List[str] = []

    if "arquivos" in parts:
        roots = [f"{INTERNAL}/{f}" for f in folders] if folders else [INTERNAL]
        print("[Arquivos] Listando arquivos da memória interna (pode levar alguns minutos)...")
        files = list_remote_files(adb, roots)
        print(f"  {len(files)} arquivos, {human_size(sum(f.size for f in files))}.")
        res = pull_files(adb, files, INTERNAL, dest / "arquivos")
        errors += res.errors
        report["itens"]["arquivos"] = {"total": res.total, "copiados_agora": res.copied,
                                       "ja_existiam": res.skipped, "bytes": res.bytes_total,
                                       "falhas": len(res.errors)}

    if "sd" in parts and not folders:
        for card in list_sd_cards(adb):
            card_id = card.rsplit("/", 1)[-1]
            print(f"\n[Cartão SD {card_id}] Listando arquivos...")
            files = list_remote_files(adb, [card])
            print(f"  {len(files)} arquivos, {human_size(sum(f.size for f in files))}.")
            res = pull_files(adb, files, card, dest / "cartao_sd" / card_id)
            errors += res.errors
            report["itens"][f"cartao_sd_{card_id}"] = {"total": res.total, "falhas": len(res.errors)}

    exports = [
        ("contatos", "Contatos", lambda: export_contacts(adb, dest / "contatos")),
        ("sms", "SMS", lambda: export_sms(adb, dest / "mensagens")),
        ("chamadas", "Chamadas", lambda: export_calls(adb, dest / "chamadas")),
        ("calendario", "Agenda", lambda: export_calendar(adb, dest / "calendario")),
        ("apps", "Apps", lambda: export_apps(adb, dest / "apps", with_apks=args.apks)),
    ]
    for key, label, fn in exports:
        if key not in parts:
            continue
        print(f"\n[{label}] Exportando...")
        try:
            msg = fn()
            print(f"  OK: {msg}")
            report["itens"][key] = msg
        except ProviderDenied as exc:
            msg = (f"{label}: o aparelho bloqueou a leitura pelo cabo ({exc}). "
                   "Veja no README como exportar pelo próprio celular.")
            print("  " + msg)
            errors.append(msg)
            report["itens"][key] = "bloqueado pelo aparelho"
        except Exception as exc:  # noqa: BLE001 - um item com problema não derruba o backup
            errors.append(f"{label}: {exc}")
            print(f"  Falhou: {exc}")
            report["itens"][key] = f"erro: {exc}"

    report["fim"] = dt.datetime.now().isoformat(timespec="seconds")
    report["erros"] = errors
    report["nao_incluido"] = NOT_BACKED_UP
    (dest / "backup_info.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    write_text_report(dest, report)
    if errors:
        (dest / "erros.txt").write_text("\n".join(errors) + "\n", "utf-8")

    print("\n" + "=" * 60)
    count, total = dir_size(dest)
    print(f"Backup concluído: {count} arquivos, {human_size(total)} em\n  {dest}")
    if errors:
        print(f"{len(errors)} itens com problema (veja erros.txt). Rode o backup de novo: "
              "ele continua de onde parou e tenta só o que faltou.")
    print("Leia RELATORIO.txt para ver o que NÃO dá para copiar pelo cabo.")
    return 0 if not errors else 2


def write_text_report(dest: Path, report: dict) -> None:
    info = report["aparelho"]
    lines = [
        "RELATÓRIO DO BACKUP",
        "=" * 60,
        f"Aparelho: {info.get('fabricante')} {info.get('modelo')} - Android {info.get('android')}",
        f"Início:   {report['inicio']}",
        f"Fim:      {report.get('fim')}",
        "",
        "O QUE FOI SALVO",
    ]
    for key, value in report["itens"].items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        lines.append(f"  - {key}: {value}")
    lines += ["", f"PROBLEMAS: {len(report['erros'])} (detalhes em erros.txt)", "",
              "O QUE NÃO DÁ PARA COPIAR PELO CABO (faça manualmente):"]
    lines += [f"  - {item}" for item in NOT_BACKED_UP]
    lines += ["", "Antes de vender: python backup_android.py checklist"]
    (dest / "RELATORIO.txt").write_text("\n".join(lines) + "\n", "utf-8")


def find_backup_dir(path: Optional[str]) -> Path:
    if path:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"Pasta não encontrada: {p}")
        return p
    candidates = sorted((d for d in Path.cwd().glob("Backup_*") if d.is_dir()),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("Nenhuma pasta Backup_* aqui. Informe a pasta: --pasta CAMINHO")
    return candidates[0]


# ---- Google Drive ---------------------------------------------------------
def find_rclone() -> Optional[str]:
    local = TOOLS_DIR / "rclone" / exe_name("rclone")
    return str(local) if local.exists() else shutil.which("rclone")


def cmd_drive(args) -> int:
    src = find_backup_dir(args.pasta)
    rclone = find_rclone()
    if not rclone:
        print("rclone não encontrado. Rode:  python backup_android.py instalar\n"
              "  (ou baixe em https://rclone.org/downloads/)")
        return 1
    remote = args.remote
    remotes = subprocess.run([rclone, "listremotes"], stdout=subprocess.PIPE).stdout.decode().split()
    if f"{remote}:" not in remotes:
        print(f"Ainda não há uma conta Google ligada ao rclone (nome '{remote}').")
        print("Vou abrir o navegador para você entrar na sua conta Google e autorizar.")
        if not ask_yes("Continuar?", assume_yes=args.sim):
            return 1
        rc = subprocess.run([rclone, "config", "create", remote, "drive", "scope", "drive"]).returncode
        if rc != 0:
            print("Não consegui configurar. Tente manualmente:  rclone config")
            return 1

    count, size = dir_size(src)
    about = subprocess.run([rclone, "about", f"{remote}:", "--json"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
    try:
        free = json.loads(about.stdout.decode()).get("free")
    except ValueError:
        free = None
    print(f"Backup: {src.name} - {count} arquivos, {human_size(size)}")
    if free is not None:
        print(f"Espaço livre no Google Drive: {human_size(free)}")
        if size > free:
            print("ATENÇÃO: não cabe tudo no seu Drive. Opções: assinar o Google One, enviar só\n"
                  "parte (--subpasta arquivos/DCIM) ou guardar o resto num HD externo.")
            if not ask_yes("Enviar mesmo assim (vai parar quando encher)?", default=False):
                return 1
    source = src / args.subpasta if args.subpasta else src
    target = f"{remote}:{args.destino}/{src.name}" + (f"/{args.subpasta}" if args.subpasta else "")
    print(f"\nEnviando para {target} ... (pode interromper e rodar de novo: ele continua)\n")
    rc = subprocess.run([rclone, "copy", str(source), target, "--progress",
                         "--transfers", "4", "--checkers", "8", "--retries", "5"]).returncode
    if rc != 0:
        print("\nO envio terminou com erros. Rode o mesmo comando de novo para tentar o que faltou.")
        return rc
    print("\nConferindo se tudo chegou...")
    rc = subprocess.run([rclone, "check", str(source), target, "--one-way", "--size-only"]).returncode
    print("Tudo conferido no Google Drive!" if rc == 0 else "Há diferenças: rode o envio de novo.")
    return rc


# ---- iPhone ---------------------------------------------------------------
IPHONE_GUIDE = """COMO PASSAR SEUS DADOS PARA O IPHONE
====================================

>>> O CAMINHO MAIS COMPLETO (faça ANTES de vender o Android) <<<
App "Mover para iOS" (Move to iOS), da Apple, na Play Store.
Durante a configuração inicial do iPhone, na tela "Transferir Apps e Dados",
escolha "Do Android". Ele transfere: contatos, SMS, fotos, vídeos, agenda,
contas de e-mail e o HISTÓRICO DO WHATSAPP (marque WhatsApp na lista).
Se o iPhone já foi configurado, só dá para usar isso apagando o iPhone.
O histórico do WhatsApp NÃO passa pelo backup do Google Drive para o iPhone.

>>> USANDO ESTA PASTA (se não puder usar o Mover para iOS) <<<

1) FOTOS E VÍDEOS  (pasta Fotos_e_Videos)
   a) iCloud: em icloud.com > Fotos > botão de enviar (arraste as pastas),
      ou no Windows instale o "iCloud para Windows" e copie para a pasta
      "Fotos do iCloud". No Mac: abra o app Fotos > Arquivo > Importar.
   b) Google Fotos: envie a pasta pelo photos.google.com e instale o app
      Google Fotos no iPhone (não ocupa o iCloud).

2) CONTATOS  (contatos.vcf)
   icloud.com > Contatos > engrenagem > "Importar vCard..." > escolha o arquivo.
   Ou mande o .vcf por e-mail para você mesmo, abra no iPhone e toque em
   "Adicionar todos os contatos".
   Se seus contatos já estavam na conta Google: Ajustes > Contatos > Contas >
   adicionar conta Google - eles aparecem sozinhos.

3) AGENDA  (calendario.ics)
   Abra o arquivo pelo e-mail no iPhone e toque em "Adicionar todos",
   ou adicione sua conta Google em Ajustes > Calendário > Contas.

4) SMS  (pasta conversas)
   A Apple não permite importar SMS no iPhone a não ser pelo "Mover para iOS".
   As conversas ficam aqui em .txt para consulta.

5) ARQUIVOS, DOCUMENTOS E DOWNLOADS
   Envie para o Google Drive (python backup_android.py drive) e acesse pelo
   app Google Drive ou pelo app Arquivos do iPhone.
"""


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)  # não ocupa espaço extra no disco
    except OSError:
        shutil.copy2(src, dst)


def cmd_iphone(args) -> int:
    src = find_backup_dir(args.pasta)
    out = src / "Para_iPhone"
    media_out = out / "Fotos_e_Videos"
    media_out.mkdir(parents=True, exist_ok=True)
    files_root = src / "arquivos"
    sources = ["DCIM", "Pictures", "Movies", "Download",
               "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
               "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video",
               "WhatsApp/Media/WhatsApp Images", "WhatsApp/Media/WhatsApp Video"]
    count = 0
    for rel in sources:
        base = local_path_for(files_root, rel)
        if not base.is_dir():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            media = [f for f in files if Path(f).suffix.lower() in MEDIA_EXTS and not f.startswith(".")]
            if not media:
                continue
            label = " - ".join(Path(root).relative_to(files_root).parts[-2:])
            target = media_out / sanitize_name(label)
            target.mkdir(exist_ok=True)
            for f in media:
                dst = target / f
                if not dst.exists():
                    _link_or_copy(Path(root) / f, dst)
                count += 1
    copied = []
    for rel in ("contatos/contatos.vcf", "calendario/calendario.ics"):
        if (src / rel).exists():
            shutil.copy2(src / rel, out / Path(rel).name)
            copied.append(Path(rel).name)
    if (src / "mensagens" / "conversas").is_dir():
        shutil.copytree(src / "mensagens" / "conversas", out / "conversas", dirs_exist_ok=True)
        copied.append("conversas/")
    (out / "COMO_PASSAR_PRO_IPHONE.txt").write_text(IPHONE_GUIDE, "utf-8")
    print(f"Pronto: {out}")
    print(f"  {count} fotos/vídeos organizados em Fotos_e_Videos")
    if copied:
        print(f"  + {', '.join(copied)}")
    print("  Leia COMO_PASSAR_PRO_IPHONE.txt para o passo a passo.")
    return 0


# ---- Checklist ------------------------------------------------------------
CHECKLIST = """CHECKLIST ANTES DE VENDER O CELULAR
===================================
[ ] 1. Backup feito com este programa e RELATORIO.txt sem erros importantes.
[ ] 2. Backup copiado para um segundo lugar (Google Drive ou HD externo).
        Um backup só em um lugar não é backup.
[ ] 3. Vai para iPhone? Use o "Mover para iOS" ANTES de resetar o Android
        (é o único jeito de levar o histórico do WhatsApp e os SMS).
        Vai ficar no Android? WhatsApp > Configurações > Conversas > Backup.
[ ] 4. APPS AUTENTICADORES (Google Authenticator, Microsoft Authenticator,
        Authy...): transfira as contas para o novo celular. Se esquecer,
        você pode perder acesso a e-mails, redes sociais e corretoras.
[ ] 5. Bancos e Pix: cadastre o novo aparelho / desvincule o antigo no app.
[ ] 6. Samsung Notes, Samsung Pass, Samsung Saúde: sincronize ou exporte
        (Notes > compartilhar > PDF; Pass não vai pro iPhone - anote as senhas
        no gerenciador da Apple ou do Google).
[ ] 7. Desparear relógio, fones (Galaxy Buds) e outros acessórios.
[ ] 8. Sair da conta Samsung e REMOVER a conta Google
        (Configurações > Contas e backup > Gerenciar contas). Se não tirar,
        o comprador fica preso no bloqueio de fábrica (FRP).
[ ] 9. Desativar "Encontrar meu celular" / "Find My Mobile".
[ ] 10. eSIM: peça a transferência à operadora. Retire chip físico e cartão SD.
[ ] 11. Restaurar padrão de fábrica:
        Configurações > Gerenciamento geral > Redefinir > Restaurar padrão de fábrica.
"""


def cmd_checklist(_args) -> int:
    print(CHECKLIST)
    return 0


# ---- Instalação de ferramentas -------------------------------------------
def _download_zip(url: str, target: Path) -> None:
    print(f"  Baixando {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(target)


def cmd_install(args) -> int:
    try:
        return _install_tools()
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"\nNão consegui baixar ({exc}). Confira a internet ou baixe manualmente:\n"
              "  ADB:    https://developer.android.com/tools/releases/platform-tools\n"
              "  rclone: https://rclone.org/downloads/\n"
              f"e extraia em {TOOLS_DIR} (pastas platform-tools/ e rclone/).")
        return 1


def _install_tools() -> int:
    system = platform.system()
    os_adb = {"Windows": "windows", "Darwin": "darwin"}.get(system, "linux")
    os_rclone = {"Windows": "windows", "Darwin": "osx"}.get(system, "linux")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"

    if not (TOOLS_DIR / "platform-tools" / exe_name("adb")).exists():
        print("Instalando ADB (Google Platform Tools)...")
        _download_zip(f"https://dl.google.com/android/repository/platform-tools-latest-{os_adb}.zip", TOOLS_DIR)
    adb = TOOLS_DIR / "platform-tools" / exe_name("adb")
    if os.name != "nt" and adb.exists():
        adb.chmod(0o755)
    print(f"  ADB: {adb}")

    rclone = TOOLS_DIR / "rclone" / exe_name("rclone")
    if not rclone.exists():
        print("Instalando rclone (envio para o Google Drive)...")
        tmp = TOOLS_DIR / "_rclone_tmp"
        _download_zip(f"https://downloads.rclone.org/rclone-current-{os_rclone}-{arch}.zip", tmp)
        found = next(tmp.rglob(exe_name("rclone")))
        rclone.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(found), rclone)
        shutil.rmtree(tmp, ignore_errors=True)
        if os.name != "nt":
            rclone.chmod(0o755)
    print(f"  rclone: {rclone}")
    print("Ferramentas prontas.")
    return 0


# --------------------------------------------------------------------------
# Menu e argumentos
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backup_android.py",
        description="Backup completo de celulares Android pelo cabo USB, com envio "
                    "para Google Drive e preparação para iPhone.")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("verificar", help="testa a conexão com o celular")
    s.add_argument("--serial")
    s.add_argument("--sim", action="store_true", help="não fazer perguntas")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("backup", help="faz o backup completo (retomável)")
    s.add_argument("--destino", help="pasta onde salvar (padrão: ./Backup_<modelo>)")
    s.add_argument("--somente", help="ex.: fotos,whatsapp,contatos  (categorias: "
                   + ", ".join(list(FILE_CATEGORIES) + ALL_PARTS) + ")")
    s.add_argument("--apks", action="store_true", help="também copia os instaladores (APK) dos apps")
    s.add_argument("--serial", help="escolhe o celular se houver mais de um")
    s.add_argument("--sim", action="store_true", help="não fazer perguntas")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("drive", help="envia o backup para o Google Drive")
    s.add_argument("--pasta", help="pasta do backup (padrão: a mais recente aqui)")
    s.add_argument("--destino", default="Backup Celular", help="pasta no Drive")
    s.add_argument("--subpasta", help="envia só uma parte, ex.: arquivos/DCIM")
    s.add_argument("--remote", default="gdrive", help="nome da conexão no rclone")
    s.add_argument("--sim", action="store_true", help="não fazer perguntas")
    s.set_defaults(func=cmd_drive)

    s = sub.add_parser("iphone", help="organiza fotos, contatos e agenda para o iPhone")
    s.add_argument("--pasta", help="pasta do backup (padrão: a mais recente aqui)")
    s.set_defaults(func=cmd_iphone)

    s = sub.add_parser("checklist", help="o que fazer antes de vender o celular")
    s.set_defaults(func=cmd_checklist)

    s = sub.add_parser("instalar", help="baixa ADB e rclone para a pasta ferramentas/")
    s.set_defaults(func=cmd_install)
    return p


MENU = """
==============================================
  BACKUP ANDROID v{v}
==============================================
  1. Verificar se o celular está conectado
  2. Fazer BACKUP COMPLETO
  3. Enviar o backup para o GOOGLE DRIVE
  4. Preparar arquivos para o IPHONE
  5. Checklist antes de vender
  6. Instalar ferramentas (ADB e rclone)
  0. Sair
"""


def interactive_menu(parser: argparse.ArgumentParser) -> int:
    actions = {"1": ["verificar"], "2": ["backup"], "3": ["drive"], "4": ["iphone"],
               "5": ["checklist"], "6": ["instalar"]}
    while True:
        print(MENU.format(v=VERSION))
        try:
            choice = input("Escolha: ").strip()
        except EOFError:
            return 0
        if choice == "0":
            return 0
        if choice not in actions:
            continue
        args = parser.parse_args(actions[choice])
        try:
            args.func(args)
        except (AdbError, SystemExit) as exc:
            print(f"\n{exc}")
        except KeyboardInterrupt:
            print("\nInterrompido. Rode de novo para continuar de onde parou.")
        input("\nEnter para voltar ao menu...")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        return interactive_menu(parser)
    try:
        return args.func(args)
    except AdbError as exc:
        print(f"\nErro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido. Rode o mesmo comando de novo para continuar de onde parou.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
