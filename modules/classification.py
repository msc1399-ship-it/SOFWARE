import re
import unicodedata

import pandas as pd

COLUMN_MAPPING = {
    "cn": ["cn", "codigo", "cod_nacional", "codigo nacional"],
    "descripcion": ["descripcion", "articulo", "producto"],
    "bruto": ["bruto", "importe bruto", "precio bruto"],
    "neto": ["neto", "importe neto", "precio"],
    "iva": ["iva", "tipo iva"],
    "unidades": ["unidades", "cantidad"],
    "observaciones": ["observaciones", "observacion", "obs", "observ."]
}

PVP_COLUMN_OPTIONS = [
    "pvp",
    "precio venta publico",
    "precio_venta_publico",
    "precio venta publico iva",
    "pvp iva",
    "pvp_iva",
    "pvp con iva",
]

PVA_COLUMN_OPTIONS = [
    "pva",
    "precio venta almacen",
    "precio_venta_almacen",
    "bruto unitario",
    "precio bruto unitario",
]

UMBRAL_PVP_SIN_IVA_ESPECIALIDAD_CARA = 143.05
UMBRAL_PVA_ESPECIALIDAD_CARA = 98.59

def normalize_columns(df):

    mapping = {}

    for standard, options in COLUMN_MAPPING.items():

        for col in df.columns:

            if col.lower() in options:
                mapping[col] = standard

    df = df.rename(columns=mapping)

    return df


def _normalizar_nombre_columna(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _buscar_columna(df, opciones):
    columnas_normalizadas = {
        _normalizar_nombre_columna(col): col
        for col in df.columns
    }
    opciones_normalizadas = [_normalizar_nombre_columna(opcion) for opcion in opciones]

    for opcion in opciones_normalizadas:
        if opcion in columnas_normalizadas:
            return columnas_normalizadas[opcion]

    for opcion in opciones_normalizadas:
        for columna_norm, columna_original in columnas_normalizadas.items():
            if opcion and opcion in columna_norm:
                return columna_original

    return None


def _serie_numerica(df, columna):
    if columna is None or columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index, dtype="float64")

    serie = df[columna]
    if pd.api.types.is_numeric_dtype(serie):
        return pd.to_numeric(serie, errors="coerce").fillna(0.0)

    texto = (
        serie.astype(str)
        .str.replace("€", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.strip()
    )
    texto = texto.str.replace(r"\.(?=\d{3}(?:\D|$))", "", regex=True)
    texto = texto.str.replace(",", ".", regex=False)
    return pd.to_numeric(texto, errors="coerce").fillna(0.0)


def _iva_es_cuatro(iva):
    iva_normalizado = iva.copy()
    iva_normalizado = iva_normalizado.where(iva_normalizado.abs() > 1, iva_normalizado * 100)
    return (iva_normalizado - 4).abs() <= 0.01


def _pvp_incluye_iva(nombre_columna):
    if not nombre_columna:
        return False

    nombre = _normalizar_nombre_columna(nombre_columna)
    if "sin iva" in nombre or "base" in nombre:
        return False

    return (
        "con iva" in nombre
        or "iva" in nombre
        or nombre == "pvp"
        or "precio venta publico" in nombre
    )


def clasificar_especialidad_cara(df):
    if df is None:
        return df

    df = df.copy()

    iva_col = _buscar_columna(df, ["iva", "tipo iva"])
    bruto_col = _buscar_columna(df, ["bruto", "importe bruto", "precio bruto"])
    neto_col = _buscar_columna(df, ["neto", "importe neto", "precio"])
    unidades_col = _buscar_columna(df, ["unidades", "cantidad"])
    pvp_col = _buscar_columna(df, PVP_COLUMN_OPTIONS)
    pva_col = _buscar_columna(df, PVA_COLUMN_OPTIONS)

    iva = _serie_numerica(df, iva_col)
    bruto = _serie_numerica(df, bruto_col)
    neto = _serie_numerica(df, neto_col)
    unidades = _serie_numerica(df, unidades_col)
    unidades_validas = unidades.where(unidades > 0)

    df["bruto_unitario"] = (bruto / unidades_validas).fillna(0.0)
    df["neto_unitario"] = (neto / unidades_validas).fillna(0.0)

    mask_iva4 = _iva_es_cuatro(iva)
    mask_pvp = pd.Series([False] * len(df), index=df.index)
    mask_pva = pd.Series([False] * len(df), index=df.index)
    pvp_fiable = pd.Series([False] * len(df), index=df.index)

    if pvp_col:
        pvp = _serie_numerica(df, pvp_col)
        pvp_sin_iva = pvp / 1.04 if _pvp_incluye_iva(pvp_col) else pvp
        pvp_fiable = pvp_sin_iva > 0
        mask_pvp = pvp_sin_iva >= UMBRAL_PVP_SIN_IVA_ESPECIALIDAD_CARA

    if pva_col:
        pva_unitario = _serie_numerica(df, pva_col)
    else:
        pva_unitario = df["bruto_unitario"]
    mask_pva = (~pvp_fiable) & (pva_unitario >= UMBRAL_PVA_ESPECIALIDAD_CARA)

    df["es_especialidad_cara"] = mask_iva4 & (mask_pvp | mask_pva)
    df["tipo_especialidad_cara"] = ""
    df.loc[df["es_especialidad_cara"] & mask_pvp, "tipo_especialidad_cara"] = "pvp_sin_iva"
    df.loc[
        df["es_especialidad_cara"] & ~mask_pvp & mask_pva,
        "tipo_especialidad_cara",
    ] = "pva_bruto_unitario"

    descuento = bruto - neto
    df["descuento_especialidad_cara_euros"] = descuento.where(df["es_especialidad_cara"], 0.0).fillna(0.0)

    base_iva4 = neto.where(mask_iva4, 0.0).fillna(0.0)
    base_iva4_cara = base_iva4.where(df["es_especialidad_cara"], 0.0).fillna(0.0)
    df["base_iva4_total"] = base_iva4
    df["base_iva4_especialidad_cara"] = base_iva4_cara
    df["base_iva4_sujeta_ajuste"] = base_iva4 - base_iva4_cara

    return df


