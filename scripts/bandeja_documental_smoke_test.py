from pathlib import Path
from tempfile import TemporaryDirectory

from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def main() -> None:
    tmp = TemporaryDirectory()
    base = Path(tmp.name)
    db_path = str(base / "bandeja.db")
    repo = BandejaDocumentalRepository(db_path)
    service = BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))

    expediente_id = service.crear_expediente_desde_asunto(
        "ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Smoke",
        "cliente@example.com",
    )
    resultado = service.registrar_subida_manual(
        expediente_id,
        [
            ("compras_distribuidor.xlsx", b"compras"),
            ("ventas_mayo.csv", b"ventas"),
            ("factura_laboratorio.pdf", b"factura"),
            ("albaran_1.pdf", b"albaran"),
            ("liquidacion.pdf", b"liquidacion"),
        ],
    )
    duplicado = service.registrar_subida_manual(expediente_id, [("compras_copia.xlsx", b"compras")])
    for error in repo.list_errores(expediente_id, solo_pendientes=True):
        repo.marcar_error_resuelto(error["id_error"])
    ok, motivos = service.marcar_listo_para_analisis(expediente_id)
    payload = service.preparar_payload_para_analisis(expediente_id)

    assert resultado["registrados"]
    assert duplicado["duplicados"]
    assert ok, motivos
    assert payload["expediente_id"] == expediente_id
    print("ok smoke", expediente_id, Path(db_path).exists())
    tmp.cleanup()


if __name__ == "__main__":
    main()
