from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


class EstadoExpediente(str, Enum):
    PENDIENTE_DOCUMENTACION = "Pendiente de documentacion"
    DOCUMENTACION_RECIBIDA = "Documentacion recibida"
    DOCUMENTACION_INCOMPLETA = "Documentacion incompleta"
    PENDIENTE_REVISION = "Pendiente de revision manual"
    LISTO_ANALISIS = "Listo para analisis"
    EN_ANALISIS = "En analisis"
    ANALIZADO = "Analizado"
    ERROR_DOCUMENTAL = "Error documental"


class TipoDocumento(str, Enum):
    COMPRAS = "Compras"
    VENTAS = "Ventas"
    FACTURAS = "Facturas"
    ALBARANES = "Albaranes"
    LIQUIDACIONES = "Liquidaciones"
    STOCK = "Stock"
    OTROS = "Otros"


class EstadoDocumento(str, Enum):
    RECIBIDO = "recibido"
    INCORRECTO = "incorrecto"
    REEMPLAZADO = "reemplazado"
    ELIMINADO = "eliminado"
    DUPLICADO = "duplicado"


class PerfilDocumental(str, Enum):
    BIDAFARMA = "BIDAFARMA"
    COFARES = "COFARES"
    ALLIANCE = "ALLIANCE"
    HEFAME = "HEFAME"
    LABORATORIOS = "LABORATORIOS"
    GENERICO = "GENERICO"


EXTENSIONES_ADMITIDAS = {".xlsx", ".xls", ".csv", ".pdf", ".zip"}

CARPETAS_TIPO_DOCUMENTO = {
    TipoDocumento.COMPRAS.value: "01_Compras",
    TipoDocumento.VENTAS.value: "02_Ventas",
    TipoDocumento.FACTURAS.value: "03_Facturas",
    TipoDocumento.ALBARANES.value: "04_Albaranes",
    TipoDocumento.LIQUIDACIONES.value: "05_Liquidaciones",
    TipoDocumento.STOCK.value: "06_Stock",
    TipoDocumento.OTROS.value: "07_Otros",
}

CHECKLISTS_OBLIGATORIOS = {
    "ASESORIA": [
        TipoDocumento.COMPRAS.value,
        TipoDocumento.VENTAS.value,
        TipoDocumento.FACTURAS.value,
        TipoDocumento.ALBARANES.value,
        TipoDocumento.LIQUIDACIONES.value,
    ],
    "AUDITORIA_INICIAL": [
        TipoDocumento.VENTAS.value,
        TipoDocumento.COMPRAS.value,
        TipoDocumento.FACTURAS.value,
        TipoDocumento.ALBARANES.value,
        TipoDocumento.LIQUIDACIONES.value,
    ],
    "REVISION_FACTURAS": [
        TipoDocumento.FACTURAS.value,
    ],
}

PATRONES_DOCUMENTOS = [
    (TipoDocumento.COMPRAS.value, ("compra", "compras", "pedido")),
    (TipoDocumento.VENTAS.value, ("venta", "ventas", "ticket")),
    (TipoDocumento.FACTURAS.value, ("factura", "invoice")),
    (TipoDocumento.ALBARANES.value, ("albaran", "albaranes")),
    (TipoDocumento.LIQUIDACIONES.value, ("liquidacion", "abono", "descuento")),
    (TipoDocumento.STOCK.value, ("stock", "inventario", "existencias")),
]


@dataclass
class ClienteFarmacia:
    cliente: str
    farmacia: str
    email: str = ""


