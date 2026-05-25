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
    perfil_documental: str = bd.PerfilDocumental.GENERICO.value
    subtipo_documental: str = ""
    pdf_compuesto: bool = False
    contiene_factura: bool = False
    contiene_albaranes: bool = False
    paginas_factura: List[int] = field(default_factory=list)
    paginas_albaranes: List[int] = field(default_factory=list)
    numero_factura: str = ""
    fechas_detectadas: List[str] = field(default_factory=list)
    periodo_detectado: str = ""
    albaranes_detectados_count: int = 0
    numero_albaranes_detectados: int = 0
    tipos_albaran_detectados: List[str] = field(default_factory=list)
    posibles_liquidaciones_detectadas: List[str] = field(default_factory=list)
    texto_extraido_resumido: str = ""
    clasificacion_paginas: List[Dict[str, object]] = field(default_factory=list)
    contiene_tipo_74: bool = False
    contiene_zv_zacofarva: bool = False
    posible_liquidacion_embebida: bool = False
    subtipo_bidafarma: str = ""

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
    perfil_documental: str = bd.PerfilDocumental.GENERICO.value
    documentos_faltantes_perfil: List[str] = field(default_factory=list)

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

SUBTIPO_BIDAFARMA_NORMAL_GOTEO = "BIDAFARMA_NORMAL_GOTEO"
SUBTIPO_BIDAFARMA_TRANSFER = "BIDAFARMA_TRANSFER"
SUBTIPO_BIDAFARMA_MIXTO = "BIDAFARMA_MIXTO"
SUBTIPO_BIDAFARMA_OTROS = "BIDAFARMA_OTROS"


def ejecutar_preanalisis_expediente(
    expediente_id: str,
    repo: Optional[BandejaDocumentalRepository] = None,
) -> ResultadoPreanalisisExpediente:
    repo = repo or BandejaDocumentalRepository()
    expediente = repo.get_expediente(expediente_id)
    if not expediente:
        raise ValueError(f"Expediente no encontrado: {expediente_id}")

    documentos = [
        doc for doc in repo.list_documentos(expediente_id, include_deleted=False)
        if doc.get("estado_documento") == bd.EstadoDocumento.RECIBIDO.value
    ]
    perfil = str(expediente.get("perfil_documental", bd.PerfilDocumental.GENERICO.value))
    if perfil == bd.PerfilDocumental.GENERICO.value:
        perfil = _detectar_perfil_expediente(documentos)
        repo.update_expediente_fields(expediente_id, perfil_documental=perfil)
    resultados = [_preanalizar_documento(expediente_id, doc, perfil) for doc in documentos]
    faltantes_perfil, warnings_perfil, errores_perfil = _evaluar_perfil_documental(perfil, resultados, expediente)
    warnings_globales = _warnings_globales(resultados) + warnings_perfil
    errores_globales = []
    if not documentos:
        errores_globales.append("El expediente no tiene documentos recibidos validos.")
    errores_globales.extend(errores_perfil)

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
        perfil_documental=perfil,
        documentos_faltantes_perfil=faltantes_perfil,
    )
    repo.save_preanalisis_expediente(resultado.to_dict())
    for doc in resultados:
        repo.save_preanalisis_documento(doc.to_dict(), resultado.fecha_ejecucion, estado)
    repo.add_evento(expediente_id, "preanalisis_documental", f"Estado: {estado}", "preanalisis")
    return resultado


