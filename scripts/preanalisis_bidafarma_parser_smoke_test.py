from pathlib import Path
from tempfile import TemporaryDirectory

from modules import bandeja_documental as bd
from modules import preanalisis_documental as pre
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def _pdf_texto(*paginas: str) -> bytes:
    texto = " \f ".join(paginas)
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(texto) + 20} >> stream\nBT ({texto}) Tj ET\nendstream endobj\n".encode("latin-1")
        + b"trailer << /Root 1 0 R >>\n%%EOF"
    )


def _analizar_pdf(nombre: str, contenido: bytes):
    tmp = TemporaryDirectory(ignore_cleanup_errors=True)
    base = Path(tmp.name)
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    service = BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))
    exp = service.crear_expediente_desde_asunto("REVISION_PROVEEDOR_BIDAFARMA_MAYO_2026", "Cliente", "c@example.com")
    repo.update_expediente_fields(exp, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(exp, [(nombre, contenido)])
    resultado = pre.ejecutar_preanalisis_expediente(exp, repo=repo)
    doc = resultado.resultados_documentos[0]
    tmp.cleanup()
    return doc


def main() -> None:
    normal = _analizar_pdf(
        "bidafarma_normal_goteo.pdf",
        _pdf_texto(
            "Bidafarma Vida Pharma Factura Nº F-100 Base imponible IVA Total factura fecha 01/05/2026",
            "Albaran Nº A-100 Pedido P-1 ZV Goteo codigo nacional",
        ),
    )
    assert normal.contiene_factura
    assert normal.contiene_albaranes
    assert normal.paginas_factura == [1]
    assert normal.paginas_albaranes == [2]
    assert normal.clasificacion_paginas[0]["clasificacion"] == "FACTURA"
    assert normal.clasificacion_paginas[1]["clasificacion"] == "ALBARAN"
    assert normal.numero_factura
    assert normal.numero_albaranes_detectados >= 1
    assert normal.contiene_zv_zacofarva
    assert normal.tipo_documental_detectado != "Liquidaciones"
    assert normal.subtipo_documental == pre.SUBTIPO_BIDAFARMA_NORMAL_GOTEO

    transfer = _analizar_pdf(
        "bidafarma_transfer.pdf",
        _pdf_texto(
            "Bidafarma Factura transfer Bitransfer Nº FT-200 Base imponible IVA",
            "Albaran Nº TR-1 Transfer Pedido P-2",
        ),
    )
    assert transfer.subtipo_documental == pre.SUBTIPO_BIDAFARMA_TRANSFER

    mixto_tipo74 = _analizar_pdf(
        "bidafarma_mixto_tipo74.pdf",
        _pdf_texto(
            "Bidafarma Factura transfer Bitransfer goteo Nº FM-300",
            "Albaran Nº A-74 Tipo 74 abono cargo regularizacion",
        ),
    )
    assert mixto_tipo74.subtipo_documental == pre.SUBTIPO_BIDAFARMA_MIXTO
    assert "74" in mixto_tipo74.tipos_albaran_detectados
    assert mixto_tipo74.contiene_tipo_74
    assert mixto_tipo74.contiene_zv_zacofarva is False
    assert mixto_tipo74.posible_liquidacion_embebida
    assert mixto_tipo74.posibles_liquidaciones_detectadas
    assert mixto_tipo74.tipo_documental_detectado != "Liquidaciones"

    sin_albaranes = _analizar_pdf(
        "bidafarma_factura_sola.pdf",
        _pdf_texto("Bidafarma Factura Nº F-400 Base imponible IVA Total factura"),
    )
    assert sin_albaranes.contiene_factura
    assert not sin_albaranes.contiene_albaranes
    assert any("Warning suave" in warning for warning in sin_albaranes.warnings)

    corrupto = _analizar_pdf("bidafarma_corrupto.pdf", b"no soy un pdf")
    assert corrupto.errores_detectados

    escaneado = _analizar_pdf("bidafarma_escaneado.pdf", b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF")
    assert not escaneado.pdf_texto_extraible
    assert escaneado.warnings

    print("ok bidafarma parser smoke")


if __name__ == "__main__":
    main()