@dataclass
class DocumentoRecibido:
    id_documento: str
    expediente_id: str
    nombre_original: str
    nombre_normalizado: str
    tipo_documental: str
    extension: str
    tamano_bytes: int
    hash_archivo: str
    fecha_recepcion: str
    origen: str
    estado_documento: str = EstadoDocumento.RECIBIDO.value
    ruta_archivo: str = ""
    observaciones: str = ""
    fecha_eliminacion: str = ""
    motivo_eliminacion: str = ""
    reemplaza_documento_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class ExpedienteDocumental:
    expediente_id: str
    cliente: str
    farmacia: str
    email_remitente: str
    tipo_servicio: str
    periodo: str
    ano: int
    fecha_recepcion: str
    estado: str
    documentos_recibidos: List[str] = field(default_factory=list)
    documentos_faltantes: List[str] = field(default_factory=list)
    observaciones: str = ""
    ruta_almacenamiento: str = ""
    fecha_ultima_actualizacion: str = ""
    dedupe_key: str = ""
    perfil_documental: str = PerfilDocumental.GENERICO.value

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalizar_texto(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    sin_acentos = "".join(ch for ch in normalizado if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", sin_acentos.lower().strip())


def slugify(texto: str, default: str = "archivo") -> str:
    base = normalizar_texto(texto)
    base = re.sub(r"[^a-z0-9._-]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("._-")
    return base or default


def clasificar_documento(nombre_archivo: str) -> str:
    nombre = normalizar_texto(Path(nombre_archivo).stem)
    for tipo, tokens in PATRONES_DOCUMENTOS:
        if any(token in nombre for token in tokens):
            return tipo
    return TipoDocumento.OTROS.value


def extension_valida(nombre_archivo: str) -> bool:
    return Path(nombre_archivo).suffix.lower() in EXTENSIONES_ADMITIDAS


def calcular_sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def checklist_para_servicio(tipo_servicio: str) -> List[str]:
    return list(CHECKLISTS_OBLIGATORIOS.get(tipo_servicio, []))


def parsear_asunto(asunto: str) -> Dict[str, object]:
    asunto = (asunto or "").strip()
    farmacia_patron = r"FARMACIA_[A-Z0-9_]+"

    asesoria = re.fullmatch(rf"ASESORIA_([1-4]T)_(20\d{{2}})_({farmacia_patron})", asunto, re.I)
    if asesoria:
        periodo, ano, farmacia = asesoria.groups()
        return {
            "tipo_servicio": "ASESORIA",
            "periodo": periodo.upper(),
            "ano": int(ano),
            "farmacia": farmacia.replace("FARMACIA_", "").replace("_", " ").title(),
        }

    auditoria = re.fullmatch(rf"AUDITORIA_INICIAL_(20\d{{2}})_({farmacia_patron})", asunto, re.I)
    if auditoria:
        ano, farmacia = auditoria.groups()
        return {
            "tipo_servicio": "AUDITORIA_INICIAL",
            "periodo": "INICIAL",
            "ano": int(ano),
            "farmacia": farmacia.replace("FARMACIA_", "").replace("_", " ").title(),
        }

    revision = re.fullmatch(rf"REVISION_FACTURAS_([A-Z]+)_(20\d{{2}})_({farmacia_patron})", asunto, re.I)
    if revision:
        periodo, ano, farmacia = revision.groups()
        return {
            "tipo_servicio": "REVISION_FACTURAS",
            "periodo": periodo.upper(),
            "ano": int(ano),
            "farmacia": farmacia.replace("FARMACIA_", "").replace("_", " ").title(),
        }

    raise ValueError(
        "Asunto no valido. Usa formatos como ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL, "
        "AUDITORIA_INICIAL_2026_FARMACIA_SAN_MIGUEL o "
        "REVISION_FACTURAS_MAYO_2026_FARMACIA_SAN_MIGUEL."
    )


def crear_expediente(asunto: str, cliente: str, email_remitente: str = "") -> ExpedienteDocumental:
    datos = parsear_asunto(asunto)
    farmacia_slug = slugify(str(datos["farmacia"]))
    dedupe_key = "|".join(
        [
            farmacia_slug,
            str(datos["tipo_servicio"]),
            str(datos["periodo"]),
            str(datos["ano"]),
        ]
    )
    expediente_id = f"EXP-{farmacia_slug}-{datos['tipo_servicio']}-{datos['periodo']}-{datos['ano']}".upper()
    checklist = checklist_para_servicio(str(datos["tipo_servicio"]))
    ahora = ahora_iso()
    return ExpedienteDocumental(
        expediente_id=expediente_id,
        cliente=cliente or str(datos["farmacia"]),
        farmacia=str(datos["farmacia"]),
        email_remitente=email_remitente,
        tipo_servicio=str(datos["tipo_servicio"]),
        periodo=str(datos["periodo"]),
        ano=int(datos["ano"]),
        fecha_recepcion=ahora,
        estado=EstadoExpediente.PENDIENTE_DOCUMENTACION.value,
        documentos_recibidos=[],
        documentos_faltantes=checklist,
        fecha_ultima_actualizacion=ahora,
        dedupe_key=dedupe_key,
    )


def tipos_recibidos_y_faltantes(tipo_servicio: str, documentos: Iterable[Dict[str, object]]) -> Tuple[List[str], List[str]]:
    obligatorios = checklist_para_servicio(tipo_servicio)
    recibidos = sorted(
        {
            str(doc.get("tipo_documental", ""))
            for doc in documentos
            if doc.get("estado_documento") == EstadoDocumento.RECIBIDO.value
            and str(doc.get("tipo_documental", "")) in obligatorios
        }
    )
    faltantes = [tipo for tipo in obligatorios if tipo not in recibidos]
    return recibidos, faltantes
