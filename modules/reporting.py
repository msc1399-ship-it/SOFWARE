import pandas as pd

from modules import distributor_analysis
from modules import faceta


def _df_seguro(df):
    if df is None:
        return pd.DataFrame()
    return df.copy()


def _serie_numerica(df, columna):
    if df is None:
        return pd.Series(dtype="float64")
    if df.empty or columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index if df is not None else None, dtype="float64")

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


def _descuento_pct(bruto, coste):
    if bruto is None or abs(float(bruto)) <= 0.0001:
        return None
    return round((1 - (float(coste) / float(bruto))) * 100, 2)


def _df_sin_lineas_faceta(df):
    df = _df_seguro(df)
    if df.empty or "tipo" not in df.columns:
        return df

    descripcion = df.get("descripcion", pd.Series("", index=df.index))
    mask_faceta = pd.Series(
        [
            faceta.es_linea_faceta(tipo, desc)
            for tipo, desc in zip(df["tipo"], descripcion)
        ],
        index=df.index,
    )
    return df[~mask_faceta].copy()


def _cargo_faceta(analisis_faceta, bloque):
    if not analisis_faceta:
        return 0.0

    detalle = _df_seguro(analisis_faceta.get("detalle_tramo_fijo"))
    if detalle.empty or "cargo_faceta_tramo_fijo" not in detalle.columns:
        return 0.0

    resumen = analisis_faceta.get("resumen") or {}
    cargo_unitario = float(resumen.get("cargo_unitario_tramo_fijo", 0) or 0)
    if cargo_unitario > 0:
        cargos = _serie_numerica(detalle, "unidades") * cargo_unitario
    else:
        cargos = _serie_numerica(detalle, "cargo_faceta_tramo_fijo")
    if bloque == "goteo_puro":
        return float(cargos.sum())

    bloque_detalle = _clasificar_bloque(detalle)
    if bloque == "especialidad":
        return float(cargos[bloque_detalle.eq("especialidad")].sum())
    if bloque == "parafarmacia":
        return float(cargos[bloque_detalle.eq("parafarmacia")].sum())
    return 0.0


def _periodo(df):
    if df is None or df.empty:
        return None

    columnas_fecha = [
        col for col in df.columns
        if "fecha" in str(col).lower() or str(col).lower() in {"date", "dia", "día"}
    ]
    for columna in columnas_fecha:
        fechas = pd.to_datetime(df[columna], errors="coerce").dropna()
        if not fechas.empty:
            return {
                "desde": fechas.min().date().isoformat(),
                "hasta": fechas.max().date().isoformat(),
            }
    return None


