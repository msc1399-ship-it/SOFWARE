from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from modules import bandeja_documental as bd
from modules.bandeja_documental_repository import BandejaDocumentalRepository


class EstadoPreanalisis(str, Enum):
    PREANALISIS_PENDIENTE = "PREANALISIS_PENDIENTE"
    PREANALISIS_COMPLETADO = "PREANALISIS_COMPLETADO"
    PREANALISIS_WARNING = "PREANALISIS_WARNING"
    PREANALISIS_ERROR = "PREANALISIS_ERROR"


@dataclass
class ResultadoPreanalisisDocumento:
    expediente_id: str
    documento_id: str
    nombre_archivo: str
    ruta_archivo: str
    extension: str
    tamano_bytes: int
    hash_archivo: str
    tipo_documental_esperado: str
    tipo_documental_detectado: str = ""
    confianza_tipo: float = 0.0
    proveedor_detectado: str = "Otros"
    confianza_proveedor: float = 0.0
    formato_detectado: str = ""
    encoding_detectado: str = ""
    hojas_detectadas: List[str] = field(default_factory=list)
    columnas_detectadas: List[str] = field(default_factory=list)
    numero_filas: int = 0
    numero_columnas: int = 0
    pdf_paginas: int = 0
    pdf_texto_extraible: bool = False
    zip_archivos_internos: List[str] = field(default_factory=list)
    errores_detectados: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    valido_para_analisis: bool = False
    resumen: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class ResultadoPreanalisisExpediente:
    expediente_id: str
    fecha_ejecucion: str
    estado_preanalisis: str
    documentos_ok: int
    documentos_warning: int
    documentos_error: int
    proveedores_detectados: List[str]
    warnings_globales: List[str]
    errores_globales: List[str]
    valido_global: bool
    resultados_documentos: List[ResultadoPreanalisisDocumento]

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["resultados_documentos"] = [doc.to_dict() for doc in self.resultados_documentos]
        return data


PROVEEDOR_KEYWORDS = {
    "Bidafarma": ["bidafarma", "bida", "bitransfer"],
    "Cofares": ["cofares"],
    "Alliance": ["alliance", "alliance healthcare"],
    "Hefame": ["hefame"],
    "Fedefarma": ["fedefarma"],
}

TIPO_KEYWORDS = {
    bd.TipoDocumento.VENTAS.value: ["venta", "ventas", "ticket", "tickets", "tpv", "caja", "importe venta"],
    bd.TipoDocumento.COMPRAS.value: ["compra", "compras", "pedido", "distribuidor", "coste", "precio compra"],
    bd.TipoDocumento.FACTURAS.value: ["factura", "invoice", "base imponible", "iva", "nif", "total factura"],
    bd.TipoDocumento.ALBARANES.value: ["albaran", "albaranes", "albarán", "codigo nacional", "c.n.", "unidades servidas"],
    bd.TipoDocumento.LIQUIDACIONES.value: ["liquidacion", "liquidación", "abono", "descuento", "transfer", "goteo"],
    bd.TipoDocumento.STOCK.value: ["stock", "inventario", "existencias", "caducidad", "lote"],
}

COLUMN_HINTS = {
    bd.TipoDocumento.VENTAS.value: ["fecha", "ticket", "venta", "pvp", "importe", "cantidad", "unidades"],
    bd.TipoDocumento.COMPRAS.value: ["pedido", "proveedor", "coste", "compra", "descuento", "cantidad"],
    bd.TipoDocumento.FACTURAS.value: ["factura", "nif", "base", "iva", "total", "vencimiento"],
    bd.TipoDocumento.ALBARANES.value: ["albaran", "albarán", "cn", "codigo", "nacional", "unidades", "servidas"],
    bd.TipoDocumento.LIQUIDACIONES.value: ["liquidacion", "abono", "descuento", "transfer", "goteo", "cargo"],
    bd.TipoDocumento.STOCK.value: ["stock", "inventario", "existencias", "lote", "caducidad"],
}


