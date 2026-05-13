import re
import unicodedata

import pandas as pd


CONCEPTOS_FIN_BLOQUE = {
    "total",
    "base",
    "iva",
    "re",
    "exento",
}

PATRON_CARGO_TARIFA = r"margen tramo fijo|tramo fijo|tramo 0|tramo0|diferencia de escala|ajuste escala|escala|cargo tramo|cargo escala"


def _normalizar_texto(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar_numero(valor):
    if pd.isna(valor):
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).replace("€", "").replace("%", "").replace(" ", "").strip()
    if not texto:
        return None

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


def _buscar_valor_despues_de(df_raw, etiqueta):
    etiqueta_normalizada = _normalizar_texto(etiqueta)

    for _, fila in df_raw.iterrows():
        valores = list(fila.values)
        for indice, valor in enumerate(valores[:-1]):
            if _normalizar_texto(valor) == etiqueta_normalizada:
                siguiente = valores[indice + 1]
                numero = _normalizar_numero(siguiente)
                return numero if numero is not None else siguiente

    return None


def _extraer_conceptos(df_raw):
    fila_encabezado = None
    concepto_idx = None
    importe_idx = None

    for indice, fila in df_raw.iterrows():
        valores = list(fila.values)
        normalizados = [_normalizar_texto(valor) for valor in valores]

        if "concepto" in normalizados and "importe" in normalizados:
            fila_encabezado = indice
            concepto_idx = normalizados.index("concepto")
            importe_idx = normalizados.index("importe")
            break

    if fila_encabezado is None:
        return []

    conceptos = []

    for _, fila in df_raw.iloc[fila_encabezado + 1:].iterrows():
        valores = list(fila.values)

        concepto = valores[concepto_idx] if concepto_idx < len(valores) else None
        concepto_texto = _normalizar_texto(concepto)

        if not concepto_texto or concepto_texto == "nan":
            continue

        if concepto_texto in CONCEPTOS_FIN_BLOQUE:
            break

        importe = valores[importe_idx] if importe_idx < len(valores) else None
        importe_num = _normalizar_numero(importe)

        if importe_num is None:
            continue

        conceptos.append(
            {
                "concepto": str(concepto).strip(),
                "concepto_normalizado": concepto_texto,
                "importe": round(float(importe_num), 4),
            }
        )

    return conceptos


def leer_albaran_faceta_v(file):
    if hasattr(file, "seek"):
        file.seek(0)

    try:
        df_raw = pd.read_excel(file, header=None)
    except Exception:
        return None

    conceptos = _extraer_conceptos(df_raw)
    if not conceptos:
        return None

    tp = _buscar_valor_despues_de(df_raw, "tp")
    texto_global = " ".join(
        _normalizar_texto(valor)
        for valor in df_raw.fillna("").astype(str).values.flatten().tolist()
        if str(valor).strip()
    )

    es_faceta_v = (
        float(tp) == 74.0 if isinstance(tp, (int, float)) else False
    ) or "margen tramo fijo" in texto_global or "liquidacion" in texto_global

    if not es_faceta_v:
        return None

    resultado = pd.DataFrame(conceptos)
    resultado["tp"] = tp
    resultado["fecha"] = _buscar_valor_despues_de(df_raw, "fecha")
    resultado["hora"] = _buscar_valor_despues_de(df_raw, "hora")
    resultado["albaran"] = _buscar_valor_despues_de(df_raw, "albaran")
    resultado["farmacia"] = _buscar_valor_despues_de(df_raw, "farmacia")
    resultado["tarifa"] = "tp_74"

    return resultado


def _serie_numerica(df, columna):
    if columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)

    return df[columna].apply(lambda valor: _normalizar_numero(valor) or 0.0)


def _extraer_nombre_liquidacion(concepto):
    texto = _normalizar_texto(concepto)
    texto = texto.replace("liquidacion", "").strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def hay_cargo_tarifa(df_faceta):
    if df_faceta is None or df_faceta.empty or "concepto_normalizado" not in df_faceta.columns:
        return False

    return bool(
        df_faceta["concepto_normalizado"].astype(str).str.contains(
            PATRON_CARGO_TARIFA,
            na=False,
        ).any()
    )


def detectar_tipo_albaran_74(df_faceta):
    if df_faceta is None or df_faceta.empty or "concepto_normalizado" not in df_faceta.columns:
        return None

    conceptos = df_faceta["concepto_normalizado"].astype(str)

    if conceptos.str.contains("tramo 0|tramo0|diferencia de escala|ajuste escala|cargo escala", na=False).any():
        return 3

    if conceptos.str.contains("margen tramo fijo|tramo fijo|cargo tramo", na=False).any():
        return 2

    if conceptos.str.contains("liquidacion", na=False).any():
        return 1

    return None


