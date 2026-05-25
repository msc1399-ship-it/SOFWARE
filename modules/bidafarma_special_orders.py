import re
import unicodedata

import pandas as pd


TIPOS_PEDIDO_ESPECIAL = {
    "SX": {
        "categoria": "pedido_especial_dia_farmaceutico",
        "descripcion": "Día del Farmacéutico",
        "descuento_especialidad": 7.0,
        "cargo_parafarmacia": 3.0,
    },
    "SQ": {
        "categoria": "especialidad_cara_con_descuento",
        "descripcion": "Especialidad cara con descuento",
    },
}


def _df_seguro(df):
    if df is None:
        return pd.DataFrame()
    return df.copy()


def _normalizar_texto(valor):
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", " ", texto.upper()).strip()


def _serie_numerica(df, columna):
    if df is None or df.empty or columna not in df.columns:
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


def _iva_normalizada(iva):
    iva_norm = iva.copy()
    return iva_norm.where(iva_norm.abs() > 1, iva_norm * 100)


def _buscar_columnas_tipo(df):
    candidatas = []
    tokens = [
        "tipo_albaran",
        "tipo",
        "clase",
        "codigo_albaran",
        "observaciones",
        "observacion",
        "seccion",
        "seccion_albaran",
        "descripcion_cabecera",
        "tarifa",
    ]
    for columna in df.columns:
        nombre = _normalizar_texto(columna).replace(" ", "_").lower()
        if any(token in nombre for token in tokens):
            candidatas.append(columna)
    return candidatas


def _extraer_codigo_tipo(valor):
    texto = _normalizar_texto(valor)
    if not texto or texto in {"NAN", "NONE"}:
        return None
    tokens = texto.split()
    for codigo in ["SX", "SQ", "ZV"]:
        if codigo in tokens or texto == codigo:
            return codigo
    return None


def detectar_tipo_albaran_bidafarma(df):
    df = _df_seguro(df)
    if df.empty:
        return pd.Series(dtype="object")

    resultado = pd.Series([None] * len(df), index=df.index, dtype="object")
    for columna in _buscar_columnas_tipo(df):
        codigos = df[columna].map(_extraer_codigo_tipo)
        resultado = resultado.where(resultado.notna(), codigos)
    return resultado


def calcular_condicion_real_pedido_especial(df):
    df = _df_seguro(df)
    if df.empty:
        return pd.Series(dtype="float64")
    bruto = _serie_numerica(df, "bruto")
    neto = _serie_numerica(df, "neto")
    condicion = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    mask = bruto.abs().gt(0.0001)
    condicion.loc[mask] = ((bruto.loc[mask] - neto.loc[mask]) / bruto.loc[mask] * 100).round(2)
    return condicion


def aplicar_reglas_sx(df):
    df = _df_seguro(df)
    if df.empty:
        return df

    mask_sx = df.get("tipo_albaran_bidafarma", pd.Series("", index=df.index)).eq("SX")
    if not mask_sx.any():
        return df

    bruto = _serie_numerica(df, "bruto")
    neto = _serie_numerica(df, "neto")
    iva = _iva_normalizada(_serie_numerica(df, "iva"))
    mask_especialidad = mask_sx & iva.le(4.01)
    mask_parafarmacia = mask_sx & ~mask_especialidad

    descuento_real = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    cargo_real = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
    mask_bruto = bruto.abs().gt(0.0001)
    descuento_real.loc[mask_bruto] = ((bruto.loc[mask_bruto] - neto.loc[mask_bruto]) / bruto.loc[mask_bruto] * 100)
    cargo_real.loc[mask_bruto] = ((neto.loc[mask_bruto] - bruto.loc[mask_bruto]) / bruto.loc[mask_bruto] * 100)

    df.loc[mask_especialidad, "descuento_esperado_pedido_especial"] = TIPOS_PEDIDO_ESPECIAL["SX"]["descuento_especialidad"]
    df.loc[mask_parafarmacia, "cargo_esperado_pedido_especial"] = TIPOS_PEDIDO_ESPECIAL["SX"]["cargo_parafarmacia"]
    df.loc[mask_especialidad, "diferencia_condicion_pedido_especial"] = (
        descuento_real.loc[mask_especialidad] - TIPOS_PEDIDO_ESPECIAL["SX"]["descuento_especialidad"]
    ).round(2)
    df.loc[mask_parafarmacia, "diferencia_condicion_pedido_especial"] = (
        cargo_real.loc[mask_parafarmacia].clip(lower=0) - TIPOS_PEDIDO_ESPECIAL["SX"]["cargo_parafarmacia"]
    ).round(2)
    return df


