from pathlib import Path
from tempfile import TemporaryDirectory

from modules.bandeja_documental_inbox import AdjuntoEntrante, EmailEntrante, procesar_email_entrante
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def main() -> None:
    tmp = TemporaryDirectory()
    base = Path(tmp.name)
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    service = BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))

    email = EmailEntrante(
        message_id="msg-smoke-1",
        thread_id="thread-smoke",
        asunto="ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL",
        remitente_email="cliente@example.com",
        remitente_nombre="Cliente Smoke",
        adjuntos=[
            AdjuntoEntrante("compras.xlsx", b"compras"),
            AdjuntoEntrante("ventas.csv", b"ventas"),
            AdjuntoEntrante("malware.exe", b"no"),
        ],
    )
    resultado = procesar_email_entrante(email, service=service)
    duplicado = procesar_email_entrante(email, service=service)
    invalido = procesar_email_entrante(
        EmailEntrante(
            message_id="msg-smoke-invalid",
            thread_id="thread-invalid",
            asunto="ASUNTO_INVALIDO",
            remitente_email="cliente@example.com",
        ),
        service=service,
    )

    assert resultado.expediente_id
    assert "malware.exe" in resultado.errores
    assert duplicado.email_duplicado
    assert not invalido.ok
    print("ok inbox smoke", resultado.expediente_id)
    tmp.cleanup()


if __name__ == "__main__":
    main()