def es_linea_faceta(valor_tipo=None, valor_descripcion=None):
    tipo_texto = _normalizar_texto(valor_tipo)
    descripcion_texto = _normalizar_texto(valor_descripcion)

    if tipo_texto in {"74", "tp74"}:
        return True

    if "margen tramo fijo" in descripcion_texto or "liquidacion" in descripcion_texto:
        return True

    return False


def extraer_faceta_desde_lineas(df_compras):
    if df_compras is None or df_compras.empty:
        return pd.DataFrame()

    df = df_compras.copy()
    tipos = df["tipo"] if "tipo" in df.columns else pd.Series([""] * len(df), index=df.index)
    descripciones = df["descripcion"] if "descripcion" in df.columns else pd.Series([""] * len(df), index=df.index)

    mask_faceta = pd.Series(
        [es_linea_faceta(tipo, descripcion) for tipo, descripcion in zip(tipos, descripciones)],
        index=df.index,
    )

    df_faceta = df[mask_faceta].copy()
    if df_faceta.empty:
        return pd.DataFrame()

    df_faceta["concepto"] = df_faceta.get("descripcion", "").astype(str)
    df_faceta["concepto_normalizado"] = df_faceta["concepto"].apply(_normalizar_texto)
    df_faceta["importe"] = _serie_numerica(df_faceta, "neto")
    df_faceta["tp"] = _serie_numerica(df_faceta, "tipo")
    if "fecha_albaran" in df_faceta.columns:
        df_faceta["fecha"] = df_faceta["fecha_albaran"]
    if "albaran" in df_faceta.columns:
        df_faceta["albaran"] = df_faceta["albaran"]
    df_faceta["tarifa"] = "tp_74"

    columnas = [col for col in ["concepto", "concepto_normalizado", "importe", "tp", "fecha", "albaran", "tarifa"] if col in df_faceta.columns]
    return df_faceta[columnas].reset_index(drop=True)