def _preanalizar_documento(expediente_id: str, doc: Dict[str, object], perfil: str) -> ResultadoPreanalisisDocumento:
    resultado = ResultadoPreanalisisDocumento(
        expediente_id=expediente_id,
        documento_id=str(doc["id_documento"]),
        nombre_archivo=str(doc["nombre_original"]),
        ruta_archivo=str(doc.get("ruta_archivo", "")),
        extension=str(doc.get("extension", "")).lower(),
        tamano_bytes=int(doc.get("tamano_bytes", 0) or 0),
        hash_archivo=str(doc.get("hash_archivo", "")),
        tipo_documental_esperado=str(doc.get("tipo_documental", "")),
        perfil_documental=perfil,
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
    if not contenido.lstrip().startswith(b"%PDF"):
        resultado.errores_detectados.append("PDF ilegible o corrupto: cabecera PDF no detectada.")
        return
    resultado.pdf_paginas = max(1, len(re.findall(rb"/Type\s*/Page\b", contenido)))
    paginas_texto = _extraer_paginas_pdf_basico(contenido)
    texto = "\f".join(paginas_texto) if paginas_texto else ""
    resultado.pdf_texto_extraible = len(texto.strip()) >= 20
    if not resultado.pdf_texto_extraible:
        resultado.warnings.append("PDF sin texto extraible; podria ser escaneado.")
    resultado.columnas_detectadas = []
    resultado.numero_filas = 0
    resultado.numero_columnas = 0
    resultado.hojas_detectadas = []
    resultado.warnings.extend(_warnings_pdf_texto(texto))
    resultado._texto_detectado = texto  # type: ignore[attr-defined]
    resultado._paginas_texto_detectadas = paginas_texto  # type: ignore[attr-defined]
    resultado.texto_extraido_resumido = _resumir_texto(texto)
    preanalizar_pdf_bidafarma(resultado, texto)
    _analizar_pdf_laboratorio(resultado, texto)


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
        evidencia_interna = " ".join(resultado.zip_archivos_internos)
        for name in resultado.zip_archivos_internos:
            suf = Path(name).suffix.lower()
            if suf in {".xlsx", ".xls", ".csv", ".pdf"}:
                resultado.warnings.append(f"ZIP contiene documento interno procesable: {name}")
    if not resultado.zip_archivos_internos:
        resultado.warnings.append("ZIP sin archivos internos utiles.")
    nombres = [Path(name).name.lower() for name in resultado.zip_archivos_internos]
    repetidos = sorted({name for name in nombres if nombres.count(name) > 1})
    if repetidos:
        resultado.warnings.append("ZIP con documentos internos repetidos: " + ", ".join(repetidos))


def _detectar_tipo_y_proveedor(resultado: ResultadoPreanalisisDocumento) -> None:
    texto = _texto_evidencia(resultado)
    if (
        resultado.perfil_documental == bd.PerfilDocumental.BIDAFARMA.value
        and resultado.contiene_factura
        and resultado.contiene_albaranes
    ):
        resultado.tipo_documental_detectado = bd.TipoDocumento.FACTURAS.value
        resultado.confianza_tipo = max(resultado.confianza_tipo, 0.9)
        resultado.proveedor_detectado = "Bidafarma"
        resultado.confianza_proveedor = max(resultado.confianza_proveedor, 0.95)
        return
    scores_tipo = _score_keywords(texto, TIPO_KEYWORDS)
    if scores_tipo:
        tipo, score = max(scores_tipo.items(), key=lambda item: item[1])
        confianza = min(1.0, score / 6)
        if confianza >= resultado.confianza_tipo:
            resultado.tipo_documental_detectado = tipo
            resultado.confianza_tipo = confianza
    else:
        tipo_nombre = bd.clasificar_documento(resultado.nombre_archivo)
        resultado.tipo_documental_detectado = tipo_nombre
        resultado.confianza_tipo = 0.25 if tipo_nombre != bd.TipoDocumento.OTROS.value else 0.0

    scores_proveedor = _score_keywords(texto, PROVEEDOR_KEYWORDS)
    if scores_proveedor:
        proveedor, score = max(scores_proveedor.items(), key=lambda item: item[1])
        confianza = min(1.0, score / 3)
        if confianza >= resultado.confianza_proveedor:
            resultado.proveedor_detectado = proveedor
            resultado.confianza_proveedor = confianza
    else:
        resultado.proveedor_detectado = "Otros"
        resultado.confianza_proveedor = 0.0

    if resultado.perfil_documental == bd.PerfilDocumental.BIDAFARMA.value and resultado.proveedor_detectado == "Otros":
        evidencia = _texto_evidencia(resultado)
        if any(token in evidencia for token in ("vida pharma", "bidafarma", "bitransfer", "zacofarva", "zv")):
            resultado.proveedor_detectado = "Bidafarma"
            resultado.confianza_proveedor = max(resultado.confianza_proveedor, 0.65)


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


def _detectar_perfil_expediente(documentos: List[Dict[str, object]]) -> str:
    texto = bd.normalizar_texto(" ".join(str(doc.get("nombre_original", "")) for doc in documentos))
    if any(token in texto for token in ("bidafarma", "vida pharma", "bitransfer", "goteo", "zacofarva")):
        return bd.PerfilDocumental.BIDAFARMA.value
    if "cofares" in texto:
        return bd.PerfilDocumental.COFARES.value
    if "alliance" in texto:
        return bd.PerfilDocumental.ALLIANCE.value
    if "hefame" in texto:
        return bd.PerfilDocumental.HEFAME.value
    if "laboratorio" in texto or "laboratorios" in texto:
        return bd.PerfilDocumental.LABORATORIOS.value
    return bd.PerfilDocumental.GENERICO.value


def _evaluar_perfil_documental(
    perfil: str,
    resultados: List[ResultadoPreanalisisDocumento],
    expediente: Dict[str, object],
) -> Tuple[List[str], List[str], List[str]]:
    faltantes: List[str] = []
    warnings: List[str] = []
    errores: List[str] = []

    if perfil == bd.PerfilDocumental.BIDAFARMA.value:
        tiene_ventas = _bloque_ventas_ok(resultados)
        tiene_stock = _bloque_stock_ok(resultados)
        pdfs_bidafarma = [
            doc for doc in resultados
            if doc.extension == ".pdf" and doc.proveedor_detectado == "Bidafarma" and not doc.errores_detectados
        ]
        tiene_compras = any(doc.contiene_factura for doc in pdfs_bidafarma) or _bloque_compras_ok(resultados)
        tiene_albaranes_embebidos = any(doc.contiene_albaranes for doc in pdfs_bidafarma)
        especifico = bd.es_analisis_especifico_proveedor(str(expediente.get("tipo_servicio", "")))
        if not tiene_ventas:
            if not especifico:
                faltantes.append(bd.BloqueDocumental.VENTAS.value)
        if not tiene_stock:
            if not especifico:
                faltantes.append(bd.BloqueDocumental.STOCK.value)
        if not tiene_compras:
            faltantes.append(bd.BloqueDocumental.COMPRAS_PROVEEDOR.value)
        if not tiene_albaranes_embebidos:
            warnings.append(
                "El PDF parece factura Bidafarma, pero no se han detectado albaranes embebidos. Revisar manualmente."
            )
        return faltantes, warnings, errores

    if perfil == bd.PerfilDocumental.LABORATORIOS.value:
        pdfs = [doc for doc in resultados if doc.extension == ".pdf"]
        if not pdfs:
            faltantes.append("PDF facturas laboratorio")
        for doc in pdfs:
            if not doc.pdf_texto_extraible:
                warnings.append(f"{doc.nombre_archivo}: PDF de laboratorio sin texto extraible; no bloquea en esta fase.")
        return faltantes, warnings, errores

    especifico = bd.es_analisis_especifico_proveedor(str(expediente.get("tipo_servicio", "")))
    tiene_compras = _bloque_compras_ok(resultados)
    tiene_ventas = _bloque_ventas_ok(resultados)
    tiene_stock = _bloque_stock_ok(resultados)
    if not tiene_compras:
        faltantes.append(bd.BloqueDocumental.COMPRAS_PROVEEDOR.value)
    if not especifico and not tiene_ventas:
        faltantes.append(bd.BloqueDocumental.VENTAS.value)
    if not especifico and not tiene_stock:
        faltantes.append(bd.BloqueDocumental.STOCK.value)
    if especifico:
        warnings.append("Este expediente se tratara como analisis especifico de proveedor. No se exigira ventas ni stock.")
    return faltantes, warnings, errores


def _bloque_compras_ok(resultados: List[ResultadoPreanalisisDocumento]) -> bool:
    return any(
        (
            doc.tipo_documental_detectado in {bd.TipoDocumento.COMPRAS.value, bd.TipoDocumento.FACTURAS.value, bd.TipoDocumento.ALBARANES.value}
            or (
                doc.extension == ".zip"
                and any(token in bd.normalizar_texto(" ".join(doc.zip_archivos_internos)) for token in ("compra", "compras", "factura", "bidafarma", "albaran"))
            )
        )
        and not doc.errores_detectados
        for doc in resultados
    )


def _bloque_ventas_ok(resultados: List[ResultadoPreanalisisDocumento]) -> bool:
    return any(
        doc.extension in {".xlsx", ".xls", ".csv"}
        and (
            doc.tipo_documental_detectado == bd.TipoDocumento.VENTAS.value
            or _columnas_contienen(doc, ("venta", "ticket", "pvp", "precio venta", "importe"))
        )
        and not doc.errores_detectados
        for doc in resultados
    )


def _bloque_stock_ok(resultados: List[ResultadoPreanalisisDocumento]) -> bool:
    return any(
        doc.extension in {".xlsx", ".xls", ".csv"}
        and (
            doc.tipo_documental_detectado == bd.TipoDocumento.STOCK.value
            or _columnas_contienen(doc, ("stock", "inventario", "existencias", "unidades stock"))
        )
        and not doc.errores_detectados
        for doc in resultados
    )


def _columnas_contienen(doc: ResultadoPreanalisisDocumento, tokens: Tuple[str, ...]) -> bool:
    texto = bd.normalizar_texto(" ".join(doc.columnas_detectadas))
    return any(token in texto for token in tokens)


def preanalizar_pdf_bidafarma(resultado: ResultadoPreanalisisDocumento, texto: str) -> None:
    evidencia = bd.normalizar_texto(f"{resultado.nombre_archivo} {texto}")
    if not any(token in evidencia for token in ("bidafarma", "vida pharma", "bitransfer", "goteo", "zacofarva", "zv")):
        return

    paginas = getattr(resultado, "_paginas_texto_detectadas", None) or _paginas_texto_pdf(texto, max(1, resultado.pdf_paginas))
    clasificacion = [_clasificar_pagina_bidafarma(idx, pagina) for idx, pagina in enumerate(paginas, start=1)]
    resultado.clasificacion_paginas = clasificacion
    resultado.pdf_compuesto = True
    resultado.contiene_factura = any(item["clasificacion"] == "FACTURA" for item in clasificacion)
    resultado.contiene_albaranes = any(item["clasificacion"] == "ALBARAN" for item in clasificacion)
    resultado.contiene_tipo_74 = "tipo 74" in evidencia or "tp 74" in evidencia
    resultado.contiene_zv_zacofarva = any(token in evidencia for token in ("zacofarva", "zv"))
    resultado.posible_liquidacion_embebida = any(
        bool(item.get("posible_liquidacion")) for item in clasificacion
    )
    resultado.paginas_factura = [
        int(item["pagina"]) for item in clasificacion if item["clasificacion"] == "FACTURA"
    ]
    resultado.paginas_albaranes = [
        int(item["pagina"]) for item in clasificacion if item["clasificacion"] == "ALBARAN"
    ]
    if resultado.paginas_albaranes:
        primera_albaran = min(resultado.paginas_albaranes)
        resultado.paginas_factura = [idx for idx in resultado.paginas_factura if idx < primera_albaran] or list(range(1, primera_albaran))
    if not resultado.paginas_factura and resultado.contiene_factura:
        resultado.paginas_factura = [1]
    if not resultado.paginas_albaranes and resultado.contiene_albaranes:
        resultado.paginas_albaranes = [idx for idx in range(2, resultado.pdf_paginas + 1)] or [1]

    albaranes = re.findall(
        r"(?:albar[aá]n|alb\.?|pedido)\s*(?:n[ºo.]*)?\s*([A-Z0-9/-]{2,})",
        evidencia,
        flags=re.I,
    )
    resultado.albaranes_detectados_count = _count_distinct(albaranes)
    resultado.numero_albaranes_detectados = resultado.albaranes_detectados_count
    resultado.tipos_albaran_detectados = _dedupe_preserve(
        match.upper() for match in re.findall(r"(?:tipo|tp)\s*([0-9]{2})", evidencia, flags=re.I)
    )
    resultado.numero_factura = _first_match(
        evidencia,
        [
            r"factura\s*(?:n[ºo.]*)?\s*([A-Z0-9/-]{3,})",
            r"n[ºo.]*\s*factura\s*([A-Z0-9/-]{3,})",
        ],
    )
    resultado.fechas_detectadas = _dedupe_preserve(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", evidencia))
    resultado.periodo_detectado = _detectar_periodo(resultado.fechas_detectadas, evidencia)
    resultado.posibles_liquidaciones_detectadas = _dedupe_preserve(
        token for token in ("tipo 74", "abono", "regularizacion", "regularización", "cargo", "liquidacion", "liquidación")
        if token in evidencia
    )

    hay_transfer = any(token in evidencia for token in ("transfer", "bitransfer"))
    hay_goteo = "goteo" in evidencia
    if hay_transfer and hay_goteo:
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_MIXTO
    elif hay_transfer:
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_TRANSFER
    elif any(token in evidencia for token in ("goteo", "factura", "albaran", "albarán")):
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_NORMAL_GOTEO
    else:
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_OTROS
    resultado.subtipo_bidafarma = resultado.subtipo_documental

    if resultado.contiene_factura and resultado.contiene_albaranes:
        resultado.tipo_documental_detectado = bd.TipoDocumento.FACTURAS.value
        resultado.confianza_tipo = max(resultado.confianza_tipo, 0.9)
        resultado.proveedor_detectado = "Bidafarma"
        resultado.confianza_proveedor = max(resultado.confianza_proveedor, 0.95)
    elif resultado.contiene_factura:
        resultado.warnings.append(
            "Warning suave: PDF Bidafarma legible con factura, pero sin patrones claros de albaranes embebidos. Revisar manualmente."
        )


def _clasificar_pagina_bidafarma(pagina: int, texto: str) -> Dict[str, object]:
    texto_norm = bd.normalizar_texto(texto)
    factura_fuertes = ("base imponible", "iva", "total factura")
    factura_kw = ("factura", "n factura", "nº factura", "base imponible", "iva", "total factura", "vencimiento", "forma de pago")
    albaran_kw = ("albaran", "albaranes", "albarán", "n albaran", "nº albaran", "pedido", "codigo nacional", "código nacional", "unidades", "pva", "descuento", "zacofarva", "zv", "goteo", "transfer", "bitransfer")
    liquidacion_kw = ("abono", "liquidacion", "liquidación", "regularizacion", "regularización", "cargo", "tipo 74")

    score_factura = sum(1 for token in factura_kw if token in texto_norm)
    score_albaran = sum(1 for token in albaran_kw if token in texto_norm)
    score_liquidacion = sum(1 for token in liquidacion_kw if token in texto_norm)
    tiene_factura_fuerte = any(token in texto_norm for token in factura_fuertes)
    posible_liquidacion = score_liquidacion > 0

    tiene_marca_albaran = any(token in texto_norm for token in ("albaran", "albarán", "pedido", "codigo nacional", "código nacional"))
    if score_factura and tiene_factura_fuerte:
        clasificacion = "FACTURA"
    elif score_factura and "factura" in texto_norm and not tiene_marca_albaran:
        clasificacion = "FACTURA"
    elif score_albaran:
        clasificacion = "ALBARAN"
    elif score_liquidacion:
        clasificacion = "LIQUIDACION_ABONO"
    elif score_factura:
        clasificacion = "FACTURA"
    elif any(token in texto_norm for token in ("resumen", "total", "periodo")):
        clasificacion = "RESUMEN"
    else:
        clasificacion = "DESCONOCIDA"

    if clasificacion == "ALBARAN" and posible_liquidacion:
        liquidacion_pura = False
    else:
        liquidacion_pura = clasificacion == "LIQUIDACION_ABONO"

    return {
        "pagina": pagina,
        "clasificacion": clasificacion,
        "score_factura": score_factura,
        "score_albaran": score_albaran,
        "score_liquidacion": score_liquidacion,
        "posible_liquidacion": posible_liquidacion,
        "liquidacion_pura": liquidacion_pura,
        "texto_resumido": _resumir_texto(texto, limite=260),
    }




def _analizar_pdf_bidafarma(resultado: ResultadoPreanalisisDocumento, texto: str) -> None:
    evidencia = bd.normalizar_texto(f"{resultado.nombre_archivo} {texto}")
    if not any(token in evidencia for token in ("bidafarma", "vida pharma", "bitransfer", "goteo", "zacofarva", "zv")):
        return

    paginas = _paginas_texto_pdf(texto, max(1, resultado.pdf_paginas))
    resultado.pdf_compuesto = True
    resultado.contiene_factura = "factura" in evidencia
    resultado.contiene_albaranes = any(token in evidencia for token in ("albaran", "albaranes", "albarán"))
    resultado.paginas_factura = [
        idx for idx, pagina in enumerate(paginas, start=1)
        if "factura" in bd.normalizar_texto(pagina)
    ]
    resultado.paginas_albaranes = [
        idx for idx, pagina in enumerate(paginas, start=1)
        if any(token in bd.normalizar_texto(pagina) for token in ("albaran", "albaranes", "albarán"))
    ]
    if not resultado.paginas_factura and resultado.contiene_factura:
        resultado.paginas_factura = [1]
    if not resultado.paginas_albaranes and resultado.contiene_albaranes:
        resultado.paginas_albaranes = [idx for idx in range(2, resultado.pdf_paginas + 1)] or [1]
    resultado.albaranes_detectados_count = _count_distinct(
        re.findall(r"(?:albar[aá]n|alb\.?)\s*(?:n[ºo.]*)?\s*([A-Z0-9/-]{4,})", evidencia, flags=re.I)
    )
    resultado.numero_factura = _first_match(
        evidencia,
        [
            r"factura\s*(?:n[ºo.]*)?\s*([A-Z0-9/-]{4,})",
            r"n[ºo.]*\s*factura\s*([A-Z0-9/-]{4,})",
        ],
    )
    resultado.fechas_detectadas = _dedupe_preserve(
        re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", evidencia)
    )
    resultado.posibles_liquidaciones_detectadas = _dedupe_preserve(
        token for token in ("tipo 74", "abono", "cargo", "liquidacion", "liquidación")
        if token in evidencia
    )
    if any(token in evidencia for token in ("transfer", "bitransfer")):
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_TRANSFER
    elif any(token in evidencia for token in ("goteo", "factura", "albaran", "albarán")):
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_NORMAL_GOTEO
    else:
        resultado.subtipo_documental = SUBTIPO_BIDAFARMA_OTROS
    if resultado.contiene_factura and resultado.contiene_albaranes:
        resultado.tipo_documental_detectado = bd.TipoDocumento.FACTURAS.value
        resultado.confianza_tipo = max(resultado.confianza_tipo, 0.85)
        resultado.proveedor_detectado = "Bidafarma"
        resultado.confianza_proveedor = max(resultado.confianza_proveedor, 0.9)
    elif resultado.contiene_factura:
        resultado.warnings.append(
            "El PDF parece factura Bidafarma, pero no se han detectado albaranes embebidos. Revisar manualmente."
        )


def _analizar_pdf_laboratorio(resultado: ResultadoPreanalisisDocumento, texto: str) -> None:
    if resultado.perfil_documental != bd.PerfilDocumental.LABORATORIOS.value:
        return
    if resultado.extension != ".pdf":
        return
    resultado.tipo_documental_detectado = bd.TipoDocumento.FACTURAS.value
    resultado.confianza_tipo = max(resultado.confianza_tipo, 0.5)
    if not resultado.pdf_texto_extraible:
        resultado.warnings.append("Factura de laboratorio escaneada o sin texto extraible; aceptada sin bloqueo.")


def _paginas_texto_pdf(texto: str, pdf_paginas: int) -> List[str]:
    if "\f" in texto:
        paginas = texto.split("\f")
    elif "%%PAGE%%" in texto:
        paginas = texto.split("%%PAGE%%")
    else:
        paginas = [texto]
    if len(paginas) < pdf_paginas:
        paginas.extend([""] * (pdf_paginas - len(paginas)))
    return paginas[:pdf_paginas]


def _first_match(texto: str, patterns: List[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, texto, flags=re.I)
        if match:
            return match.group(1)
    return ""


def _count_distinct(items: List[str]) -> int:
    return len({item for item in items if item})


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
    return ""


def _extraer_paginas_pdf_basico(contenido: bytes) -> List[str]:
    texto = contenido.decode("latin-1", errors="ignore")
    textos_parentesis = re.findall(r"\(([^)]{1,})\)", texto)
    if not textos_parentesis:
        return []
    combinado = "\f".join(textos_parentesis)
    paginas = _paginas_texto_pdf(combinado, max(1, combinado.count("\f") + 1))
    return [pagina.strip() for pagina in paginas if pagina.strip()]


def _resumir_texto(texto: str, limite: int = 700) -> str:
    limpio = re.sub(r"\s+", " ", texto).strip()
    return limpio[:limite]


def _detectar_periodo(fechas: List[str], evidencia: str) -> str:
    trimestre = re.search(r"\b([1-4]t)\b", evidencia, flags=re.I)
    if trimestre:
        return trimestre.group(1).upper()
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    for mes in meses:
        if mes in evidencia:
            return mes.upper()
    if fechas:
        partes = re.split(r"[/-]", fechas[0])
        if len(partes) >= 2:
            return f"MES_{partes[1].zfill(2)}"
    return ""


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
