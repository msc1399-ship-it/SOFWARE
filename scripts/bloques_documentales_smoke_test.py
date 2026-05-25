from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pandas as pd

from modules import bandeja_documental as bd
from modules import preanalisis_documental
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def _xlsx(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buffer.getvalue()


def _ventas() -> bytes:
    return _xlsx(pd.DataFrame({"fecha": ["2026-05-01"], "codigo nacional": ["123"], "producto": ["P"], "unidades": [1], "pvp": [10], "importe": [10]}))


def _stock() -> bytes:
    return _xlsx(pd.DataFrame({"codigo nacional": ["123"], "producto": ["P"], "unidades stock": [4], "coste medio": [6], "pvp": [10]}))


def _pdf_bidafarma() -> bytes:
    texto = "Factura Bidafarma Vida Pharma numero F-1 fecha 01/05/2026 \f Albaran numero A-1 codigo nacional tipo 74 abono"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(texto) + 20} >> stream\nBT ({texto}) Tj ET\nendstream endobj\n".encode("latin-1")
        + b"trailer << /Root 1 0 R >>\n%%EOF"
    )


def _pdf_bidafarma_transfer() -> bytes:
    texto = "Factura Bidafarma Bitransfer numero T-1 base imponible IVA total factura \f Albaran transfer numero AT-1 codigo nacional unidades pva"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(texto) + 20} >> stream\nBT ({texto}) Tj ET\nendstream endobj\n".encode("latin-1")
        + b"trailer << /Root 1 0 R >>\n%%EOF"
    )


def _zip_completo() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zf:
        zf.writestr("compras_bidafarma_goteo.pdf", _pdf_bidafarma())
        zf.writestr("compras_bidafarma_transfer.pdf", _pdf_bidafarma_transfer())
        zf.writestr("ventas_periodo.xlsx", _ventas())
        zf.writestr("stock_periodo.xlsx", _stock())
    return buffer.getvalue()


def _nuevo_service(base: Path) -> BandejaDocumentalService:
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    return BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))


