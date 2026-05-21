import re
import unicodedata

import pandas as pd


PVP_COLUMN_OPTIONS = [
    "pvp",
    "pvp iva",
    "pvp_iva",
    "pvp con iva",
    "precio venta publico",
    "precio_venta_publico",
    "precio venta publico iva",
]

CN_COLUMN_OPTIONS = ["cn", "codigo nacional", "codigo_nacional", "cod nacional", "codigo", "cod"]
FINANCIACION_COLUMN_OPTIONS = ["financiado", "financiacion", "financiación", "financiada", "fin"]
LABORATORIO_COLUMN_OPTIONS = ["laboratorio", "lab", "titular", "fabricante"]
FAMILIA_COLUMN_OPTIONS = ["familia", "categoria", "categoría", "tipo producto", "tipo_producto"]
DESCRIPCION_COLUMN_OPTIONS = ["descripcion", "descripción", "producto", "articulo", "artículo", "nombre"]


def _normalizar_texto(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _buscar_columna(columnas, opciones):
    columnas_norm = {_normalizar_texto(col): col for col in columnas}
    opciones_norm = [_normalizar_texto(opcion) for opcion in opciones]

    for opcion in opciones_norm:
        if opcion in columnas_norm:
            return columnas_norm[opcion]

    for opcion in opciones_norm:
        for col_norm, col_original in columnas_norm.items():
            if opcion and opcion in col_norm:
                return col_original
    return None


def _buscar_columnas(columnas, opciones):
    columnas_norm = {_normalizar_texto(col): col for col in columnas}
    opciones_norm = [_normalizar_texto(opcion) for opcion in opciones]
    encontradas = []

    for col_norm, col_original in columnas_norm.items():
        if col_norm in opciones_norm or any(opcion and opcion in col_norm for opcion in opciones_norm):
            encontradas.append(col_original)
    return encontradas


def _normalizar_cn(valor):
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]
    cn = re.sub(r"\D", "", texto)
    return cn or None


def _serie_numerica(df, columna):
    if df is None or columna is None or columna not in df.columns:
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


def _iva_es_diez(iva):
    iva_norm = iva.copy()
    iva_norm = iva_norm.where(iva_norm.abs() > 1, iva_norm * 100)
    return (iva_norm - 10).abs() <= 0.01


def _valor_financiado(valor):
    texto = _normalizar_texto(valor)
    if not texto or texto in {"nan", "none", "0", "no", "n", "false", "falso"}:
        return False
    if any(token in texto for token in ["financi", "si", "s", "sns", "incluido", "aportacion"]):
        return True
    return False


def normalizar_columnas_nomenclator(df_nomenclator):
    if df_nomenclator is None or df_nomenclator.empty:
        return pd.DataFrame(columns=["cn", "financiado", "laboratorio", "familia", "descripcion"])

    df = df_nomenclator.copy()
    df.columns = [str(col).strip() for col in df.columns]

    col_cn = _buscar_columna(df.columns, CN_COLUMN_OPTIONS)
    if not col_cn:
        raise ValueError("El nomenclátor no contiene una columna reconocible de código nacional/CN.")

    col_financiacion = _buscar_columna(df.columns, FINANCIACION_COLUMN_OPTIONS)
    col_laboratorio = _buscar_columna(df.columns, LABORATORIO_COLUMN_OPTIONS)
    col_familia = _buscar_columna(df.columns, FAMILIA_COLUMN_OPTIONS)
    col_descripcion = _buscar_columna(df.columns, DESCRIPCION_COLUMN_OPTIONS)

    normalizado = pd.DataFrame()
    normalizado["cn"] = df[col_cn].apply(_normalizar_cn)
    if col_financiacion:
        normalizado["financiado"] = df[col_financiacion].apply(_valor_financiado)
    else:
        normalizado["financiado"] = True
    normalizado["laboratorio_nomenclator"] = df[col_laboratorio].astype(str).str.strip() if col_laboratorio else ""
    normalizado["familia_nomenclator"] = df[col_familia].astype(str).str.strip() if col_familia else ""
    normalizado["descripcion_nomenclator"] = df[col_descripcion].astype(str).str.strip() if col_descripcion else ""

    normalizado = normalizado.dropna(subset=["cn"])
    normalizado = normalizado[normalizado["cn"].astype(str).str.len() > 0]
    return normalizado.drop_duplicates(subset=["cn"], keep="last").reset_index(drop=True)


