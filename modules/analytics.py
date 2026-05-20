import pandas as pd
import re
import unicodedata

# =========================
# UTILIDADES
# =========================

def extraer_numero_albaran(texto):
    if pd.isna(texto):
        return None

    if isinstance(texto, (int, float)) and not isinstance(texto, bool):
        if float(texto).is_integer():
            return str(int(texto))

    texto = str(texto).lower().strip()
    if not texto:
        return None

    texto = re.sub(r"\.0+$", "", texto)
    if re.fullmatch(r"[a-z.\s-]*\d[\d.\s-]*", texto):
        numero = re.sub(r"\D", "", texto)
        return numero or None

    grupos = re.findall(r"\d+", texto)
    grupos_largos = [grupo for grupo in grupos if len(grupo) >= 4]
    if grupos_largos:
        return grupos_largos[-1]

    numero = re.sub(r"\D", "", texto)
    return numero or None


def normalizar_nombre_columna(columna):
    texto = str(columna).lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip()


def normalizar_texto(texto):
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip()


def _parece_numero_albaran(numero):
    return bool(numero and re.fullmatch(r"\d{6,10}", str(numero)))


def _columnas_albaran(df):
    columnas = []
    tokens_excluir = ["total", "importe", "base", "iva", "recargo", "fecha"]
    tokens_numero = ["numero", "num", "n ", "nº", "nro", "albaran"]
    for col in df.columns:
        nombre = normalizar_nombre_columna(col)
        if "albar" not in nombre:
            continue
        if any(token in nombre for token in tokens_excluir):
            continue
        if any(token in nombre for token in tokens_numero):
            columnas.append(col)
    return columnas


def _columna_total_albaran(df):
    for col in df.columns:
        nombre = normalizar_nombre_columna(col)
        if "albar" in nombre and any(token in nombre for token in ["total", "importe"]):
            return col
    return None


