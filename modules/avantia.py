import re
import unicodedata

import pandas as pd


CARGO_AVANTIA_SIN_BONIFICACION = 2.0


def _normalizar_texto(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar_numero(valor):
    if pd.isna(valor):
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).replace("€", "").replace("%", "").replace(" ", "").strip()
    if not texto:
        return None

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


def _normalizar_pct(valor):
    numero = _normalizar_numero(valor)
    if numero is None:
        return None

    numero = abs(numero)
    if 0 < numero <= 0.2:
        return numero * 100

    if 0 < numero <= 20:
        return numero

    return None


def _serie_numerica(df, columna):
    if columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)

    return df[columna].apply(lambda valor: _normalizar_numero(valor) or 0.0)


def _importe_gasto(gastos_factura, tipo):
    if gastos_factura is None or gastos_factura.empty or "tipo" not in gastos_factura.columns:
        return 0.0

    gastos = gastos_factura[gastos_factura["tipo"] == tipo]
    if gastos.empty or "importe" not in gastos.columns:
        return 0.0

    return round(float(gastos["importe"].sum()), 2)


def hay_avantia(df_compras, gastos_factura):
    tiene_cuota = _importe_gasto(gastos_factura, "avantia") > 0
    tiene_lineas = False

    if df_compras is not None and not df_compras.empty and "descripcion" in df_compras.columns:
        tiene_lineas = (
            df_compras["descripcion"]
            .astype(str)
            .str.lower()
            .str.contains("avantia", na=False)
        ).any()

    return bool(tiene_cuota or tiene_lineas)


def _detectar_categoria(texto):
    if "generico" in texto:
        return "especialidad"
    if "resto" in texto and "laboratorio" in texto:
        return "parafarmacia"
    if "especial" in texto:
        return "especialidad"
    if "parafarm" in texto:
        return "parafarmacia"
    return None


def _normalizar_clave_columna(valor):
    texto = _normalizar_texto(valor)
    return re.sub(r"[^a-z0-9]+", "", texto)


def _detectar_columnas_resumen(fila):
    columnas = {}

    for indice, valor in enumerate(fila):
        clave = _normalizar_clave_columna(valor)
        if not clave:
            continue

        if clave == "tipo":
            columnas["tipo"] = indice
        elif clave == "cargo":
            columnas["cargo"] = indice
        elif "bonificacion" in clave and "gasto" in clave:
            columnas["bonificacion_gasto"] = indice

    if "tipo" in columnas and "cargo" in columnas:
        return columnas

    return {}


def _detectar_columnas_encabezado(fila):
    columnas = {}

    for indice, valor in enumerate(fila):
        texto = _normalizar_texto(valor)
        if not texto or texto == "nan":
            continue

        if "cargo" in texto:
            columnas["cargo"] = indice
        elif texto in {"pul", "p.u.l", "p u l"} or "pul" in texto:
            columnas["pul"] = indice
        elif "descuento" in texto or texto == "desc":
            columnas["descuento"] = indice
        elif "rentabilidad" in texto:
            columnas["rentabilidad"] = indice

    return columnas


def _numeros_posibles(fila):
    numeros = []
    for valor in fila:
        numero = _normalizar_pct(valor)
        if numero is not None:
            numeros.append(numero)
    return numeros


def _extraer_cargos_formato_resumen(df_raw):
    cargos = []
    columnas = {}
    categoria_actual = None

    for _, fila in df_raw.iterrows():
        valores = list(fila.values)
        texto_fila = " ".join(_normalizar_texto(valor) for valor in valores if pd.notna(valor))

        if not texto_fila:
            continue

        columnas_resumen = _detectar_columnas_resumen(valores)
        if columnas_resumen:
            columnas = columnas_resumen
            continue

        categoria_fila = _detectar_categoria(texto_fila)
        if categoria_fila:
            categoria_actual = categoria_fila
            continue

        if not columnas or not categoria_actual:
            continue

        tipo_idx = columnas.get("tipo", 0)
        tipo = _normalizar_texto(valores[tipo_idx]) if tipo_idx < len(valores) else ""
        if tipo != "goteo":
            continue

        cargo_idx = columnas.get("cargo")
        bonificacion_idx = columnas.get("bonificacion_gasto")

        cargo = _normalizar_numero(valores[cargo_idx]) if cargo_idx is not None and cargo_idx < len(valores) else 0.0
        bonificacion = (
            _normalizar_numero(valores[bonificacion_idx])
            if bonificacion_idx is not None and bonificacion_idx < len(valores)
            else 0.0
        )

        cargo = cargo or 0.0
        bonificacion = bonificacion or 0.0

        cargos.append({
            "categoria": categoria_actual,
            "tipo": "goteo",
            "cargo": round(cargo, 4),
            "bonificacion_gasto": round(bonificacion, 4),
            "gasto_neto": round(cargo - bonificacion, 4),
        })

    return cargos


