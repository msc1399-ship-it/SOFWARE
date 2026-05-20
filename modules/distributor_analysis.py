import pandas as pd

from modules import faceta, parafarmacia


TIPOS_ANALISIS = [
    "goteo_puro",
    "especialidad",
    "especialidad_cara",
    "parafarmacia_financiada",
    "parafarmacia",
    "transfer",
    "bitransfer",
    "avantia",
    "plataforma",
    "clubes",
    "nexo",
    "otros",
]


def _df_seguro(df):
    if df is None:
        return pd.DataFrame()
    return df.copy()


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


def _descuento_pct(bruto, coste):
    bruto = float(bruto or 0)
    if abs(bruto) <= 0.0001:
        return None
    return round((1 - (float(coste or 0) / bruto)) * 100, 2)


def _periodo(df):
    df = _df_seguro(df)
    if df.empty:
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
                "dias": max(1, int((fechas.max().date() - fechas.min().date()).days) + 1),
                "meses_equivalentes": round(max(1, int((fechas.max().date() - fechas.min().date()).days) + 1) / 30.4375, 2),
            }
    return None


def _df_sin_lineas_tecnicas(df):
    df = _df_seguro(df)
    if df.empty or "tipo" not in df.columns:
        return df

    descripcion = df.get("descripcion", pd.Series("", index=df.index))
    mask_faceta = pd.Series(
        [faceta.es_linea_faceta(tipo, desc) for tipo, desc in zip(df["tipo"], descripcion)],
        index=df.index,
    )
    return df[~mask_faceta].copy()


