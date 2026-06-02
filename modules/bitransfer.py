import re
import unicodedata

import pandas as pd


def _normalizar_texto(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def _normalizar_numero(valor):
    if pd.isna(valor):
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None

    texto = texto.replace("€", "").replace("%", "").replace(" ", "")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "." in texto:
        parte_decimal = texto.rsplit(".", 1)[1]
        if len(parte_decimal) == 3:
            texto = texto.replace(".", "")
    else:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


def _normalizar_porcentaje(valor):
    numero = _normalizar_numero(valor)
    if numero is None:
        return None
    if abs(numero) > 0 and abs(numero) <= 1:
        return numero * 100
    return numero


def _extraer_numero_en_texto(valor):
    texto = str(valor)
    match = re.search(r"-?\d+(?:[.,]\d+)?", texto)
    if not match:
        return None

    return _normalizar_numero(match.group(0))


def _normalizar_cn(valor):
    if pd.isna(valor):
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    if re.match(r"^\d+\.0$", texto):
        texto = texto[:-2]

    cn = re.sub(r"\D", "", texto)
    return cn or None


def _buscar_columna(columnas, opciones):
    columnas_normalizadas = {_normalizar_texto(col): col for col in columnas}

    for opcion in opciones:
        opcion_normalizada = _normalizar_texto(opcion)
        if opcion_normalizada in columnas_normalizadas:
            return columnas_normalizadas[opcion_normalizada]

    for col_normalizada, col_original in columnas_normalizadas.items():
        if any(_normalizar_texto(opcion) in col_normalizada for opcion in opciones):
            return col_original

    return None


def _mapear_encabezados(fila):
    alias = {
        "tipo": ["tipo", "plataforma"],
        "venta_bruta": ["venta bruta", "bruto"],
        "pva": ["pva"],
        "pvl": ["pvl"],
        "descuento_pct": ["descuento %", "dto %"],
        "descuento_eur": ["descuento eur", "descuento €", "dto €"],
        "cargo_pct": ["cargo %", "gasto %"],
        "cargo_eur": ["cargo eur", "cargo €", "gasto €"],
        "rentabilidad_pct": ["rentabilidad %", "margen %"],
    }

    encabezados = {}

    for indice, valor in enumerate(fila):
        texto = _normalizar_texto(valor)
        if not texto or texto == "nan":
            continue

        for nombre, opciones in alias.items():
            if any(_normalizar_texto(opcion) == texto for opcion in opciones):
                encabezados[nombre] = indice

    return encabezados


def _valor_fila(fila, encabezados, nombre):
    indice = encabezados.get(nombre)
    if indice is None or indice >= len(fila):
        return None

    return fila.iloc[indice]


def _normalizar_tipo_bitransfer(valor):
    texto = _normalizar_texto(valor)

    if texto in ["i", "individual"]:
        return "individual"

    if texto in ["g", "grupo"]:
        return "grupo"

    if texto in ["subtotal", "total"]:
        return "subtotal"

    return None


def _normalizar_nombre_plataforma(valor):
    texto = _normalizar_texto(valor)

    if not texto or texto == "nan":
        return None

    if "cuota" in texto:
        return None

    return str(valor).strip()


def leer_listado_compras_bitransfer(file):
    df = pd.read_excel(file)
    df.columns = [str(c).strip() for c in df.columns]

    columnas = {
        "cn": _buscar_columna(df.columns, ["codigo nacional", "codigo", "cn"]),
        "descripcion": _buscar_columna(df.columns, ["descripcion", "producto", "articulo"]),
        "cantidad": _buscar_columna(df.columns, ["cant.", "cantidad", "unidades"]),
        "pvl": _buscar_columna(df.columns, ["pvl", "importe", "importe bruto", "bruto"]),
        "descuento": _buscar_columna(df.columns, ["desc.", "descuento"]),
        "cargo": _buscar_columna(df.columns, ["gast.", "gasto", "cargo", "recargo"]),
        "importe_neto": _buscar_columna(df.columns, ["total", "importe neto", "neto"]),
    }

    obligatorias = ["cn", "descripcion", "pvl", "cargo", "importe_neto"]
    faltantes = [nombre for nombre in obligatorias if columnas[nombre] is None]

    if faltantes:
        raise ValueError(
            "No se han encontrado estas columnas obligatorias: "
            + ", ".join(faltantes)
        )

    resultado = pd.DataFrame()
    resultado["cn"] = df[columnas["cn"]].apply(_normalizar_cn)
    resultado["descripcion"] = df[columnas["descripcion"]].astype(str).str.strip()

    if columnas["cantidad"]:
        resultado["cantidad"] = df[columnas["cantidad"]].apply(_normalizar_numero)
    else:
        resultado["cantidad"] = None

    resultado["pvl"] = df[columnas["pvl"]].apply(_normalizar_numero)

    if columnas["descuento"]:
        resultado["descuento_pct"] = df[columnas["descuento"]].apply(_normalizar_porcentaje)
    else:
        resultado["descuento_pct"] = None

    resultado["cargo_pct"] = df[columnas["cargo"]].apply(_normalizar_porcentaje)
    resultado["importe_neto"] = df[columnas["importe_neto"]].apply(_normalizar_numero)

    resultado = resultado.dropna(subset=["cn", "descripcion", "pvl", "cargo_pct", "importe_neto"])
    resultado = resultado[resultado["cn"].str.len() > 0]

    return resultado.reset_index(drop=True)


def leer_cuadro_resumen_consumos(file):
    df = pd.read_excel(file, header=None)

    bloque = None
    encabezados = {}
    bitransfer = []
    plataformas = []
    ultima_plataforma = None

    for _, fila in df.iterrows():
        fila_texto = " ".join(
            _normalizar_texto(valor)
            for valor in fila.values
            if not pd.isna(valor)
        )
        primera_celda = _normalizar_texto(fila.iloc[0]) if len(fila) else ""
        nuevos_encabezados = _mapear_encabezados(fila)

        if nuevos_encabezados and "venta_bruta" in nuevos_encabezados:
            encabezados = nuevos_encabezados

        if primera_celda in ["bitransfer", "bittransfer", "bitrasnfer"]:
            bloque = "bitransfer"
            continue

        if primera_celda == "plataforma":
            bloque = "plataforma"
            continue

        if "cuota" in fila_texto:
            cuota = _extraer_numero_en_texto(fila_texto)
            if ultima_plataforma is not None:
                plataformas[ultima_plataforma]["cuota"] = cuota
            continue

        if not encabezados:
            continue

        if bloque == "bitransfer":
            tipo = _normalizar_tipo_bitransfer(_valor_fila(fila, encabezados, "tipo"))
            if not tipo:
                continue

            bitransfer.append({
                "tipo": tipo,
                "venta_bruta": _normalizar_numero(_valor_fila(fila, encabezados, "venta_bruta")),
                "pva": _normalizar_numero(_valor_fila(fila, encabezados, "pva")),
                "pvl": _normalizar_numero(_valor_fila(fila, encabezados, "pvl")),
                "descuento_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "descuento_pct")),
                "descuento_eur": _normalizar_numero(_valor_fila(fila, encabezados, "descuento_eur")),
                "cargo_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "cargo_pct")),
                "cargo_eur": _normalizar_numero(_valor_fila(fila, encabezados, "cargo_eur")),
                "rentabilidad_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "rentabilidad_pct")),
            })

        elif bloque == "plataforma":
            plataforma = _normalizar_nombre_plataforma(_valor_fila(fila, encabezados, "tipo"))
            if not plataforma:
                continue

            plataformas.append({
                "plataforma": plataforma,
                "venta_bruta": _normalizar_numero(_valor_fila(fila, encabezados, "venta_bruta")),
                "pva": _normalizar_numero(_valor_fila(fila, encabezados, "pva")),
                "pvl": _normalizar_numero(_valor_fila(fila, encabezados, "pvl")),
                "descuento_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "descuento_pct")),
                "descuento_eur": _normalizar_numero(_valor_fila(fila, encabezados, "descuento_eur")),
                "cargo_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "cargo_pct")),
                "cargo_eur": _normalizar_numero(_valor_fila(fila, encabezados, "cargo_eur")),
                "rentabilidad_pct": _normalizar_porcentaje(_valor_fila(fila, encabezados, "rentabilidad_pct")),
                "cuota": None,
            })
            ultima_plataforma = len(plataformas) - 1

    if not bitransfer and not plataformas:
        raise ValueError("No se ha detectado ningún bloque BitTransfer o Plataforma.")

    return {
        "bitransfer": pd.DataFrame(bitransfer),
        "plataformas": pd.DataFrame(plataformas),
    }


def conciliar_bitransfer_consumos(df_compras, resumen_consumos):
    df_compras = df_compras.copy()
    df_resumen = resumen_consumos["bitransfer"].copy()

    if df_resumen.empty:
        raise ValueError("El cuadro resumen no contiene bloque BitTransfer.")

    df_compras["cantidad"] = df_compras["cantidad"].fillna(1)
    df_compras["importe_neto_unitario"] = df_compras["importe_neto"]
    df_compras["venta_bruta"] = df_compras["pvl"] * df_compras["cantidad"]
    df_compras["cargo_teorico_unitario"] = df_compras["pvl"] * (df_compras["cargo_pct"].fillna(0) / 100)
    df_compras["coste_real_unitario"] = (
        df_compras["importe_neto_unitario"] + df_compras["cargo_teorico_unitario"]
    )
    df_compras["coste_real_total"] = df_compras["coste_real_unitario"] * df_compras["cantidad"]

    total_compras = round(df_compras["venta_bruta"].sum(), 2)
    total_resumen = df_resumen[df_resumen["tipo"] == "subtotal"]["venta_bruta"].dropna()

    if total_resumen.empty:
        total_resumen_valor = round(df_resumen[df_resumen["tipo"] != "subtotal"]["venta_bruta"].sum(), 2)
    else:
        total_resumen_valor = round(float(total_resumen.iloc[0]), 2)

    cargo_resumen = df_resumen[df_resumen["tipo"] != "subtotal"]["cargo_eur"].dropna().sum()

    if cargo_resumen == 0:
        filas_cargo = df_resumen[df_resumen["tipo"] != "subtotal"].copy()
        cargo_resumen = (
            filas_cargo["venta_bruta"].fillna(0)
            * (filas_cargo["cargo_pct"].fillna(0) / 100)
        ).sum()

    cargo_teorico_total = (
        df_compras["cargo_teorico_unitario"] * df_compras["cantidad"]
    ).sum()

    columnas_visibles = [
        "cn",
        "descripcion",
        "cantidad",
        "pvl",
        "descuento_pct",
        "cargo_pct",
        "importe_neto_unitario",
        "cargo_teorico_unitario",
        "coste_real_unitario",
    ]
    df_conciliado = df_compras[columnas_visibles].copy()

    resumen = {
        "venta_bruta_compras": float(total_compras),
        "venta_bruta_resumen": total_resumen_valor,
        "diferencia_venta_bruta": float(round(total_compras - total_resumen_valor, 2)),
        "cargo_resumen": round(float(cargo_resumen), 2),
        "cargo_teorico_compras": round(float(cargo_teorico_total), 2),
        "diferencia_cargo": round(float(cargo_teorico_total - cargo_resumen), 2),
        "importe_neto_compras": round(float((df_compras["importe_neto_unitario"] * df_compras["cantidad"]).sum()), 2),
        "coste_real_total_compras": round(float(df_compras["coste_real_total"].sum()), 2),
    }

    return df_conciliado, resumen
