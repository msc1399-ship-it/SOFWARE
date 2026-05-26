import re
import unicodedata
from collections import Counter

import pandas as pd


TIPOS_ALBARAN_74 = {
    1: "desglose",
    2: "tramo fijo",
    3: "tramo 0",
}


CONDICIONES_BIDAFARMA = {
    "X8": {
        "nombre": "Diane Vida",
        "albaran_74": "puede_aparecer",
        "tipo_albaran_74": 1,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
    },
    "NN": {
        "nombre": "Diane Cecofar",
        "albaran_74": "solo_liquidaciones",
        "tipo_albaran_74": None,
        "ajuste_comercial_factura": True,
        "cargo_adicional_gestion": False,
    },
    "XP": {
        "nombre": "Vida Volumen",
        "alias": ["VIDA VOLUMEN", "PIDA VOLUMEN"],
        "albaran_74": "puede_aparecer",
        "tipo_albaran_74": 1,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
        "familia": "VIDA_VOLUMEN",
        "albaranes": {
            "pueden_ser_brutos": True,
            "pueden_ser_netos": True,
            "brutos_sin_descuento_en_linea": True,
            "brutos_pueden_requerir_albaran_74_desglose": True,
            "netos_pueden_traer_tramo_0_ajuste_liquidaciones": True,
            "bases_factura_pueden_usar_bruto_si_albaran_bruto": True,
        },
        "faceta": {
            "tipo": "desglose",
            "validar_bases": True,
            "especialidad_normal_puede_tener_descuento": True,
            "especialidad_cara_descuento_en_euros": True,
            "parafarmacia_financiada_separada": True,
            "parafarmacia_no_financiada_puede_tener_cargo": True,
            "tramo_0": True,
            "ajuste_escala": True,
            "liquidaciones_club": True,
            "imputacion_tramo_0_ajuste": "especialidad_normal_y_parafarmacia_no_financiada",
            "imputacion_liquidaciones": "lineas_club_detectadas",
        },
        "servicios": {
            "bidanatural": True,
            "pct_sin_avantia": 2.0,
            "pct_con_avantia": 2.0,
            "devoluciones_por_defecto": True,
            "devoluciones_pct": 2.5,
        },
        "gestion": {
            "penalizacion_bajo_consumo": True,
            "importe_penalizacion_bajo_consumo": 50.0,
            "umbral_consumo": 5000.0,
        },
    },
    "XR": {
        "nombre": "Vida Línea",
        "albaran_74": "solo_liquidaciones",
        "tipo_albaran_74": None,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": True,
    },
    "NJ": {
        "nombre": "Pruébame 4",
        "albaran_74": "solo_bruto",
        "tipo_albaran_74": 1,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
        "familia": "PRUEBAME",
        "especialidad_descuento_pct": 4.0,
        "parafarmacia_cargo_pct": 3.0,
        "permite_avantia": False,
        "albaranes": {
            "pueden_ser_brutos": True,
            "pueden_ser_netos": True,
            "brutos_requieren_albaran_74": True,
            "netos_pueden_traer_ajuste_escala": True,
        },
        "faceta": {
            "tipo": "desglose",
            "validar_bases": True,
            "especialidad_normal_descuento_pct": 4.0,
            "especialidad_cara_puede_tener_descuento": True,
            "parafarmacia_financiada_iva": 10,
            "parafarmacia_no_financiada_iva": 21,
            "parafarmacia_cargo_pct": 3.0,
            "ajuste_escala": True,
            "imputacion_ajuste_escala": "goteo_especialidad_parafarmacia",
        },
        "servicios": {
            "bidanatural": True,
            "pct_sin_avantia": 2.0,
            "pct_con_avantia": 2.0,
            "devoluciones_por_defecto": True,
            "devoluciones_pct": 2.5,
        },
        "gestion": {
            "penalizacion_bajo_consumo": True,
            "importe_penalizacion_bajo_consumo": 50.0,
            "umbral_consumo": 5000.0,
        },
    },
    "X4": {
        "nombre": "Pruébame 5",
        "albaran_74": "solo_bruto",
        "tipo_albaran_74": 1,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
        "familia": "PRUEBAME",
        "especialidad_descuento_pct": 5.0,
        "parafarmacia_cargo_pct": 3.0,
        "permite_avantia": False,
        "albaranes": {
            "pueden_ser_brutos": True,
            "pueden_ser_netos": True,
            "brutos_requieren_albaran_74": True,
            "netos_pueden_traer_ajuste_escala": True,
        },
        "faceta": {
            "tipo": "desglose",
            "validar_bases": True,
            "especialidad_normal_descuento_pct": 5.0,
            "especialidad_cara_puede_tener_descuento": True,
            "parafarmacia_financiada_iva": 10,
            "parafarmacia_no_financiada_iva": 21,
            "parafarmacia_cargo_pct": 3.0,
            "ajuste_escala": True,
            "imputacion_ajuste_escala": "goteo_especialidad_parafarmacia",
        },
        "servicios": {
            "bidanatural": True,
            "pct_sin_avantia": 2.0,
            "pct_con_avantia": 2.0,
            "devoluciones_por_defecto": True,
            "devoluciones_pct": 2.5,
        },
        "gestion": {
            "penalizacion_bajo_consumo": True,
            "importe_penalizacion_bajo_consumo": 50.0,
            "umbral_consumo": 5000.0,
        },
    },
    "ND": {
        "nombre": "Pruébame 6",
        "albaran_74": "solo_bruto",
        "tipo_albaran_74": 1,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
        "familia": "PRUEBAME",
        "especialidad_descuento_pct": 6.0,
        "parafarmacia_cargo_pct": 3.0,
        "permite_avantia": False,
        "albaranes": {
            "pueden_ser_brutos": True,
            "pueden_ser_netos": True,
            "brutos_requieren_albaran_74": True,
            "netos_pueden_traer_ajuste_escala": True,
        },
        "faceta": {
            "tipo": "desglose",
            "validar_bases": True,
            "especialidad_normal_descuento_pct": 6.0,
            "especialidad_cara_puede_tener_descuento": True,
            "parafarmacia_financiada_iva": 10,
            "parafarmacia_no_financiada_iva": 21,
            "parafarmacia_cargo_pct": 3.0,
            "ajuste_escala": True,
            "imputacion_ajuste_escala": "goteo_especialidad_parafarmacia",
        },
        "servicios": {
            "bidanatural": True,
            "pct_sin_avantia": 2.0,
            "pct_con_avantia": 2.0,
            "devoluciones_por_defecto": True,
            "devoluciones_pct": 2.5,
        },
        "gestion": {
            "penalizacion_bajo_consumo": True,
            "importe_penalizacion_bajo_consumo": 50.0,
            "umbral_consumo": 5000.0,
        },
    },
    "ZV": {
        "nombre": "Zacofarva",
        "alias": ["ZACOFARVA", "ZACOFARVA GOTEO"],
        "albaran_74": "si",
        "tipo_albaran_74": 2,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": False,
        "albaranes_netos_totalizados": True,
        "facturacion": {
            "normal": "decenal",
            "facturas_normales_mes": 3,
            "transfer": "mensual_fin_mes",
            "facturas_transfer_mes": 1,
        },
        "servicios": {
            "bidanatural": True,
            "pct_sin_avantia": 2.0,
            "pct_con_avantia": 2.5,
            "devoluciones_por_defecto": False,
            "devoluciones_pct": 2.5,
        },
        "faceta": {
            "margen_tramo_fijo_referencia": 145.0,
            "margen_tramo_fijo_actualizable": True,
            "liquidaciones_club_mes_vencido": True,
            "imputacion_liquidaciones": "lineas_club_detectadas",
        },
    },
    "GS": {
        "nombre": "Socofasa",
        "albaran_74": "solo_liquidaciones",
        "tipo_albaran_74": None,
        "ajuste_comercial_factura": False,
        "cargo_adicional_gestion": True,
    },
}


