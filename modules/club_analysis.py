import unicodedata

import pandas as pd

from modules import vida_pharma_clubes


def _df_seguro(df):
    if df is None:
        return pd.DataFrame()
    return df.copy()


def _normalizar_texto(valor):
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return texto.lower().strip()


def _normalizar_columna(columna):
    texto = _normalizar_texto(columna)
    for char in [" ", "-", "/", ".", "(", ")"]:
        texto = texto.replace(char, "_")
    while "__" in texto:
        texto = texto.replace("__", "_")
    return texto.strip("_")


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


def _renombrar_columnas_flexibles(df):
    df = _df_seguro(df)
    if df.empty:
        return df

    equivalencias = {
        "laboratorio": ["laboratorio", "lab", "laboratorio_efg", "proveedor_laboratorio"],
        "familia": ["familia", "categoria", "subfamilia"],
        "grupo": ["grupo", "grupo_homogeneo", "agrupacion", "programa"],
        "compra_real": ["compra_real", "compra_actual", "importe_compra", "compras", "base_compra", "venta_bruta"],
        "objetivo_tramo": ["objetivo_tramo", "objetivo", "tramo_objetivo", "objetivo_actual"],
        "tramo_actual": ["tramo_actual", "escala_actual", "escalado_actual"],
        "siguiente_tramo": ["siguiente_tramo", "proximo_tramo", "objetivo_siguiente", "siguiente_objetivo"],
        "liquidacion_actual": ["liquidacion_actual", "liquidacion", "importe_liquidacion", "abono_actual"],
        "liquidacion_potencial": ["liquidacion_potencial", "potencial", "liquidacion_siguiente", "abono_potencial"],
        "diferencia_para_siguiente_tramo": [
            "diferencia_para_siguiente_tramo",
            "diferencia_siguiente_tramo",
            "pendiente_siguiente_tramo",
            "falta_para_siguiente_tramo",
            "importe_pendiente",
        ],
        "porcentaje_liquidacion": ["porcentaje_liquidacion", "pct_liquidacion", "liquidacion_pct", "porcentaje"],
    }

    columnas_norm = {_normalizar_columna(col): col for col in df.columns}
    renombrar = {}
    for destino, opciones in equivalencias.items():
        for opcion in opciones:
            origen = columnas_norm.get(_normalizar_columna(opcion))
            if origen is not None:
                renombrar[origen] = destino
                break

    return df.rename(columns=renombrar)


def detectar_compras_club(df):
    df = _df_seguro(df)
    if df.empty:
        return df

    texto = pd.Series("", index=df.index, dtype="object")
    columnas_prioritarias = [
        "seccion_albaran",
        "descripcion",
        "observaciones",
        "concepto",
        "tipo_compra",
        "categoria",
        "categoría",
        "descuento",
        "cargo",
        "descuento_cargo",
        "dc_descuento_cargo",
        "descuento cargo",
        "dc",
    ]
    columnas_norm = {_normalizar_columna(col): col for col in df.columns}
    for columna in columnas_prioritarias:
        origen = columnas_norm.get(_normalizar_columna(columna))
        if origen is not None:
            texto = texto + " " + df[origen].astype(str)

    for columna in df.columns:
        nombre = _normalizar_columna(columna)
        if ("categoria" in nombre or "descuento" in nombre or "cargo" in nombre) and columna not in columnas_prioritarias:
            texto = texto + " " + df[columna].astype(str)

    texto = texto.map(_normalizar_texto)
    mask = texto.str.contains("club|seleccion genericos|seleccion generica|programa genericos", na=False)
    if "seccion_albaran" in df.columns:
        mask = mask | df["seccion_albaran"].astype(str).map(_normalizar_texto).eq("club")

    return df[mask].copy()


def _mask_club(df):
    if df is None or df.empty:
        return pd.Series([], dtype=bool)
    clubes = detectar_compras_club(df)
    return pd.Series(df.index.isin(clubes.index), index=df.index)


