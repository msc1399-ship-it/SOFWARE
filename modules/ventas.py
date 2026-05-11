import re

import pandas as pd


def normalizar_numero(valor):
    if pd.isna(valor):
        return pd.NA
    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).replace("€", "").replace("%", "").replace(" ", "").strip()
    if not texto:
        return pd.NA
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return pd.NA


def normalizar_cn(valor):
    if pd.isna(valor):
        return pd.NA
    texto = re.sub(r"\D", "", str(valor))
    return texto or pd.NA


def buscar_columna(df, *opciones):
    columnas = {str(col).strip().lower(): col for col in df.columns}
    for opcion in opciones:
        opcion_norm = str(opcion).strip().lower()
        if opcion_norm in columnas:
            return columnas[opcion_norm]

    for col_norm, col_original in columnas.items():
        for opcion in opciones:
            partes = str(opcion).strip().lower().split()
            if partes and all(parte in col_norm for parte in partes):
                return col_original

    return None


def leer_tabla(file):
    nombre = str(getattr(file, "name", "")).lower()
    if hasattr(file, "seek"):
        file.seek(0)
    if nombre.endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


def normalizar_ventas_erp(df):
    if df is None or df.empty:
        return pd.DataFrame()

    trabajo = df.copy()
    trabajo.columns = [str(col).strip().lower() for col in trabajo.columns]

    col_cn = buscar_columna(
        trabajo, "cn", "codigo nacional", "código nacional", "cod nacional", "codigo"
    )
    col_descripcion = buscar_columna(
        trabajo, "descripcion", "descripción", "articulo", "artículo", "producto"
    )
    col_unidades = buscar_columna(
        trabajo, "unidades vendidas", "uds vendidas", "cantidad vendida", "unidades", "cantidad"
    )
    col_pvp = buscar_columna(trabajo, "pvp", "precio venta", "precio")
    col_venta_neta = buscar_columna(
        trabajo, "venta neta", "importe neto", "neto venta", "ventas netas", "importe venta"
    )
    col_stock = buscar_columna(trabajo, "stock", "existencias")
    col_margen_erp = buscar_columna(
        trabajo, "margen erp", "margen", "margen declarado", "rentabilidad"
    )
    col_coste_erp = buscar_columna(
        trabajo, "coste erp", "coste", "coste registrado", "precio coste", "coste medio"
    )

    if not col_cn or not col_unidades or not col_venta_neta:
        raise ValueError("El fichero de ventas debe incluir al menos CN, unidades vendidas y venta neta.")

    resultado = pd.DataFrame()
    resultado["cn"] = trabajo[col_cn].apply(normalizar_cn)
    resultado["descripcion"] = trabajo[col_descripcion].astype(str).str.strip() if col_descripcion else ""
    resultado["unidades_vendidas"] = trabajo[col_unidades].apply(normalizar_numero)
    resultado["pvp"] = trabajo[col_pvp].apply(normalizar_numero) if col_pvp else pd.NA
    resultado["venta_neta"] = trabajo[col_venta_neta].apply(normalizar_numero)
    resultado["stock"] = trabajo[col_stock].apply(normalizar_numero) if col_stock else pd.NA
    resultado["margen_erp"] = trabajo[col_margen_erp].apply(normalizar_numero) if col_margen_erp else pd.NA
    resultado["coste_erp"] = trabajo[col_coste_erp].apply(normalizar_numero) if col_coste_erp else pd.NA

    resultado = resultado.dropna(subset=["cn"])
    resultado["unidades_vendidas"] = pd.to_numeric(resultado["unidades_vendidas"], errors="coerce").fillna(0.0)
    resultado["venta_neta"] = pd.to_numeric(resultado["venta_neta"], errors="coerce").fillna(0.0)
    resultado["margen_erp"] = pd.to_numeric(resultado["margen_erp"], errors="coerce")
    resultado.loc[resultado["margen_erp"].abs().between(0, 1), "margen_erp"] *= 100
    resultado["coste_erp"] = pd.to_numeric(resultado["coste_erp"], errors="coerce")

    return resultado.reset_index(drop=True)


def coste_linea_compra(df):
    candidatos = [
        "coste_final_ajustado",
        "coste_ajustado",
        "neto_con_gestion_adicional",
        "neto_con_faceta_tramo_fijo",
        "neto_con_liquidacion",
        "neto_con_servicio",
        "neto_con_devolucion",
        "neto_con_avantia",
        "neto_con_cargo_transfer",
        "neto",
    ]
    for columna in candidatos:
        if columna in df.columns:
            return pd.to_numeric(df[columna], errors="coerce").fillna(0.0)
    return pd.Series([0.0] * len(df), index=df.index)


