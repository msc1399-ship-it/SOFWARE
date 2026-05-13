import re
import unicodedata

import pandas as pd


HOJA_BASE_IMPORTACION = "BASE_IMPORTACION"
HOJA_GRUPOS_HOMOGENEOS = "GRUPOS_HOMOGENEOS"
HOJA_OPCIONES_POR_GRUPO = "OPCIONES_POR_GRUPO"

COLUMNAS_BASE = [
    "cn",
    "nombre",
    "tipo_farmaco",
    "nombre_generico_efecto_accesorio",
    "codigo_laboratorio",
    "laboratorio",
    "estado",
    "principio_activo",
    "pvp_iva",
    "precio_referencia",
    "menor_precio_agrupacion",
    "grupo_homogeneo",
    "nombre_grupo_homogeneo",
    "es_efg_detectado",
    "tiene_grupo_homogeneo",
    "hay_efg_en_grupo",
    "es_marca_con_efg_alternativo",
    "num_efg_grupo",
    "num_marcas_grupo",
    "num_laboratorios_efg",
    "laboratorios_efg_disponibles",
    "laboratorios_disponibles_grupo",
    "diagnostico_hospitalario",
    "tratamiento_larga_duracion",
    "especial_control_medico",
    "medicamento_huerfano",
]

COLUMNAS_GRUPOS = [
    "grupo_homogeneo",
    "nombre_grupo_homogeneo",
    "principio_activo_referencia",
    "num_productos_grupo",
    "num_efg_grupo",
    "num_marcas_grupo",
    "hay_efg_en_grupo",
    "num_laboratorios_efg",
    "laboratorios_efg_disponibles",
    "laboratorios_disponibles_grupo",
    "pvp_minimo_grupo",
    "precio_referencia_minimo_grupo",
    "nota",
]

COLUMNAS_OPCIONES = [
    "grupo_homogeneo",
    "nombre_grupo_homogeneo",
    "cn",
    "nombre",
    "laboratorio",
    "es_efg_detectado",
    "pvp_iva",
    "precio_referencia",
    "menor_precio_agrupacion",
    "principio_activo",
    "estado",
    "uso",
]


def _normalizar_columna(columna):
    texto = str(columna).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return re.sub(r"_+", "_", texto).strip("_")


def _normalizar_cn(valor):
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]
    cn = re.sub(r"\D", "", texto)
    return cn or None


def _normalizar_booleano(valor):
    texto = str(valor).strip().upper()
    if texto in {"SI", "SÍ", "TRUE", "1", "YES"}:
        return True
    if texto in {"NO", "FALSE", "0", "NAN", "NONE", ""}:
        return False
    return False


def _serie_numerica(df, columna):
    if columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    texto = (
        df[columna]
        .astype(str)
        .str.replace("€", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.strip()
    )
    texto = texto.str.replace(r"\.(?=\d{3}(?:\D|$))", "", regex=True)
    texto = texto.str.replace(",", ".", regex=False)
    return pd.to_numeric(texto, errors="coerce")


def _leer_hoja(file, hoja):
    if hasattr(file, "seek"):
        file.seek(0)
    try:
        df = pd.read_excel(file, sheet_name=hoja)
    except ValueError as exc:
        raise ValueError(f"No se ha encontrado la hoja obligatoria {hoja}.") from exc
    df = df.dropna(how="all").copy()
    df.columns = [_normalizar_columna(col) for col in df.columns]
    return df


def _asegurar_columnas(df, columnas):
    for columna in columnas:
        if columna not in df.columns:
            df[columna] = None
    return df[columnas].copy()


def _normalizar_tabla_base(df):
    if "recomendacion_laboratorio_dinamica" in df.columns:
        df = df.drop(columns=["recomendacion_laboratorio_dinamica"])

    df = _asegurar_columnas(df, COLUMNAS_BASE)
    df["cn"] = df["cn"].apply(_normalizar_cn)
    df["grupo_homogeneo"] = df["grupo_homogeneo"].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA})

    for columna in [
        "es_efg_detectado",
        "tiene_grupo_homogeneo",
        "hay_efg_en_grupo",
        "es_marca_con_efg_alternativo",
        "diagnostico_hospitalario",
        "tratamiento_larga_duracion",
        "especial_control_medico",
        "medicamento_huerfano",
    ]:
        df[columna] = df[columna].apply(_normalizar_booleano)

    for columna in [
        "pvp_iva",
        "precio_referencia",
        "menor_precio_agrupacion",
        "num_efg_grupo",
        "num_marcas_grupo",
        "num_laboratorios_efg",
    ]:
        df[columna] = _serie_numerica(df, columna)

    df["fuente_recomendacion"] = "dinamica_con_datos_farmacia"
    return df.dropna(subset=["cn"]).reset_index(drop=True)