def detectar_parafarmacia_financiada(df_compras, df_nomenclator=None):
    if df_compras is None:
        return df_compras

    df = df_compras.copy()
    iva_col = _buscar_columna(df.columns, ["iva", "tipo iva"])
    bruto_col = _buscar_columna(df.columns, ["bruto", "importe bruto", "precio bruto"])
    neto_col = _buscar_columna(df.columns, ["neto", "importe neto", "precio"])
    unidades_col = _buscar_columna(df.columns, ["unidades", "cantidad"])
    pvp_cols = _buscar_columnas(df.columns, PVP_COLUMN_OPTIONS)

    iva = _serie_numerica(df, iva_col)
    bruto = _serie_numerica(df, bruto_col)
    neto = _serie_numerica(df, neto_col)
    unidades = _serie_numerica(df, unidades_col)
    mask_iva10 = _iva_es_diez(iva)

    if "seccion_albaran" in df.columns:
        seccion = df["seccion_albaran"].astype(str).str.lower().str.strip()
        mask_parafarmacia = seccion.eq("parafarmacia")
    else:
        mask_parafarmacia = pd.Series([True] * len(df), index=df.index)

    especialidad_cara = df.get(
        "es_especialidad_cara",
        pd.Series(False, index=df.index),
    ).fillna(False).astype(bool)

    cn = df.get("cn", pd.Series("", index=df.index)).apply(_normalizar_cn)
    df["cn"] = cn.where(cn.notna(), df.get("cn", pd.Series("", index=df.index)))

    mask_nomenclator = pd.Series(False, index=df.index)
    sin_nomenclator = df_nomenclator is None or df_nomenclator.empty

    if not sin_nomenclator:
        nomenclator = normalizar_columnas_nomenclator(df_nomenclator)
        financiados = set(nomenclator.loc[nomenclator["financiado"], "cn"].dropna().astype(str))
        mask_nomenclator = cn.astype(str).isin(financiados)
        extras = nomenclator[["cn", "laboratorio_nomenclator", "familia_nomenclator", "descripcion_nomenclator"]]
        columnas_extra = [col for col in extras.columns if col != "cn"]
        columnas_a_limpiar = [
            col for col in df.columns
            if col in columnas_extra or any(col == f"{base}_{sufijo}" for base in columnas_extra for sufijo in ["x", "y"])
        ]
        if columnas_a_limpiar:
            df = df.drop(columns=columnas_a_limpiar)
        df = df.merge(extras, on="cn", how="left")

    if pvp_cols:
        pvp_numerico = pd.concat([_serie_numerica(df, col) for col in pvp_cols], axis=1).max(axis=1)
    else:
        pvp_numerico = pd.Series([0.0] * len(df), index=df.index)
    mask_pvp_real = pvp_numerico.gt(0)
    texto_pvp = pd.Series("", index=df.index)
    for col in pvp_cols:
        texto_pvp = (texto_pvp + " " + df[col].astype(str).map(_normalizar_texto)).str.strip()
    mask_neto = texto_pvp.str.contains(r"\bneto\b", na=False)

    mask_base = mask_iva10 & mask_parafarmacia & ~especialidad_cara
    mask_financiada_nomenclator = mask_base & mask_nomenclator
    mask_financiada_pvp = mask_base & ~mask_nomenclator & mask_pvp_real & ~mask_neto
    mask_financiada = mask_financiada_nomenclator | mask_financiada_pvp

    fuente = pd.Series("no_detectada", index=df.index, dtype="object")
    fuente[mask_financiada_nomenclator] = "nomenclator"
    fuente[mask_financiada_pvp] = "pvp_iva"

    df["es_parafarmacia_financiada"] = mask_financiada.fillna(False).astype(bool)
    df["tipo_parafarmacia"] = "no_aplica"
    df.loc[mask_parafarmacia & ~df["es_parafarmacia_financiada"], "tipo_parafarmacia"] = "no_financiada"
    df.loc[df["es_parafarmacia_financiada"], "tipo_parafarmacia"] = "financiada"
    df["fuente_deteccion_parafarmacia_financiada"] = fuente

    descuento = bruto - neto
    df["bruto_parafarmacia_financiada"] = bruto.where(df["es_parafarmacia_financiada"], 0.0).fillna(0.0)
    df["neto_parafarmacia_financiada"] = neto.where(df["es_parafarmacia_financiada"], 0.0).fillna(0.0)
    df["descuento_parafarmacia_financiada_euros"] = descuento.where(df["es_parafarmacia_financiada"], 0.0).fillna(0.0)

    return calcular_bases_parafarmacia(df)


def calcular_bases_parafarmacia(df):
    if df is None:
        return df
    df = df.copy()
    iva_col = _buscar_columna(df.columns, ["iva", "tipo iva"])
    neto_col = _buscar_columna(df.columns, ["neto", "importe neto", "precio"])
    neto = _serie_numerica(df, neto_col)
    iva = _serie_numerica(df, iva_col)

    if "seccion_albaran" in df.columns:
        seccion = df["seccion_albaran"].astype(str).str.lower().str.strip()
        mask_parafarmacia = seccion.eq("parafarmacia")
    else:
        mask_parafarmacia = _iva_es_diez(iva) | ((iva - 21).abs() <= 0.01)

    mask_financiada = df.get(
        "es_parafarmacia_financiada",
        pd.Series(False, index=df.index),
    ).fillna(False).astype(bool)

    base_total = neto.where(mask_parafarmacia, 0.0).fillna(0.0)
    base_financiada = neto.where(mask_financiada, 0.0).fillna(0.0)
    base_no_financiada = neto.where(mask_parafarmacia & ~mask_financiada, 0.0).fillna(0.0)

    df["base_parafarmacia_total"] = base_total
    df["base_parafarmacia_financiada"] = base_financiada
    df["base_parafarmacia_no_financiada"] = base_no_financiada
    df["base_parafarmacia_sujeta_condiciones"] = base_total - base_financiada
    return df


