import json

import pandas as pd


def _limitar_lista(valores, limite=5):
    if valores is None:
        return []
    if isinstance(valores, str):
        return [valores] if valores else []
    try:
        return list(valores)[:limite]
    except TypeError:
        return []


def _df_a_registros_agregados(df, limite=10):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    permitido = [
        "bloque",
        "lineas",
        "unidades",
        "bruto",
        "neto",
        "descuento_aparente_pct",
        "cargos_imputados",
        "descuento_real_final_pct",
        "tipo",
        "importe",
        "origen",
        "lineas_detectadas",
        "bruto_total",
        "neto_total",
        "descuento_euros",
        "base_iva4_total",
        "base_iva4_especialidad_cara",
        "base_iva4_sujeta_ajuste",
        "diferencia_absoluta",
        "diferencia_pct",
    ]
    columnas = [col for col in permitido if col in df.columns]
    if not columnas:
        return []

    return df[columnas].head(limite).where(pd.notna(df[columnas].head(limite)), None).to_dict("records")


def construir_payload_agregado(
    contexto_farmacia,
    analisis_distribuidora,
    analisis_ventas=None,
    analisis_stock=None,
):
    analisis_distribuidora = analisis_distribuidora or {}
    contexto_farmacia = contexto_farmacia or {}
    resumen = analisis_distribuidora.get("resumen", {}) or {}

    return {
        "contexto_farmacia": {
            "provincia_ciudad": contexto_farmacia.get("provincia_ciudad") or "",
            "tipo_zona": contexto_farmacia.get("tipo_zona") or "",
            "epoca_ano": contexto_farmacia.get("epoca_ano") or "",
            "campana_activa": _limitar_lista(contexto_farmacia.get("campana_activa"), 10),
            "perfil_farmacia": _limitar_lista(contexto_farmacia.get("perfil_farmacia"), 10),
        },
        "distribuidora": {
            "proveedor": analisis_distribuidora.get("proveedor"),
            "periodo": resumen.get("periodo"),
            "compra_bruta_total": resumen.get("compra_bruta_total"),
            "compra_neta_total": resumen.get("compra_neta_total"),
            "abonos_totales": resumen.get("abonos_totales"),
            "unidades_totales": resumen.get("unidades_totales"),
            "descuento_medio_general": resumen.get("descuento_medio_general"),
            "desglose": _df_a_registros_agregados(analisis_distribuidora.get("desglose"), 12),
            "cargos": _df_a_registros_agregados(analisis_distribuidora.get("cargos"), 12),
            "especialidad_cara": _df_a_registros_agregados(analisis_distribuidora.get("especialidad_cara"), 1),
            "top_impacto_anonimizado": _df_a_registros_agregados(analisis_distribuidora.get("top_impacto"), 10),
        },
        "ventas_agregado": analisis_ventas if isinstance(analisis_ventas, dict) else None,
        "stock_agregado": analisis_stock if isinstance(analisis_stock, dict) else None,
    }


def _extraer_bloques(payload):
    return payload.get("distribuidora", {}).get("desglose", []) or []


def _extraer_cargos(payload):
    return payload.get("distribuidora", {}).get("cargos", []) or []


def _extraer_especialidad_cara(payload):
    datos = payload.get("distribuidora", {}).get("especialidad_cara", []) or []
    return datos[0] if datos else {}


