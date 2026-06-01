import pandas as pd


CATEGORIAS_SIMULADOR = [
    "especialidad_normal",
    "especialidad_cara",
    "parafarmacia_financiada",
    "parafarmacia_no_financiada",
    "transfer",
    "bitransfer",
    "clubes",
]


def _df_seguro(df):
    if df is None:
        return pd.DataFrame()
    if isinstance(df, pd.DataFrame):
        return df.copy()
    try:
        return pd.DataFrame(df)
    except Exception:
        return pd.DataFrame()


def _num(valor, defecto=0.0):
    try:
        if valor is None or pd.isna(valor):
            return defecto
        if isinstance(valor, str):
            limpio = valor.replace("EUR", "").replace("euro", "").replace("%", "").replace(" ", "")
            if "," in limpio:
                limpio = limpio.replace(".", "").replace(",", ".")
            return float(limpio)
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def _valor_desglose(desglose, bloque, columnas, defecto=0.0):
    df = _df_seguro(desglose)
    if df.empty or "bloque" not in df.columns:
        return defecto

    columnas = columnas if isinstance(columnas, (list, tuple)) else [columnas]
    bloque_norm = str(bloque).lower().replace(" ", "_")
    serie_bloque = df["bloque"].astype(str).str.lower().str.replace(" ", "_", regex=False)
    fila = df[serie_bloque.eq(bloque_norm)]
    if fila.empty:
        return defecto

    for columna in columnas:
        if columna in fila.columns:
            valor = fila.iloc[0].get(columna)
            if valor is not None and not pd.isna(valor):
                return _num(valor, defecto)
    return defecto


def _base_desde_analisis(analisis):
    analisis = analisis or {}
    resumen = analisis.get("resumen", {}) or {}
    volumen = analisis.get("volumen_compra", {}) or {}
    descuentos = analisis.get("descuentos_reales", {}) or {}
    gastos = analisis.get("gastos_resumen", {}) or {}
    desglose = analisis.get("desglose_por_tipo", analisis.get("desglose", pd.DataFrame()))
    especialidad_cara = analisis.get("especialidad_cara_resumen", {}) or {}
    parafarmacia_financiada = (analisis.get("parafarmacia_financiada", {}) or {}).get("resumen", {}) or {}
    clubes = analisis.get("clubes", {}) or {}

    proveedor = analisis.get("proveedor") or resumen.get("proveedor") or "proveedor"
    compra_mensual = volumen.get("compra_total_mensual")
    if compra_mensual is None:
        compra_mensual = resumen.get("compra_bruta_total", 0)

    cargos_actuales = _num(gastos.get("total_gastos"))
    franquicia = _num(gastos.get("franquicia"))
    gasto_fijo = _num(gastos.get("gasto_fijo"), cargos_actuales)

    return {
        "proveedor": proveedor,
        "compra_media_mensual": _num(compra_mensual),
        "compra_media_especialidad_normal": _valor_desglose(desglose, "especialidad", ["bruto", "bruto_compra"]),
        "compra_media_especialidad_cara": _num(especialidad_cara.get("bruto_total")),
        "unidades_especialidad_cara": _num(especialidad_cara.get("unidades")),
        "descuento_medio_especialidad_cara_euros": _num(especialidad_cara.get("descuento_medio_linea_euros")),
        "compra_media_parafarmacia_financiada": _num(parafarmacia_financiada.get("bruto_total")),
        "compra_media_parafarmacia_no_financiada": _valor_desglose(desglose, "parafarmacia", ["bruto", "bruto_compra"]),
        "compra_media_transfer": _valor_desglose(desglose, "transfer", ["bruto", "bruto_compra"]),
        "compra_media_bitransfer": _valor_desglose(desglose, "bitransfer", ["bruto", "bruto_compra"]),
        "compra_media_clubes": _valor_desglose(desglose, "clubes", ["bruto", "bruto_compra"]),
        "descuento_real_especialidad": _valor_desglose(
            desglose,
            "especialidad",
            ["descuento_real_final_pct", "descuento_aparente_pct", "descuento_medio_pct"],
        ),
        "descuento_real_parafarmacia": _valor_desglose(
            desglose,
            "parafarmacia",
            ["descuento_real_final_pct", "descuento_aparente_pct", "descuento_medio_pct"],
        ),
        "descuento_real_transfer": _valor_desglose(
            desglose,
            "transfer",
            ["descuento_real_final_pct", "descuento_aparente_pct", "descuento_medio_pct"],
        ),
        "descuento_real_bitransfer": _valor_desglose(
            desglose,
            "bitransfer",
            ["descuento_real_final_pct", "descuento_aparente_pct", "descuento_medio_pct"],
        ),
        "descuento_real_general": _num(resumen.get("descuento_medio_general")),
        "goteo_real_pct": _num(descuentos.get("goteo_real_pct")),
        "cargos_actuales": cargos_actuales,
        "franquicia_actual": franquicia,
        "penalizacion_bajo_consumo": 0.0,
        "gasto_fijo_actual": gasto_fijo,
        "rentabilidad_real_actual": _num(resumen.get("descuento_medio_general")),
        "club_compra_sin_liquidacion": _num(clubes.get("compra_sin_liquidacion")),
        "club_perdida_vs_descuento": _num(clubes.get("perdida_vs_descuento_habitual")),
    }


