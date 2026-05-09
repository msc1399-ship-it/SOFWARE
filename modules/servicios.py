import re
import unicodedata

import pandas as pd


def _normalizar_texto(valor):
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


def _normalizar_numero(valor):
    if pd.isna(valor):
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).replace("€", "").replace("%", "").replace(" ", "").strip()
    if not texto:
        return 0.0

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return 0.0


def _serie_numerica(df, columna):
    if columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)

    return df[columna].apply(_normalizar_numero)


def _contiene_observacion_b(valor):
    texto = _normalizar_texto(valor)
    if not texto or texto == "nan":
        return False

    tokens = re.split(r"[^a-z0-9]+", texto)
    return "b" in tokens


def hay_avantia(df_compras, gastos_factura):
    tiene_cuota_avantia = False
    if gastos_factura is not None and not gastos_factura.empty and "tipo" in gastos_factura.columns:
        tiene_cuota_avantia = (gastos_factura["tipo"] == "avantia").any()

    tiene_lineas_avantia = False
    if "descripcion" in df_compras.columns:
        tiene_lineas_avantia = (
            df_compras["descripcion"].astype(str).str.lower().str.contains("avantia", na=False)
        ).any()

    return bool(tiene_cuota_avantia or tiene_lineas_avantia)


def importe_servicios_factura(gastos_factura):
    if gastos_factura is None or gastos_factura.empty or "tipo" not in gastos_factura.columns:
        return 0.0

    servicios = gastos_factura[gastos_factura["tipo"] == "servicios"]
    if servicios.empty or "importe" not in servicios.columns:
        return 0.0

    return round(float(servicios["importe"].sum()), 2)


def _imputar_devoluciones_en_compras(df_goteo, abonos):
    if abonos.empty:
        return pd.DataFrame(), pd.DataFrame()

    cargos_por_cn = (
        abonos
        .groupby("cn", dropna=False)
        .agg(
            cargo_devoluciones=("cargo_servicio", "sum"),
            unidades_compra_mismo_cn=("unidades_compra_mismo_cn", "max"),
        )
        .reset_index()
    )

    cargos_por_cn["cargo_devolucion_unitario"] = cargos_por_cn.apply(
        lambda row: row["cargo_devoluciones"] / row["unidades_compra_mismo_cn"]
        if row["unidades_compra_mismo_cn"] > 0
        else 0.0,
        axis=1,
    )

    compras_positivas = df_goteo[df_goteo["unidades"] > 0].copy()
    imputables = cargos_por_cn[cargos_por_cn["unidades_compra_mismo_cn"] > 0]
    imputacion = compras_positivas.merge(
        imputables[["cn", "cargo_devoluciones", "cargo_devolucion_unitario"]],
        on="cn",
        how="inner",
    )

    if not imputacion.empty:
        imputacion["cargo_devolucion_linea"] = (
            imputacion["cargo_devolucion_unitario"] * imputacion["unidades"]
        )
        imputacion["neto_con_devolucion"] = (
            imputacion["neto"] + imputacion["cargo_devolucion_linea"]
        )
        imputacion["neto_unitario_con_devolucion"] = (
            imputacion["neto_con_devolucion"] / imputacion["unidades"].replace(0, 1)
        )

        columnas_imputacion = [
            "cn",
            "descripcion",
            "unidades",
            "bruto",
            "neto",
            "cargo_devolucion_unitario",
            "cargo_devolucion_linea",
            "neto_con_devolucion",
            "neto_unitario_con_devolucion",
        ]
        imputacion = imputacion[[col for col in columnas_imputacion if col in imputacion.columns]]
        for columna in [
            "cargo_devolucion_unitario",
            "cargo_devolucion_linea",
            "neto_con_devolucion",
            "neto_unitario_con_devolucion",
        ]:
            if columna in imputacion.columns:
                imputacion[columna] = imputacion[columna].round(4)

    pendientes = cargos_por_cn[cargos_por_cn["unidades_compra_mismo_cn"] <= 0].copy()
    if not pendientes.empty:
        pendientes = pendientes[["cn", "cargo_devoluciones"]]
        pendientes["motivo"] = "sin_compras_positivas_mismo_cn"
        pendientes["cargo_devoluciones"] = pendientes["cargo_devoluciones"].round(4)
    else:
        pendientes = pd.DataFrame(columns=["cn", "cargo_devoluciones", "motivo"])

    return imputacion, pendientes