def _extraer_cargos_desde_hoja(df_raw):
    cargos_resumen = _extraer_cargos_formato_resumen(df_raw)
    if cargos_resumen:
        return cargos_resumen

    cargos = []
    columnas = {}
    categoria_actual = None

    for _, fila in df_raw.iterrows():
        valores = list(fila.values)
        texto_fila = " ".join(_normalizar_texto(valor) for valor in valores if pd.notna(valor))

        if not texto_fila:
            continue

        columnas_detectadas = _detectar_columnas_encabezado(valores)
        if "cargo" in columnas_detectadas:
            columnas.update(columnas_detectadas)
            categoria_encabezado = _detectar_categoria(texto_fila)
            if categoria_encabezado:
                categoria_actual = categoria_encabezado
            continue

        categoria_fila = _detectar_categoria(texto_fila)
        if categoria_fila:
            categoria_actual = categoria_fila

        if not categoria_actual:
            continue

        cargo_pct = None
        if "cargo" in columnas and columnas["cargo"] < len(valores):
            cargo_pct = _normalizar_pct(valores[columnas["cargo"]])

        if cargo_pct is None and "cargo" in texto_fila:
            numeros = _numeros_posibles(valores)
            if numeros:
                cargo_pct = numeros[-1]

        if cargo_pct is None:
            continue

        cargos.append({
            "categoria": categoria_actual,
            "tipo": "goteo",
            "cargo_pct": round(cargo_pct, 4),
        })

    return cargos


def leer_cuadro_rentabilidad_avantia(file):
    hojas = pd.read_excel(file, sheet_name=None, header=None)
    cargos = []

    for _, df_raw in hojas.items():
        cargos.extend(_extraer_cargos_desde_hoja(df_raw))

    if not cargos:
        raise ValueError("No se han encontrado cargos de especialidad/parafarmacia en el cuadro Avantia.")

    df_cargos = pd.DataFrame(cargos)

    if "gasto_neto" in df_cargos.columns:
        df_cargos = (
            df_cargos
            .groupby("categoria", as_index=False)
            .agg(
                tipo=("tipo", "last"),
                cargo=("cargo", "sum"),
                bonificacion_gasto=("bonificacion_gasto", "sum"),
                gasto_neto=("gasto_neto", "sum"),
            )
        )
        for columna in ["cargo", "bonificacion_gasto", "gasto_neto"]:
            df_cargos[columna] = df_cargos[columna].round(4)
    else:
        df_cargos = (
            df_cargos
            .groupby("categoria", as_index=False)
            .agg(
                tipo=("tipo", "last"),
                cargo_pct=("cargo_pct", "last"),
            )
        )

    return df_cargos


def _cargo_categoria(df_cargos, categoria, defecto=2.0):
    if df_cargos is None or df_cargos.empty or "cargo_pct" not in df_cargos.columns:
        return defecto

    fila = df_cargos[df_cargos["categoria"] == categoria]
    if fila.empty:
        return defecto

    return float(fila["cargo_pct"].iloc[-1])


def _gasto_categoria(df_cargos, categoria):
    if df_cargos is None or df_cargos.empty or "gasto_neto" not in df_cargos.columns:
        return None

    fila = df_cargos[df_cargos["categoria"] == categoria]
    if fila.empty:
        return 0.0

    return float(fila["gasto_neto"].iloc[-1])