def _normalizar_grupos(df):
    df = _asegurar_columnas(df, COLUMNAS_GRUPOS)
    df["grupo_homogeneo"] = df["grupo_homogeneo"].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA})
    df["hay_efg_en_grupo"] = df["hay_efg_en_grupo"].apply(_normalizar_booleano)
    for columna in [
        "num_productos_grupo",
        "num_efg_grupo",
        "num_marcas_grupo",
        "num_laboratorios_efg",
        "pvp_minimo_grupo",
        "precio_referencia_minimo_grupo",
    ]:
        df[columna] = _serie_numerica(df, columna)
    return df.dropna(subset=["grupo_homogeneo"]).reset_index(drop=True)


def _normalizar_opciones(df):
    df = _asegurar_columnas(df, COLUMNAS_OPCIONES)
    df["cn"] = df["cn"].apply(_normalizar_cn)
    df["grupo_homogeneo"] = df["grupo_homogeneo"].astype(str).str.strip().replace({"": pd.NA, "nan": pd.NA})
    df["es_efg_detectado"] = df["es_efg_detectado"].apply(_normalizar_booleano)
    for columna in ["pvp_iva", "precio_referencia", "menor_precio_agrupacion"]:
        df[columna] = _serie_numerica(df, columna)
    return df.dropna(subset=["cn", "grupo_homogeneo"]).reset_index(drop=True)


def leer_base_equivalencias_efg(file):
    base = _normalizar_tabla_base(_leer_hoja(file, HOJA_BASE_IMPORTACION))
    grupos = _normalizar_grupos(_leer_hoja(file, HOJA_GRUPOS_HOMOGENEOS))
    opciones = _normalizar_opciones(_leer_hoja(file, HOJA_OPCIONES_POR_GRUPO))

    return {
        "tabla_equivalencias_efg": base,
        "grupos_homogeneos": grupos,
        "opciones_por_grupo": opciones,
        "resumen": resumen_equivalencias_efg(base, grupos, opciones),
    }


def resumen_equivalencias_efg(base, grupos, opciones):
    return {
        "productos": len(base),
        "grupos_homogeneos": grupos["grupo_homogeneo"].nunique() if not grupos.empty else 0,
        "opciones_efg": int(opciones["es_efg_detectado"].sum()) if "es_efg_detectado" in opciones.columns else 0,
        "marcas_con_alternativa_efg": int(base["es_marca_con_efg_alternativo"].sum()) if "es_marca_con_efg_alternativo" in base.columns else 0,
        "laboratorios_efg_disponibles": int(
            grupos["num_laboratorios_efg"].fillna(0).max()
        ) if "num_laboratorios_efg" in grupos.columns and not grupos.empty else 0,
    }


def obtener_opciones_efg_para_cn(cn, tabla_equivalencias_efg, opciones_por_grupo):
    cn_normalizado = _normalizar_cn(cn)
    if not cn_normalizado:
        return pd.DataFrame()

    base = tabla_equivalencias_efg.copy()
    opciones = opciones_por_grupo.copy()
    fila = base[base["cn"] == cn_normalizado]
    if fila.empty:
        return pd.DataFrame()

    grupo = fila.iloc[0].get("grupo_homogeneo")
    if pd.isna(grupo):
        return pd.DataFrame()

    opciones_grupo = opciones[
        (opciones["grupo_homogeneo"].astype(str) == str(grupo))
        & (opciones["es_efg_detectado"].fillna(False).astype(bool))
    ].copy()
    opciones_grupo["laboratorio_recomendado_dinamicamente"] = None
    opciones_grupo["criterio_recomendacion"] = "pendiente_motor_economico_farmacia"
    return opciones_grupo.reset_index(drop=True)


def preparar_oportunidades_efg_placeholder(df_ventas, tabla_equivalencias_efg, opciones_por_grupo, limite=5):
    if df_ventas is None or df_ventas.empty:
        return pd.DataFrame()

    ventas = df_ventas.copy()
    if "cn" not in ventas.columns:
        return pd.DataFrame()

    base = tabla_equivalencias_efg.copy()
    marcas = base[base["es_marca_con_efg_alternativo"].fillna(False).astype(bool)]
    ventas["cn"] = ventas["cn"].apply(_normalizar_cn)
    candidatas = ventas.merge(
        marcas[["cn", "grupo_homogeneo", "nombre", "laboratorio"]],
        on="cn",
        how="inner",
        suffixes=("", "_maestro_efg"),
    )
    if candidatas.empty:
        return pd.DataFrame()

    if "unidades_vendidas" in candidatas.columns:
        candidatas["unidades_vendidas"] = pd.to_numeric(candidatas["unidades_vendidas"], errors="coerce").fillna(0)
        candidatas = candidatas.sort_values("unidades_vendidas", ascending=False)

    salida = candidatas.head(limite).copy()
    salida["mejor_alternativa_efg_calculada"] = None
    salida["laboratorio_efg_recomendado_dinamicamente"] = None
    salida["margen_potencial"] = None
    salida["perdida_mensual_estimada"] = None
    salida["perdida_anual_estimada"] = None
    return salida.reset_index(drop=True)