def _clasificar_bloques(df):
    df = _df_seguro(df)
    if df.empty:
        return pd.Series(dtype="object")

    seccion = df.get("seccion_albaran", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()
    tipo_compra = df.get("tipo_compra", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()
    descripcion = df.get("descripcion", pd.Series("", index=df.index)).astype(str).str.lower()
    especialidad_cara = df.get("es_especialidad_cara", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    parafarmacia_financiada = df.get(
        "es_parafarmacia_financiada",
        pd.Series(False, index=df.index),
    ).fillna(False).astype(bool)

    bloque = pd.Series("otros", index=df.index, dtype="object")
    bloque[tipo_compra.eq("transfer")] = "transfer"
    bloque[seccion.eq("bitransfer") | descripcion.str.contains("bitransfer|bittransfer", na=False)] = "bitransfer"
    bloque[descripcion.str.contains("plataforma", na=False)] = "plataforma"
    bloque[seccion.eq("club") | descripcion.str.contains("club|seleccion genericos|seleccion generica", na=False)] = "clubes"
    bloque[seccion.eq("avantia") | descripcion.str.contains("avantia", na=False)] = "avantia"
    bloque[descripcion.str.contains("nexo", na=False)] = "nexo"
    bloque[tipo_compra.eq("goteo") & seccion.eq("especialidad")] = "especialidad"
    bloque[tipo_compra.eq("goteo") & seccion.eq("parafarmacia")] = "parafarmacia"
    bloque[especialidad_cara] = "especialidad_cara"
    bloque[parafarmacia_financiada] = "parafarmacia_financiada"

    goteo = (
        tipo_compra.eq("goteo")
        & seccion.isin(["especialidad", "parafarmacia"])
        & ~especialidad_cara
        & ~parafarmacia_financiada
        & ~seccion.isin(["club", "avantia", "bitransfer"])
        & ~descripcion.str.contains("club|avantia|bitransfer|bittransfer|nexo|plataforma", na=False)
    )
    bloque[goteo] = "goteo_puro"
    bloque[goteo & seccion.eq("especialidad")] = "especialidad"
    bloque[goteo & seccion.eq("parafarmacia")] = "parafarmacia"
    bloque[parafarmacia_financiada] = "parafarmacia_financiada"
    return bloque


def _cargo_faceta(analisis_faceta, bloque):
    if not analisis_faceta:
        return 0.0
    detalle = _df_seguro(analisis_faceta.get("detalle_tramo_fijo"))
    if detalle.empty or "cargo_faceta_tramo_fijo" not in detalle.columns:
        return 0.0

    cargos = _serie_numerica(detalle, "cargo_faceta_tramo_fijo")
    if bloque == "goteo_puro":
        return float(cargos.sum())

    seccion = detalle.get("seccion_albaran", pd.Series("", index=detalle.index)).astype(str).str.lower().str.strip()
    if bloque == "especialidad":
        return float(cargos[seccion.eq("especialidad")].sum())
    if bloque == "parafarmacia":
        return float(cargos[seccion.eq("parafarmacia")].sum())
    return 0.0


def _cargo_extra_por_bloque(
    bloque,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
):
    cargo = _cargo_faceta(analisis_faceta, bloque)

    if bloque == "bitransfer" and resumen_bitransfer:
        cargo += float(resumen_bitransfer.get("cargo_resumen", 0) or 0)

    if bloque == "transfer" and analisis_transfer:
        detalle = _df_seguro(analisis_transfer.get("detalle"))
        if not detalle.empty and "cargo_transfer_total" in detalle.columns:
            cargo += float(_serie_numerica(detalle, "cargo_transfer_total").sum())
        else:
            cargo += float((analisis_transfer.get("resumen") or {}).get("cargo_total", 0) or 0)

    if bloque == "avantia" and analisis_avantia:
        resumen = analisis_avantia.get("resumen") or {}
        cargo += float(resumen.get("coste_total_avantia", 0) or 0)

    return cargo


def _base_trabajo(df):
    trabajo = _df_sin_lineas_tecnicas(df)
    if trabajo.empty:
        return trabajo
    trabajo = trabajo.copy()
    trabajo["bloque_analisis"] = _clasificar_bloques(trabajo)
    trabajo["bruto_num"] = _serie_numerica(trabajo, "bruto")
    trabajo["neto_num"] = _serie_numerica(trabajo, "neto")
    trabajo["unidades_num"] = _serie_numerica(trabajo, "unidades")
    return trabajo


def calcular_volumen_compra(df):
    trabajo = _base_trabajo(df)
    bruto_total = float(trabajo["bruto_num"].sum()) if not trabajo.empty else 0.0
    neto_total = float(trabajo["neto_num"].sum()) if not trabajo.empty else 0.0
    periodo = _periodo(df)
    meses = (periodo or {}).get("meses_equivalentes") or None

    volumen = {
        "periodo": periodo,
        "compra_total_periodo": round(bruto_total, 2),
        "compra_neta_periodo": round(neto_total, 2),
        "compra_total_mensual": round(bruto_total / meses, 2) if meses else None,
        "unidades_totales": round(float(trabajo["unidades_num"].sum()), 2) if not trabajo.empty else 0.0,
    }

    for tipo in TIPOS_ANALISIS:
        parte = trabajo[trabajo["bloque_analisis"].eq(tipo)] if not trabajo.empty else pd.DataFrame()
        bruto = float(parte["bruto_num"].sum()) if not parte.empty else 0.0
        volumen[tipo] = {
            "bruto": round(bruto, 2),
            "neto": round(float(parte["neto_num"].sum()), 2) if not parte.empty else 0.0,
            "unidades": round(float(parte["unidades_num"].sum()), 2) if not parte.empty else 0.0,
            "porcentaje_compra": round((bruto / bruto_total * 100), 2) if bruto_total else 0.0,
        }
    return volumen


def calcular_desglose_por_tipo(
    df,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
):
    trabajo = _base_trabajo(df)
    filas = []
    bruto_total = float(trabajo["bruto_num"].sum()) if not trabajo.empty else 0.0

    for tipo in TIPOS_ANALISIS:
        parte = trabajo[trabajo["bloque_analisis"].eq(tipo)] if not trabajo.empty else pd.DataFrame()
        bruto = float(parte["bruto_num"].sum()) if not parte.empty else 0.0
        neto = float(parte["neto_num"].sum()) if not parte.empty else 0.0
        unidades = float(parte["unidades_num"].sum()) if not parte.empty else 0.0
        cargos = _cargo_extra_por_bloque(tipo, analisis_faceta, analisis_avantia, resumen_bitransfer, analisis_transfer)
        coste_real = neto + cargos
        descuento_euros = 0.0
        if not parte.empty and tipo == "especialidad_cara":
            descuento_euros = float(_serie_numerica(parte, "descuento_especialidad_cara_euros").sum())
        if not parte.empty and tipo == "parafarmacia_financiada":
            descuento_euros = float(_serie_numerica(parte, "descuento_parafarmacia_financiada_euros").sum())
        descuento_en_euros = tipo in ["especialidad_cara", "parafarmacia_financiada"]
        filas.append({
            "bloque": tipo,
            "lineas": int(len(parte)),
            "unidades": round(unidades, 2),
            "bruto": round(bruto, 2),
            "neto": round(neto, 2),
            "coste_ajustado": round(coste_real, 2),
            "porcentaje_compra": round((bruto / bruto_total * 100), 2) if bruto_total else 0.0,
            "descuento_aparente_pct": None if descuento_en_euros else _descuento_pct(bruto, neto),
            "descuento_especialidad_cara_euros": round(descuento_euros, 2) if tipo == "especialidad_cara" else None,
            "descuento_parafarmacia_financiada_euros": round(descuento_euros, 2) if tipo == "parafarmacia_financiada" else None,
            "descuento_medio_euros": round(descuento_euros / unidades, 2) if descuento_en_euros and unidades else None,
            "cargos_imputados": round(cargos, 2),
            "descuento_real_final_pct": None if descuento_en_euros else _descuento_pct(bruto, coste_real),
        })
    return pd.DataFrame(filas)


def calcular_gastos_ocultos(
    resultado_factura_normal=None,
    resultado_factura_transfer=None,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
    df_compras=None,
):
    filas = []
    bruto_total = float(_serie_numerica(_df_seguro(df_compras), "bruto").sum()) if df_compras is not None else 0.0

    def agregar(tipo, importe, origen, proveedor=None, segmento=None, base_segmento=None):
        importe = float(importe or 0)
        if abs(importe) <= 0.0001:
            return
        filas.append({
            "tipo_gasto": tipo,
            "importe": round(importe, 2),
            "proveedor": proveedor,
            "origen": origen,
            "segmento_afectado": segmento,
            "pct_sobre_compra_total": round((importe / bruto_total * 100), 2) if bruto_total else 0.0,
            "pct_sobre_segmento": round((importe / base_segmento * 100), 2) if base_segmento else None,
        })

    for origen, resultado in [("factura_normal", resultado_factura_normal), ("factura_transfer", resultado_factura_transfer)]:
        gastos = _df_seguro((resultado or {}).get("gastos"))
        if not gastos.empty and "tipo" in gastos.columns and "importe" in gastos.columns:
            for tipo, grupo in gastos.groupby("tipo"):
                agregar(str(tipo), _serie_numerica(grupo, "importe").sum(), origen)

    abonos_transfer = _df_seguro((resultado_factura_transfer or {}).get("abonos"))
    if not abonos_transfer.empty and "importe" in abonos_transfer.columns:
        agregar("abonos_laboratorio", _serie_numerica(abonos_transfer, "importe").sum(), "factura_transfer")

    if analisis_faceta:
        agregar("margen_tramo_fijo", (analisis_faceta.get("resumen") or {}).get("margen_tramo_fijo_total", 0), "albaran_74", segmento="goteo")
        agregar("liquidaciones_club", (analisis_faceta.get("resumen") or {}).get("liquidaciones_total", 0), "albaran_74", segmento="clubes")

    if analisis_avantia:
        resumen = analisis_avantia.get("resumen") or {}
        agregar("cuota_avantia", resumen.get("cuota_avantia", 0), "avantia", segmento="avantia")
        agregar("gastos_avantia", resumen.get("coste_total_avantia", 0), "avantia", segmento="avantia")

    if resumen_bitransfer:
        agregar("gestion_bitransfer", resumen_bitransfer.get("cargo_resumen", 0), "bitransfer", segmento="bitransfer")

    if analisis_transfer:
        agregar("logistica_transfer", (analisis_transfer.get("resumen") or {}).get("cargo_total", 0), "factura_transfer", segmento="transfer")

    gastos = pd.DataFrame(filas)
    total = float(gastos["importe"].sum()) if not gastos.empty else 0.0
    resumen = {
        "total_gastos": round(total, 2),
        "pct_gastos_sobre_compra": round((total / bruto_total * 100), 2) if bruto_total else 0.0,
        "impacto_gastos_sobre_descuento": round((total / bruto_total * 100), 2) if bruto_total else 0.0,
    }
    return gastos, resumen


def calcular_descuentos_reales(df, desglose=None, gastos_resumen=None):
    if desglose is None:
        desglose = calcular_desglose_por_tipo(df)
    gastos_resumen = gastos_resumen or {}

    def _valor(bloque, columna):
        if desglose.empty:
            return None
        fila = desglose[desglose["bloque"].eq(bloque)]
        if fila.empty or columna not in fila.columns:
            return None
        valor = fila[columna].iloc[0]
        return None if pd.isna(valor) else valor

    bruto_goteo = sum(float(_valor(bloque, "bruto") or 0) for bloque in ["especialidad", "parafarmacia"])
    coste_goteo = sum(float(_valor(bloque, "coste_ajustado") or 0) for bloque in ["especialidad", "parafarmacia"])
    bruto_total = float(_serie_numerica(_base_trabajo(df), "bruto").sum())
    coste_total = float(_serie_numerica(_base_trabajo(df), "neto").sum()) + float(gastos_resumen.get("total_gastos", 0) or 0)

    aparente_goteo = _descuento_pct(
        bruto_goteo,
        sum(float(_valor(bloque, "neto") or 0) for bloque in ["especialidad", "parafarmacia"]),
    )
    real_goteo = _descuento_pct(bruto_goteo, coste_goteo)

    return {
        "especialidad_normal_pct": _valor("especialidad", "descuento_real_final_pct"),
        "especialidad_cara_descuento_total_euros": _valor("especialidad_cara", "descuento_especialidad_cara_euros") or 0.0,
        "especialidad_cara_descuento_medio_euros": _valor("especialidad_cara", "descuento_medio_euros") or 0.0,
        "parafarmacia_pct": _valor("parafarmacia", "descuento_real_final_pct"),
        "transfer_pct": _valor("transfer", "descuento_real_final_pct"),
        "bitransfer_pct": _valor("bitransfer", "descuento_real_final_pct"),
        "plataformas_pct": _valor("plataforma", "descuento_real_final_pct"),
        "goteo_aparente_pct": aparente_goteo,
        "goteo_real_pct": real_goteo,
        "perdida_puntos_goteo": None if aparente_goteo is None or real_goteo is None else round(aparente_goteo - real_goteo, 2),
        "descuento_total_general_pct": _descuento_pct(bruto_total, coste_total),
    }


def calcular_operativa_proveedor(df):
    trabajo = _base_trabajo(df)
    if trabajo.empty:
        return {
            "numero_pedidos": 0,
            "pedidos_por_dia": None,
            "ticket_medio_pedido": None,
            "ticket_medio_albaran": None,
            "importe_medio_linea": None,
            "unidades_medias_pedido": None,
        }

    col_pedido = next((col for col in ["pedido", "numero_pedido", "n_pedido", "albaran"] if col in trabajo.columns), None)
    col_albaran = next((col for col in ["albaran", "numero_albaran", "n_albaran"] if col in trabajo.columns), None)
    pedidos = trabajo[col_pedido].dropna().astype(str).nunique() if col_pedido else 0
    albaranes = trabajo[col_albaran].dropna().astype(str).nunique() if col_albaran else pedidos
    pedidos = pedidos or albaranes or 0
    periodo = _periodo(df)
    dias = (periodo or {}).get("dias")
    bruto = float(trabajo["bruto_num"].sum())
    unidades = float(trabajo["unidades_num"].sum())
    lineas = len(trabajo)

    return {
        "numero_pedidos": int(pedidos),
        "pedidos_por_dia": round(pedidos / dias, 2) if dias and pedidos else None,
        "ticket_medio_pedido": round(bruto / pedidos, 2) if pedidos else None,
        "ticket_medio_albaran": round(bruto / albaranes, 2) if albaranes else None,
        "importe_medio_linea": round(bruto / lineas, 2) if lineas else None,
        "unidades_medias_pedido": round(unidades / pedidos, 2) if pedidos else None,
    }


def calcular_especialidad_cara(df):
    trabajo = _base_trabajo(df)
    if trabajo.empty or "es_especialidad_cara" not in trabajo.columns:
        caras = pd.DataFrame()
    else:
        caras = trabajo[trabajo["es_especialidad_cara"].fillna(False).astype(bool)].copy()

    bruto = float(_serie_numerica(caras, "bruto").sum()) if not caras.empty else 0.0
    neto = float(_serie_numerica(caras, "neto").sum()) if not caras.empty else 0.0
    unidades = float(_serie_numerica(caras, "unidades").sum()) if not caras.empty else 0.0
    descuento_euros = float(_serie_numerica(caras, "descuento_especialidad_cara_euros").sum()) if not caras.empty else 0.0
    base_iva4_total = float(_serie_numerica(trabajo, "base_iva4_total").sum()) if not trabajo.empty else 0.0
    base_iva4_cara = float(_serie_numerica(trabajo, "base_iva4_especialidad_cara").sum()) if not trabajo.empty else 0.0

    return {
        "lineas_detectadas": int(len(caras)),
        "bruto_total": round(bruto, 2),
        "neto_total": round(neto, 2),
        "unidades": round(unidades, 2),
        "descuento_total_euros": round(descuento_euros, 2),
        "descuento_medio_euros": round(descuento_euros / unidades, 2) if unidades else 0.0,
        "descuento_medio_linea_euros": round(descuento_euros / len(caras), 2) if len(caras) else 0.0,
        "base_iva4_total": round(base_iva4_total, 2),
        "base_iva4_especialidad_cara": round(base_iva4_cara, 2),
        "base_iva4_sujeta_ajuste": round(base_iva4_total - base_iva4_cara, 2),
    }


def calcular_parafarmacia_financiada(df):
    return parafarmacia.calcular_resumen_parafarmacia_financiada(df)


def calcular_top_articulos_impactados(df, limite=10):
    trabajo = _base_trabajo(df)
    if trabajo.empty or "neto" not in trabajo.columns:
        return pd.DataFrame(), "No hay datos suficientes para calcular impacto por artículo."

    posibles = [
        "coste_real",
        "coste_ajustado",
        "coste_real_total",
        "neto_con_faceta_tramo_fijo",
        "neto_con_gestion_adicional",
        "neto_con_ajuste_comercial",
    ]
    coste_col = next((col for col in posibles if col in trabajo.columns), None)
    if coste_col is None:
        return pd.DataFrame(), "No hay datos suficientes para calcular impacto por artículo."

    trabajo = trabajo.copy()
    trabajo["coste_real_num"] = _serie_numerica(trabajo, coste_col)
    trabajo["descuento_aparente_pct"] = trabajo.apply(
        lambda row: _descuento_pct(row["bruto_num"], row["neto_num"]),
        axis=1,
    )
    trabajo["diferencia_absoluta"] = (trabajo["coste_real_num"] - trabajo["neto_num"]).abs()
    trabajo["diferencia_porcentual"] = trabajo.apply(
        lambda row: None if abs(row["neto_num"]) <= 0.0001 else round(row["diferencia_absoluta"] / abs(row["neto_num"]) * 100, 2),
        axis=1,
    )
    trabajo = trabajo.sort_values("diferencia_absoluta", ascending=False).head(limite)
    columnas = [
        "cn",
        "descripcion",
        "tipo_compra",
        "bruto",
        "neto",
        "descuento_aparente_pct",
        coste_col,
        "coste_real_num",
        "diferencia_absoluta",
        "diferencia_porcentual",
    ]
    columnas = [col for col in columnas if col in trabajo.columns]
    return trabajo[columnas].rename(columns={coste_col: "coste_imputado", "coste_real_num": "coste_real"}).reset_index(drop=True), None


def generar_diagnostico(volumen, descuentos, gastos_resumen, especialidad_cara, top_impacto, mensaje_top=None):
    alertas = []
    oportunidades = []

    if gastos_resumen.get("pct_gastos_sobre_compra", 0) > 1:
        alertas.append("Los gastos superan el 1% de la compra total; revisar cargos ocultos y logística.")
    if descuentos.get("perdida_puntos_goteo") and descuentos["perdida_puntos_goteo"] > 0:
        oportunidades.append(
            f"El goteo pierde {descuentos['perdida_puntos_goteo']:.2f} puntos tras cargos frente al descuento aparente."
        )
    if especialidad_cara.get("lineas_detectadas", 0) > 0:
        oportunidades.append("Separar especialidad cara / RDL 4/2010 del ajuste comercial general.")
    if mensaje_top:
        alertas.append(mensaje_top)

    return {
        "alertas": alertas,
        "oportunidades": oportunidades,
        "resumen": "Análisis generado con datos agregados de compras, cargos disponibles y clasificación por tipo.",
    }


def calcular_condiciones_comerciales(
    condicion_detectada=None,
    analisis_faceta=None,
    analisis_ajuste=None,
    analisis_cargo_adicional=None,
    descuentos=None,
):
    filas = []
    detalles = {}
    descuentos = descuentos or {}

    def _tipo_albaran_74(valor):
        return {
            1: "liquidacion_club",
            2: "margen_tramo_fijo",
            3: "tramo_cero_ajuste_escala",
        }.get(valor, valor)

    if condicion_detectada:
        filas.append({
            "condicion": condicion_detectada.get("nombre"),
            "tipo_condicion": condicion_detectada.get("acronimo"),
            "cargo_total": 0.0,
            "base_aplicacion": None,
            "lineas_afectadas": None,
            "impacto_sobre_neto": 0.0,
            "impacto_sobre_descuento_real": None,
            "origen": "condicion_detectada",
        })

    if analisis_faceta:
        resumen = analisis_faceta.get("resumen") or {}
        cargo = float(resumen.get("margen_tramo_fijo_total", 0) or 0)
        filas.append({
            "condicion": (condicion_detectada or {}).get("nombre"),
            "tipo_condicion": _tipo_albaran_74(resumen.get("tipo_albaran_74")) or "margen_tramo_fijo",
            "cargo_total": round(cargo, 2),
            "base_aplicacion": resumen.get("base_aplicacion"),
            "lineas_afectadas": resumen.get("lineas_tramo_fijo"),
            "impacto_sobre_neto": round(cargo, 2),
            "impacto_sobre_descuento_real": descuentos.get("perdida_puntos_goteo"),
            "origen": "albaran_74",
        })
        detalles["detalle_tramo_fijo"] = _df_seguro(analisis_faceta.get("detalle_tramo_fijo"))
        detalles["conceptos"] = _df_seguro(analisis_faceta.get("conceptos"))
        detalles["resumen_liquidaciones"] = _df_seguro(analisis_faceta.get("resumen_liquidaciones"))
        detalles["detalle_liquidaciones"] = _df_seguro(analisis_faceta.get("detalle_liquidaciones"))

    if analisis_ajuste:
        resumen = analisis_ajuste.get("resumen") or {}
        descuento = float(resumen.get("descuento_total", 0) or 0)
        filas.append({
            "condicion": (condicion_detectada or {}).get("nombre"),
            "tipo_condicion": "ajuste_comercial",
            "cargo_total": round(-descuento, 2),
            "base_aplicacion": resumen.get("base_aplicacion"),
            "lineas_afectadas": resumen.get("lineas_afectadas"),
            "impacto_sobre_neto": round(-descuento, 2),
            "impacto_sobre_descuento_real": resumen.get("descuento_pct"),
            "origen": "factura_normal",
        })
        detalles["detalle_ajuste_comercial"] = _df_seguro(analisis_ajuste.get("detalle"))

    if analisis_cargo_adicional:
        resumen = analisis_cargo_adicional.get("resumen") or {}
        cargo = float(resumen.get("cargo_total", 0) or 0)
        filas.append({
            "condicion": (condicion_detectada or {}).get("nombre"),
            "tipo_condicion": "franquicia_gestion",
            "cargo_total": round(cargo, 2),
            "base_aplicacion": resumen.get("base_aplicacion"),
            "lineas_afectadas": resumen.get("lineas_afectadas"),
            "impacto_sobre_neto": round(cargo, 2),
            "impacto_sobre_descuento_real": None,
            "origen": "factura_normal",
        })
        detalles["detalle_cargo_adicional"] = _df_seguro(analisis_cargo_adicional.get("detalle"))

    return {
        "resumen": pd.DataFrame(filas),
        "detalles": detalles,
    }


def generar_analisis_distribuidora(
    df_compras,
    resultado_factura_normal=None,
    resultado_factura_transfer=None,
    proveedor=None,
    analisis_faceta=None,
    analisis_avantia=None,
    resumen_bitransfer=None,
    analisis_transfer=None,
    analisis_clubes=None,
    condicion_detectada=None,
    analisis_ajuste=None,
    analisis_cargo_adicional=None,
):
    df = _df_seguro(df_compras)
    if df.empty:
        return {"ok": False, "mensaje": "No hay datos de compras suficientes para generar el análisis."}

    proveedor_detectado = proveedor
    if not proveedor_detectado and "proveedor" in df.columns:
        valores = df["proveedor"].dropna().astype(str).unique().tolist()
        proveedor_detectado = ", ".join(valores[:3]) if valores else None

    volumen = calcular_volumen_compra(df)
    desglose = calcular_desglose_por_tipo(
        df,
        analisis_faceta=analisis_faceta,
        analisis_avantia=analisis_avantia,
        resumen_bitransfer=resumen_bitransfer,
        analisis_transfer=analisis_transfer,
    )
    gastos, gastos_resumen = calcular_gastos_ocultos(
        resultado_factura_normal=resultado_factura_normal,
        resultado_factura_transfer=resultado_factura_transfer,
        analisis_faceta=analisis_faceta,
        analisis_avantia=analisis_avantia,
        resumen_bitransfer=resumen_bitransfer,
        analisis_transfer=analisis_transfer,
        df_compras=df,
    )
    descuentos = calcular_descuentos_reales(df, desglose=desglose, gastos_resumen=gastos_resumen)
    especialidad_cara = calcular_especialidad_cara(df)
    parafarmacia_financiada = calcular_parafarmacia_financiada(df)
    operativa = calcular_operativa_proveedor(df)
    top_impacto, mensaje_top = calcular_top_articulos_impactados(df)
    diagnostico = generar_diagnostico(volumen, descuentos, gastos_resumen, especialidad_cara, top_impacto, mensaje_top)
    if parafarmacia_financiada.get("resumen", {}).get("lineas_detectadas", 0) > 0:
        diagnostico.setdefault("oportunidades", []).append(
            "Separar parafarmacia financiada de las condiciones comerciales generales y revisar volumen por laboratorio."
        )
    condiciones_comerciales = calcular_condiciones_comerciales(
        condicion_detectada=condicion_detectada,
        analisis_faceta=analisis_faceta,
        analisis_ajuste=analisis_ajuste,
        analisis_cargo_adicional=analisis_cargo_adicional,
        descuentos=descuentos,
    )

    resumen_compat = {
        "periodo": volumen.get("periodo"),
        "compra_bruta_total": volumen.get("compra_total_periodo", 0),
        "compra_neta_total": volumen.get("compra_neta_periodo", 0),
        "abonos_totales": 0.0,
        "unidades_totales": volumen.get("unidades_totales", 0),
        "descuento_medio_general": descuentos.get("descuento_total_general_pct"),
    }

    especialidad_df = pd.DataFrame([{
        "lineas_detectadas": especialidad_cara["lineas_detectadas"],
        "bruto_total": especialidad_cara["bruto_total"],
        "neto_total": especialidad_cara["neto_total"],
        "descuento_euros": especialidad_cara["descuento_total_euros"],
        "base_iva4_total": especialidad_cara["base_iva4_total"],
        "base_iva4_especialidad_cara": especialidad_cara["base_iva4_especialidad_cara"],
        "base_iva4_sujeta_ajuste": especialidad_cara["base_iva4_sujeta_ajuste"],
    }])

    return {
        "ok": True,
        "tipo": "distribuidora",
        "proveedor": proveedor_detectado or "distribuidora",
        "periodo": volumen.get("periodo"),
        "volumen_compra": volumen,
        "desglose_por_tipo": desglose,
        "descuentos_reales": descuentos,
        "gastos_ocultos": gastos,
        "gastos_resumen": gastos_resumen,
        "especialidad_cara_resumen": especialidad_cara,
        "parafarmacia_financiada": parafarmacia_financiada,
        "operativa_proveedor": operativa,
        "top_impacto": top_impacto,
        "top_impacto_mensaje": mensaje_top,
        "diagnostico": diagnostico,
        "clubes": analisis_clubes,
        "condiciones_comerciales": condiciones_comerciales,
        # Claves de compatibilidad con la UI/IA/resumen final actuales.
        "resumen": resumen_compat,
        "desglose": desglose,
        "cargos": gastos,
        "especialidad_cara": especialidad_df,
    }
