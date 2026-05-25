from __future__ import annotations

import json
import logging
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from modules import bandeja_documental as bd
from modules.bandeja_documental_repository import BandejaDocumentalRepository

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class BandejaDocumentalService:
    _locks: Dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(
        self,
        repo: Optional[BandejaDocumentalRepository] = None,
        storage_root: str = "data/documentos",
        export_root: str = "data/exportaciones",
    ) -> None:
        self.repo = repo or BandejaDocumentalRepository()
        self.storage_root = Path(storage_root)
        self.export_root = Path(export_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)

    def _lock_for(self, expediente_id: str) -> threading.Lock:
        with self._locks_guard:
            if expediente_id not in self._locks:
                self._locks[expediente_id] = threading.Lock()
            return self._locks[expediente_id]

    def crear_expediente_desde_asunto(self, asunto: str, cliente: str, email_remitente: str = "", origen: str = "manual") -> str:
        try:
            expediente = bd.crear_expediente(asunto, cliente, email_remitente)
        except ValueError as exc:
            self.repo.add_error_ingestion(
                origen=origen,
                asunto=asunto,
                email_remitente=email_remitente,
                motivo_error="asunto_invalido",
                detalle=str(exc),
            )
            logger.warning("documental.asunto_invalido", extra={"asunto": asunto, "origen": origen})
            raise

        expediente_id = self.repo.upsert_expediente(expediente.to_dict())
        self.repo.add_evento(expediente_id, "expediente_creado_o_actualizado", f"Asunto: {asunto}", origen)
        logger.info("documental.expediente_upsert", extra={"expediente_id": expediente_id, "origen": origen})
        return expediente_id

    def registrar_subida_manual(
        self,
        expediente_id: str,
        archivos: List[Tuple[str, bytes]],
        origen: str = "manual",
        usuario: str = "usuario",
    ) -> Dict[str, object]:
        with self._lock_for(expediente_id):
            return self._registrar_archivos(expediente_id, archivos, origen=origen, usuario=usuario)

    def _registrar_archivos(
        self,
        expediente_id: str,
        archivos: List[Tuple[str, bytes]],
        origen: str,
        usuario: str,
    ) -> Dict[str, object]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            raise ValueError(f"Expediente no encontrado: {expediente_id}")

        registrados: List[str] = []
        duplicados: List[str] = []
        errores: List[str] = []

        archivos_expandidos = self._expandir_zips(archivos)
        for nombre_original, contenido in archivos_expandidos:
            extension = Path(nombre_original).suffix.lower()
            if not bd.extension_valida(nombre_original):
                detalle = f"Extension no admitida: {extension or 'sin extension'}"
                self.repo.add_error_ingestion(
                    origen=origen,
                    nombre_archivo=nombre_original,
                    motivo_error="documento_extension_invalida",
                    detalle=detalle,
                    expediente_id_relacionado=expediente_id,
                )
                self.repo.add_evento(expediente_id, "documento_incorrecto", f"{nombre_original}: {detalle}", usuario)
                errores.append(nombre_original)
                logger.warning("documental.documento_extension_invalida", extra={"expediente_id": expediente_id, "archivo": nombre_original})
                continue

            hash_archivo = bd.calcular_sha256(contenido)
            duplicado = self.repo.get_documento_by_hash(expediente_id, hash_archivo)
            if duplicado:
                self.repo.add_error_ingestion(
                    origen=origen,
                    nombre_archivo=nombre_original,
                    motivo_error="documento_duplicado",
                    detalle=f"Mismo hash que {duplicado.get('nombre_original')}",
                    expediente_id_relacionado=expediente_id,
                )
                self.repo.add_evento(expediente_id, "documento_duplicado", f"{nombre_original} duplicado por hash", usuario)
                duplicados.append(nombre_original)
                logger.info("documental.documento_duplicado", extra={"expediente_id": expediente_id, "hash": hash_archivo})
                continue

            tipo = bd.clasificar_documento(nombre_original)
            reemplaza = ""
            perfil = str(expediente.get("perfil_documental", bd.PerfilDocumental.GENERICO.value))
            permite_multiples = (
                perfil == bd.PerfilDocumental.BIDAFARMA.value
                or extension in {".pdf", ".zip"}
                or "::" in nombre_original
            )
            anterior = None if permite_multiples else self.repo.get_documento_activo_por_tipo(expediente_id, tipo)
            if anterior:
                reemplaza = str(anterior["id_documento"])
                self.repo.update_documento_estado(
                    reemplaza,
                    bd.EstadoDocumento.REEMPLAZADO.value,
                    observaciones=f"Reemplazado por nuevo documento {nombre_original}",
                )
                self.repo.add_evento(expediente_id, "documento_reemplazado", f"{anterior['nombre_original']} -> {nombre_original}", usuario)

            ruta_destino, nombre_normalizado = self._ruta_destino(expediente, tipo, nombre_original, hash_archivo)
            ruta_destino.parent.mkdir(parents=True, exist_ok=True)
            ruta_destino.write_bytes(contenido)

            documento = bd.DocumentoRecibido(
                id_documento=str(uuid.uuid4()),
                expediente_id=expediente_id,
                nombre_original=nombre_original,
                nombre_normalizado=nombre_normalizado,
                tipo_documental=tipo,
                extension=extension,
                tamano_bytes=len(contenido),
                hash_archivo=hash_archivo,
                fecha_recepcion=bd.ahora_iso(),
                origen=origen,
                ruta_archivo=str(ruta_destino),
                reemplaza_documento_id=reemplaza,
            )
            self.repo.save_documento(documento.to_dict())
            self.repo.add_evento(expediente_id, "documento_anadido", f"{nombre_original} como {tipo}", usuario)
            registrados.append(nombre_original)
            logger.info("documental.documento_guardado", extra={"expediente_id": expediente_id, "archivo": nombre_original, "tipo": tipo})

        estado = self.recalcular_checklist_y_estado(expediente_id, usuario=usuario)
        return {
            "registrados": registrados,
            "duplicados": duplicados,
            "errores": errores,
            "estado_final": estado,
        }

    def _expandir_zips(self, archivos: List[Tuple[str, bytes]]) -> List[Tuple[str, bytes]]:
        expandidos: List[Tuple[str, bytes]] = []
        for nombre_original, contenido in archivos:
            extension = Path(nombre_original).suffix.lower()
            if extension != ".zip":
                expandidos.append((nombre_original, contenido))
                continue
            expandidos.append((nombre_original, contenido))
            try:
                import io

                with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        interno = Path(info.filename)
                        if interno.suffix.lower() not in bd.EXTENSIONES_ADMITIDAS or interno.suffix.lower() == ".zip":
                            continue
                        expandidos.append((f"{Path(nombre_original).stem}::{interno.name}", zf.read(info)))
            except zipfile.BadZipFile:
                logger.warning("documental.zip_corrupto", extra={"archivo": nombre_original})
        return expandidos

    def _ruta_destino(self, expediente: Dict[str, object], tipo: str, nombre_original: str, hash_archivo: str) -> Tuple[Path, str]:
        farmacia_slug = bd.slugify(str(expediente["farmacia"]))
        servicio_periodo = bd.slugify(f"{expediente['tipo_servicio']}_{expediente['periodo']}")
        carpeta_tipo = bd.CARPETAS_TIPO_DOCUMENTO.get(tipo, bd.CARPETAS_TIPO_DOCUMENTO[bd.TipoDocumento.OTROS.value])
        destino_dir = self.storage_root / farmacia_slug / str(expediente["ano"]) / servicio_periodo / carpeta_tipo
        stem = bd.slugify(Path(nombre_original).stem, "documento")
        suffix = Path(nombre_original).suffix.lower()
        nombre = f"{stem}{suffix}"
        destino = destino_dir / nombre
        if destino.exists():
            nombre = f"{stem}_{hash_archivo[:8]}{suffix}"
            destino = destino_dir / nombre
        contador = 1
        while destino.exists():
            nombre = f"{stem}_{hash_archivo[:8]}_{contador}{suffix}"
            destino = destino_dir / nombre
            contador += 1
        return destino, nombre

    def recalcular_checklist_y_estado(self, expediente_id: str, usuario: str = "sistema") -> str:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            raise ValueError(f"Expediente no encontrado: {expediente_id}")
        documentos = self.repo.list_documentos(expediente_id, include_deleted=False)
        evaluacion = self.evaluar_bloques_documentales(expediente_id)
        recibidos = evaluacion["bloques_recibidos"]
        faltantes = evaluacion["bloques_faltantes"]
        incorrectos = [
            doc for doc in documentos
            if doc.get("estado_documento") == bd.EstadoDocumento.INCORRECTO.value
        ]
        errores_pendientes = self.repo.count_errores_pendientes(expediente_id)
        activos = [
            doc for doc in documentos
            if doc.get("estado_documento") in {bd.EstadoDocumento.RECIBIDO.value, bd.EstadoDocumento.INCORRECTO.value}
        ]

        if not activos:
            estado = bd.EstadoExpediente.PENDIENTE_DOCUMENTACION.value
        elif faltantes:
            estado = bd.EstadoExpediente.DOCUMENTACION_INCOMPLETA.value
        elif incorrectos or errores_pendientes:
            estado = bd.EstadoExpediente.PENDIENTE_REVISION.value
        else:
            estado = bd.EstadoExpediente.DOCUMENTACION_RECIBIDA.value

        self.repo.update_expediente_fields(
            expediente_id,
            estado=estado,
            documentos_recibidos=recibidos,
            documentos_faltantes=faltantes,
        )
        self.repo.replace_bloques_expediente(expediente_id, evaluacion.get("bloques_detalle", {}))
        self.repo.add_evento(expediente_id, "checklist_recalculado", f"Estado: {estado}. Faltantes: {', '.join(faltantes) or 'ninguno'}", usuario)
        logger.info("documental.checklist_recalculado", extra={"expediente_id": expediente_id, "estado": estado})
        return estado

    def validar_listo_para_analisis(self, expediente_id: str) -> Tuple[bool, List[str]]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            return False, ["Expediente no encontrado"]
        documentos = self.repo.list_documentos(expediente_id, include_deleted=False)
        evaluacion = self.evaluar_bloques_documentales(expediente_id)
        faltantes = evaluacion["bloques_faltantes"]
        motivos = []
        if faltantes:
            motivos.append("Faltan bloques documentales minimos: " + ", ".join(faltantes))
        if self.repo.count_errores_pendientes(expediente_id):
            motivos.append("Hay errores de ingestion pendientes")
        incorrectos = [
            doc["nombre_original"]
            for doc in documentos
            if doc.get("estado_documento") == bd.EstadoDocumento.INCORRECTO.value
        ]
        if incorrectos:
            motivos.append("Hay documentos incorrectos activos: " + ", ".join(incorrectos))
        return not motivos, motivos

    def _recibidos_faltantes_por_perfil(
        self,
        expediente: Dict[str, object],
        documentos: List[Dict[str, object]],
    ) -> Tuple[List[str], List[str]]:
        evaluacion = self._evaluar_bloques_desde_documentos(expediente, documentos)
        return evaluacion["bloques_recibidos"], evaluacion["bloques_faltantes"]

    def evaluar_bloques_documentales(self, expediente_id: str) -> Dict[str, object]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            raise ValueError(f"Expediente no encontrado: {expediente_id}")
        documentos = self.repo.list_documentos(expediente_id, include_deleted=False)
        return self._evaluar_bloques_desde_documentos(expediente, documentos)

    def _evaluar_bloques_desde_documentos(
        self,
        expediente: Dict[str, object],
        documentos: List[Dict[str, object]],
    ) -> Dict[str, object]:
        perfil = str(expediente.get("perfil_documental", bd.PerfilDocumental.GENERICO.value))
        tipo_servicio = str(expediente.get("tipo_servicio", ""))
        especifico_proveedor = bd.es_analisis_especifico_proveedor(tipo_servicio)
        activos = [
            doc for doc in documentos
            if doc.get("estado_documento") == bd.EstadoDocumento.RECIBIDO.value
        ]
        preanalisis_docs = self._preanalisis_activo(expediente.get("expediente_id"), activos)
        if tipo_servicio != "ASESORIA" and not especifico_proveedor and perfil != bd.PerfilDocumental.LABORATORIOS.value:
            recibidos, faltantes = bd.tipos_recibidos_y_faltantes(tipo_servicio, documentos)
            return {
                "perfil_documental": perfil,
                "analisis_especifico_proveedor": False,
                "bloques": {tipo: tipo in recibidos for tipo in bd.checklist_para_servicio(tipo_servicio)},
                "bloques_recibidos": recibidos,
                "bloques_faltantes": faltantes,
                "bloques_obligatorios": bd.checklist_para_servicio(tipo_servicio),
                "bloques_opcionales": bd.BLOQUES_OPCIONALES,
                "avisos": [],
                "bloques_detalle": {
                    tipo: self._detalle_bloque(tipo in recibidos, "Checklist documental generico.", "documentos", 0.5)
                    for tipo in bd.checklist_para_servicio(tipo_servicio)
                },
            }
        compra_pre = self._bloque_compras_desde_preanalisis(preanalisis_docs)
        ventas_pre = self._bloque_tabular_desde_preanalisis(preanalisis_docs, bd.TipoDocumento.VENTAS.value)
        stock_pre = self._bloque_tabular_desde_preanalisis(preanalisis_docs, bd.TipoDocumento.STOCK.value)
        compra_fallback = self._primer_detalle_documento(activos, lambda doc: self._doc_satisface_compras(doc, perfil), "Documento de compras detectado antes de preanalisis.")
        ventas_fallback = self._primer_detalle_documento(activos, self._doc_satisface_ventas, "Documento de ventas detectado antes de preanalisis.")
        stock_fallback = self._primer_detalle_documento(activos, self._doc_satisface_stock, "Documento de stock detectado antes de preanalisis.")
        compra_detalle = compra_pre if compra_pre["completo"] else compra_fallback
        ventas_detalle = ventas_pre if ventas_pre["completo"] else ventas_fallback
        stock_detalle = stock_pre if stock_pre["completo"] else stock_fallback
        tiene_compras = bool(compra_detalle["completo"])
        tiene_ventas = bool(ventas_detalle["completo"])
        tiene_stock = bool(stock_detalle["completo"])
        if perfil == bd.PerfilDocumental.LABORATORIOS.value:
            tiene_pdf = any(str(doc.get("extension", "")).lower() == ".pdf" for doc in activos)
            return {
                "perfil_documental": perfil,
                "analisis_especifico_proveedor": especifico_proveedor,
                "bloques": {bd.BloqueDocumental.FACTURAS_LABORATORIO.value: tiene_pdf},
                "bloques_recibidos": [bd.BloqueDocumental.FACTURAS_LABORATORIO.value] if tiene_pdf else [],
                "bloques_faltantes": [] if tiene_pdf else [bd.BloqueDocumental.FACTURAS_LABORATORIO.value],
                "bloques_obligatorios": [bd.BloqueDocumental.FACTURAS_LABORATORIO.value],
                "bloques_opcionales": [bd.BloqueDocumental.OTROS.value],
                "avisos": ["Perfil laboratorio: PDFs aceptados aunque sean escaneados; no bloquea OCR en esta fase."],
                "bloques_detalle": {
                    bd.BloqueDocumental.FACTURAS_LABORATORIO.value: self._detalle_bloque(
                        tiene_pdf,
                        "PDF de laboratorio recibido." if tiene_pdf else "",
                        "documentos",
                        0.5 if tiene_pdf else 0,
                    )
                },
            }
        opcionales = {
            bd.BloqueDocumental.ALBARANES_SEPARADOS.value: any(
                doc.get("tipo_documental") == bd.TipoDocumento.ALBARANES.value for doc in activos
            ),
            bd.BloqueDocumental.LIQUIDACIONES_SEPARADAS.value: any(
                doc.get("tipo_documental") == bd.TipoDocumento.LIQUIDACIONES.value for doc in activos
            ),
            bd.BloqueDocumental.FACTURAS_LABORATORIO.value: any(
                "laboratorio" in bd.normalizar_texto(str(doc.get("nombre_original", ""))) for doc in activos
            ),
        }
        bloques = {
            bd.BloqueDocumental.COMPRAS_PROVEEDOR.value: tiene_compras,
            bd.BloqueDocumental.VENTAS.value: tiene_ventas,
            bd.BloqueDocumental.STOCK.value: tiene_stock,
            **opcionales,
        }
        bloques_detalle = {
            bd.BloqueDocumental.COMPRAS_PROVEEDOR.value: compra_detalle,
            bd.BloqueDocumental.VENTAS.value: ventas_detalle,
            bd.BloqueDocumental.STOCK.value: stock_detalle,
            bd.BloqueDocumental.ALBARANES_SEPARADOS.value: self._detalle_bloque(
                bool(opcionales[bd.BloqueDocumental.ALBARANES_SEPARADOS.value]),
                "Albaranes separados recibidos.",
                "documentos",
                0.5,
            ),
            bd.BloqueDocumental.LIQUIDACIONES_SEPARADAS.value: self._detalle_bloque(
                bool(opcionales[bd.BloqueDocumental.LIQUIDACIONES_SEPARADAS.value]),
                "Liquidaciones/abonos separados recibidos.",
                "documentos",
                0.5,
            ),
            bd.BloqueDocumental.FACTURAS_LABORATORIO.value: self._detalle_bloque(
                bool(opcionales[bd.BloqueDocumental.FACTURAS_LABORATORIO.value]),
                "Factura de laboratorio detectada.",
                "documentos",
                0.5,
            ),
        }
        obligatorios = bd.bloques_minimos_para_servicio(tipo_servicio)
        faltantes = [bloque for bloque in obligatorios if not bloques.get(bloque)]
        recibidos = [bloque for bloque, ok in bloques.items() if ok]
        avisos = []
        if especifico_proveedor:
            avisos.append("Este expediente se tratara como analisis especifico de proveedor. No se exigira ventas ni stock.")
        return {
            "perfil_documental": perfil,
            "analisis_especifico_proveedor": especifico_proveedor,
            "bloques": bloques,
            "bloques_recibidos": recibidos,
            "bloques_faltantes": faltantes,
            "bloques_obligatorios": obligatorios,
            "bloques_opcionales": bd.BLOQUES_OPCIONALES,
            "avisos": avisos,
            "bloques_detalle": bloques_detalle,
        }

    def _preanalisis_activo(self, expediente_id: object, activos: List[Dict[str, object]]) -> List[Dict[str, object]]:
        if not expediente_id:
            return []
        ids_activos = {str(doc.get("id_documento")) for doc in activos}
        return [
            doc for doc in self.repo.list_preanalisis_documentos(str(expediente_id))
            if str(doc.get("documento_id")) in ids_activos
        ]

    def _detalle_bloque(self, completo: bool, razon: str, fuente: str, confianza: float) -> Dict[str, object]:
        return {
            "completo": bool(completo),
            "razon": razon if completo else "",
            "fuente": fuente if completo else "",
            "confianza": float(confianza if completo else 0),
        }

    def _primer_detalle_documento(self, documentos: List[Dict[str, object]], predicate, razon: str) -> Dict[str, object]:
        for doc in documentos:
            if predicate(doc):
                return self._detalle_bloque(True, f"{razon} Fuente: {doc.get('nombre_original', '')}", "documentos", 0.45)
        return self._detalle_bloque(False, "", "", 0)

    def _bloque_compras_desde_preanalisis(self, docs: List[Dict[str, object]]) -> Dict[str, object]:
        for doc in docs:
            if doc.get("errores_detectados"):
                continue
            proveedor = bd.normalizar_texto(str(doc.get("proveedor_detectado", "")))
            tipo = str(doc.get("tipo_documental_detectado", ""))
            pdf_compuesto = bool(doc.get("pdf_compuesto"))
            contiene_factura = bool(doc.get("contiene_factura"))
            if proveedor in {"bidafarma", "vida pharma"} and contiene_factura:
                return self._detalle_bloque(
                    True,
                    f"Bloque satisfecho mediante PDF Bidafarma compuesto detectado: {doc.get('nombre_archivo', '')}.",
                    "preanalisis",
                    max(float(doc.get("confianza_proveedor", 0) or 0), 0.9),
                )
            if tipo in {bd.TipoDocumento.COMPRAS.value, bd.TipoDocumento.FACTURAS.value, bd.TipoDocumento.ALBARANES.value}:
                return self._detalle_bloque(
                    True,
                    f"Bloque satisfecho por tipo documental detectado ({tipo}): {doc.get('nombre_archivo', '')}.",
                    "preanalisis",
                    float(doc.get("confianza_tipo", 0) or 0.5),
                )
            if pdf_compuesto and contiene_factura:
                return self._detalle_bloque(
                    True,
                    f"Bloque satisfecho mediante PDF compuesto valido: {doc.get('nombre_archivo', '')}.",
                    "preanalisis",
                    max(float(doc.get("confianza_tipo", 0) or 0), 0.7),
                )
            if str(doc.get("extension", "")).lower() == ".zip" and any(
                token in bd.normalizar_texto(" ".join(doc.get("zip_archivos_internos", [])))
                for token in ("compra", "compras", "factura", "bidafarma", "albaran")
            ):
                return self._detalle_bloque(
                    True,
                    f"Bloque satisfecho por ZIP con documentación de compras: {doc.get('nombre_archivo', '')}.",
                    "preanalisis",
                    0.6,
                )
        return self._detalle_bloque(False, "", "", 0)

    def _bloque_tabular_desde_preanalisis(self, docs: List[Dict[str, object]], tipo_esperado: str) -> Dict[str, object]:
        for doc in docs:
            extension = str(doc.get("extension", "")).lower()
            if extension not in {".xlsx", ".xls", ".csv"} or doc.get("errores_detectados"):
                continue
            tipo_detectado = str(doc.get("tipo_documental_detectado", ""))
            confianza = float(doc.get("confianza_tipo", 0) or 0)
            columnas = bd.normalizar_texto(" ".join(doc.get("columnas_detectadas", [])))
            if tipo_esperado == bd.TipoDocumento.VENTAS.value:
                estructura_score = sum(1 for token in ("fecha", "venta", "ticket", "pvp", "precio venta", "importe", "unidades") if token in columnas)
                estructura_ok = estructura_score >= 3
                bloque = bd.BloqueDocumental.VENTAS.value
            else:
                estructura_score = sum(1 for token in ("stock", "inventario", "existencias", "unidades stock", "coste medio", "pvp") if token in columnas)
                estructura_ok = estructura_score >= 2
                bloque = bd.BloqueDocumental.STOCK.value
            if estructura_ok and (tipo_detectado == tipo_esperado or confianza >= 0.45):
                return self._detalle_bloque(
                    True,
                    f"{bloque} validado por estructura tabular detectada: {doc.get('nombre_archivo', '')}.",
                    "preanalisis",
                    max(confianza, min(0.95, 0.35 + estructura_score * 0.1)),
                )
        return self._detalle_bloque(False, "", "", 0)

    def _doc_satisface_compras(self, doc: Dict[str, object], perfil: str) -> bool:
        nombre = bd.normalizar_texto(str(doc.get("nombre_original", "")))
        extension = str(doc.get("extension", "")).lower()
        tipo = doc.get("tipo_documental")
        if tipo == bd.TipoDocumento.COMPRAS.value:
            return True
        if extension in {".xlsx", ".xls", ".csv"} and any(token in nombre for token in ("compra", "compras", "pedido", "proveedor")):
            return True
        if extension == ".zip" and any(token in nombre for token in ("compra", "compras", "bidafarma", "proveedor", "factura", "albaran")):
            return True
        if extension == ".pdf" and (
            tipo in {bd.TipoDocumento.FACTURAS.value, bd.TipoDocumento.ALBARANES.value, bd.TipoDocumento.LIQUIDACIONES.value, bd.TipoDocumento.OTROS.value}
        ):
            if perfil == bd.PerfilDocumental.BIDAFARMA.value and any(
                token in nombre for token in ("bidafarma", "vida", "pharma", "goteo", "transfer", "bitransfer", "factura", "albaran")
            ):
                return True
            return any(token in nombre for token in ("factura", "compras", "proveedor", "distribuidor", "albaran"))
        return False

    def _doc_satisface_ventas(self, doc: Dict[str, object]) -> bool:
        nombre = bd.normalizar_texto(str(doc.get("nombre_original", "")))
        extension = str(doc.get("extension", "")).lower()
        return extension in {".xlsx", ".xls", ".csv"} and (
            doc.get("tipo_documental") == bd.TipoDocumento.VENTAS.value or "venta" in nombre or "ticket" in nombre
        )

    def _doc_satisface_stock(self, doc: Dict[str, object]) -> bool:
        nombre = bd.normalizar_texto(str(doc.get("nombre_original", "")))
        extension = str(doc.get("extension", "")).lower()
        return extension in {".xlsx", ".xls", ".csv"} and (
            doc.get("tipo_documental") == bd.TipoDocumento.STOCK.value
            or any(token in nombre for token in ("stock", "inventario", "existencias"))
        )

    def marcar_listo_para_analisis(self, expediente_id: str, usuario: str = "usuario") -> Tuple[bool, List[str]]:
        ok, motivos = self.validar_listo_para_analisis(expediente_id)
        if not ok:
            self.repo.add_evento(expediente_id, "intento_listo_fallido", " | ".join(motivos), usuario)
            return False, motivos
        self.repo.update_expediente_fields(expediente_id, estado=bd.EstadoExpediente.LISTO_ANALISIS.value)
        self.repo.add_evento(expediente_id, "marcado_listo_analisis", "Expediente validado por usuario", usuario)
        logger.info("documental.listo_analisis", extra={"expediente_id": expediente_id})
        return True, []

    def marcar_documento_incorrecto(self, expediente_id: str, id_documento: str, motivo: str = "Marcado manualmente") -> None:
        self.repo.update_documento_estado(id_documento, bd.EstadoDocumento.INCORRECTO.value, observaciones=motivo)
        self.repo.add_evento(expediente_id, "documento_marcado_incorrecto", f"{id_documento}: {motivo}", "usuario")
        self.recalcular_checklist_y_estado(expediente_id)

    def soft_delete_documento(self, expediente_id: str, id_documento: str, motivo: str = "") -> None:
        self.repo.update_documento_estado(
            id_documento,
            bd.EstadoDocumento.ELIMINADO.value,
            observaciones=motivo,
            fecha_eliminacion=bd.ahora_iso(),
            motivo_eliminacion=motivo,
        )
        self.repo.add_evento(expediente_id, "documento_eliminado", f"{id_documento}: {motivo}", "usuario")
        self.recalcular_checklist_y_estado(expediente_id)

    def preparar_payload_para_analisis(self, expediente_id: str) -> Dict[str, object]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            raise ValueError(f"Expediente no encontrado: {expediente_id}")
        documentos_por_tipo: Dict[str, List[Dict[str, object]]] = {}
        for doc in self.repo.list_documentos(expediente_id, include_deleted=False):
            if doc.get("estado_documento") != bd.EstadoDocumento.RECIBIDO.value:
                continue
            documentos_por_tipo.setdefault(str(doc["tipo_documental"]), []).append(
                {
                    "id_documento": doc["id_documento"],
                    "nombre_original": doc["nombre_original"],
                    "ruta_archivo": doc["ruta_archivo"],
                    "hash_archivo": doc["hash_archivo"],
                    "tamano_bytes": doc["tamano_bytes"],
                }
            )
        payload = {
            "expediente_id": expediente_id,
            "farmacia": expediente["farmacia"],
            "cliente": expediente["cliente"],
            "servicio": expediente["tipo_servicio"],
            "periodo": expediente["periodo"],
            "ano": expediente["ano"],
            "estado": expediente["estado"],
            "fecha_preparacion": bd.ahora_iso(),
            "documentos_por_tipo": documentos_por_tipo,
        }
        self.repo.add_evento(expediente_id, "preview_payload_analisis", "Preview generado sin ejecutar analisis", "usuario")
        return payload

    def diagnostico_integridad(self) -> Dict[str, object]:
        documentos = self.repo.list_documentos(include_deleted=False)
        db_paths = {str(Path(doc["ruta_archivo"]).resolve()) for doc in documentos if doc.get("ruta_archivo")}
        faltan_archivos = []
        hashes_inconsistentes = []
        for doc in documentos:
            ruta = doc.get("ruta_archivo")
            if not ruta:
                continue
            path = Path(str(ruta))
            if not path.exists():
                faltan_archivos.append(doc)
                continue
            if bd.calcular_sha256(path.read_bytes()) != doc.get("hash_archivo"):
                hashes_inconsistentes.append(doc)

        archivos_fisicos = {
            str(path.resolve())
            for path in self.storage_root.rglob("*")
            if path.is_file()
        }
        huerfanos = sorted(archivos_fisicos - db_paths)
        return {
            "documentos_db_sin_archivo": faltan_archivos,
            "archivos_fisicos_no_registrados": huerfanos,
            "hashes_inconsistentes": hashes_inconsistentes,
        }

    def exportar_expediente_json(self, expediente_id: str) -> Dict[str, object]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            raise ValueError(f"Expediente no encontrado: {expediente_id}")
        return {
            "expediente": expediente,
            "documentos": self.repo.list_documentos(expediente_id),
            "historial": self.repo.list_eventos(expediente_id),
            "errores": self.repo.list_errores(expediente_id),
            "payload_preview": self.preparar_payload_para_analisis(expediente_id),
        }

    def exportar_expediente_zip(self, expediente_id: str) -> Path:
        data = self.exportar_expediente_json(expediente_id)
        destino = self.export_root / f"{bd.slugify(expediente_id)}.zip"
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", json.dumps(data, ensure_ascii=False, indent=2))
            for doc in data["documentos"]:
                ruta = Path(str(doc.get("ruta_archivo", "")))
                if ruta.exists() and ruta.is_file():
                    zf.write(ruta, f"documentos/{doc['tipo_documental']}/{ruta.name}")
        return destino

    def storage_size_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.storage_root.rglob("*") if path.is_file())
