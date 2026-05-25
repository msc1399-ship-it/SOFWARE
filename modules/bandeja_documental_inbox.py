from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from modules import bandeja_documental as bd
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


@dataclass
class AdjuntoEntrante:
    nombre_archivo: str
    contenido_bytes: bytes
    content_type: str = ""
    tamano_bytes: int = 0
    attachment_id: str = ""

    def __post_init__(self) -> None:
        if not self.tamano_bytes:
            self.tamano_bytes = len(self.contenido_bytes)


@dataclass
class EmailEntrante:
    message_id: str
    thread_id: str
    asunto: str
    remitente_email: str
    remitente_nombre: str = ""
    fecha_recepcion: str = ""
    cuerpo_texto: str = ""
    adjuntos: List[AdjuntoEntrante] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.fecha_recepcion:
            self.fecha_recepcion = bd.ahora_iso()


@dataclass
class ResultadoIngestionEmail:
    ok: bool
    expediente_id: str
    estado_final: str
    documentos_registrados: List[str]
    duplicados_detectados: List[str]
    errores: List[str]
    observaciones: List[str]
    message_id: str
    email_duplicado: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def procesar_email_entrante(
    email: EmailEntrante,
    cliente_nombre: Optional[str] = None,
    service: Optional[BandejaDocumentalService] = None,
) -> ResultadoIngestionEmail:
    service = service or BandejaDocumentalService()
    repo: BandejaDocumentalRepository = service.repo

    procesado = repo.get_email_procesado(email.message_id)
    if procesado:
        expediente_id = str(procesado.get("expediente_id", ""))
        if expediente_id:
            repo.add_evento(expediente_id, "email_duplicado", f"Message-ID ya procesado: {email.message_id}", "email")
        return ResultadoIngestionEmail(
            ok=True,
            expediente_id=expediente_id,
            estado_final=str(procesado.get("estado", "")),
            documentos_registrados=[],
            duplicados_detectados=[],
            errores=[],
            observaciones=["Email duplicado, no reprocesado"],
            message_id=email.message_id,
            email_duplicado=True,
        )

    try:
        expediente_id = service.crear_expediente_desde_asunto(
            email.asunto,
            cliente_nombre or email.remitente_nombre or email.remitente_email,
            email_remitente=email.remitente_email,
            origen="email",
        )
    except ValueError as exc:
        resultado = ResultadoIngestionEmail(
            ok=False,
            expediente_id="",
            estado_final=bd.EstadoExpediente.ERROR_DOCUMENTAL.value,
            documentos_registrados=[],
            duplicados_detectados=[],
            errores=[str(exc)],
            observaciones=["Asunto invalido. No se crea expediente valido."],
            message_id=email.message_id,
        )
        repo.save_email_procesado(
            {
                "message_id": email.message_id,
                "thread_id": email.thread_id,
                "asunto": email.asunto,
                "remitente_email": email.remitente_email,
                "fecha_recepcion": email.fecha_recepcion,
                "estado": "error_asunto",
                "resultado": resultado.to_dict(),
            }
        )
        return resultado

    archivos = [(adj.nombre_archivo, adj.contenido_bytes) for adj in email.adjuntos]
    resultado_archivos = service.registrar_subida_manual(expediente_id, archivos, origen="email", usuario="email")
    estado_final = str(resultado_archivos["estado_final"])
    resultado = ResultadoIngestionEmail(
        ok=not bool(resultado_archivos["errores"]),
        expediente_id=expediente_id,
        estado_final=estado_final,
        documentos_registrados=list(resultado_archivos["registrados"]),
        duplicados_detectados=list(resultado_archivos["duplicados"]),
        errores=list(resultado_archivos["errores"]),
        observaciones=[f"thread_id={email.thread_id}"],
        message_id=email.message_id,
    )
    repo.add_evento(
        expediente_id,
        "email_procesado",
        f"message_id={email.message_id}; thread_id={email.thread_id}; resultado={json.dumps(resultado.to_dict(), ensure_ascii=False)}",
        "email",
    )
    repo.save_email_procesado(
        {
            "message_id": email.message_id,
            "thread_id": email.thread_id,
            "expediente_id": expediente_id,
            "asunto": email.asunto,
            "remitente_email": email.remitente_email,
            "fecha_recepcion": email.fecha_recepcion,
            "estado": estado_final,
            "resultado": resultado.to_dict(),
        }
    )
    return resultado
