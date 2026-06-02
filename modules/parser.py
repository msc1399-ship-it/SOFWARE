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


def normalize_columns(df):

    mapping = {}

    for standard, options in COLUMN_MAPPING.items():

        for col in df.columns:

            if col.lower() in options:

                mapping[col] = standard

    df = df.rename(columns=mapping)

    return df

# ==========================
# DETECTOR DE SECCIONES
# ==========================

def parse_sections(df):
    if df is None or df.empty:
        return df

    resultado = df.copy()
    descripcion = resultado.get("descripcion", pd.Series("", index=resultado.index)).astype(str).str.lower()
    iva = pd.to_numeric(resultado.get("iva", pd.Series(pd.NA, index=resultado.index)), errors="coerce")

    secciones = pd.Series("desconocido", index=resultado.index, dtype="object")
    secciones[iva.eq(4)] = "especialidad"
    secciones[iva.isin([10, 21])] = "parafarmacia"
    secciones[descripcion.str.contains("club", na=False)] = "club"
    secciones[descripcion.str.contains("avantia", na=False)] = "avantia"
    secciones[descripcion.str.contains("bitransfer", na=False)] = "bitransfer"

    resultado["seccion_albaran"] = secciones
    return resultado
