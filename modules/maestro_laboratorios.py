import re

import pandas as pd


COLUMNAS_CN = [
    "cn",
    "codigo nacional",
    "codigo_nacional",
    "cod_nacional",
    "codigo",
]

COLUMNAS_LABORATORIO = [
    "laboratorio",
    "lab",
    "nombre laboratorio",
    "titular",
]

COLUMNAS_DESCRIPCION = [
    "descripcion",
    "descripción",
    "producto",
    "articulo",
    "artículo",
]

COLUMNAS_TIPO = [
    "tipo",
    "tipo_producto",
    "categoria",
    "categoría",
]


def normalizar_cn(valor):
    if pd.isna(valor):
        return None

    texto_original = str(valor).strip()
    if re.match(r"^\d+\.0$", texto_original):
        texto_original = texto_original[:-2]
    texto = re.sub(r"\D", "", texto_original)
    return texto or None


def _normalizar_columnas(df):
    resultado = df.copy()
    resultado.columns = [str(col).strip().lower() for col in resultado.columns]
    return resultado


def _buscar_columna(columnas, candidatas):
    for candidata in candidatas:
        if candidata in columnas:
            return candidata
    return None


def leer_maestro_laboratorios(file):
    nombre = str(getattr(file, "name", "")).lower()

    if hasattr(file, "seek"):
        file.seek(0)

    if nombre.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df = _normalizar_columnas(df)

    col_cn = _buscar_columna(df.columns, COLUMNAS_CN)
    col_laboratorio = _buscar_columna(df.columns, COLUMNAS_LABORATORIO)
    col_descripcion = _buscar_columna(df.columns, COLUMNAS_DESCRIPCION)
    col_tipo = _buscar_columna(df.columns, COLUMNAS_TIPO)

    if not col_cn or not col_laboratorio:
        raise ValueError(
            "El fichero maestro debe incluir al menos una columna de código nacional y otra de laboratorio."
        )

    resultado = pd.DataFrame()
    resultado["cn"] = df[col_cn].apply(normalizar_cn)
    resultado["laboratorio_maestro"] = df[col_laboratorio].astype(str).str.strip()
    resultado["descripcion_maestra"] = (
        df[col_descripcion].astype(str).str.strip() if col_descripcion else None
    )
    if col_tipo:
        resultado["tipo_producto"] = df[col_tipo].astype(str).str.strip()

    resultado = resultado.dropna(subset=["cn", "laboratorio_maestro"])
    resultado = resultado[resultado["cn"].str.len() > 0]
    resultado = resultado[resultado["laboratorio_maestro"] != ""]
    resultado = resultado.drop_duplicates(subset=["cn"], keep="first").reset_index(drop=True)

    return resultado


def enriquecer_con_laboratorio(df, maestro_df):
    if df is None or df.empty or maestro_df is None or maestro_df.empty or "cn" not in df.columns:
        return df

    resultado = df.copy()
    resultado["cn"] = resultado["cn"].apply(normalizar_cn)

    columnas_merge = ["cn", "laboratorio_maestro"]
    if "descripcion_maestra" in maestro_df.columns:
        columnas_merge.append("descripcion_maestra")
    if "tipo_producto" in maestro_df.columns:
        columnas_merge.append("tipo_producto")
    if "fuente_maestro" in maestro_df.columns:
        columnas_merge.append("fuente_maestro")

    columnas_existentes = [col for col in columnas_merge if col in resultado.columns and col != "cn"]
    if columnas_existentes:
        resultado = resultado.drop(columns=columnas_existentes)

    maestro_lookup = (
        maestro_df[columnas_merge]
        .dropna(subset=["cn"])
        .drop_duplicates(subset=["cn"], keep="first")
        .set_index("cn")
    )
    resultado = resultado.join(maestro_lookup, on="cn")

    return resultado