def ejecutar_preanalisis_expediente(
    expediente_id: str,
    repo: Optional[BandejaDocumentalRepository] = None,
) -> ResultadoPreanalisisExpediente:
    repo = repo or BandejaDocumentalRepository()
    expediente = repo.get_expediente(expediente_id)
    if not expediente:
        raise ValueError(f"Expediente no encontrado: {expediente_id}")
    if expediente["estado"] != bd.EstadoExpediente.LISTO_ANALISIS.value:
        raise ValueError("Solo se permite preanalisis si el expediente esta Listo para analisis.")

    documentos = [
        doc for doc in repo.list_documentos(expediente_id, include_deleted=False)
        if doc.get("estado_documento") == bd.EstadoDocumento.RECIBIDO.value
    ]
    resultados = [_preanalizar_documento(expediente_id, doc) for doc in documentos]
    warnings_globales = _warnings_globales(resultados)
    errores_globales = []
    if not documentos:
        errores_globales.append("El expediente no tiene documentos recibidos validos.")

    documentos_error = sum(1 for doc in resultados if doc.errores_detectados)
    documentos_warning = sum(1 for doc in resultados if doc.warnings and not doc.errores_detectados)
    documentos_ok = sum(1 for doc in resultados if doc.valido_para_analisis and not doc.warnings)
    proveedores = sorted({doc.proveedor_detectado for doc in resultados if doc.proveedor_detectado and doc.proveedor_detectado != "Otros"})
    valido_global = not documentos_error and not errores_globales

    if documentos_error or errores_globales:
        estado = EstadoPreanalisis.PREANALISIS_ERROR.value
    elif documentos_warning or warnings_globales:
        estado = EstadoPreanalisis.PREANALISIS_WARNING.value
    else:
        estado = EstadoPreanalisis.PREANALISIS_COMPLETADO.value

    resultado = ResultadoPreanalisisExpediente(
        expediente_id=expediente_id,
        fecha_ejecucion=bd.ahora_iso(),
        estado_preanalisis=estado,
        documentos_ok=documentos_ok,
        documentos_warning=documentos_warning,
        documentos_error=documentos_error,
        proveedores_detectados=proveedores,
        warnings_globales=warnings_globales,
        errores_globales=errores_globales,
        valido_global=valido_global,
        resultados_documentos=resultados,
    )
    repo.save_preanalisis_expediente(resultado.to_dict())
    for doc in resultados:
        repo.save_preanalisis_documento(doc.to_dict(), resultado.fecha_ejecucion, estado)
    repo.add_evento(expediente_id, "preanalisis_documental", f"Estado: {estado}", "preanalisis")
    return resultado


def _preanalizar_documento(expediente_id: str, doc: Dict[str, object]) -> ResultadoPreanalisisDocumento:
    resultado = ResultadoPreanalisisDocumento(
        expediente_id=expediente_id,
        documento_id=str(doc["id_documento"]),
        nombre_archivo=str(doc["nombre_original"]),
        ruta_archivo=str(doc.get("ruta_archivo", "")),
        extension=str(doc.get("extension", "")).lower(),
        tamano_bytes=int(doc.get("tamano_bytes", 0) or 0),
        hash_archivo=str(doc.get("hash_archivo", "")),
        tipo_documental_esperado=str(doc.get("tipo_documental", "")),
    )
    path = Path(resultado.ruta_archivo)
    if not path.exists() or not path.is_file():
        resultado.errores_detectados.append("Archivo fisico no encontrado.")
        resultado.resumen = "No se pudo abrir el archivo desde filesystem."
        return resultado

    try:
        if resultado.extension in {".xlsx", ".xls"}:
            _analizar_excel(path, resultado)
        elif resultado.extension == ".csv":
            _analizar_csv(path, resultado)
        elif resultado.extension == ".pdf":
            _analizar_pdf(path, resultado)
        elif resultado.extension == ".zip":
            _analizar_zip(path, resultado)
        else:
            resultado.errores_detectados.append(f"Extension no soportada en preanalisis: {resultado.extension}")
    except Exception as exc:
        resultado.errores_detectados.append(f"Error abriendo documento: {exc}")

    _detectar_tipo_y_proveedor(resultado)
    if resultado.tipo_documental_detectado and resultado.tipo_documental_esperado != resultado.tipo_documental_detectado:
        resultado.warnings.append(
            f"Tipo esperado ({resultado.tipo_documental_esperado}) distinto del detectado ({resultado.tipo_documental_detectado})."
        )
    if not resultado.tipo_documental_detectado or resultado.confianza_tipo < 0.45:
        resultado.warnings.append("Tipo documental ambiguo o baja confianza.")
    resultado.valido_para_analisis = not resultado.errores_detectados
    resultado.resumen = _resumen_documento(resultado)
    return resultado


