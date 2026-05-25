from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def main() -> None:
    tmp = TemporaryDirectory()
    base = Path(tmp.name)
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    service = BandejaDocumentalService(
        repo=repo,
        storage_root=str(base / "documentos"),
        export_root=str(base / "exportaciones"),
    )
    expediente_id = service.crear_expediente_desde_asunto(
        "ASESORIA_3T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Hardening",
        "cliente@example.com",
    )

    def subir() -> dict:
        return service.registrar_subida_manual(expediente_id, [("compras.xlsx", b"mismo contenido")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultados = list(pool.map(lambda _: subir(), range(2)))

    registrados = sum(len(r["registrados"]) for r in resultados)
    duplicados = sum(len(r["duplicados"]) for r in resultados)
    assert registrados == 1
    assert duplicados == 1

    doc_id = repo.list_documentos(expediente_id)[0]["id_documento"]
    service.soft_delete_documento(expediente_id, doc_id, "prueba soft delete")
    diagnostico = service.diagnostico_integridad()
    export_zip = service.exportar_expediente_zip(expediente_id)

    assert export_zip.exists()
    assert "documentos_db_sin_archivo" in diagnostico
    print("ok hardening smoke", expediente_id)
    tmp.cleanup()


if __name__ == "__main__":
    main()