def construir_base_historica_expediente(analisis_historicos):
    if analisis_historicos is None:
        return pd.DataFrame()
    if isinstance(analisis_historicos, dict):
        valores = list(analisis_historicos.values()) if all(isinstance(v, dict) for v in analisis_historicos.values()) else [analisis_historicos]
    elif isinstance(analisis_historicos, list):
        valores = analisis_historicos
    else:
        valores = [analisis_historicos]

    filas = [
        _base_desde_analisis(analisis)
        for analisis in valores
        if isinstance(analisis, dict) and analisis.get("ok", True)
    ]
    return pd.DataFrame(filas)


def calcular_promedios_historicos(df_historico):
    df = _df_seguro(df_historico)
    if df.empty:
        return {}
    numericas = df.select_dtypes(include="number")
    return numericas.mean(numeric_only=True).fillna(0).round(4).to_dict()


def _fila_base(base_actual, proveedor=None):
    df = _df_seguro(base_actual)
    if df.empty:
        return {}
    if proveedor and "proveedor" in df.columns:
        fila = df[df["proveedor"].astype(str).eq(str(proveedor))]
        if not fila.empty:
            return fila.iloc[0].to_dict()
    return df.iloc[0].to_dict()


def _calcular_mix_derivado(base):
    total_categorias = sum(_num(base.get(campo)) for campo in [
        "compra_media_especialidad_normal",
        "compra_media_parafarmacia_no_financiada",
        "compra_media_transfer",
        "compra_media_bitransfer",
        "compra_media_clubes",
    ])
    if total_categorias <= 0:
        return {
            "especialidad_normal": 0.7,
            "parafarmacia_no_financiada": 0.2,
            "transfer": 0.05,
            "bitransfer": 0.05,
            "clubes": 0.0,
        }
    return {
        "especialidad_normal": _num(base.get("compra_media_especialidad_normal")) / total_categorias,
        "parafarmacia_no_financiada": _num(base.get("compra_media_parafarmacia_no_financiada")) / total_categorias,
        "transfer": _num(base.get("compra_media_transfer")) / total_categorias,
        "bitransfer": _num(base.get("compra_media_bitransfer")) / total_categorias,
        "clubes": _num(base.get("compra_media_clubes")) / total_categorias,
    }


def calcular_impacto_derivacion_volumen(base_actual, escenario):
    base = _fila_base(base_actual, escenario.get("proveedor_destino"))
    volumen_derivado = _num(escenario.get("volumen_derivar"))
    compra_actual = _num(base.get("compra_media_mensual"))
    compra_objetivo = escenario.get("volumen_mensual_objetivo")
    compra_objetivo = _num(compra_objetivo, compra_actual + volumen_derivado)
    incremento = compra_objetivo - compra_actual
    return {
        "compra_actual": round(compra_actual, 2),
        "compra_objetivo": round(compra_objetivo, 2),
        "volumen_derivado": round(volumen_derivado, 2),
        "incremento_volumen": round(incremento, 2),
    }