def _cargo_bruto_categoria(df_cargos, categoria):
    if df_cargos is None or df_cargos.empty or "cargo" not in df_cargos.columns:
        return 0.0

    fila = df_cargos[df_cargos["categoria"] == categoria]
    if fila.empty:
        return 0.0

    return float(fila["cargo"].iloc[-1])


def _bonificacion_categoria(df_cargos, categoria):
    if df_cargos is None or df_cargos.empty or "bonificacion_gasto" not in df_cargos.columns:
        return 0.0

    fila = df_cargos[df_cargos["categoria"] == categoria]
    if fila.empty:
        return 0.0

    return float(fila["bonificacion_gasto"].iloc[-1])


def _tiene_bonificacion_categoria(df_cargos, categoria):
    return abs(_bonificacion_categoria(df_cargos, categoria)) > 0.0001


def _calcular_pct_efectivo(importe, base):
    if base <= 0:
        return 0.0
    return round((importe / base) * 100, 4)


def _crear_resumen_cargos_calculados(
    pct_especialidad,
    pct_parafarmacia,
    cargo_especialidad,
    cargo_parafarmacia,
    df_cargos=None,
):
    filas = []
    for categoria, pct_aplicado, cargo_calculado in [
        ("especialidad", pct_especialidad, cargo_especialidad),
        ("parafarmacia", pct_parafarmacia, cargo_parafarmacia),
    ]:
        bonificacion = _bonificacion_categoria(df_cargos, categoria)
        filas.append({
            "categoria": categoria,
            "tipo": "goteo",
            "cargo_pct_aplicado": round(float(pct_aplicado or 0.0), 4),
            "cargo_calculado": round(float(cargo_calculado or 0.0), 2),
            "bonificacion_gasto": round(float(bonificacion or 0.0), 2),
            "gasto_neto": round(float(cargo_calculado or 0.0) - float(bonificacion or 0.0), 2),
        })
    return pd.DataFrame(filas)