def _analizar_excel(path: Path, resultado: ResultadoPreanalisisDocumento) -> None:
    resultado.formato_detectado = "excel"
    with pd.ExcelFile(path) as excel:
        resultado.hojas_detectadas = list(excel.sheet_names)
        if not resultado.hojas_detectadas:
            resultado.errores_detectados.append("Excel sin hojas detectables.")
            return

        total_filas = 0
        columnas = []
        for sheet in resultado.hojas_detectadas:
            df = pd.read_excel(excel, sheet_name=sheet)
            total_filas += int(len(df.index))
            columnas.extend([str(col) for col in df.columns if not str(col).startswith("Unnamed")])
    resultado.columnas_detectadas = _dedupe_preserve(columnas)
    resultado.numero_filas = total_filas
    resultado.numero_columnas = len(resultado.columnas_detectadas)
    if total_filas == 0:
        resultado.errores_detectados.append("Excel vacio.")
    if not resultado.columnas_detectadas:
        resultado.errores_detectados.append("No se detectan columnas utiles.")
    if total_filas and not resultado.errores_detectados:
        resultado.warnings.extend(_warnings_columnas(resultado.columnas_detectadas))


def _analizar_csv(path: Path, resultado: ResultadoPreanalisisDocumento) -> None:
    resultado.formato_detectado = "csv"
    encoding, sample = _detectar_encoding(path)
    resultado.encoding_detectado = encoding
    delimitador = _detectar_delimitador(sample)
    resultado.formato_detectado = f"csv({delimitador})"
    df = pd.read_csv(path, encoding=encoding, sep=delimitador)
    resultado.columnas_detectadas = [str(col) for col in df.columns]
    resultado.numero_filas = int(len(df.index))
    resultado.numero_columnas = int(len(df.columns))
    if resultado.numero_filas == 0:
        resultado.errores_detectados.append("CSV vacio.")
    if not resultado.columnas_detectadas:
        resultado.errores_detectados.append("No se detectan columnas utiles.")
    resultado.warnings.extend(_warnings_columnas(resultado.columnas_detectadas))


def _analizar_pdf(path: Path, resultado: ResultadoPreanalisisDocumento) -> None:
    resultado.formato_detectado = "pdf"
    contenido = path.read_bytes()
    resultado.pdf_paginas = max(1, len(re.findall(rb"/Type\s*/Page\b", contenido)))
    texto = _extraer_texto_pdf_basico(contenido)
    resultado.pdf_texto_extraible = len(texto.strip()) >= 20
    if not resultado.pdf_texto_extraible:
        resultado.warnings.append("PDF sin texto extraible; podria ser escaneado.")
    resultado.columnas_detectadas = []
    resultado.numero_filas = 0
    resultado.numero_columnas = 0
    resultado.hojas_detectadas = []
    resultado.warnings.extend(_warnings_pdf_texto(texto))
    resultado._texto_detectado = texto  # type: ignore[attr-defined]


def _analizar_zip(path: Path, resultado: ResultadoPreanalisisDocumento) -> None:
    resultado.formato_detectado = "zip"
    if not zipfile.is_zipfile(path):
        resultado.errores_detectados.append("ZIP corrupto o no legible.")
        return
    with zipfile.ZipFile(path) as zf:
        corrupto = zf.testzip()
        if corrupto:
            resultado.errores_detectados.append(f"ZIP corrupto en archivo interno: {corrupto}")
        resultado.zip_archivos_internos = [name for name in zf.namelist() if not name.endswith("/")]
    if not resultado.zip_archivos_internos:
        resultado.warnings.append("ZIP sin archivos internos utiles.")
    nombres = [Path(name).name.lower() for name in resultado.zip_archivos_internos]
    repetidos = sorted({name for name in nombres if nombres.count(name) > 1})
    if repetidos:
        resultado.warnings.append("ZIP con documentos internos repetidos: " + ", ".join(repetidos))


def _detectar_tipo_y_proveedor(resultado: ResultadoPreanalisisDocumento) -> None:
    texto = _texto_evidencia(resultado)
    scores_tipo = _score_keywords(texto, TIPO_KEYWORDS)
    if scores_tipo:
        tipo, score = max(scores_tipo.items(), key=lambda item: item[1])
        resultado.tipo_documental_detectado = tipo
        resultado.confianza_tipo = min(1.0, score / 6)
    else:
        tipo_nombre = bd.clasificar_documento(resultado.nombre_archivo)
        resultado.tipo_documental_detectado = tipo_nombre
        resultado.confianza_tipo = 0.25 if tipo_nombre != bd.TipoDocumento.OTROS.value else 0.0

    scores_proveedor = _score_keywords(texto, PROVEEDOR_KEYWORDS)
    if scores_proveedor:
        proveedor, score = max(scores_proveedor.items(), key=lambda item: item[1])
        resultado.proveedor_detectado = proveedor
        resultado.confianza_proveedor = min(1.0, score / 3)
    else:
        resultado.proveedor_detectado = "Otros"
        resultado.confianza_proveedor = 0.0