def coste_medio_real_por_cn(dataframes_compras):
    piezas = [df.copy() for df in dataframes_compras if df is not None and not df.empty]
    if not piezas:
        return pd.DataFrame()

    compras = pd.concat(piezas, ignore_index=True)
    if "cn" not in compras.columns or "unidades" not in compras.columns:
        return pd.DataFrame()

    compras["cn"] = compras["cn"].apply(normalizar_cn)
    compras["unidades_compradas"] = pd.to_numeric(compras["unidades"], errors="coerce").fillna(0.0)
    compras["coste_real_linea"] = coste_linea_compra(compras)
    compras = compras.dropna(subset=["cn"])
    compras = compras[(compras["unidades_compradas"] > 0) & (compras["coste_real_linea"] > 0)]

    if compras.empty:
        return pd.DataFrame()

    agg = {
        "unidades_compradas_periodo": ("unidades_compradas", "sum"),
        "coste_real_total_compras": ("coste_real_linea", "sum"),
    }
    if "proveedor" in compras.columns:
        agg["proveedores"] = ("proveedor", lambda serie: ", ".join(sorted(set(serie.dropna().astype(str)))))

    resumen = compras.groupby("cn", dropna=False).agg(**agg).reset_index()
    resumen["coste_medio_real"] = (
        resumen["coste_real_total_compras"] / resumen["unidades_compradas_periodo"].replace(0, pd.NA)
    )
    return resumen


def analizar_margen_real(df_ventas, df_costes):
    if df_ventas is None or df_ventas.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    analisis = df_ventas.copy()
    if df_costes is not None and not df_costes.empty:
        analisis = analisis.merge(df_costes, on="cn", how="left")
    else:
        analisis["unidades_compradas_periodo"] = pd.NA
        analisis["coste_real_total_compras"] = pd.NA
        analisis["coste_medio_real"] = pd.NA

    analisis["tiene_compras_reales"] = analisis["coste_medio_real"].notna()
    analisis["unidades_sin_compra_periodo"] = (
        analisis["unidades_vendidas"] - analisis["unidades_compradas_periodo"].fillna(0.0)
    ).clip(lower=0)
    analisis["coste_real_total_vendido"] = analisis["unidades_vendidas"] * analisis["coste_medio_real"]
    analisis["margen_real_pct"] = (
        (analisis["venta_neta"] - analisis["coste_real_total_vendido"])
        / analisis["venta_neta"].replace(0, pd.NA)
        * 100
    )

    motivos = []
    for _, fila in analisis.iterrows():
        motivo = []
        margen_erp = fila.get("margen_erp")
        coste_erp = fila.get("coste_erp")
        if pd.isna(margen_erp):
            motivo.append("margen_erp_vacio")
        elif margen_erp > 100:
            motivo.append("margen_erp_mayor_100")
        elif margen_erp < 0:
            motivo.append("margen_erp_negativo")
        if pd.isna(coste_erp) or coste_erp <= 0:
            motivo.append("coste_erp_vacio")
        elif fila.get("venta_neta", 0) > 0 and fila.get("unidades_vendidas", 0) > 0:
            coste_erp_total = coste_erp * fila.get("unidades_vendidas", 0)
            margen_por_coste_erp = ((fila.get("venta_neta", 0) - coste_erp_total) / fila.get("venta_neta", 0)) * 100
            if margen_por_coste_erp < 0 or margen_por_coste_erp > 100:
                motivo.append("coste_erp_incoherente")
            elif not pd.isna(margen_erp) and abs(margen_erp - margen_por_coste_erp) > 50:
                motivo.append("margen_erp_incoherente_con_coste")
        motivos.append(", ".join(motivo))

    analisis["motivo_coste_erp_no_fiable"] = motivos
    analisis["coste_erp_no_fiable"] = analisis["motivo_coste_erp_no_fiable"].astype(str).str.len() > 0
    analisis["diferencia_margen_erp_vs_real"] = analisis["margen_erp"] - analisis["margen_real_pct"]
    analisis["diferencia_margen_abs"] = analisis["diferencia_margen_erp_vs_real"].abs()

    for columna in [
        "coste_medio_real",
        "coste_real_total_vendido",
        "margen_real_pct",
        "margen_erp",
        "diferencia_margen_erp_vs_real",
        "diferencia_margen_abs",
    ]:
        analisis[columna] = pd.to_numeric(analisis[columna], errors="coerce").round(4)

    discordancia = (
        analisis[
            ~analisis["coste_erp_no_fiable"]
            & analisis["tiene_compras_reales"]
            & analisis["diferencia_margen_abs"].notna()
        ]
        .sort_values("diferencia_margen_abs", ascending=False)
        .reset_index(drop=True)
    )
    no_fiable = analisis[analisis["coste_erp_no_fiable"]].reset_index(drop=True)

    return analisis.reset_index(drop=True), discordancia, no_fiable