def _numero_decimal(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    texto = str(valor).replace("€", "").replace("EUR", "").replace(" ", "").strip()
    if not texto:
        return None
    texto = re.sub(r"\.(?=\d{3}(?:\D|$))", "", texto)
    texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _sumar_total_albaranes_factura(df):
    columnas_albaran = _columnas_albaran(df)
    columna_total = _columna_total_albaran(df)
    if not columnas_albaran or not columna_total:
        return None

    total = 0.0
    encontrados = 0
    for _, row in df.iterrows():
        if not any(_parece_numero_albaran(extraer_numero_albaran(row.get(col))) for col in columnas_albaran):
            continue
        importe = _numero_decimal(row.get(columna_total))
        if importe is None:
            continue
        total += importe
        encontrados += 1

    return round(total, 2) if encontrados else None


def _extraer_albaranes_factura(df, tokens_fin_bloque):
    albaranes = []
    columnas = _columnas_albaran(df)
    leyendo_albaranes = True

    for _, row in df.iterrows():
        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = normalizar_texto(" ".join(valores))
        if len(texto) < 5:
            continue

        for col in columnas:
            num = extraer_numero_albaran(row.get(col))
            if _parece_numero_albaran(num):
                albaranes.append(num)

        if any(token in texto for token in tokens_fin_bloque):
            leyendo_albaranes = False

        if not leyendo_albaranes and columnas:
            break

    return albaranes


def limpiar_texto(texto):
    texto = re.sub(r"\d+(\.\d+)?", "", texto)
    return texto.strip()


def limpiar_concepto_abono(texto):
    texto = re.sub(r"-?\d+(\.\d+)?", "", texto)
    texto = texto.replace("  ", " ")
    return texto.strip()


# =========================
# FACTURA NORMAL
# =========================

def analizar_factura_bidafarma(file):

    df = pd.read_excel(file)
    df.columns = [normalizar_nombre_columna(c) for c in df.columns]

    albaranes = []
    gastos = []
    ajustes_comerciales = []

    albaranes = _extraer_albaranes_factura(
        df,
        tokens_fin_bloque=["servicio", "gestion", "avantia", "ajuste comercial"],
    )
    total_albaranes_factura = _sumar_total_albaranes_factura(df)

    for _, row in df.iterrows():

        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = " ".join(valores).lower()

        if len(texto) < 5:
            continue

        if any(x in texto for x in ["iva", "recargo", "total"]):
            continue

        # IMPORTE ROBUSTO
        importe = None

        for col in df.columns:
            if any(x in col for x in ["importe", "total", "base"]):
                try:
                    val = str(row[col]).replace(",", ".").replace("€", "").strip()
                    importe = float(val)
                    break
                except:
                    continue

        if importe is None:
            numeros = re.findall(r"-?\d+[.,]?\d*", texto)
            for num in reversed(numeros):
                try:
                    importe = float(num.replace(",", "."))
                    break
                except:
                    continue

        if importe is None:
            continue

        texto_limpio = limpiar_texto(texto)

        if "servicio" in texto:
            gastos.append({
                "tipo": "servicios",
                "concepto": texto_limpio,
                "importe": round(importe, 2)
            })

        elif "gestion" in texto or "gestión" in texto:
            gastos.append({
                "tipo": "gestion",
                "concepto": "gastos de gestión",
                "importe": round(importe, 2)
            })

        elif "avantia" in texto:
            gastos.append({
                "tipo": "avantia",
                "concepto": "cuota avantia",
                "importe": round(importe, 2)
            })

        elif "ajuste comercial" in texto:
            ajustes_comerciales.append({
                "tipo": "ajuste_comercial",
                "concepto": texto_limpio,
                "importe": round(importe, 2)
            })

    total_gastos = sum([g["importe"] for g in gastos])
    iva = total_gastos * 0.21
    total_final = total_gastos + iva

    return {
        "albaranes": list(set(albaranes)),
        "gastos": pd.DataFrame(gastos),
        "ajustes_comerciales": pd.DataFrame(ajustes_comerciales),
        "resumen_costes": {
            "base": round(total_gastos, 2),
            "iva": round(iva, 2),
            "total": round(total_final, 2)
        },
        "total_albaranes_factura": total_albaranes_factura,
    }


# =========================
# FACTURA TRANSFER
# =========================

def analizar_factura_transfer(file):

    df = pd.read_excel(file)
    df.columns = [normalizar_nombre_columna(c) for c in df.columns]

    albaranes = []
    gastos = []
    abonos = []

    albaranes = _extraer_albaranes_factura(
        df,
        tokens_fin_bloque=["log", "abono", "laboratorio"],
    )
    total_albaranes_factura = _sumar_total_albaranes_factura(df)

    for _, row in df.iterrows():

        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = " ".join(valores).lower()

        if len(texto) < 5:
            continue

        if any(x in texto for x in ["iva", "recargo", "total"]):
            continue

        # =========================
        # IMPORTE ROBUSTO (FIX)
        # =========================
        importe = None

        # 1. columnas típicas
        for col in df.columns:
            if any(x in col for x in ["importe", "total", "base"]):
                try:
                    val = str(row[col]).replace(",", ".").replace("€", "").strip()
                    importe = float(val)
                    break
                except:
                    continue

        # 2. fallback regex
        if importe is None:
            numeros = re.findall(r"-?\d+[.,]?\d*", texto)

            for num in reversed(numeros):
                try:
                    importe = float(num.replace(",", "."))
                    break
                except:
                    continue

        if importe is None:
            continue

        # LOGÍSTICA
        if "log" in texto or "logistico" in texto:
            gastos.append({
                "tipo": "logistica",
                "concepto": "servicios logisticos",
                "importe": round(importe, 2)
            })

        # ABONOS
        elif "abono" in texto or "laboratorio" in texto:
            abonos.append({
                "tipo": "abono",
                "concepto": limpiar_concepto_abono(texto),
                "importe": round(importe, 2)
            })

    total_logistica = sum([g["importe"] for g in gastos])
    total_abonos = sum([a["importe"] for a in abonos])

    base = total_logistica + total_abonos
    iva = base * 0.21
    total_final = base + iva

    return {
        "albaranes": list(set(albaranes)),
        "gastos": pd.DataFrame(gastos),
        "abonos": pd.DataFrame(abonos),
        "resumen_logistica": {
            "base": round(base, 2),
            "iva": round(iva, 2),
            "total": round(total_final, 2)
        },
        "total_albaranes_factura": total_albaranes_factura,
    }