def calcular_descuento_habitual_especialidad(df_compras):
    df = _df_seguro(df_compras)
    if df.empty:
        return None

    seccion = df.get("seccion_albaran", pd.Series("", index=df.index)).astype(str).map(_normalizar_texto)
    tipo_compra = df.get("tipo_compra", pd.Series("", index=df.index)).astype(str).map(_normalizar_texto)
    iva = _serie_numerica(df, "iva")
    bruto = _serie_numerica(df, "bruto")
    neto = _serie_numerica(df, "neto")
    unidades = _serie_numerica(df, "unidades")
    es_cara = df.get("es_especialidad_cara", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    es_club = _mask_club(df)

    mask = (
        tipo_compra.eq("goteo")
        & seccion.eq("especialidad")
        & iva.sub(4).abs().le(0.01)
        & bruto.gt(0)
        & neto.ge(0)
        & unidades.ge(0)
        & ~es_cara
        & ~es_club
    )
    base = df[mask].copy()
    if base.empty:
        return None

    bruto_total = float(bruto[mask].sum())
    neto_total = float(neto[mask].sum())
    if bruto_total <= 0:
        return None

    descuento = (1 - (neto_total / bruto_total)) * 100
    if descuento <= 0 or descuento >= 100:
        return None
    return round(float(descuento), 2)


def _valor_desglose(desglose, bloque, columna):
    if desglose is None or desglose.empty or "bloque" not in desglose.columns or columna not in desglose.columns:
        return None
    fila = desglose[desglose["bloque"].astype(str).str.lower().eq(bloque)]
    if fila.empty:
        return None
    valor = pd.to_numeric(pd.Series([fila.iloc[0].get(columna)]), errors="coerce").iloc[0]
    return None if pd.isna(valor) else float(valor)


def calcular_perdida_oportunidad_club_simulada(
    df_club_sin_liquidacion,
    desglose=None,
    descuento_fallback=None,
):
    df_club = _df_seguro(df_club_sin_liquidacion)
    compra_club = float(_serie_numerica(df_club, "bruto").sum()) if not df_club.empty else 0.0
    unidades_club = float(_serie_numerica(df_club, "unidades").abs().sum()) if not df_club.empty else 0.0
    if compra_club <= 0:
        return {
            "perdida_euros": 0.0,
            "descuento_pct": None,
            "metodo": "sin_compra_club",
            "cargo_incremental_franquicia": 0.0,
        }

    bruto_especialidad = _valor_desglose(desglose, "especialidad", "bruto") or 0.0
    neto_especialidad = _valor_desglose(desglose, "especialidad", "neto") or 0.0
    unidades_especialidad = _valor_desglose(desglose, "especialidad", "unidades") or 0.0
    cargos_especialidad = _valor_desglose(desglose, "especialidad", "cargos_imputados") or 0.0
    bruto_parafarmacia = _valor_desglose(desglose, "parafarmacia", "bruto") or 0.0
    unidades_parafarmacia = _valor_desglose(desglose, "parafarmacia", "unidades") or 0.0
    cargos_parafarmacia = _valor_desglose(desglose, "parafarmacia", "cargos_imputados") or 0.0
    unidades_goteo_puro = _valor_desglose(desglose, "goteo_puro", "unidades") or 0.0
    cargos_goteo_puro = _valor_desglose(desglose, "goteo_puro", "cargos_imputados") or 0.0

    cargo_total_franquicia = cargos_goteo_puro or (cargos_especialidad + cargos_parafarmacia)
    unidades_elegibles_actuales = unidades_goteo_puro or (unidades_especialidad + unidades_parafarmacia)
    unidades_elegibles_simuladas = unidades_elegibles_actuales + unidades_club
    cargo_especialidad_actual = cargos_especialidad
    cargo_especialidad_simulado = (
        cargo_total_franquicia * ((unidades_especialidad + unidades_club) / unidades_elegibles_simuladas)
        if cargo_total_franquicia > 0 and unidades_elegibles_simuladas > 0
        else cargo_especialidad_actual
    )
    cargo_incremental = max(0.0, cargo_especialidad_simulado - cargo_especialidad_actual)
    cargo_unitario_simulado = (
        cargo_total_franquicia / unidades_elegibles_simuladas
        if cargo_total_franquicia > 0 and unidades_elegibles_simuladas > 0
        else 0.0
    )
    cargo_club_simulado = cargo_unitario_simulado * unidades_club

    if bruto_especialidad > 0:
        descuento_pct = (1 - (neto_especialidad / bruto_especialidad)) * 100
        metodo = "descuento_aparente_especialidad_menos_franquicia_simulada"
    elif descuento_fallback is not None:
        descuento_pct = float(descuento_fallback)
        metodo = "fallback_menos_franquicia_simulada"
    else:
        return {
            "perdida_euros": 0.0,
            "descuento_pct": None,
            "metodo": "sin_referencia",
            "cargo_incremental_franquicia": round(cargo_incremental, 2),
        }

    descuento_bruto_simulado = compra_club * (float(descuento_pct) / 100)
    perdida = descuento_bruto_simulado - cargo_club_simulado
    return {
        "perdida_euros": round(float(perdida), 2),
        "descuento_pct": round(float(descuento_pct), 2),
        "metodo": metodo,
        "descuento_bruto_simulado": round(float(descuento_bruto_simulado), 2),
        "cargo_incremental_franquicia": round(float(cargo_incremental), 2),
        "cargo_club_franquicia_simulada": round(float(cargo_club_simulado), 2),
        "cargo_unitario_franquicia_simulada": round(float(cargo_unitario_simulado), 4),
        "cargo_especialidad_actual": round(float(cargo_especialidad_actual), 2),
        "cargo_especialidad_simulado": round(float(cargo_especialidad_simulado), 2),
        "unidades_club_simuladas": round(float(unidades_club), 2),
        "unidades_elegibles_simuladas": round(float(unidades_elegibles_simuladas), 2),
    }


def calcular_descuento_referencia_clubes(
    df_compras,
    desglose=None,
    compra_club_sin_liquidacion=0.0,
    descuento_fallback=None,
):
    bruto_especialidad = _valor_desglose(desglose, "especialidad", "bruto")
    neto_especialidad = _valor_desglose(desglose, "especialidad", "neto")
    cargos_especialidad = _valor_desglose(desglose, "especialidad", "cargos_imputados") or 0.0
    aparente_especialidad = _valor_desglose(desglose, "especialidad", "descuento_aparente_pct")
    real_especialidad = _valor_desglose(desglose, "especialidad", "descuento_real_final_pct")

    if bruto_especialidad and bruto_especialidad > 0:
        if aparente_especialidad is None and neto_especialidad is not None:
            aparente_especialidad = (1 - (neto_especialidad / bruto_especialidad)) * 100

        if abs(cargos_especialidad) <= 0.0001:
            referencia = aparente_especialidad
            metodo = "descuento_aparente_especialidad"
        else:
            club_bruto = max(0.0, float(compra_club_sin_liquidacion or 0.0))
            aparente = float(aparente_especialidad or 0.0)
            club_neto_simulado = club_bruto * (1 - aparente / 100)
            bruto_simulado = bruto_especialidad + club_bruto
            coste_simulado = float(neto_especialidad or 0.0) + club_neto_simulado + cargos_especialidad
            referencia = (1 - (coste_simulado / bruto_simulado)) * 100 if bruto_simulado else real_especialidad
            metodo = "descuento_real_simulado_especialidad_con_clubes"

        if referencia is not None:
            return {
                "descuento_pct": round(float(referencia), 2),
                "metodo": metodo,
                "descuento_aparente_especialidad_pct": None if aparente_especialidad is None else round(float(aparente_especialidad), 2),
                "cargos_especialidad": round(float(cargos_especialidad), 2),
                "compra_club_simulada": round(float(compra_club_sin_liquidacion or 0.0), 2),
            }

    descuento_agregado = calcular_descuento_habitual_especialidad(df_compras)
    if descuento_agregado is not None:
        return {
            "descuento_pct": descuento_agregado,
            "metodo": "descuento_agregado_especialidad",
            "descuento_aparente_especialidad_pct": descuento_agregado,
            "cargos_especialidad": 0.0,
            "compra_club_simulada": round(float(compra_club_sin_liquidacion or 0.0), 2),
        }

    if descuento_fallback is not None:
        return {
            "descuento_pct": round(float(descuento_fallback), 2),
            "metodo": "descuento_fallback_goteo_real",
            "descuento_aparente_especialidad_pct": None,
            "cargos_especialidad": 0.0,
            "compra_club_simulada": round(float(compra_club_sin_liquidacion or 0.0), 2),
        }

    return {
        "descuento_pct": None,
        "metodo": "sin_referencia",
        "descuento_aparente_especialidad_pct": None,
        "cargos_especialidad": 0.0,
        "compra_club_simulada": round(float(compra_club_sin_liquidacion or 0.0), 2),
    }


def normalizar_documento_clubes(df):
    df = _renombrar_columnas_flexibles(df)
    if df.empty:
        return df

    for columna in [
        "compra_real",
        "objetivo_tramo",
        "siguiente_tramo",
        "liquidacion_actual",
        "liquidacion_potencial",
        "diferencia_para_siguiente_tramo",
        "porcentaje_liquidacion",
    ]:
        if columna in df.columns:
            df[columna] = _serie_numerica(df, columna)

    if "diferencia_para_siguiente_tramo" not in df.columns:
        compra = _serie_numerica(df, "compra_real")
        siguiente = _serie_numerica(df, "siguiente_tramo")
        df["diferencia_para_siguiente_tramo"] = (siguiente - compra).clip(lower=0)

    if "liquidacion_actual" not in df.columns:
        df["liquidacion_actual"] = 0.0
    if "liquidacion_potencial" not in df.columns:
        df["liquidacion_potencial"] = 0.0

    return df


def calcular_compra_club_sin_liquidacion(df_club, df_liquidaciones):
    df_club = _df_seguro(df_club)
    if df_club.empty:
        return 0.0, 0.0, df_club

    df_club = df_club.copy()
    df_club["bruto_num"] = _serie_numerica(df_club, "bruto")
    df_club["tiene_liquidacion_club"] = False

    liquidaciones = normalizar_documento_clubes(df_liquidaciones)
    if liquidaciones.empty:
        compra_total = float(df_club["bruto_num"].sum())
        return 0.0, compra_total, df_club

    claves = [col for col in ["laboratorio", "familia", "grupo"] if col in df_club.columns and col in liquidaciones.columns]
    liquidaciones_con_abono = liquidaciones[_serie_numerica(liquidaciones, "liquidacion_actual").gt(0)].copy()

    if claves and not liquidaciones_con_abono.empty:
        claves_liquidadas = set(
            tuple(_normalizar_texto(valor) for valor in fila)
            for fila in liquidaciones_con_abono[claves].fillna("").itertuples(index=False, name=None)
        )

        def _tiene_liquidacion(row):
            clave = tuple(_normalizar_texto(row.get(col, "")) for col in claves)
            return clave in claves_liquidadas

        df_club["tiene_liquidacion_club"] = df_club.apply(_tiene_liquidacion, axis=1)
    elif "compra_real" in liquidaciones.columns:
        compra_con_liquidacion_aux = float(
            _serie_numerica(liquidaciones_con_abono, "compra_real").sum()
        )
        compra_total = float(df_club["bruto_num"].sum())
        compra_con_liquidacion = min(compra_total, max(0.0, compra_con_liquidacion_aux))
        compra_sin_liquidacion = max(0.0, compra_total - compra_con_liquidacion)
        return compra_con_liquidacion, compra_sin_liquidacion, df_club

    compra_con_liquidacion = float(df_club.loc[df_club["tiene_liquidacion_club"], "bruto_num"].sum())
    compra_sin_liquidacion = float(df_club.loc[~df_club["tiene_liquidacion_club"], "bruto_num"].sum())
    return compra_con_liquidacion, compra_sin_liquidacion, df_club


def calcular_perdida_vs_descuento_habitual(df_club, descuento_habitual):
    df_club = _df_seguro(df_club)
    if df_club.empty or descuento_habitual is None:
        return 0.0

    compra = float(_serie_numerica(df_club, "bruto").sum())
    return round(compra * (float(descuento_habitual) / 100), 2)


def calcular_diferencia_siguiente_tramo(df_escalados):
    escalados = normalizar_documento_clubes(df_escalados)
    if escalados.empty:
        return pd.DataFrame()

    columnas = [
        "laboratorio",
        "familia",
        "grupo",
        "modo",
        "compra_real",
        "base_liquidable",
        "siguiente_tramo",
        "diferencia_para_siguiente_tramo",
        "porcentaje_liquidacion",
        "liquidacion_esperada",
        "liquidacion_actual",
        "liquidacion_potencial",
        "fuente",
    ]
    columnas = [col for col in columnas if col in escalados.columns]
    return escalados[columnas].copy()


def calcular_liquidacion_potencial(df_escalados):
    escalados = normalizar_documento_clubes(df_escalados)
    if escalados.empty:
        return pd.DataFrame()

    diferencia = _serie_numerica(escalados, "diferencia_para_siguiente_tramo")
    oportunidades = escalados[diferencia.le(500)].copy()
    if oportunidades.empty:
        return pd.DataFrame()

    oportunidades["perdida_potencial"] = (
        _serie_numerica(oportunidades, "liquidacion_potencial")
        - _serie_numerica(oportunidades, "liquidacion_actual")
    ).clip(lower=0).round(2)
    oportunidades = oportunidades[oportunidades["perdida_potencial"].gt(0)].copy()

    columnas = [
        "laboratorio",
        "familia",
        "grupo",
        "modo",
        "compra_real",
        "base_liquidable",
        "siguiente_tramo",
        "diferencia_para_siguiente_tramo",
        "liquidacion_actual",
        "liquidacion_potencial",
        "perdida_potencial",
        "fuente",
    ]
    columnas = [col for col in columnas if col in oportunidades.columns]
    return oportunidades[columnas].sort_values("perdida_potencial", ascending=False).reset_index(drop=True)


def generar_resumen_clubes(
    proveedor,
    df_club,
    compra_con_liquidacion,
    compra_sin_liquidacion,
    perdida_vs_descuento_habitual,
    escalados,
    oportunidades,
    alertas,
):
    compra_total = float(_serie_numerica(df_club, "bruto").sum()) if df_club is not None and not df_club.empty else 0.0
    porcentaje_sin = (compra_sin_liquidacion / compra_total * 100) if compra_total else 0.0
    return {
        "proveedor": proveedor,
        "lineas_club": 0 if df_club is None else int(len(df_club)),
        "compra_total_club": round(compra_total, 2),
        "compra_con_liquidacion": round(float(compra_con_liquidacion), 2),
        "compra_sin_liquidacion": round(float(compra_sin_liquidacion), 2),
        "pct_club_sin_liquidacion": round(float(porcentaje_sin), 2),
        "perdida_vs_descuento_habitual": round(float(perdida_vs_descuento_habitual), 2),
        "escalados": escalados,
        "oportunidades_siguiente_tramo": oportunidades,
        "alertas": alertas,
    }


def actualizar_referencia_descuento_clubes(
    analisis_clubes,
    df_compras,
    desglose=None,
    descuento_goteo_real=None,
):
    if not analisis_clubes or not analisis_clubes.get("ok"):
        return analisis_clubes

    actualizado = dict(analisis_clubes)
    compra_sin_liquidacion = float(actualizado.get("compra_sin_liquidacion", 0) or 0)
    referencia = calcular_descuento_referencia_clubes(
        df_compras,
        desglose=desglose,
        compra_club_sin_liquidacion=compra_sin_liquidacion,
        descuento_fallback=descuento_goteo_real,
    )
    descuento_habitual = referencia.get("descuento_pct")
    actualizado["descuento_habitual_referencia_pct"] = descuento_habitual
    actualizado["descuento_habitual_referencia_metodo"] = referencia.get("metodo")
    actualizado["descuento_aparente_especialidad_pct"] = referencia.get("descuento_aparente_especialidad_pct")
    actualizado["cargos_especialidad_referencia"] = referencia.get("cargos_especialidad")
    actualizado["compra_club_simulada_referencia"] = referencia.get("compra_club_simulada")
    detalle_club = _df_seguro(actualizado.get("detalle_club"))
    if not detalle_club.empty and "tiene_liquidacion_club" in detalle_club.columns:
        club_sin_liquidacion = detalle_club[
            ~detalle_club["tiene_liquidacion_club"].fillna(False).astype(bool)
        ].copy()
    else:
        club_sin_liquidacion = detalle_club
    simulacion = calcular_perdida_oportunidad_club_simulada(
        club_sin_liquidacion,
        desglose=desglose,
        descuento_fallback=descuento_habitual,
    )
    actualizado["perdida_vs_descuento_habitual"] = simulacion.get("perdida_euros", 0.0)
    actualizado["descuento_habitual_referencia_pct"] = simulacion.get("descuento_pct", descuento_habitual)
    actualizado["descuento_habitual_referencia_metodo"] = simulacion.get("metodo", referencia.get("metodo"))
    actualizado["descuento_bruto_club_simulado"] = simulacion.get("descuento_bruto_simulado", 0.0)
    actualizado["cargo_incremental_franquicia_simulada"] = simulacion.get("cargo_incremental_franquicia", 0.0)
    actualizado["cargo_club_franquicia_simulada"] = simulacion.get("cargo_club_franquicia_simulada", 0.0)
    actualizado["cargo_unitario_franquicia_simulada"] = simulacion.get("cargo_unitario_franquicia_simulada", 0.0)
    actualizado["unidades_club_simuladas"] = simulacion.get("unidades_club_simuladas", 0.0)
    actualizado["unidades_elegibles_simuladas"] = simulacion.get("unidades_elegibles_simuladas", 0.0)
    return actualizado


def analizar_clubes(
    df_compras,
    df_escalados=None,
    df_liquidaciones=None,
    proveedor=None,
    descuento_goteo_real=None,
    desglose=None,
):
    df_club = detectar_compras_club(df_compras)
    alertas = []

    if df_club.empty:
        return {
            "ok": False,
            "proveedor": proveedor,
            "mensaje": "No se han detectado compras de clubes o seleccion genericos.",
            "compra_total_club": 0.0,
            "compra_con_liquidacion": 0.0,
            "compra_sin_liquidacion": 0.0,
            "pct_club_sin_liquidacion": 0.0,
            "perdida_vs_descuento_habitual": 0.0,
            "escalados": pd.DataFrame(),
            "oportunidades_siguiente_tramo": pd.DataFrame(),
            "alertas": [],
        }

    documento = df_liquidaciones if df_liquidaciones is not None else df_escalados
    documento_norm = normalizar_documento_clubes(documento)
    escalados_master = pd.DataFrame()
    if documento_norm.empty:
        if _normalizar_texto(proveedor) in {"bidafarma", "vida pharma", "vidapharma"}:
            escalados_master = vida_pharma_clubes.generar_prevision_escalados(df_compras)
        if escalados_master.empty:
            alertas.append("Falta documento de escalados/liquidaciones para calcular perdida real.")
        else:
            modo = str(escalados_master["modo"].iloc[0]).upper()
            alertas.append(
                "Se usa la base maestra de escalados Vida Pharma "
                f"({modo}) para estimar liquidaciones esperadas. "
                "Faltan listados de moleculas para separar suma de escalado y base liquidable definitiva."
            )
    else:
        columnas_minimas = {"liquidacion_actual", "liquidacion_potencial", "siguiente_tramo"}
        faltantes = sorted(col for col in columnas_minimas if col not in documento_norm.columns)
        if faltantes:
            alertas.append("Documento auxiliar parcial: faltan columnas " + ", ".join(faltantes) + ".")

    compra_con_liquidacion, compra_sin_liquidacion, df_club_marcado = calcular_compra_club_sin_liquidacion(
        df_club,
        documento_norm,
    )
    club_sin_liquidacion = df_club_marcado[
        ~df_club_marcado.get(
            "tiene_liquidacion_club",
            pd.Series(False, index=df_club_marcado.index),
        ).fillna(False).astype(bool)
    ].copy()

    referencia = calcular_descuento_referencia_clubes(
        df_compras,
        desglose=desglose,
        compra_club_sin_liquidacion=compra_sin_liquidacion,
        descuento_fallback=descuento_goteo_real,
    )
    descuento_habitual = referencia.get("descuento_pct")
    simulacion_perdida = calcular_perdida_oportunidad_club_simulada(
        club_sin_liquidacion,
        desglose=desglose,
        descuento_fallback=descuento_habitual,
    )
    perdida_vs_goteo = simulacion_perdida.get("perdida_euros", 0.0)
    if simulacion_perdida.get("descuento_pct") is not None:
        descuento_habitual = simulacion_perdida.get("descuento_pct")
    if descuento_habitual is None:
        alertas.append("No hay descuento habitual de especialidad disponible para estimar perdida vs condicion comercial.")

    base_escalados = escalados_master if not escalados_master.empty else documento_norm
    escalados = calcular_diferencia_siguiente_tramo(base_escalados)
    oportunidades = calcular_liquidacion_potencial(base_escalados)

    return {
        "ok": True,
        **generar_resumen_clubes(
            proveedor=proveedor,
            df_club=df_club_marcado,
            compra_con_liquidacion=compra_con_liquidacion,
            compra_sin_liquidacion=compra_sin_liquidacion,
            perdida_vs_descuento_habitual=perdida_vs_goteo,
            escalados=escalados,
            oportunidades=oportunidades,
            alertas=alertas,
        ),
        "detalle_club": df_club_marcado,
        "descuento_habitual_referencia_pct": descuento_habitual,
        "descuento_habitual_referencia_metodo": simulacion_perdida.get("metodo", referencia.get("metodo")),
        "descuento_aparente_especialidad_pct": referencia.get("descuento_aparente_especialidad_pct"),
        "cargos_especialidad_referencia": referencia.get("cargos_especialidad"),
        "compra_club_simulada_referencia": referencia.get("compra_club_simulada"),
        "descuento_bruto_club_simulado": simulacion_perdida.get("descuento_bruto_simulado", 0.0),
        "cargo_incremental_franquicia_simulada": simulacion_perdida.get("cargo_incremental_franquicia", 0.0),
        "cargo_club_franquicia_simulada": simulacion_perdida.get("cargo_club_franquicia_simulada", 0.0),
        "cargo_unitario_franquicia_simulada": simulacion_perdida.get("cargo_unitario_franquicia_simulada", 0.0),
        "unidades_club_simuladas": simulacion_perdida.get("unidades_club_simuladas", 0.0),
        "unidades_elegibles_simuladas": simulacion_perdida.get("unidades_elegibles_simuladas", 0.0),
    }
