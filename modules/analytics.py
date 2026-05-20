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

    col_albaran = next((c for c in df.columns if "albaran" in c or "albar" in c), None)

    leyendo_albaranes = True

    for _, row in df.iterrows():

        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = " ".join(valores).lower()

        if len(texto) < 5:
            continue

        if any(x in texto for x in ["servicio", "gestion", "gestión", "avantia", "ajuste comercial"]):
            leyendo_albaranes = False

        if leyendo_albaranes and col_albaran:
            valor = str(row[col_albaran]).strip()
            num = extraer_numero_albaran(valor)
            if num:
                albaranes.append(num)

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
        }
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

    col_albaran = next((c for c in df.columns if "albaran" in c or "albar" in c), None)

    leyendo_albaranes = True

    for _, row in df.iterrows():

        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = " ".join(valores).lower()

        if len(texto) < 5:
            continue

        if any(x in texto for x in ["log", "abono", "laboratorio"]):
            leyendo_albaranes = False

        # ALBARANES
        if leyendo_albaranes and col_albaran:
            valor = str(row[col_albaran]).strip()
            num = extraer_numero_albaran(valor)
            if num:
                albaranes.append(num)

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
        }
    }
