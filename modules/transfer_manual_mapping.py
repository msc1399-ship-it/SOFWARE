import re

import pandas as pd


TOLERANCIA_ABONO_TRANSFER = 0.05
PORCENTAJE_CARGO_TRANSFER = 0.017


def _normalizar_albaran(valor):
    if pd.isna(valor):
        return ""
    texto = str(valor).strip()
    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]
    return re.sub(r"[^\dA-Za-z-]", "", texto)


def _serie_numerica(df, columna):
    if df is None or columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index if df is not None else None, dtype="float64")
    serie = df[columna]
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce").fillna(0.0)
    texto = (
        serie.astype(str)
        .str.replace("€", "", regex=False)
        .str.replace("EUR", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.strip()
    )
    texto = texto.str.replace(r"\.(?=\d{3}(?:\D|$))", "", regex=True)
    texto = texto.str.replace(",", ".", regex=False)
    return pd.to_numeric(texto, errors="coerce").fillna(0.0)


def _id_abono(indice, concepto, importe):
    concepto_limpio = re.sub(r"[^a-zA-Z0-9]+", "_", str(concepto).strip().lower()).strip("_")[:40]
    centimos = int(round(abs(float(importe or 0)) * 100))
    return f"abono_{indice}_{concepto_limpio}_{centimos}"


def detectar_abonos_transfer_no_asociados(resultado_transfer, asociaciones_auto=None):
    abonos = (resultado_transfer or {}).get("abonos")
    if abonos is None or abonos.empty:
        return []

    asociaciones = asociaciones_auto.copy() if asociaciones_auto is not None else pd.DataFrame()
    asociados = set()
    if not asociaciones.empty and "id_abono" in asociaciones.columns:
        columna_labs = "laboratorios_detectados"
        for _, fila in asociaciones.iterrows():
            if columna_labs in fila and str(fila.get(columna_labs, "")).strip():
                asociados.add(str(fila.get("id_abono")))

    pendientes = []
    for indice, fila in abonos.reset_index(drop=True).iterrows():
        concepto = str(fila.get("concepto", "")).strip()
        importe = float(fila.get("importe", 0) or 0)
        id_abono = str(fila.get("id_abono") or _id_abono(indice, concepto, importe))
        if id_abono in asociados:
            continue
        pendientes.append({
            "id_abono": id_abono,
            "concepto": concepto,
            "laboratorio_concepto": concepto,
            "importe_abono": round(importe, 2),
            "origen_factura": "factura_transfer",
            "estado_asociacion": "pendiente_manual",
            "albaranes_asociados": [],
        })
    return pendientes


def preparar_albaranes_transfer_para_selector(df_transfer):
    if df_transfer is None or df_transfer.empty or "albaran" not in df_transfer.columns:
        return pd.DataFrame()

    df = df_transfer.copy()
    df["albaran"] = df["albaran"].apply(_normalizar_albaran)
    df["bruto_num"] = _serie_numerica(df, "bruto")
    df["neto_num"] = _serie_numerica(df, "neto")

    agregaciones = {
        "bruto_num": "sum",
        "neto_num": "sum",
    }
    if "fecha" in df.columns:
        agregaciones["fecha"] = "first"
    if "proveedor" in df.columns:
        agregaciones["proveedor"] = "first"
    if "tipo_compra" in df.columns:
        agregaciones["tipo_compra"] = "first"
    if "descripcion" in df.columns:
        agregaciones["descripcion"] = "first"

    resumen = df.groupby("albaran", dropna=False).agg(agregaciones).reset_index()
    resumen = resumen[resumen["albaran"].astype(str).str.len() > 0].copy()
    resumen = resumen.rename(columns={"bruto_num": "bruto_total", "neto_num": "neto_total"})
    resumen["base_selector"] = resumen["bruto_total"].where(resumen["bruto_total"].abs() > 0, resumen["neto_total"])
    resumen["usa_neto_fallback"] = resumen["bruto_total"].abs() <= 0

    def etiqueta(row):
        fecha = row.get("fecha", "")
        fecha_txt = "" if pd.isna(fecha) or str(fecha).strip() == "" else f" - {fecha}"
        proveedor = row.get("proveedor", "")
        proveedor_txt = "" if pd.isna(proveedor) or str(proveedor).strip() == "" else f" - {proveedor}"
        return f"{row['albaran']}{fecha_txt} - {row['base_selector']:.2f} €{proveedor_txt}"

    resumen["etiqueta"] = resumen.apply(etiqueta, axis=1)
    return resumen.sort_values("albaran").reset_index(drop=True)


def calcular_validacion_abono_manual(
    importe_abono,
    albaranes_seleccionados,
    df_transfer,
    porcentaje=PORCENTAJE_CARGO_TRANSFER,
    tolerancia=TOLERANCIA_ABONO_TRANSFER,
):
    albaranes = {_normalizar_albaran(albaran) for albaran in (albaranes_seleccionados or [])}
    selector = preparar_albaranes_transfer_para_selector(df_transfer)
    seleccion = selector[selector["albaran"].astype(str).isin(albaranes)].copy()

    base_manual = float(seleccion["base_selector"].sum()) if not seleccion.empty else 0.0
    cargo_teorico = base_manual * float(porcentaje or 0)
    diferencia = abs(abs(cargo_teorico) - abs(float(importe_abono or 0)))
    return {
        "base_manual": round(base_manual, 2),
        "cargo_teorico_1_7": round(cargo_teorico, 2),
        "importe_abono": round(float(importe_abono or 0), 2),
        "diferencia": round(diferencia, 2),
        "estado_validacion": "cuadra" if diferencia <= tolerancia else "descuadre",
        "usa_neto_fallback": bool(seleccion["usa_neto_fallback"].any()) if not seleccion.empty else False,
    }


def aplicar_imputaciones_manuales_transfer(df_transfer, imputaciones_manuales):
    if df_transfer is None:
        return df_transfer
    df = df_transfer.copy()
    if "albaran" not in df.columns:
        df["bonificado_transfer_manual"] = False
        return df

    albaranes_manual = set()
    for imputacion in imputaciones_manuales or []:
        albaranes_manual.update(_normalizar_albaran(alb) for alb in imputacion.get("albaranes_asociados", []))

    df["bonificado_transfer_manual"] = df["albaran"].apply(_normalizar_albaran).isin(albaranes_manual)
    return df


def generar_resumen_imputaciones_transfer(imputaciones_manuales):
    if not imputaciones_manuales:
        return pd.DataFrame()
    filas = []
    for imputacion in imputaciones_manuales:
        filas.append({
            "id_abono": imputacion.get("id_abono"),
            "concepto": imputacion.get("concepto"),
            "importe_abono": imputacion.get("importe_abono"),
            "albaranes_imputados": ", ".join(map(str, imputacion.get("albaranes_asociados", []))),
            "base_manual": imputacion.get("base_manual"),
            "cargo_teorico_1_7": imputacion.get("cargo_teorico_1_7"),
            "diferencia": imputacion.get("diferencia"),
            "estado_validacion": imputacion.get("estado_validacion"),
        })
    return pd.DataFrame(filas)
