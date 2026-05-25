from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from modules import bandeja_documental as bd
from modules import preanalisis_documental
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ventas")
    return buffer.getvalue()


def _pdf_bytes(texto: str) -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + f"4 0 obj << /Length {len(texto) + 20} >> stream\nBT ({texto}) Tj ET\nendstream endobj\n".encode("latin-1")
        + b"5 0 obj << /Type /Page /Parent 2 0 R /Contents 6 0 R >> endobj\n"
        b"6 0 obj << /Length 10 >> stream\nBT () Tj ET\nendstream endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF"
    )


def _pdf_bidafarma(decena: str, transfer: bool = False, con_albaranes: bool = True) -> bytes:
    subtipo = "Factura transfer Bitransfer Bidafarma" if transfer else "Factura Bidafarma Vida Pharma goteo"
    albaranes = (
        " %%PAGE%% Albaran numero ALB-1234 codigo nacional unidades servidas tipo 74 abono cargo"
        if con_albaranes
        else " %%PAGE%% Resumen factura sin detalle logistico"
    )
    texto = (
        f"{subtipo} factura numero F-{decena}-2026 fecha 10/05/2026 "
        f"Zacofarva ZV base imponible IVA total factura"
        f"{albaranes}"
    )
    return _pdf_bytes(texto)


def main() -> None:
    tmp = TemporaryDirectory(ignore_cleanup_errors=True)
    base = Path(tmp.name)
    repo = BandejaDocumentalRepository(str(base / "bandeja.db"))
    service = BandejaDocumentalService(repo=repo, storage_root=str(base / "documentos"))

    expediente_id = service.crear_expediente_desde_asunto(
        "ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Bidafarma",
        "cliente@example.com",
    )
    repo.update_expediente_fields(expediente_id, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(
        expediente_id,
        [
            (
                "ventas_farmacia_2T.xlsx",
                _xlsx_bytes(
                    pd.DataFrame(
                        {
                            "fecha": ["2026-05-01"],
                            "codigo nacional": ["123456"],
                            "descripcion": ["Producto"],
                            "unidades": [2],
                            "pvp": [10.2],
                            "importe": [20.4],
                        }
                    )
                ),
            ),
            ("bidafarma_factura_primera_decena.pdf", _pdf_bidafarma("D1")),
            ("bidafarma_factura_segunda_decena.pdf", _pdf_bidafarma("D2")),
            ("bidafarma_factura_tercera_decena.pdf", _pdf_bidafarma("D3")),
            ("bidafarma_transfer_mensual.pdf", _pdf_bidafarma("TR", transfer=True)),
        ],
    )
    ok, motivos = service.marcar_listo_para_analisis(expediente_id)
    assert ok, motivos
    resultado = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_id, repo=repo)
    docs = repo.list_preanalisis_documentos(expediente_id)
    pdfs = [doc for doc in docs if doc["extension"] == ".pdf"]

    assert resultado.perfil_documental == bd.PerfilDocumental.BIDAFARMA.value
    assert "Liquidaciones" not in resultado.documentos_faltantes_perfil
    assert not resultado.documentos_faltantes_perfil
    assert len(pdfs) == 4
    assert all(doc["pdf_compuesto"] for doc in pdfs)
    assert all(doc["contiene_factura"] for doc in pdfs)
    assert all(doc["contiene_albaranes"] for doc in pdfs)
    assert any(doc["subtipo_documental"] == preanalisis_documental.SUBTIPO_BIDAFARMA_TRANSFER for doc in pdfs)
    assert any(doc["posibles_liquidaciones_detectadas"] for doc in pdfs)

    expediente_sin_albaranes = service.crear_expediente_desde_asunto(
        "ASESORIA_3T_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Bidafarma",
        "cliente@example.com",
    )
    repo.update_expediente_fields(expediente_sin_albaranes, perfil_documental=bd.PerfilDocumental.BIDAFARMA.value)
    service.registrar_subida_manual(
        expediente_sin_albaranes,
        [
            ("ventas_farmacia_3T.xlsx", _xlsx_bytes(pd.DataFrame({"fecha": ["2026-07-01"], "codigo nacional": ["1"], "importe": [1]}))),
            ("bidafarma_factura_resumen.pdf", _pdf_bidafarma("D1", con_albaranes=False)),
        ],
    )
    ok_sin, motivos_sin = service.marcar_listo_para_analisis(expediente_sin_albaranes)
    assert ok_sin, motivos_sin
    resultado_sin = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_sin_albaranes, repo=repo)
    assert resultado_sin.valido_global
    assert any("albaranes embebidos" in warning for warning in resultado_sin.warnings_globales)

    expediente_lab = service.crear_expediente_desde_asunto(
        "REVISION_FACTURAS_MAYO_2026_FARMACIA_SAN_MIGUEL",
        "Cliente Laboratorios",
        "cliente@example.com",
    )
    repo.update_expediente_fields(expediente_lab, perfil_documental=bd.PerfilDocumental.LABORATORIOS.value)
    service.registrar_subida_manual(expediente_lab, [("factura_laboratorio_scan.pdf", b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF")])
    ok_lab, motivos_lab = service.marcar_listo_para_analisis(expediente_lab)
    assert ok_lab, motivos_lab
    resultado_lab = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_lab, repo=repo)
    assert resultado_lab.valido_global
    assert resultado_lab.warnings_globales

    print("ok bidafarma preanalisis smoke", expediente_id)
    tmp.cleanup()


if __name__ == "__main__":
    main()