def simular_escenario_condiciones(base_actual, escenario):
    base = _fila_base(base_actual, escenario.get("proveedor_destino"))
    proveedor = escenario.get("proveedor_destino") or base.get("proveedor") or "proveedor"
    derivacion = calcular_impacto_derivacion_volumen(base_actual, escenario)
    compra_actual = max(_num(base.get("compra_media_mensual")), 0.0)
    compra_objetivo = max(_num(derivacion.get("compra_objetivo")), 0.0)
    factor = (compra_objetivo / compra_actual) if compra_actual > 0 else 1.0
    mix = _calcular_mix_derivado(base)
    volumen_derivado = _num(derivacion.get("volumen_derivado"))

    base_esp = _num(
        escenario.get("base_especialidad_normal"),
        (_num(base.get("compra_media_especialidad_normal")) * factor) + (volumen_derivado * mix["especialidad_normal"]),
    )
    base_para = _num(
        escenario.get("base_parafarmacia_no_financiada"),
        (_num(base.get("compra_media_parafarmacia_no_financiada")) * factor) + (volumen_derivado * mix["parafarmacia_no_financiada"]),
    )
    base_transfer = _num(
        escenario.get("base_transfer"),
        (_num(base.get("compra_media_transfer")) * factor) + (volumen_derivado * mix["transfer"]),
    )
    base_bitransfer = _num(
        escenario.get("base_bitransfer"),
        (_num(base.get("compra_media_bitransfer")) * factor) + (volumen_derivado * mix["bitransfer"]),
    )
    base_clubes = _num(base.get("compra_media_clubes")) * factor + (volumen_derivado * mix["clubes"])
    base_parafarmacia_financiada = _num(base.get("compra_media_parafarmacia_financiada")) * factor
    unidades_cara = _num(escenario.get("especialidad_cara_unidades"), _num(base.get("unidades_especialidad_cara")) * factor)

    desc_esp_actual = _num(base.get("descuento_real_especialidad"))
    desc_para_actual = _num(base.get("descuento_real_parafarmacia"))
    desc_transfer_actual = _num(base.get("descuento_real_transfer"))
    desc_bitransfer_actual = _num(base.get("descuento_real_bitransfer"))
    desc_cara_actual = _num(base.get("descuento_medio_especialidad_cara_euros"))

    desc_esp_nuevo = _num(escenario.get("nuevo_descuento_especialidad_pct"), desc_esp_actual)
    desc_para_nuevo = _num(escenario.get("nuevo_descuento_parafarmacia_pct"), desc_para_actual)
    desc_transfer_nuevo = _num(escenario.get("nuevo_descuento_transfer_pct"), desc_transfer_actual)
    desc_bitransfer_nuevo = _num(escenario.get("nuevo_descuento_bitransfer_pct"), desc_bitransfer_actual)
    desc_cara_nuevo = _num(escenario.get("nuevo_descuento_especialidad_cara_euros"), desc_cara_actual)

    impacto_esp = base_esp * (desc_esp_nuevo - desc_esp_actual) / 100
    impacto_para = base_para * (desc_para_nuevo - desc_para_actual) / 100
    impacto_transfer = base_transfer * (desc_transfer_nuevo - desc_transfer_actual) / 100
    impacto_bitransfer = base_bitransfer * (desc_bitransfer_nuevo - desc_bitransfer_actual) / 100
    impacto_cara = unidades_cara * (desc_cara_nuevo - desc_cara_actual)

    cargos_actuales = _num(base.get("cargos_actuales")) + _num(base.get("franquicia_actual"))
    nuevos_cargos = _num(escenario.get("nuevo_cargo_fijo_mensual"), cargos_actuales)
    penalizacion = _num(escenario.get("penalizacion_bajo_consumo_proveedor_afectado"))
    liquidaciones_adicionales = _num(escenario.get("liquidaciones_adicionales"))
    liquidaciones_perdidas = _num(escenario.get("liquidaciones_perdidas"))
    impacto_cargos = nuevos_cargos - cargos_actuales

    impacto_mensual = (
        impacto_esp
        + impacto_para
        + impacto_transfer
        + impacto_bitransfer
        + impacto_cara
        - impacto_cargos
        - penalizacion
        + liquidaciones_adicionales
        - liquidaciones_perdidas
    )

    detalle_categoria = pd.DataFrame([
        {"categoria": "especialidad_normal", "base": base_esp, "actual": desc_esp_actual, "nuevo": desc_esp_nuevo, "impacto_mensual": impacto_esp},
        {"categoria": "parafarmacia_no_financiada", "base": base_para, "actual": desc_para_actual, "nuevo": desc_para_nuevo, "impacto_mensual": impacto_para},
        {"categoria": "especialidad_cara_euros", "base": unidades_cara, "actual": desc_cara_actual, "nuevo": desc_cara_nuevo, "impacto_mensual": impacto_cara},
        {"categoria": "parafarmacia_financiada", "base": base_parafarmacia_financiada, "actual": 0.0, "nuevo": 0.0, "impacto_mensual": 0.0},
        {"categoria": "transfer", "base": base_transfer, "actual": desc_transfer_actual, "nuevo": desc_transfer_nuevo, "impacto_mensual": impacto_transfer},
        {"categoria": "bitransfer", "base": base_bitransfer, "actual": desc_bitransfer_actual, "nuevo": desc_bitransfer_nuevo, "impacto_mensual": impacto_bitransfer},
        {"categoria": "clubes", "base": base_clubes, "actual": 0.0, "nuevo": 0.0, "impacto_mensual": 0.0},
        {"categoria": "cargos_fijos", "base": 0.0, "actual": cargos_actuales, "nuevo": nuevos_cargos, "impacto_mensual": -impacto_cargos},
        {"categoria": "penalizaciones", "base": 0.0, "actual": 0.0, "nuevo": penalizacion, "impacto_mensual": -penalizacion},
        {"categoria": "liquidaciones", "base": 0.0, "actual": liquidaciones_perdidas, "nuevo": liquidaciones_adicionales, "impacto_mensual": liquidaciones_adicionales - liquidaciones_perdidas},
    ])
    for col in ["base", "actual", "nuevo", "impacto_mensual"]:
        detalle_categoria[col] = detalle_categoria[col].round(2)

    riesgos = []
    oportunidades = []
    if penalizacion > 0:
        riesgos.append(f"El proveedor afectado incorpora una penalizacion mensual de {penalizacion:.2f} EUR.")
    if impacto_cargos > 0:
        riesgos.append(f"Los cargos fijos suben {impacto_cargos:.2f} EUR al mes.")
    if liquidaciones_perdidas > 0:
        riesgos.append(f"Se estiman liquidaciones perdidas por {liquidaciones_perdidas:.2f} EUR al mes.")
    if base_clubes > 0:
        riesgos.append("Hay compras en clubes: revisa si la derivacion de volumen mueve escalados o liquidaciones.")
    if base_parafarmacia_financiada > 0:
        riesgos.append("La parafarmacia financiada queda excluida de descuentos comerciales generales.")
    if desc_esp_nuevo > desc_esp_actual:
        oportunidades.append(f"Mejora de especialidad normal: {desc_esp_actual:.2f}% -> {desc_esp_nuevo:.2f}%.")
    if desc_para_nuevo > desc_para_actual:
        oportunidades.append(f"Mejora de parafarmacia no financiada: {desc_para_actual:.2f}% -> {desc_para_nuevo:.2f}%.")
    if liquidaciones_adicionales > 0:
        oportunidades.append(f"Liquidaciones adicionales estimadas: {liquidaciones_adicionales:.2f} EUR al mes.")

    detalle_proveedor = pd.DataFrame([{
        "proveedor": proveedor,
        **derivacion,
        "cargos_actuales": round(cargos_actuales, 2),
        "nuevos_cargos": round(nuevos_cargos, 2),
        "penalizacion_proveedor_afectado": round(penalizacion, 2),
        "impacto_mensual": round(impacto_mensual, 2),
        "impacto_anual": round(impacto_mensual * 12, 2),
    }])

    resultado = {
        "escenario_actual": base,
        "escenario_propuesto": dict(escenario),
        "impacto_mensual": round(float(impacto_mensual), 2),
        "impacto_anual": round(float(impacto_mensual * 12), 2),
        "detalle_por_proveedor": detalle_proveedor,
        "detalle_por_categoria": detalle_categoria,
        "riesgos": riesgos,
        "oportunidades": oportunidades,
    }
    resultado["recomendacion"] = generar_recomendacion_simulacion(resultado)
    return resultado