def _normalizar_texto(valor):
    if pd.isna(valor):
        return ""
    texto = str(valor).strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _series_texto(df, columnas):
    for columna in columnas:
        if columna in df.columns:
            yield df[columna].astype(str)


def _extraer_acronimos_directos(df, columnas):
    contador = Counter()

    if df is None or df.empty:
        return contador

    for columna in columnas:
        if columna not in df.columns:
            continue

        for valor in df[columna].astype(str):
            acronimo = _normalizar_texto(valor)
            if acronimo in CONDICIONES_BIDAFARMA:
                contador[acronimo] += 1
                continue

            for clave, config in CONDICIONES_BIDAFARMA.items():
                alias = {_normalizar_texto(valor) for valor in config.get("alias", [])}
                if acronimo in alias:
                    contador[clave] += 1
                    break

    return contador


def extraer_acronimos(df):
    if df is None or df.empty:
        return Counter()

    candidatos = [
        "tipo",
        "tarifa",
        "condicion",
        "condición",
        "acronimo",
        "acrónimo",
        "observaciones",
        "descripcion",
    ]

    contador = Counter()
    tokens_condicion = list(CONDICIONES_BIDAFARMA.keys())
    for config in CONDICIONES_BIDAFARMA.values():
        tokens_condicion.extend(config.get("alias", []))
    tokens_condicion = sorted({_normalizar_texto(token) for token in tokens_condicion}, key=len, reverse=True)
    mapa_tokens = {
        _normalizar_texto(clave): clave
        for clave in CONDICIONES_BIDAFARMA
    }
    for clave, config in CONDICIONES_BIDAFARMA.items():
        for alias in config.get("alias", []):
            mapa_tokens[_normalizar_texto(alias)] = clave

    patron = re.compile(r"\b(" + "|".join(re.escape(token) for token in tokens_condicion) + r")\b", re.IGNORECASE)

    for serie in _series_texto(df, candidatos):
        for valor in serie:
            texto = _normalizar_texto(valor)
            if not texto:
                continue
            for match in patron.findall(texto):
                acronimo = mapa_tokens.get(_normalizar_texto(match))
                if acronimo in CONDICIONES_BIDAFARMA:
                    contador[acronimo] += 1

    if contador:
        return contador

    for columna in df.columns:
        if pd.api.types.is_numeric_dtype(df[columna]):
            continue
        for valor in df[columna].astype(str):
            texto = _normalizar_texto(valor)
            if not texto:
                continue
            for match in patron.findall(texto):
                acronimo = mapa_tokens.get(_normalizar_texto(match))
                if acronimo in CONDICIONES_BIDAFARMA:
                    contador[acronimo] += 1

    return contador


def detectar_condicion(df=None, df_faceta=None):
    columnas_prioritarias = ["tipo", "tarifa", "condicion", "condición", "acronimo", "acrónimo"]
    contador_directo = _extraer_acronimos_directos(df, columnas_prioritarias)

    if contador_directo:
        acronimo, apariciones = contador_directo.most_common(1)[0]
        config = CONDICIONES_BIDAFARMA[acronimo].copy()
        config["acronimo"] = acronimo
        config["apariciones"] = apariciones
        config["origen"] = "directo"
        return config

    contador = extraer_acronimos(df)
    if df_faceta is not None and not df_faceta.empty:
        contador.update(extraer_acronimos(df_faceta))

    if contador:
        acronimo, apariciones = contador.most_common(1)[0]
        config = CONDICIONES_BIDAFARMA[acronimo].copy()
        config["acronimo"] = acronimo
        config["apariciones"] = apariciones
        config["origen"] = "texto"
        return config

    return None


def nombre_tipo_74(tipo_codigo):
    return TIPOS_ALBARAN_74.get(tipo_codigo, "-")