def analizar_gastos_servicios(df_compras, gastos_factura, condicion=None):
    df_goteo = df_compras[df_compras["tipo_compra"] == "goteo"].copy()

    if df_goteo.empty:
        return None

    if "observaciones" not in df_goteo.columns:
        df_goteo["observaciones"] = ""

    df_goteo["bruto"] = _serie_numerica(df_goteo, "bruto")
    df_goteo["neto"] = _serie_numerica(df_goteo, "neto")
    df_goteo["unidades"] = _serie_numerica(df_goteo, "unidades")

    tiene_avantia = hay_avantia(df_goteo, gastos_factura)
    reglas_servicios = (condicion or {}).get("servicios", {})
    pct_vida_natural = (
        reglas_servicios.get("pct_con_avantia", 2.5)
        if tiene_avantia
        else reglas_servicios.get("pct_sin_avantia", 2.0)
    )
    imputar_devoluciones = reglas_servicios.get("devoluciones_por_defecto", True)
    pct_devoluciones = reglas_servicios.get("devoluciones_pct", 2.5)
    total_servicios_factura = importe_servicios_factura(gastos_factura)

    vida_natural = df_goteo[df_goteo["observaciones"].apply(_contiene_observacion_b)].copy()
    if not vida_natural.empty:
        vida_natural["tipo_servicio"] = "bidanatural"
        vida_natural["cargo_pct"] = pct_vida_natural
        vida_natural["base_cargo"] = vida_natural["bruto"].abs()
        vida_natural["cargo_servicio"] = vida_natural["base_cargo"] * (pct_vida_natural / 100)
        vida_natural["cargo_servicio_unitario"] = (
            vida_natural["cargo_servicio"] / vida_natural["unidades"].abs().replace(0, 1)
        )
        vida_natural["neto_con_servicio"] = vida_natural["neto"] + vida_natural["cargo_servicio"]
        vida_natural["neto_unitario_con_servicio"] = (
            vida_natural["neto_con_servicio"] / vida_natural["unidades"].abs().replace(0, 1)
        )
        vida_natural["estado_imputacion"] = "imputado_en_linea"

    total_vida_natural = round(float(vida_natural["cargo_servicio"].sum()), 2) if not vida_natural.empty else 0.0
    diferencia_servicios = round(total_servicios_factura - total_vida_natural, 2)

    abonos = pd.DataFrame()
    total_devoluciones = 0.0
    imputacion_devoluciones = pd.DataFrame()
    pendiente_otros_gastos = pd.DataFrame()

    if diferencia_servicios > 0.05 and imputar_devoluciones:
        abonos = df_goteo[df_goteo["neto"] < 0].copy()
        if not abonos.empty:
            abonos["tipo_servicio"] = "devoluciones"
            abonos["cargo_pct"] = pct_devoluciones
            abonos["base_cargo"] = abonos["bruto"].abs()
            abonos["cargo_teorico"] = abonos["base_cargo"] * (pct_devoluciones / 100)
            total_teorico_abonos = abonos["cargo_teorico"].sum()

            if total_teorico_abonos > 0:
                abonos["cargo_servicio"] = (
                    abonos["cargo_teorico"] / total_teorico_abonos
                ) * diferencia_servicios
            else:
                abonos["cargo_servicio"] = 0.0

            compras_positivas_por_cn = (
                df_goteo[df_goteo["unidades"] > 0]
                .groupby("cn")["unidades"]
                .sum()
            )

            abonos["unidades_compra_mismo_cn"] = (
                abonos["cn"].map(compras_positivas_por_cn).fillna(0)
            )
            abonos["cargo_servicio_unitario"] = abonos.apply(
                lambda row: row["cargo_servicio"] / row["unidades_compra_mismo_cn"]
                if row["unidades_compra_mismo_cn"] > 0
                else 0.0,
                axis=1,
            )
            abonos["estado_imputacion"] = abonos["unidades_compra_mismo_cn"].apply(
                lambda unidades: "imputable_a_compras_mismo_cn"
                if unidades > 0
                else "pendiente_otros_gastos"
            )

            total_devoluciones = round(float(abonos["cargo_servicio"].sum()), 2)
            imputacion_devoluciones, pendiente_otros_gastos = _imputar_devoluciones_en_compras(
                df_goteo,
                abonos,
            )

    detalle = pd.concat([vida_natural, abonos], ignore_index=True)

    columnas_detalle = [
        "tipo_servicio",
        "cn",
        "descripcion",
        "unidades",
        "bruto",
        "neto",
        "observaciones",
        "cargo_pct",
        "base_cargo",
        "cargo_servicio",
        "cargo_servicio_unitario",
        "neto_con_servicio",
        "neto_unitario_con_servicio",
        "estado_imputacion",
    ]

    if not detalle.empty:
        detalle = detalle[[col for col in columnas_detalle if col in detalle.columns]]
        detalle["cargo_servicio"] = detalle["cargo_servicio"].round(4)
        if "cargo_servicio_unitario" in detalle.columns:
            detalle["cargo_servicio_unitario"] = detalle["cargo_servicio_unitario"].round(4)
        if "neto_con_servicio" in detalle.columns:
            detalle["neto_con_servicio"] = detalle["neto_con_servicio"].round(4)
        if "neto_unitario_con_servicio" in detalle.columns:
            detalle["neto_unitario_con_servicio"] = detalle["neto_unitario_con_servicio"].round(4)

    if not detalle.empty and "cn" in detalle.columns:
        resumen_cn = (
            detalle
            .groupby(["cn", "descripcion"], dropna=False)
            .agg(
                unidades=("unidades", "sum"),
                bruto=("bruto", "sum"),
                neto=("neto", "sum"),
                cargo_servicio=("cargo_servicio", "sum"),
                neto_con_servicio=("neto_con_servicio", "sum"),
            )
            .reset_index()
        )
        resumen_cn["cargo_servicio"] = resumen_cn["cargo_servicio"].round(4)
        resumen_cn["neto_con_servicio"] = resumen_cn["neto_con_servicio"].round(4)
    else:
        resumen_cn = pd.DataFrame()

    resumen = {
        "tiene_avantia": tiene_avantia,
        "cargo_pct_vida_natural": pct_vida_natural,
        "servicios_factura": total_servicios_factura,
        "cargo_vida_natural": total_vida_natural,
        "diferencia_servicios": diferencia_servicios,
        "cargo_devoluciones": total_devoluciones,
        "imputa_devoluciones": imputar_devoluciones,
        "devoluciones_cuadran": abs(diferencia_servicios - total_devoluciones) <= 0.05
        and diferencia_servicios > 0.05,
    }

    return {
        "detalle": detalle,
        "resumen_cn": resumen_cn,
        "imputacion_devoluciones": imputacion_devoluciones,
        "pendiente_otros_gastos": pendiente_otros_gastos,
        "resumen": resumen,
    }