def _recomendaciones_locales(payload):
    distribuidora = payload.get("distribuidora", {})
    contexto = payload.get("contexto_farmacia", {})
    bloques = _extraer_bloques(payload)
    cargos = _extraer_cargos(payload)
    especialidad_cara = _extraer_especialidad_cara(payload)

    oportunidades = []
    riesgos = []
    pedido = []
    negociacion = []
    acciones = []
    advertencias = [
        "Recomendaciones generadas con datos agregados. No sustituyen la revisión profesional de la farmacia.",
    ]

    compra_bruta = float(distribuidora.get("compra_bruta_total") or 0)
    descuento = distribuidora.get("descuento_medio_general")
    proveedor = distribuidora.get("proveedor") or "la distribuidora"

    bloques_ordenados = sorted(
        bloques,
        key=lambda item: float(item.get("bruto") or 0),
        reverse=True,
    )
    if bloques_ordenados:
        principal = bloques_ordenados[0]
        oportunidades.append(
            f"El mayor volumen se concentra en {principal.get('bloque')}, con {principal.get('bruto', 0)} € brutos. Conviene revisar condiciones y mix de compra en ese bloque."
        )

    if descuento is not None and float(descuento) < 3:
        riesgos.append(
            f"El descuento medio general de {proveedor} es bajo ({float(descuento):.2f}%). Puede haber margen de renegociación o cambio de canal."
        )
        negociacion.append(
            "Solicitar revisión de condiciones para los bloques con mayor volumen y menor descuento real."
        )

    cargos_totales = sum(float(cargo.get("importe") or 0) for cargo in cargos)
    if compra_bruta and cargos_totales / compra_bruta > 0.01:
        riesgos.append(
            f"Los cargos agregados representan aproximadamente {(cargos_totales / compra_bruta) * 100:.2f}% de la compra bruta."
        )
        oportunidades.append(
            "Analizar si los cargos de gestión, servicios o logística pueden compensarse con rappels, escalados o cambios de proveedor."
        )

    if float(especialidad_cara.get("lineas_detectadas") or 0) > 0:
        oportunidades.append(
            "Hay especialidad cara/RDL 4/2010 detectada. Mantenerla separada del descuento porcentual y revisar descuentos en euros por unidad."
        )
        acciones.append(
            "Comprobar si las líneas de especialidad cara tienen descuento en euros y compararlo entre proveedores."
        )

    campanas = contexto.get("campana_activa") or []
    epoca = contexto.get("epoca_ano") or ""
    if "protección solar" in campanas or "turismo verano" in campanas or epoca == "verano":
        pedido.append(
            "Preparar propuesta estacional de protección solar, hidratación, repelentes y productos de rotación turística."
        )
    if "gripe/resfriado" in campanas or epoca == "invierno":
        pedido.append(
            "Reforzar familias de gripe/resfriado, mucolíticos, antitusivos, termómetros y autocuidado respiratorio."
        )
    if "alergia" in campanas or epoca == "primavera":
        pedido.append(
            "Revisar stock de antihistamínicos, colirios, sprays nasales y productos complementarios de alergia."
        )
    if "dermocosmética" in campanas or "dermocosmética" in (contexto.get("perfil_farmacia") or []):
        pedido.append(
            "Priorizar dermocosmética de alta rotación y revisar acuerdos activos por laboratorio antes de ampliar pedido."
        )

    if not pedido:
        pedido.append(
            "Todavía no hay contexto suficiente para una propuesta de pedido específica. Añade campaña activa, perfil y cruces con ventas/stock."
        )

    acciones.extend([
        "Generar análisis por distribuidora y comparar descuentos reales por bloque.",
        "Cruzar ventas y stock antes de convertir estas recomendaciones en pedido.",
        "Revisar los cargos agregados más relevantes y su impacto sobre el margen neto real.",
    ])

    resumen = (
        f"Se ha analizado información agregada de {proveedor}. "
        f"La compra bruta agregada es {compra_bruta:.2f} € y el descuento medio general es "
        f"{'-' if descuento is None else f'{float(descuento):.2f}%'}."
    )

    return {
        "resumen_ejecutivo": resumen,
        "riesgos_detectados": riesgos[:5],
        "oportunidades": oportunidades[:5],
        "recomendaciones_pedido": pedido[:5],
        "recomendaciones_negociacion": negociacion[:5],
        "acciones_prioritarias": acciones[:5],
        "advertencias": advertencias,
        "modo": "local_privacidad",
    }


def _api_key_disponible():
    try:
        import streamlit as st
        return bool(st.secrets.get("OPENAI_API_KEY", ""))
    except Exception:
        return False


def generar_recomendaciones_ia(
    contexto_farmacia,
    analisis_distribuidora,
    analisis_ventas=None,
    analisis_stock=None,
):
    payload = construir_payload_agregado(
        contexto_farmacia,
        analisis_distribuidora,
        analisis_ventas=analisis_ventas,
        analisis_stock=analisis_stock,
    )

    if not _api_key_disponible():
        return _recomendaciones_locales(payload)

    # Integración futura:
    # - usar st.secrets["OPENAI_API_KEY"]
    # - enviar solo `payload`
    # - exigir respuesta JSON con las mismas claves estructuradas.
    # Por privacidad, la primera versión mantiene el motor local incluso si existe clave.
    recomendaciones = _recomendaciones_locales(payload)
    recomendaciones["modo"] = "local_privacidad_api_preparada"
    recomendaciones["advertencias"].append(
        "OPENAI_API_KEY está configurada, pero esta versión aún usa reglas locales para evitar enviar datos fuera de la app."
    )
    return recomendaciones


def payload_debug_json(contexto_farmacia, analisis_distribuidora, analisis_ventas=None, analisis_stock=None):
    payload = construir_payload_agregado(
        contexto_farmacia,
        analisis_distribuidora,
        analisis_ventas=analisis_ventas,
        analisis_stock=analisis_stock,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