def main() -> None:
    tmp = TemporaryDirectory(ignore_cleanup_errors=True)
    base = Path(tmp.name)
    service = _nuevo_service(base)
    repo = service.repo

    # A) Asesoria estandar completa.
    exp_ok = service.crear_expediente_desde_asunto("ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL", "Cliente", "c@example.com")
    repo.update_expediente_fields(exp_ok, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(exp_ok, [("bidafarma_factura.pdf", _pdf_bidafarma()), ("ventas.xlsx", _ventas()), ("stock.xlsx", _stock())])
    pre_ok = preanalisis_documental.ejecutar_preanalisis_expediente(exp_ok, repo=repo)
    bloques_ok = service.evaluar_bloques_documentales(exp_ok)
    assert not bloques_ok["bloques_faltantes"]
    assert bloques_ok["bloques"][bd.BloqueDocumental.COMPRAS_PROVEEDOR.value]
    assert bloques_ok["bloques_detalle"][bd.BloqueDocumental.COMPRAS_PROVEEDOR.value]["fuente"] == "preanalisis"
    ok, motivos = service.marcar_listo_para_analisis(exp_ok)
    assert ok, motivos
    assert bd.BloqueDocumental.LIQUIDACIONES_SEPARADAS.value not in pre_ok.documentos_faltantes_perfil
    assert bd.BloqueDocumental.ALBARANES_SEPARADOS.value not in pre_ok.documentos_faltantes_perfil

    # B) Asesoria sin ventas.
    exp_sin_ventas = service.crear_expediente_desde_asunto("ASESORIA_3T_2026_FARMACIA_SAN_MIGUEL", "Cliente", "c@example.com")
    repo.update_expediente_fields(exp_sin_ventas, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(exp_sin_ventas, [("bidafarma_factura.pdf", _pdf_bidafarma()), ("stock.xlsx", _stock())])
    ok_sin_ventas, motivos_sin_ventas = service.marcar_listo_para_analisis(exp_sin_ventas)
    assert not ok_sin_ventas and bd.BloqueDocumental.VENTAS.value in " ".join(motivos_sin_ventas)

    # C) Asesoria sin stock.
    exp_sin_stock = service.crear_expediente_desde_asunto("ASESORIA_4T_2026_FARMACIA_SAN_MIGUEL", "Cliente", "c@example.com")
    repo.update_expediente_fields(exp_sin_stock, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(exp_sin_stock, [("bidafarma_factura.pdf", _pdf_bidafarma()), ("ventas.xlsx", _ventas())])
    ok_sin_stock, motivos_sin_stock = service.marcar_listo_para_analisis(exp_sin_stock)
    assert not ok_sin_stock and bd.BloqueDocumental.STOCK.value in " ".join(motivos_sin_stock)

    # D) Revision especifica proveedor: solo compras.
    exp_proveedor = service.crear_expediente_desde_asunto("REVISION_PROVEEDOR_BIDAFARMA_MAYO_2026", "Cliente", "c@example.com")
    service.registrar_subida_manual(exp_proveedor, [("bidafarma_factura.pdf", _pdf_bidafarma())])
    bloques_proveedor = service.evaluar_bloques_documentales(exp_proveedor)
    assert bloques_proveedor["analisis_especifico_proveedor"]
    ok_proveedor, motivos_proveedor = service.marcar_listo_para_analisis(exp_proveedor)
    assert ok_proveedor, motivos_proveedor

    # E) ZIP completo: se expande y satisface bloques.
    exp_zip = service.crear_expediente_desde_asunto("ASESORIA_1T_2026_FARMACIA_SAN_MIGUEL", "Cliente", "c@example.com")
    service.registrar_subida_manual(exp_zip, [("documentacion_completa.zip", _zip_completo())])
    preanalisis_documental.ejecutar_preanalisis_expediente(exp_zip, repo=repo)
    repo.update_expediente_fields(exp_zip, perfil_documental=bd.PerfilDocumental.GENERICO.value)
    bloques_zip = service.evaluar_bloques_documentales(exp_zip)
    assert bloques_zip["bloques"][bd.BloqueDocumental.COMPRAS_PROVEEDOR.value], bloques_zip
    assert bloques_zip["bloques"][bd.BloqueDocumental.VENTAS.value], bloques_zip
    assert bloques_zip["bloques"][bd.BloqueDocumental.STOCK.value], bloques_zip
    assert bloques_zip["bloques_detalle"][bd.BloqueDocumental.COMPRAS_PROVEEDOR.value]["fuente"] == "preanalisis"
    assert "Bidafarma" in bloques_zip["bloques_detalle"][bd.BloqueDocumental.COMPRAS_PROVEEDOR.value]["razon"]
    debug_zip = service.debug_compras_proveedor_preanalisis(exp_zip)
    assert any(row["computa_compras_proveedor"] for row in debug_zip), debug_zip
    assert not bloques_zip["bloques_faltantes"], bloques_zip
    ok_zip, motivos_zip = service.marcar_listo_para_analisis(exp_zip)
    assert ok_zip, motivos_zip

    # F) Condiciones directas de preanalisis: subtipo Bidafarma computa aunque el perfil sea generico.
    computa_subtipo, _, _ = service._doc_preanalisis_satisface_compras(
        {
            "nombre_archivo": "archivo_ambiguo.pdf",
            "proveedor_detectado": "Otros",
            "subtipo_bidafarma": "BIDAFARMA_TRANSFER",
            "pdf_compuesto": False,
            "contiene_factura": False,
            "tipo_documental_detectado": "Liquidaciones",
            "errores_detectados": [],
        }
    )
    assert computa_subtipo

    print("ok bloques documentales smoke")
    tmp.cleanup()


if __name__ == "__main__":
    main()