def aplicar_reglas_sq(df):
    df = _df_seguro(df)
    if df.empty:
        return df

    mask_sq = df.get("tipo_albaran_bidafarma", pd.Series("", index=df.index)).eq("SQ")
    if not mask_sq.any():
        return df

    bruto = _serie_numerica(df, "bruto")
    neto = _serie_numerica(df, "neto")
    unidades = _serie_numerica(df, "unidades").where(_serie_numerica(df, "unidades").gt(0))
    iva = _iva_normalizada(_serie_numerica(df, "iva"))
    bruto_unitario = bruto / unidades
    mask_cara_sq = mask_sq & iva.sub(4).abs().le(0.01) & (
        df.get("es_especialidad_cara", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        | bruto_unitario.fillna(0).ge(98.59)
    )

    df.loc[mask_cara_sq, "es_especialidad_cara"] = True
    df.loc[mask_cara_sq, "tipo_especialidad_cara"] = "SQ_especialidad_cara_con_descuento"
    df.loc[mask_cara_sq, "descuento_especialidad_cara_euros"] = (bruto.loc[mask_cara_sq] - neto.loc[mask_cara_sq]).round(2)

    if "base_iva4_especialidad_cara" in df.columns:
        df.loc[mask_cara_sq, "base_iva4_especialidad_cara"] = neto.loc[mask_cara_sq]
    if "base_iva4_sujeta_ajuste" in df.columns:
        base_iva4_total = _serie_numerica(df, "base_iva4_total")
        base_iva4_cara = _serie_numerica(df, "base_iva4_especialidad_cara")
        df["base_iva4_sujeta_ajuste"] = base_iva4_total - base_iva4_cara
    return df


def clasificar_pedidos_especiales_bidafarma(df):
    df = _df_seguro(df)
    if df.empty:
        return df

    tipo = detectar_tipo_albaran_bidafarma(df)
    if "tipo_albaran_bidafarma" in df.columns:
        tipo = df["tipo_albaran_bidafarma"].where(df["tipo_albaran_bidafarma"].notna(), tipo)
    df["tipo_albaran_bidafarma"] = tipo
    df["es_pedido_especial_bidafarma"] = df["tipo_albaran_bidafarma"].isin(["SX", "SQ"])
    df["categoria_pedido_especial_bidafarma"] = "no_aplica"
    for codigo, config in TIPOS_PEDIDO_ESPECIAL.items():
        df.loc[df["tipo_albaran_bidafarma"].eq(codigo), "categoria_pedido_especial_bidafarma"] = config["categoria"]

    for columna in [
        "descuento_esperado_pedido_especial",
        "cargo_esperado_pedido_especial",
        "diferencia_condicion_pedido_especial",
    ]:
        if columna not in df.columns:
            df[columna] = 0.0

    df = aplicar_reglas_sx(df)
    df = aplicar_reglas_sq(df)

    neto = _serie_numerica(df, "neto")
    mask_especial = df["es_pedido_especial_bidafarma"].fillna(False).astype(bool)
    df["base_pedidos_especiales_bidafarma"] = neto.where(mask_especial, 0.0).fillna(0.0)
    base_goteo = neto.where(df.get("tipo_compra", pd.Series("", index=df.index)).astype(str).str.lower().eq("goteo"), 0.0)
    df["base_goteo_sujeta_condiciones_bidafarma"] = base_goteo - df["base_pedidos_especiales_bidafarma"]
    return df


def _condicion_linea(row):
    bruto = float(row.get("bruto_num", 0) or 0)
    neto = float(row.get("neto_num", 0) or 0)
    if abs(bruto) <= 0.0001:
        return None
    return round((1 - (neto / bruto)) * 100, 2)


def _categoria_linea(row):
    if bool(row.get("es_parafarmacia_financiada", False)):
        return "parafarmacia_financiada"
    iva = float(row.get("iva_num", 0) or 0)
    return "especialidad" if iva <= 4.01 else "parafarmacia"


def encontrar_mejor_condicion_alternativa(linea, df_compras):
    df = _df_seguro(df_compras)
    if df.empty:
        return None

    trabajo = df.copy()
    trabajo["bruto_num"] = _serie_numerica(trabajo, "bruto")
    trabajo["neto_num"] = _serie_numerica(trabajo, "neto")
    trabajo["iva_num"] = _iva_normalizada(_serie_numerica(trabajo, "iva"))
    trabajo["condicion_pct"] = trabajo.apply(_condicion_linea, axis=1)
    trabajo["categoria_comparativa"] = trabajo.apply(_categoria_linea, axis=1)

    especiales = trabajo.get("es_pedido_especial_bidafarma", pd.Series(False, index=trabajo.index)).fillna(False).astype(bool)
    financiada = trabajo.get("es_parafarmacia_financiada", pd.Series(False, index=trabajo.index)).fillna(False).astype(bool)
    caras = trabajo.get("es_especialidad_cara", pd.Series(False, index=trabajo.index)).fillna(False).astype(bool)
    candidatos = trabajo[~especiales & ~financiada & ~caras & trabajo["bruto_num"].gt(0)].copy()
    candidatos = candidatos[pd.to_numeric(candidatos["condicion_pct"], errors="coerce").notna()].copy()
    if candidatos.empty:
        return None

    cn = str(linea.get("cn", "")).strip()
    laboratorio = str(linea.get("laboratorio_maestro", "") or linea.get("laboratorio", "")).strip()
    categoria = _categoria_linea(linea)

    grupos = [
        ("alta", candidatos[candidatos.get("cn", pd.Series("", index=candidatos.index)).astype(str).str.strip().eq(cn)], "mismo_cn"),
        (
            "media",
            candidatos[candidatos.get("laboratorio_maestro", pd.Series("", index=candidatos.index)).astype(str).str.strip().eq(laboratorio)]
            if laboratorio else pd.DataFrame(),
            "mismo_laboratorio",
        ),
        ("baja", candidatos[candidatos["categoria_comparativa"].eq(categoria)], "misma_categoria"),
    ]
    for confianza, grupo, motivo in grupos:
        if grupo is None or grupo.empty:
            continue
        mejor = grupo.sort_values("condicion_pct", ascending=False).iloc[0]
        via = mejor.get("tipo_compra") or mejor.get("bloque_analisis") or motivo
        return {
            "condicion": round(float(mejor["condicion_pct"]), 2),
            "via": str(via),
            "confianza": confianza,
            "motivo": motivo,
        }
    return None


def calcular_perdida_oportunidad_pedido_especial(df_especiales, df_compras):
    especiales = _df_seguro(df_especiales)
    if especiales.empty:
        return especiales

    especiales = especiales.copy()
    especiales["bruto_num"] = _serie_numerica(especiales, "bruto")
    especiales["neto_num"] = _serie_numerica(especiales, "neto")
    especiales["iva_num"] = _iva_normalizada(_serie_numerica(especiales, "iva"))
    especiales["condicion_real_pedido_especial"] = especiales.apply(_condicion_linea, axis=1)
    columnas_resultado = [
        "mejor_condicion_alternativa",
        "via_alternativa_recomendada",
        "diferencia_puntos_vs_alternativa",
        "perdida_oportunidad_pedido_especial",
        "pedido_especial_conveniente",
        "motivo_recomendacion_pedido_especial",
        "confianza_comparativa_pedido_especial",
    ]
    for columna in columnas_resultado:
        especiales[columna] = None

    for idx, linea in especiales.iterrows():
        if bool(linea.get("es_especialidad_cara", False)) and str(linea.get("tipo_albaran_bidafarma", "")) == "SQ":
            especiales.at[idx, "motivo_recomendacion_pedido_especial"] = "SQ integrado en especialidad cara; no se compara contra descuentos porcentuales normales."
            especiales.at[idx, "pedido_especial_conveniente"] = None
            continue
        if bool(linea.get("es_parafarmacia_financiada", False)):
            especiales.at[idx, "motivo_recomendacion_pedido_especial"] = "Parafarmacia financiada excluida salvo condición alternativa explícita."
            especiales.at[idx, "pedido_especial_conveniente"] = None
            continue
        alternativa = encontrar_mejor_condicion_alternativa(linea, df_compras)
        if not alternativa:
            especiales.at[idx, "motivo_recomendacion_pedido_especial"] = "Sin comparativa suficiente."
            especiales.at[idx, "pedido_especial_conveniente"] = None
            continue
        real = float(linea.get("condicion_real_pedido_especial") or 0)
        diferencia = round(float(alternativa["condicion"]) - real, 2)
        perdida = round(max(0.0, float(linea.get("bruto_num") or 0) * diferencia / 100), 2)
        especiales.at[idx, "mejor_condicion_alternativa"] = alternativa["condicion"]
        especiales.at[idx, "via_alternativa_recomendada"] = alternativa["via"]
        especiales.at[idx, "diferencia_puntos_vs_alternativa"] = diferencia
        especiales.at[idx, "perdida_oportunidad_pedido_especial"] = perdida
        especiales.at[idx, "pedido_especial_conveniente"] = diferencia <= 0
        especiales.at[idx, "confianza_comparativa_pedido_especial"] = alternativa["confianza"]
        if diferencia > 0:
            especiales.at[idx, "motivo_recomendacion_pedido_especial"] = (
                f"Condición alternativa {alternativa['via']} superior en {diferencia:.2f} puntos."
            )
        else:
            especiales.at[idx, "motivo_recomendacion_pedido_especial"] = "Pedido especial favorable o neutro frente a alternativas detectadas."
    return especiales


def generar_resumen_rentabilidad_pedidos_especiales(df):
    df = _df_seguro(df)
    if df.empty:
        return {"resumen": {}, "top_perdida": pd.DataFrame(), "laboratorios_afectados": pd.DataFrame()}

    perdida = _serie_numerica(df, "perdida_oportunidad_pedido_especial")
    favorables = df.get("pedido_especial_conveniente", pd.Series([None] * len(df), index=df.index))
    desfavorables = perdida.gt(0)
    diferencia = _serie_numerica(df, "diferencia_puntos_vs_alternativa")
    bruto = _serie_numerica(df, "bruto")
    ahorro = float((bruto * diferencia.clip(upper=0).abs() / 100).sum()) if "bruto" in df.columns else 0.0
    top_cols = [
        "cn",
        "descripcion",
        "laboratorio_maestro",
        "tipo_albaran_bidafarma",
        "bruto",
        "neto",
        "condicion_real_pedido_especial",
        "mejor_condicion_alternativa",
        "via_alternativa_recomendada",
        "perdida_oportunidad_pedido_especial",
        "confianza_comparativa_pedido_especial",
    ]
    top_cols = [col for col in top_cols if col in df.columns]
    top = df[perdida.gt(0)].sort_values("perdida_oportunidad_pedido_especial", ascending=False).head(10)
    top = top[top_cols].reset_index(drop=True) if not top.empty else pd.DataFrame(columns=top_cols)

    labs = pd.DataFrame()
    if "laboratorio_maestro" in df.columns and perdida.gt(0).any():
        labs = (
            df.assign(perdida_num=perdida)
            .groupby("laboratorio_maestro", dropna=False)["perdida_num"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
            .rename(columns={"perdida_num": "perdida_oportunidad"})
        )

    return {
        "resumen": {
            "perdida_oportunidad_total": round(float(perdida.sum()), 2),
            "ahorro_generado_estimado": round(float(ahorro), 2),
            "lineas_favorables": int(favorables.eq(True).sum()),
            "lineas_desfavorables": int(desfavorables.sum()),
        },
        "top_perdida": top,
        "laboratorios_afectados": labs,
    }


def generar_resumen_pedidos_especiales_bidafarma(df):
    trabajo = _df_seguro(df)
    if trabajo.empty or "es_pedido_especial_bidafarma" not in trabajo.columns:
        return {"ok": False, "mensaje": "No se han detectado pedidos especiales Bidafarma."}

    especiales = trabajo[trabajo["es_pedido_especial_bidafarma"].fillna(False).astype(bool)].copy()
    if especiales.empty:
        return {"ok": False, "mensaje": "No se han detectado pedidos especiales Bidafarma."}

    especiales["bruto_num"] = _serie_numerica(especiales, "bruto")
    especiales["neto_num"] = _serie_numerica(especiales, "neto")
    especiales["unidades_num"] = _serie_numerica(especiales, "unidades")
    especiales["iva_num"] = _iva_normalizada(_serie_numerica(especiales, "iva"))
    especiales = calcular_perdida_oportunidad_pedido_especial(especiales, trabajo)
    rentabilidad = generar_resumen_rentabilidad_pedidos_especiales(especiales)

    sx = especiales[especiales["tipo_albaran_bidafarma"].eq("SX")].copy()
    sq = especiales[especiales["tipo_albaran_bidafarma"].eq("SQ")].copy()
    sx_esp = sx[sx["iva_num"].le(4.01)]
    sx_para = sx[~sx["iva_num"].le(4.01)]

    return {
        "ok": True,
        "tipos_detectados": sorted(especiales["tipo_albaran_bidafarma"].dropna().astype(str).unique().tolist()),
        "resumen": {
            "lineas": int(len(especiales)),
            "unidades": round(float(especiales["unidades_num"].sum()), 2),
            "bruto_total": round(float(especiales["bruto_num"].sum()), 2),
            "neto_total": round(float(especiales["neto_num"].sum()), 2),
            "base_pedidos_especiales_bidafarma": round(float(especiales["neto_num"].sum()), 2),
        },
        "sx": {
            "compra_total_sx": round(float(sx["bruto_num"].sum()), 2) if not sx.empty else 0.0,
            "especialidad_sx": round(float(sx_esp["bruto_num"].sum()), 2) if not sx_esp.empty else 0.0,
            "parafarmacia_sx": round(float(sx_para["bruto_num"].sum()), 2) if not sx_para.empty else 0.0,
            "descuento_medio_especialidad_sx": round(float(sx_esp["condicion_real_pedido_especial"].mean()), 2) if not sx_esp.empty else 0.0,
            "cargo_medio_parafarmacia_sx": round(float((-sx_para["condicion_real_pedido_especial"]).clip(lower=0).mean()), 2) if not sx_para.empty else 0.0,
            "diferencia_total_vs_condicion": round(float(_serie_numerica(sx, "diferencia_condicion_pedido_especial").sum()), 2) if not sx.empty else 0.0,
        },
        "sq": {
            "compra_total_sq": round(float(sq["bruto_num"].sum()), 2) if not sq.empty else 0.0,
            "lineas_sq_especialidad_cara": int(sq.get("es_especialidad_cara", pd.Series(False, index=sq.index)).fillna(False).astype(bool).sum()) if not sq.empty else 0,
            "descuento_total_euros": round(float(_serie_numerica(sq, "descuento_especialidad_cara_euros").sum()), 2) if not sq.empty else 0.0,
            "descuento_medio_euros": round(float(_serie_numerica(sq, "descuento_especialidad_cara_euros").mean()), 2) if not sq.empty else 0.0,
        },
        "rentabilidad": rentabilidad,
        "detalle": especiales.reset_index(drop=True),
    }
