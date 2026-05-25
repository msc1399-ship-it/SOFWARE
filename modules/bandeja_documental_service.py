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

        for nombre_original, contenido in archivos:
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
            permite_multiples = perfil == bd.PerfilDocumental.BIDAFARMA.value or extension == ".pdf"
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
        recibidos, faltantes = self._recibidos_faltantes_por_perfil(expediente, documentos)
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
        self.repo.add_evento(expediente_id, "checklist_recalculado", f"Estado: {estado}. Faltantes: {', '.join(faltantes) or 'ninguno'}", usuario)
        logger.info("documental.checklist_recalculado", extra={"expediente_id": expediente_id, "estado": estado})
        return estado

    def validar_listo_para_analisis(self, expediente_id: str) -> Tuple[bool, List[str]]:
        expediente = self.repo.get_expediente(expediente_id)
        if not expediente:
            return False, ["Expediente no encontrado"]
        documentos = self.repo.list_documentos(expediente_id, include_deleted=False)
        _, faltantes = self._recibidos_faltantes_por_perfil(expediente, documentos)
        motivos = []
        if faltantes:
            motivos.append("Faltan documentos obligatorios: " + ", ".join(faltantes))
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
        perfil = str(expediente.get("perfil_documental", bd.PerfilDocumental.GENERICO.value))
        activos = [
            doc for doc in documentos
            if doc.get("estado_documento") == bd.EstadoDocumento.RECIBIDO.value
        ]
        if perfil == bd.PerfilDocumental.BIDAFARMA.value:
            tiene_ventas = any(
                doc.get("extension") in {".xlsx", ".xls", ".csv"}
                and (
                    doc.get("tipo_documental") == bd.TipoDocumento.VENTAS.value
                    or "venta" in bd.normalizar_texto(str(doc.get("nombre_original", "")))
                )
                for doc in activos
            )
            tiene_pdf_bidafarma = any(
                doc.get("extension") == ".pdf"
                and (
                    doc.get("tipo_documental") in {
                        bd.TipoDocumento.FACTURAS.value,
                        bd.TipoDocumento.ALBARANES.value,
                        bd.TipoDocumento.LIQUIDACIONES.value,
                        bd.TipoDocumento.OTROS.value,
                    }
                )
                and any(
                    token in bd.normalizar_texto(str(doc.get("nombre_original", "")))
                    for token in ("bidafarma", "vida", "pharma", "goteo", "transfer", "bitransfer", "factura", "albaran")
                )
                for doc in activos
            )
            recibidos = []
            faltantes = []
            if tiene_ventas:
                recibidos.append("Ventas Excel")
            else:
                faltantes.append("Ventas Excel")
            if tiene_pdf_bidafarma:
                recibidos.append("PDF Bidafarma con factura/albaranes embebidos")
            else:
                faltantes.append("PDF Bidafarma compuesto")
            return recibidos, faltantes

        if perfil == bd.PerfilDocumental.LABORATORIOS.value:
            tiene_pdf = any(doc.get("extension") == ".pdf" for doc in activos)
            return (["PDF facturas laboratorio"] if tiene_pdf else [], [] if tiene_pdf else ["PDF facturas laboratorio"])

        return bd.tipos_recibidos_y_faltantes(str(expediente["tipo_servicio"]), documentos)

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