def analizar_avantia(df_compras, gastos_factura, df_cargos=None):
    if not hay_avantia(df_compras, gastos_factura):
        return None

    df_goteo = df_compras[df_compras["tipo_compra"] == "goteo"].copy()
    if df_goteo.empty or "descripcion" not in df_goteo.columns:
        return None

    df_avantia = df_goteo[
        df_goteo["descripcion"].astype(str).str.lower().str.contains("avantia", na=False)
    ].copy()

    if df_avantia.empty:
        return {
            "detalle": pd.DataFrame(),
            "cargos": df_cargos if df_cargos is not None else pd.DataFrame(),
            "cargos_calculados": _crear_resumen_cargos_calculados(0.0, 0.0, 0.0, 0.0, df_cargos),
            "resumen": {
                "cuota_avantia": _importe_gasto(gastos_factura, "avantia"),
                "gasto_gestion": _importe_gasto(gastos_factura, "gestion"),
                "cargo_especialidad": 0.0,
                "cargo_parafarmacia": 0.0,
                "cargo_total": 0.0,
                "cargo_bruto_especialidad": 0.0,
                "cargo_bruto_parafarmacia": 0.0,
                "bonificacion_especialidad": 0.0,
                "bonificacion_parafarmacia": 0.0,
                "pct_especialidad": 0.0,
                "pct_parafarmacia": 0.0,
                "cuota_prorrateada": 0.0,
                "coste_total_avantia": 0.0,
                "unidades_avantia": 0.0,
            },
        }

    df_avantia["iva"] = _serie_numerica(df_avantia, "iva")
    df_avantia["bruto"] = _serie_numerica(df_avantia, "bruto")
    df_avantia["neto"] = _serie_numerica(df_avantia, "neto")
    df_avantia["unidades"] = _serie_numerica(df_avantia, "unidades")

    df_avantia["categoria_avantia"] = df_avantia["iva"].apply(
        lambda iva: "especialidad" if iva == 4 else "parafarmacia" if iva in [10, 21] else "sin_categoria"
    )
    pct_especialidad = _cargo_categoria(df_cargos, "especialidad", CARGO_AVANTIA_SIN_BONIFICACION)
    pct_parafarmacia = _cargo_categoria(df_cargos, "parafarmacia", CARGO_AVANTIA_SIN_BONIFICACION)

    df_avantia["cargo_pct_avantia"] = df_avantia["categoria_avantia"].map({
        "especialidad": pct_especialidad,
        "parafarmacia": pct_parafarmacia,
    }).fillna(0.0)
    df_avantia["cargo_avantia"] = (
        df_avantia["bruto"].abs() * (df_avantia["cargo_pct_avantia"] / 100)
    )

    cuota_avantia = _importe_gasto(gastos_factura, "avantia")
    unidades_totales = float(df_avantia["unidades"].abs().sum())
    df_avantia["cuota_avantia_unitaria"] = (
        cuota_avantia / unidades_totales if unidades_totales > 0 else 0.0
    )
    df_avantia["cuota_avantia_linea"] = df_avantia["cuota_avantia_unitaria"] * df_avantia["unidades"].abs()
    df_avantia["coste_avantia"] = df_avantia["neto"] + df_avantia["cargo_avantia"]
    df_avantia["neto_con_avantia"] = df_avantia["coste_avantia"] + df_avantia["cuota_avantia_linea"]
    df_avantia["neto_unitario_con_avantia"] = (
        df_avantia["neto_con_avantia"] / df_avantia["unidades"].abs().replace(0, 1)
    )

    detalle_cols = [
        "cn",
        "descripcion",
        "categoria_avantia",
        "iva",
        "unidades",
        "bruto",
        "neto",
        "cargo_pct_avantia",
        "cargo_avantia",
        "cuota_avantia_linea",
        "coste_avantia",
        "neto_con_avantia",
        "neto_unitario_con_avantia",
    ]
    detalle = df_avantia[[col for col in detalle_cols if col in df_avantia.columns]].copy()

    columnas_redondeo = [
        "bruto",
        "neto",
        "cargo_pct_avantia",
        "cargo_avantia",
        "cuota_avantia_linea",
        "coste_avantia",
        "neto_con_avantia",
        "neto_unitario_con_avantia",
    ]
    for columna in columnas_redondeo:
        if columna in detalle.columns:
            detalle[columna] = detalle[columna].round(4)

    cargo_especialidad = float(
        df_avantia[df_avantia["categoria_avantia"] == "especialidad"]["cargo_avantia"].sum()
    )
    cargo_parafarmacia = float(
        df_avantia[df_avantia["categoria_avantia"] == "parafarmacia"]["cargo_avantia"].sum()
    )
    cargo_total = cargo_especialidad + cargo_parafarmacia

    resumen = {
        "cuota_avantia": cuota_avantia,
        "gasto_gestion": _importe_gasto(gastos_factura, "gestion"),
        "pct_especialidad": pct_especialidad,
        "pct_parafarmacia": pct_parafarmacia,
        "cargo_bruto_especialidad": round(_cargo_bruto_categoria(df_cargos, "especialidad"), 2),
        "cargo_bruto_parafarmacia": round(_cargo_bruto_categoria(df_cargos, "parafarmacia"), 2),
        "bonificacion_especialidad": round(_bonificacion_categoria(df_cargos, "especialidad"), 2),
        "bonificacion_parafarmacia": round(_bonificacion_categoria(df_cargos, "parafarmacia"), 2),
        "cargo_especialidad": round(cargo_especialidad, 2),
        "cargo_parafarmacia": round(cargo_parafarmacia, 2),
        "cargo_total": round(cargo_total, 2),
        "cuota_prorrateada": round(cuota_avantia, 2),
        "coste_total_avantia": round(cargo_total + cuota_avantia, 2),
        "unidades_avantia": unidades_totales,
    }

    return {
        "detalle": detalle,
        "cargos": df_cargos if df_cargos is not None else pd.DataFrame(),
        "cargos_calculados": _crear_resumen_cargos_calculados(
            pct_especialidad,
            pct_parafarmacia,
            cargo_especialidad,
            cargo_parafarmacia,
            df_cargos,
        ),
        "resumen": resumen,
    }