def _texto_evidencia(resultado: ResultadoPreanalisisDocumento) -> str:
    partes = [
        resultado.nombre_archivo,
        " ".join(resultado.columnas_detectadas),
        " ".join(resultado.hojas_detectadas),
        " ".join(resultado.zip_archivos_internos),
    ]
    texto_pdf = getattr(resultado, "_texto_detectado", "")
    if texto_pdf:
        partes.append(str(texto_pdf))
    return bd.normalizar_texto(" ".join(partes))


def _score_keywords(texto: str, keyword_map: Dict[str, List[str]]) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    for etiqueta, keywords in keyword_map.items():
        score = 0
        for keyword in keywords:
            if bd.normalizar_texto(keyword) in texto:
                score += 2 if " " in keyword else 1
        if score:
            scores[etiqueta] = score
    return scores


def _detectar_encoding(path: Path) -> Tuple[str, str]:
    raw = path.read_bytes()[:8192]
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return encoding, raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "latin-1", raw.decode("latin-1", errors="replace")


def _detectar_delimitador(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        return ";"


def _extraer_texto_pdf_basico(contenido: bytes) -> str:
    texto = contenido.decode("latin-1", errors="ignore")
    textos_parentesis = re.findall(r"\(([^)]{2,})\)", texto)
    if textos_parentesis:
        return " ".join(textos_parentesis)
    limpio = re.sub(r"[^A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ _.,;:/-]+", " ", texto)
    return re.sub(r"\s+", " ", limpio)


def _warnings_columnas(columnas: List[str]) -> List[str]:
    warnings = []
    columnas_norm = [bd.normalizar_texto(col) for col in columnas]
    if len(columnas_norm) < 2:
        warnings.append("Muy pocas columnas detectadas.")
    unnamed = [col for col in columnas_norm if col.startswith("unnamed")]
    if unnamed:
        warnings.append("Columnas sin cabecera clara detectadas.")
    if not any(any(token in col for token in ("fecha", "factura", "ticket", "albar", "codigo", "cn", "importe", "stock")) for col in columnas_norm):
        warnings.append("Columnas poco reconocibles para documentos de farmacia.")
    return warnings


def _warnings_pdf_texto(texto: str) -> List[str]:
    texto_norm = bd.normalizar_texto(texto)
    if not texto_norm:
        return []
    keywords = ["bidafarma", "cofares", "alliance", "factura", "albaran", "liquidacion", "transfer", "goteo"]
    if not any(keyword in texto_norm for keyword in keywords):
        return ["PDF legible, pero sin palabras clave documentales reconocidas."]
    return []


def _warnings_globales(resultados: List[ResultadoPreanalisisDocumento]) -> List[str]:
    warnings = []
    pares = [(doc.tipo_documental_detectado, doc.proveedor_detectado) for doc in resultados if doc.tipo_documental_detectado]
    repetidos = sorted({f"{tipo}/{proveedor}" for tipo, proveedor in pares if pares.count((tipo, proveedor)) > 1})
    if repetidos:
        warnings.append("Posible duplicado logico por tipo/proveedor: " + ", ".join(repetidos))
    ambiguos = [doc.nombre_archivo for doc in resultados if doc.confianza_tipo < 0.45]
    if ambiguos:
        warnings.append("Documentos con tipo ambiguo: " + ", ".join(ambiguos))
    return warnings


def _resumen_documento(resultado: ResultadoPreanalisisDocumento) -> str:
    if resultado.errores_detectados:
        return "Error documental: " + "; ".join(resultado.errores_detectados)
    partes = [
        f"{resultado.formato_detectado or resultado.extension}",
        f"tipo={resultado.tipo_documental_detectado or 'no detectado'}",
        f"proveedor={resultado.proveedor_detectado}",
    ]
    if resultado.numero_columnas:
        partes.append(f"{resultado.numero_filas} filas / {resultado.numero_columnas} columnas")
    if resultado.pdf_paginas:
        partes.append(f"{resultado.pdf_paginas} paginas PDF")
    if resultado.zip_archivos_internos:
        partes.append(f"{len(resultado.zip_archivos_internos)} archivos ZIP")
    if resultado.warnings:
        partes.append(f"{len(resultado.warnings)} warnings")
    return " | ".join(partes)


def _dedupe_preserve(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