def excluir_parafarmacia_financiada_de_condiciones(df):
    if df is None:
        return df
    df = df.copy()
    if "es_parafarmacia_financiada" not in df.columns:
        df = detectar_parafarmacia_financiada(df)
    return df[~df["es_parafarmacia_financiada"].fillna(False).astype(bool)].copy()


def calcular_resumen_parafarmacia_financiada(df):
    if df is None or df.empty or "es_parafarmacia_financiada" not in df.columns:
        financiada = pd.DataFrame()
        trabajo = pd.DataFrame() if df is None else df.copy()
    else:
        trabajo = df.copy()
        financiada = trabajo[trabajo["es_parafarmacia_financiada"].fillna(False).astype(bool)].copy()

    bruto_total_compra = float(_serie_numerica(trabajo, "bruto").sum()) if not trabajo.empty else 0.0
    para_total = float(_serie_numerica(trabajo, "base_parafarmacia_total").sum()) if "base_parafarmacia_total" in trabajo.columns else 0.0
    bruto = float(_serie_numerica(financiada, "bruto").sum()) if not financiada.empty else 0.0
    neto = float(_serie_numerica(financiada, "neto").sum()) if not financiada.empty else 0.0
    unidades = float(_serie_numerica(financiada, "unidades").sum()) if not financiada.empty else 0.0
    descuento = float(_serie_numerica(financiada, "descuento_parafarmacia_financiada_euros").sum()) if not financiada.empty else 0.0

    resumen = {
        "lineas_detectadas": int(len(financiada)),
        "unidades": round(unidades, 2),
        "bruto_total": round(bruto, 2),
        "neto_total": round(neto, 2),
        "descuento_total_euros": round(descuento, 2),
        "descuento_medio_euros": round(descuento / unidades, 2) if unidades else 0.0,
        "descuento_medio_pct": round((descuento / bruto * 100), 2) if bruto else 0.0,
        "porcentaje_sobre_compra_total": round((bruto / bruto_total_compra * 100), 2) if bruto_total_compra else 0.0,
        "porcentaje_sobre_parafarmacia_total": round((neto / para_total * 100), 2) if para_total else 0.0,
        "base_parafarmacia_total": round(float(_serie_numerica(trabajo, "base_parafarmacia_total").sum()), 2) if not trabajo.empty else 0.0,
        "base_parafarmacia_financiada": round(float(_serie_numerica(trabajo, "base_parafarmacia_financiada").sum()), 2) if not trabajo.empty else 0.0,
        "base_parafarmacia_no_financiada": round(float(_serie_numerica(trabajo, "base_parafarmacia_no_financiada").sum()), 2) if not trabajo.empty else 0.0,
        "base_parafarmacia_sujeta_condiciones": round(float(_serie_numerica(trabajo, "base_parafarmacia_sujeta_condiciones").sum()), 2) if not trabajo.empty else 0.0,
    }

    top_laboratorios = pd.DataFrame()
    top_cn = pd.DataFrame()
    if not financiada.empty:
        laboratorio_col = next(
            (col for col in ["laboratorio_maestro", "laboratorio", "laboratorio_nomenclator"] if col in financiada.columns),
            None,
        )
        if laboratorio_col:
            top_laboratorios = (
                financiada.assign(bruto_num=_serie_numerica(financiada, "bruto"))
                .groupby(laboratorio_col, dropna=False)["bruto_num"]
                .sum()
                .sort_values(ascending=False)
                .head(10)
                .reset_index()
                .rename(columns={laboratorio_col: "laboratorio", "bruto_num": "bruto_total"})
            )
        columnas_cn = [col for col in ["cn", "descripcion", "familia_nomenclator", "laboratorio_nomenclator"] if col in financiada.columns]
        top_cn = financiada.copy()
        top_cn["bruto_num"] = _serie_numerica(top_cn, "bruto")
        top_cn["neto_num"] = _serie_numerica(top_cn, "neto")
        top_cn["unidades_num"] = _serie_numerica(top_cn, "unidades")
        top_cn = (
            top_cn.groupby(columnas_cn, dropna=False)[["bruto_num", "neto_num", "unidades_num"]]
            .sum()
            .sort_values("bruto_num", ascending=False)
            .head(10)
            .reset_index()
            .rename(columns={"bruto_num": "bruto_total", "neto_num": "neto_total", "unidades_num": "unidades"})
        )

    return {
        "resumen": resumen,
        "top_laboratorios": top_laboratorios,
        "top_cn": top_cn,
        "detalle": financiada.reset_index(drop=True),
    }