def _clasificar_bloque(df):
    seccion = df.get("seccion_albaran", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()
    tipo_compra = df.get("tipo_compra", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()
    descripcion = df.get("descripcion", pd.Series("", index=df.index)).astype(str).str.lower()
    especialidad_cara = df.get("es_especialidad_cara", pd.Series(False, index=df.index)).fillna(False).astype(bool)

    bloque = pd.Series("otros", index=df.index, dtype="object")
    bloque[tipo_compra.eq("transfer")] = "transfer"
    bloque[seccion.eq("bitransfer") | descripcion.str.contains("bitransfer|bittransfer", na=False)] = "bitransfer"
    bloque[descripcion.str.contains("plataforma", na=False)] = "plataforma"
    bloque[seccion.eq("club") | descripcion.str.contains("club", na=False)] = "clubes"
    bloque[seccion.eq("avantia") | descripcion.str.contains("avantia", na=False)] = "avantia"
    bloque[descripcion.str.contains("nexo", na=False)] = "nexo"
    bloque[tipo_compra.eq("goteo") & seccion.eq("parafarmacia")] = "parafarmacia"
    bloque[tipo_compra.eq("goteo") & seccion.eq("especialidad")] = "especialidad"
    bloque[tipo_compra.eq("goteo") & seccion.isin(["especialidad", "parafarmacia"])] = bloque[
        tipo_compra.eq("goteo") & seccion.isin(["especialidad", "parafarmacia"])
    ]
    bloque[especialidad_cara] = "especialidad_cara"

    goteo_normal = (
        tipo_compra.eq("goteo")
        & seccion.isin(["especialidad", "parafarmacia"])
        & ~especialidad_cara
        & ~seccion.isin(["club", "avantia", "bitransfer"])
        & ~descripcion.str.contains("club|avantia|bitransfer|bittransfer|nexo", na=False)
    )
    bloque[goteo_normal] = "goteo_puro"
    bloque[goteo_normal & seccion.eq("especialidad")] = "especialidad"
    bloque[goteo_normal & seccion.eq("parafarmacia")] = "parafarmacia"
    return bloque


def calcular_resumen_compras(df):
    df = _df_seguro(df)
    if df.empty:
        return {}

    bruto = _serie_numerica(df, "bruto")
    neto = _serie_numerica(df, "neto")
    unidades = _serie_numerica(df, "unidades")
    abonos = neto[neto < 0].sum()

    bruto_total = float(bruto[bruto > 0].sum() + bruto[bruto < 0].sum())
    neto_total = float(neto[neto > 0].sum() + neto[neto < 0].sum())
    return {
        "periodo": _periodo(df),
        "compra_bruta_total": round(bruto_total, 2),
        "compra_neta_total": round(neto_total, 2),
        "abonos_totales": round(float(abonos), 2),
        "unidades_totales": round(float(unidades.sum()), 2),
        "descuento_medio_general": _descuento_pct(bruto_total, neto_total),
    }


def calcular_desglose_por_tipo(df, analisis_faceta=None):
    df = _df_sin_lineas_faceta(df)
    if df.empty:
        return pd.DataFrame()

    trabajo = df.copy()
    trabajo["bloque"] = _clasificar_bloque(trabajo)
    trabajo["bruto_num"] = _serie_numerica(trabajo, "bruto")
    trabajo["neto_num"] = _serie_numerica(trabajo, "neto")
    trabajo["unidades_num"] = _serie_numerica(trabajo, "unidades")
    seccion = trabajo.get("seccion_albaran", pd.Series("", index=trabajo.index)).astype(str).str.lower().str.strip()
    tipo_compra = trabajo.get("tipo_compra", pd.Series("", index=trabajo.index)).astype(str).str.lower().str.strip()
    descripcion = trabajo.get("descripcion", pd.Series("", index=trabajo.index)).astype(str).str.lower()
    especialidad_cara = trabajo.get("es_especialidad_cara", pd.Series(False, index=trabajo.index)).fillna(False).astype(bool)

    mask_bitransfer = trabajo["bloque"].eq("bitransfer")
    mask_clubes = trabajo["bloque"].eq("clubes")
    mask_avantia = trabajo["bloque"].eq("avantia")
    mask_nexo = trabajo["bloque"].eq("nexo")
    mask_goteo_puro = (
        tipo_compra.eq("goteo")
        & seccion.isin(["especialidad", "parafarmacia"])
        & ~especialidad_cara
        & ~mask_bitransfer
        & ~mask_clubes
        & ~mask_avantia
        & ~mask_nexo
        & ~descripcion.str.contains("club|avantia|bitransfer|bittransfer|nexo", na=False)
    )

    definiciones = [
        ("goteo_puro", mask_goteo_puro),
        ("especialidad", mask_goteo_puro & seccion.eq("especialidad")),
        ("especialidad_cara", especialidad_cara),
        ("parafarmacia", mask_goteo_puro & seccion.eq("parafarmacia")),
        ("transfer", trabajo["bloque"].eq("transfer")),
        ("bitransfer", mask_bitransfer),
        ("clubes", mask_clubes),
        ("plataforma", trabajo["bloque"].eq("plataforma")),
        ("avantia", mask_avantia),
        ("nexo", mask_nexo),
        ("otros", trabajo["bloque"].eq("otros")),
    ]

    filas = []
    for bloque, mask in definiciones:
        parte = trabajo[mask]
        if parte.empty:
            continue
        bruto = float(parte["bruto_num"].sum())
        neto = float(parte["neto_num"].sum())
        cargos_imputados = _cargo_faceta(analisis_faceta, bloque)
        coste_real = neto + cargos_imputados
        filas.append({
            "bloque": bloque,
            "lineas": len(parte),
            "unidades": round(float(parte["unidades_num"].sum()), 2),
            "bruto": round(bruto, 2),
            "neto": round(neto, 2),
            "coste_ajustado": round(coste_real, 2),
            "descuento_aparente_pct": _descuento_pct(bruto, neto),
            "cargos_imputados": round(cargos_imputados, 2),
            "descuento_real_final_pct": _descuento_pct(bruto, coste_real),
        })

    return pd.DataFrame(filas)


def calcular_resumen_cargos(
    resultado_factura_normal=None,
    resultado_factura_transfer=None,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
):
    filas = []

    gastos_normal = _df_seguro((resultado_factura_normal or {}).get("gastos"))
    if not gastos_normal.empty and "tipo" in gastos_normal.columns and "importe" in gastos_normal.columns:
        for tipo, grupo in gastos_normal.groupby("tipo"):
            filas.append({
                "tipo": tipo,
                "importe": round(float(_serie_numerica(grupo, "importe").sum()), 2),
                "origen": "factura_normal",
            })

    gastos_transfer = _df_seguro((resultado_factura_transfer or {}).get("gastos"))
    if not gastos_transfer.empty and "tipo" in gastos_transfer.columns and "importe" in gastos_transfer.columns:
        for tipo, grupo in gastos_transfer.groupby("tipo"):
            filas.append({
                "tipo": tipo,
                "importe": round(float(_serie_numerica(grupo, "importe").sum()), 2),
                "origen": "factura_transfer",
            })

    abonos_transfer = _df_seguro((resultado_factura_transfer or {}).get("abonos"))
    if not abonos_transfer.empty and "importe" in abonos_transfer.columns:
        filas.append({
            "tipo": "abonos_laboratorio",
            "importe": round(float(_serie_numerica(abonos_transfer, "importe").sum()), 2),
            "origen": "factura_transfer",
        })

    if analisis_faceta:
        filas.append({
            "tipo": "margen_tramo_fijo_tp74",
            "importe": float(analisis_faceta.get("resumen", {}).get("margen_tramo_fijo_total", 0) or 0),
            "origen": "albaran_74",
        })

    if analisis_avantia:
        filas.append({
            "tipo": "cuota_avantia",
            "importe": float(analisis_avantia.get("resumen", {}).get("cuota_avantia", 0) or 0),
            "origen": "avantia",
        })

    if resumen_bitransfer:
        filas.append({
            "tipo": "gestion_bitransfer",
            "importe": float(resumen_bitransfer.get("cargo_resumen", 0) or 0),
            "origen": "bitransfer",
        })

    if analisis_transfer:
        filas.append({
            "tipo": "logistica_transfer",
            "importe": float(analisis_transfer.get("resumen", {}).get("cargo_total", 0) or 0),
            "origen": "factura_transfer",
        })

    return pd.DataFrame(filas)


def calcular_resumen_especialidad_cara(df):
    df = _df_seguro(df)
    columnas = [
        "lineas_detectadas",
        "bruto_total",
        "neto_total",
        "descuento_euros",
        "base_iva4_total",
        "base_iva4_especialidad_cara",
        "base_iva4_sujeta_ajuste",
    ]
    if df.empty or "es_especialidad_cara" not in df.columns:
        return pd.DataFrame([{col: 0 for col in columnas}])

    caras = df[df["es_especialidad_cara"].fillna(False).astype(bool)].copy()
    return pd.DataFrame([{
        "lineas_detectadas": len(caras),
        "bruto_total": round(float(_serie_numerica(caras, "bruto").sum()), 2),
        "neto_total": round(float(_serie_numerica(caras, "neto").sum()), 2),
        "descuento_euros": round(float(_serie_numerica(caras, "descuento_especialidad_cara_euros").sum()), 2),
        "base_iva4_total": round(float(_serie_numerica(df, "base_iva4_total").sum()), 2),
        "base_iva4_especialidad_cara": round(float(_serie_numerica(df, "base_iva4_especialidad_cara").sum()), 2),
        "base_iva4_sujeta_ajuste": round(float(_serie_numerica(df, "base_iva4_sujeta_ajuste").sum()), 2),
    }])


def calcular_top_impacto(df, limite=10):
    df = _df_seguro(df)
    if df.empty:
        return pd.DataFrame()

    posibles_coste_real = [
        "coste_real",
        "coste_ajustado",
        "coste_real_total",
        "neto_con_faceta_tramo_fijo",
        "neto_con_gestion_adicional",
        "neto_con_ajuste_comercial",
    ]
    coste_col = next((col for col in posibles_coste_real if col in df.columns), None)
    if coste_col is None or "neto" not in df.columns:
        return pd.DataFrame()

    trabajo = df.copy()
    trabajo["neto_num"] = _serie_numerica(trabajo, "neto")
    trabajo["coste_real_num"] = _serie_numerica(trabajo, coste_col)
    trabajo["diferencia_absoluta"] = (trabajo["coste_real_num"] - trabajo["neto_num"]).abs()
    trabajo["diferencia_pct"] = trabajo.apply(
        lambda row: None if abs(row["neto_num"]) <= 0.0001 else round((row["diferencia_absoluta"] / abs(row["neto_num"])) * 100, 2),
        axis=1,
    )
    trabajo = trabajo.sort_values("diferencia_absoluta", ascending=False).head(limite)
    columnas = [
        "cn",
        "descripcion",
        "bruto",
        "neto",
        coste_col,
        "diferencia_absoluta",
        "diferencia_pct",
    ]
    columnas = [col for col in columnas if col in trabajo.columns]
    resultado = trabajo[columnas].rename(columns={coste_col: "coste_real"})
    return resultado.reset_index(drop=True)


def generar_analisis_distribuidora(
    df_compras,
    proveedor=None,
    resultado_factura_normal=None,
    resultado_factura_transfer=None,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
    analisis_clubes=None,
    condicion_detectada=None,
    analisis_ajuste=None,
    analisis_cargo_adicional=None,
):
    return distributor_analysis.generar_analisis_distribuidora(
        df_compras,
        resultado_factura_normal=resultado_factura_normal,
        resultado_factura_transfer=resultado_factura_transfer,
        proveedor=proveedor,
        analisis_faceta=analisis_faceta,
        analisis_avantia=analisis_avantia,
        resumen_bitransfer=resumen_bitransfer,
        analisis_transfer=analisis_transfer,
        analisis_clubes=analisis_clubes,
        condicion_detectada=condicion_detectada,
        analisis_ajuste=analisis_ajuste,
        analisis_cargo_adicional=analisis_cargo_adicional,
    )


def generar_resumen_final(
    analisis_distribuidoras=None,
    analisis_laboratorios=None,
    analisis_ventas=None,
    analisis_stock=None,
):
    analisis_distribuidoras = analisis_distribuidoras or []
    if isinstance(analisis_distribuidoras, dict):
        analisis_distribuidoras = [analisis_distribuidoras]

    filas = []
    for analisis in analisis_distribuidoras:
        if not analisis or not analisis.get("ok"):
            continue
        resumen = analisis.get("resumen", {})
        filas.append({
            "modulo": "distribuidora",
            "proveedor": analisis.get("proveedor"),
            "compra_bruta_total": resumen.get("compra_bruta_total"),
            "compra_neta_total": resumen.get("compra_neta_total"),
            "descuento_medio_general": resumen.get("descuento_medio_general"),
        })

    return {
        "ok": bool(filas),
        "distribuidoras": pd.DataFrame(filas),
        "laboratorios": analisis_laboratorios,
        "ventas": analisis_ventas,
        "stock": analisis_stock,
    }