def comparar_escenarios(base_actual, escenarios):
    resultados = []
    for escenario in escenarios or []:
        resultado = simular_escenario_condiciones(base_actual, escenario)
        resultados.append({
            "escenario": escenario.get("nombre", "escenario"),
            "proveedor": escenario.get("proveedor_destino"),
            "impacto_mensual": resultado["impacto_mensual"],
            "impacto_anual": resultado["impacto_anual"],
            "recomendacion": resultado["recomendacion"],
        })
    return pd.DataFrame(resultados).sort_values("impacto_mensual", ascending=False).reset_index(drop=True)


def generar_resumen_simulacion(resultado_simulacion):
    if not resultado_simulacion:
        return {}
    return {
        "impacto_mensual": resultado_simulacion.get("impacto_mensual", 0.0),
        "impacto_anual": resultado_simulacion.get("impacto_anual", 0.0),
        "riesgos": resultado_simulacion.get("riesgos", []),
        "oportunidades": resultado_simulacion.get("oportunidades", []),
        "recomendacion": resultado_simulacion.get("recomendacion", ""),
    }


def generar_recomendacion_simulacion(resultado_simulacion):
    impacto = _num((resultado_simulacion or {}).get("impacto_mensual"))
    riesgos = (resultado_simulacion or {}).get("riesgos", [])
    if impacto > 0:
        texto = f"El escenario mejora el resultado en {impacto:.2f} EUR al mes ({impacto * 12:.2f} EUR al ano)."
        if riesgos:
            texto += " Conviene negociar o vigilar los riesgos antes de aceptarlo."
        return texto
    if impacto < 0:
        return f"El escenario empeora el resultado en {abs(impacto):.2f} EUR al mes. No compensa salvo que haya ventajas cualitativas externas."
    return "El escenario es neutro con los datos disponibles."
