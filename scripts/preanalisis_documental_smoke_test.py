from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pandas as pd

from modules import preanalisis_documental
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Datos")
    return buffer.getvalue()


def _zip_bytes() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("bidafarma/albaran_001.csv", "albaran;codigo nacional;unidades\nA1;123456;2\n")
        zf.writestr("bidafarma/factura_001.pdf", _pdf_bytes("Factura Bidafarma IVA base imponible"))
    return buffer.getvalue()


def _pdf_bytes(texto: str) -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(texto) + 20} >> stream\nBT ({texto}) Tj ET\nendstream endobj\n".encode("latin-1")
        + b"trailer << /Root 1 0 R >>\n%%EOF"
    )


def main() -> None:
    tmp = TemporaryDirectory(ignore_cleanup_errors=True)
    base = Path(tmp.name)
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    service = BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))

    expediente_id = service.crear_expediente_desde_asunto(
        "ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Preanalisis",
        "cliente@example.com",
    )
    service.registrar_subida_manual(
        expediente_id,
        [
            (
                "compras_bidafarma.xlsx",
                _xlsx_bytes(pd.DataFrame({"proveedor": ["Bidafarma"], "compra": [10], "coste": [5.3]})),
            ),
            (
                "ventas_2T_2026.xlsx",
                _xlsx_bytes(pd.DataFrame({"albaran": ["A1"], "codigo nacional": ["123456"], "unidades servidas": [2]})),
            ),
            (
                "factura_laboratorio.pdf",
                _pdf_bytes("Factura laboratorio NIF IVA base imponible total factura"),
            ),
            ("albaran_corrupto.zip", b"esto no es un zip"),
            ("liquidacion_transfer.pdf", _pdf_bytes("Liquidacion Bidafarma transfer goteo descuento")),
            ("stock_periodo.xlsx", _xlsx_bytes(pd.DataFrame({"codigo nacional": ["123"], "unidades stock": [4], "pvp": [10]}))),
        ],
    )
    for error in repo.list_errores(expediente_id, solo_pendientes=True):
        repo.marcar_error_resuelto(error["id_error"])
    ok, motivos = service.marcar_listo_para_analisis(expediente_id)
    assert ok, motivos

    resultado = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_id, repo=repo)
    docs = {doc.nombre_archivo: doc for doc in resultado.resultados_documentos}

    assert resultado.estado_preanalisis == preanalisis_documental.EstadoPreanalisis.PREANALISIS_ERROR.value
    assert docs["compras_bidafarma.xlsx"].proveedor_detectado == "Bidafarma"
    assert docs["ventas_2T_2026.xlsx"].tipo_documental_detectado == "Albaranes"
    assert docs["ventas_2T_2026.xlsx"].warnings
    assert docs["factura_laboratorio.pdf"].pdf_paginas >= 1
    assert docs["albaran_corrupto.zip"].errores_detectados
    assert repo.get_preanalisis_expediente(expediente_id)
    assert repo.list_preanalisis_documentos(expediente_id)

    expediente_zip = service.crear_expediente_desde_asunto(
        "ASESORIA_3T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Preanalisis",
        "cliente@example.com",
    )
    service.registrar_subida_manual(
        expediente_zip,
        [
            ("compras_bidafarma.xlsx", _xlsx_bytes(pd.DataFrame({"proveedor": ["Bidafarma"], "compra": [10], "coste": [5.3]}))),
            ("ventas.csv", b"ticket;fecha;importe venta\n1;2026-05-01;12.4\n"),
            ("stock.csv", b"codigo nacional;unidades stock;pvp\n1;5;10\n"),
            ("factura.pdf", _pdf_bytes("Factura laboratorio NIF IVA base imponible total factura")),
            ("albaran_bidafarma.zip", _zip_bytes()),
            ("liquidacion.pdf", _pdf_bytes("Liquidacion Bidafarma transfer goteo descuento")),
        ],
    )
    ok_zip, motivos_zip = service.marcar_listo_para_analisis(expediente_zip)
    assert ok_zip, motivos_zip
    resultado_zip = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_zip, repo=repo)
    docs_zip = {doc.nombre_archivo: doc for doc in resultado_zip.resultados_documentos}
    assert docs_zip["albaran_bidafarma.zip"].zip_archivos_internos

    expediente_edge = service.crear_expediente_desde_asunto(
        "REVISION_FACTURAS_MAYO_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Preanalisis",
        "cliente@example.com",
    )
    service.registrar_subida_manual(
        expediente_edge,
        [
            ("factura_laboratorio.pdf", _pdf_bytes("Factura laboratorio NIF IVA base imponible total factura")),
            ("stock_vacio.xlsx", _xlsx_bytes(pd.DataFrame())),
            ("otros_scan.pdf", b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF"),
        ],
    )
    ok_edge, motivos_edge = service.marcar_listo_para_analisis(expediente_edge)
    assert ok_edge, motivos_edge
    resultado_edge = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_edge, repo=repo)
    docs_edge = {doc.nombre_archivo: doc for doc in resultado_edge.resultados_documentos}
    assert docs_edge["stock_vacio.xlsx"].errores_detectados
    assert docs_edge["otros_scan.pdf"].warnings or docs_edge["otros_scan.pdf"].pdf_texto_extraible is False

    expediente_ambiguo = service.crear_expediente_desde_asunto(
        "REVISION_FACTURAS_JUNIO_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Preanalisis",
        "cliente@example.com",
    )
    service.registrar_subida_manual(
        expediente_ambiguo,
        [
            ("factura_laboratorio.pdf", _pdf_bytes("Factura laboratorio NIF IVA base imponible total factura")),
            ("otros_ambiguo.csv", b"foo;bar\n1;2\n"),
        ],
    )
    ok_ambiguo, motivos_ambiguo = service.marcar_listo_para_analisis(expediente_ambiguo)
    assert ok_ambiguo, motivos_ambiguo
    resultado_ambiguo = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_ambiguo, repo=repo)
    docs_ambiguo = {doc.nombre_archivo: doc for doc in resultado_ambiguo.resultados_documentos}
    assert docs_ambiguo["otros_ambiguo.csv"].warnings
    print("ok preanalisis smoke", expediente_id, resultado.estado_preanalisis)
    tmp.cleanup()


if __name__ == "__main__":
    main()