def analizar_faceta_v(df_compras, df_faceta):
    if df_compras is None or df_compras.empty or df_faceta is None or df_faceta.empty:
        return None

    df_goteo = df_compras[df_compras["tipo_compra"] == "goteo"].copy()
    if df_goteo.empty:
        return None

    if "seccion_albaran" not in df_goteo.columns:
        return None

    df_goteo["descripcion"] = df_goteo.get("descripcion", "").astype(str)
    df_goteo["bruto"] = _serie_numerica(df_goteo, "bruto")
    df_goteo["neto"] = _serie_numerica(df_goteo, "neto")
    df_goteo["unidades"] = _serie_numerica(df_goteo, "unidades")
    no_especialidad_cara = ~df_goteo.get(
        "es_especialidad_cara",
        pd.Series(False, index=df_goteo.index),
    ).fillna(False).astype(bool)

    descripcion_normalizada = df_goteo["descripcion"].apply(_normalizar_texto)
    tipos_normalizados = df_goteo.get("tipo", pd.Series([""] * len(df_goteo), index=df_goteo.index))
    mask_linea_tp74 = pd.Series(
        [
            es_linea_faceta(tipo, descripcion)
            for tipo, descripcion in zip(tipos_normalizados, df_goteo["descripcion"])
        ],
        index=df_goteo.index,
    )

    mask_tramo_fijo = (
        df_goteo["seccion_albaran"].isin(["especialidad", "parafarmacia"])
        & df_goteo["neto"].gt(0)
        & no_especialidad_cara
        & ~mask_linea_tp74
        & ~descripcion_normalizada.str.contains("club", na=False)
        & ~descripcion_normalizada.str.contains("bitransfer|bittransfer", na=False)
        & ~descripcion_normalizada.str.contains("avantia", na=False)
    )

    tramo_fijo = df_goteo[mask_tramo_fijo].copy()
    cargos_tarifa = df_faceta[
        df_faceta["concepto_normalizado"].str.contains(PATRON_CARGO_TARIFA, na=False)
    ].copy()
    tipo_albaran_74 = detectar_tipo_albaran_74(df_faceta)

    margen_tramo_fijo_total = round(float(cargos_tarifa["importe"].sum()), 4)

    base_aplicacion_tramo_fijo = float(tramo_fijo["bruto"].abs().sum()) if not tramo_fijo.empty else 0.0
    base_tramo_fijo = margen_tramo_fijo_total * 0.076

    if not tramo_fijo.empty:
        if base_aplicacion_tramo_fijo > 0:
            tramo_fijo["cargo_faceta_tramo_fijo"] = (
                tramo_fijo["bruto"].abs() / base_aplicacion_tramo_fijo
            ) * margen_tramo_fijo_total
        else:
            tramo_fijo["cargo_faceta_tramo_fijo"] = 0.0
        tramo_fijo["neto_con_faceta_tramo_fijo"] = (
            tramo_fijo["neto"] + tramo_fijo["cargo_faceta_tramo_fijo"]
        )
    else:
        tramo_fijo["cargo_faceta_tramo_fijo"] = []

    liquidaciones = df_faceta[
        df_faceta["concepto_normalizado"].str.contains("liquidacion", na=False)
    ].copy()

    detalle_liquidaciones = []
    liquidaciones_resumen = []

    for _, liquidacion in liquidaciones.iterrows():
        objetivo = _extraer_nombre_liquidacion(liquidacion["concepto"])
        if not objetivo:
            continue

        mask_objetivo = (
            (df_goteo["tipo_compra"] == "goteo")
            & descripcion_normalizada.str.contains("club", na=False)
            & descripcion_normalizada.str.contains(objetivo, na=False)
        )

        df_objetivo = df_goteo[mask_objetivo].copy()
        base_objetivo = float(df_objetivo["bruto"].abs().sum())
        importe_liquidacion = float(liquidacion["importe"])
        pct_liquidacion = (importe_liquidacion / base_objetivo * 100) if base_objetivo > 0 else 0.0

        liquidaciones_resumen.append(
            {
                "concepto": liquidacion["concepto"],
                "grupo_objetivo": objetivo,
                "base_liquidacion": round(base_objetivo, 4),
                "importe_liquidacion": round(importe_liquidacion, 4),
                "pct_liquidacion": round(pct_liquidacion, 4),
            }
        )

        if df_objetivo.empty or base_objetivo <= 0:
            continue

        df_objetivo["grupo_liquidacion"] = objetivo
        df_objetivo["importe_liquidacion_total"] = importe_liquidacion
        df_objetivo["pct_liquidacion"] = pct_liquidacion
        df_objetivo["liquidacion_faceta_linea"] = (
            df_objetivo["bruto"].abs() / base_objetivo
        ) * importe_liquidacion
        df_objetivo["neto_con_liquidacion"] = (
            df_objetivo["neto"] + df_objetivo["liquidacion_faceta_linea"]
        )
        detalle_liquidaciones.append(df_objetivo)

    detalle_tramo_fijo = tramo_fijo.copy()
    if not detalle_tramo_fijo.empty:
        detalle_tramo_fijo["cargo_faceta_tramo_fijo"] = detalle_tramo_fijo["cargo_faceta_tramo_fijo"].round(4)
        detalle_tramo_fijo["neto_con_faceta_tramo_fijo"] = detalle_tramo_fijo["neto_con_faceta_tramo_fijo"].round(4)

    detalle_liquidaciones_df = (
        pd.concat(detalle_liquidaciones, ignore_index=True) if detalle_liquidaciones else pd.DataFrame()
    )
    if not detalle_liquidaciones_df.empty:
        detalle_liquidaciones_df["liquidacion_faceta_linea"] = detalle_liquidaciones_df["liquidacion_faceta_linea"].round(4)
        detalle_liquidaciones_df["neto_con_liquidacion"] = detalle_liquidaciones_df["neto_con_liquidacion"].round(4)

    return {
        "conceptos": df_faceta.copy(),
        "cargos_tarifa": cargos_tarifa.copy(),
        "detalle_tramo_fijo": detalle_tramo_fijo,
        "detalle_liquidaciones": detalle_liquidaciones_df,
        "resumen_liquidaciones": pd.DataFrame(liquidaciones_resumen),
        "resumen": {
            "tipo_albaran_74": tipo_albaran_74,
            "margen_tramo_fijo_total": round(margen_tramo_fijo_total, 2),
            "base_tramo_fijo": round(base_tramo_fijo, 2),
            "base_aplicacion": round(base_aplicacion_tramo_fijo, 2),
            "lineas_tramo_fijo": 0 if tramo_fijo.empty else len(tramo_fijo),
            "liquidaciones_total": round(float(liquidaciones["importe"].sum()), 2) if not liquidaciones.empty else 0.0,
            "lineas_liquidaciones": 0 if detalle_liquidaciones_df.empty else len(detalle_liquidaciones_df),
        },
    }


def pct_descuento_medio(bruto_total, coste_total):
    if bruto_total <= 0:
        return 0.0
    return round((1 - (coste_total / bruto_total)) * 100, 2)
