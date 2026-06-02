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
    texto = texto.replace("€", "")
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
    texto = re.sub(r"[^0-9,.\-]", "", texto)
    if not texto:
        return None
    texto = re.sub(r"\.(?=\d{3}(?:\D|$))", "", texto)
    texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _importe_desde_fila(row):
    importes = []
    for valor in row.values:
        numero = _numero_decimal(valor)
        if numero is not None:
            importes.append(numero)
    return importes[-1] if importes else None


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


def _bases_iva_vacias():
    return {
        "base_iva_4": 0.0,
        "base_iva_10": 0.0,
        "base_iva_21": 0.0,
    }


def _normalizar_tipo_iva(valor):
    numero = _numero_decimal(valor)
    if numero is None:
        return None
    if abs(numero - 4) <= 0.01:
        return 4
    if abs(numero - 10) <= 0.01:
        return 10
    if abs(numero - 21) <= 0.01:
        return 21
    return None


def _extraer_bases_iva_factura(file):
    if hasattr(file, "seek"):
        file.seek(0)
    df_raw = pd.read_excel(file, header=None)
    if hasattr(file, "seek"):
        file.seek(0)

    bases = _bases_iva_vacias()
    fila_cabecera = None
    col_base = None
    col_iva_pct = None

    for idx, row in df_raw.iterrows():
        celdas = {col: normalizar_texto(valor) for col, valor in row.items() if pd.notna(valor)}
        if not celdas:
            continue

        contiene_base = any(texto in {"base", "bases"} or texto.startswith("base ") for texto in celdas.values())
        contiene_pct_iva = any("%iva" in texto.replace(" ", "") or "% iva" in texto for texto in celdas.values())
        if not (contiene_base and contiene_pct_iva):
            continue

        fila_cabecera = idx
        for col, texto in celdas.items():
            texto_compacto = texto.replace(" ", "")
            if col_base is None and (texto in {"base", "bases"} or texto.startswith("base ")):
                col_base = col
            if col_iva_pct is None and ("%iva" in texto_compacto or "% iva" in texto):
                col_iva_pct = col
        break

    if fila_cabecera is None or col_base is None or col_iva_pct is None:
        return bases

    for _, row in df_raw.iloc[int(fila_cabecera) + 1:].iterrows():
        texto_fila = normalizar_texto(" ".join(str(x) for x in row.values if pd.notna(x)))
        if any(token in texto_fila for token in ["total compras", "totales", "servicio", "gestion", "logistica"]):
            break

        tipo_iva = _normalizar_tipo_iva(row.get(col_iva_pct))
        if tipo_iva is None:
            continue

        base = _numero_decimal(row.get(col_base))
        if base is None:
            continue

        bases[f"base_iva_{tipo_iva}"] += base

    return {clave: round(float(valor), 2) for clave, valor in bases.items()}


def _detectar_columnas_albaran_en_crudo(df_raw):
    col_albaran = None
    col_total = None
    fila_cabecera = None

    for idx, row in df_raw.iterrows():
        for col_idx, valor in row.items():
            texto = normalizar_texto(valor)
            if "albar" not in texto:
                continue
            if any(token in texto for token in ["total", "importe", "base"]):
                col_total = col_idx
            else:
                col_albaran = col_idx
            fila_cabecera = idx
        if col_albaran is not None:
            return fila_cabecera, col_albaran, col_total

    return None, None, None


def _extraer_albaranes_factura_cruda(file):
    if hasattr(file, "seek"):
        file.seek(0)
    df_raw = pd.read_excel(file, header=None)
    if hasattr(file, "seek"):
        file.seek(0)

    fila_cabecera, col_albaran, col_total = _detectar_columnas_albaran_en_crudo(df_raw)
    if col_albaran is None:
        return [], None

    albaranes = []
    total = 0.0
    total_encontrados = 0
    inicio = 0 if fila_cabecera is None else int(fila_cabecera) + 1

    for _, row in df_raw.iloc[inicio:].iterrows():
        texto_fila = normalizar_texto(" ".join(str(x) for x in row.values if pd.notna(x)))
        if any(token in texto_fila for token in ["totales", "bases", "total compras"]):
            break

        numero = extraer_numero_albaran(row.get(col_albaran))
        if not _parece_numero_albaran(numero):
            continue
        albaranes.append(numero)

        if col_total is not None:
            importe = _numero_decimal(row.get(col_total))
            if importe is not None:
                total += importe
                total_encontrados += 1

    return albaranes, (round(total, 2) if total_encontrados else None)


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

        if any(token in texto for token in tokens_fin_bloque):
            leyendo_albaranes = False
            if columnas:
                break

        for col in columnas:
            num = extraer_numero_albaran(row.get(col))
            if _parece_numero_albaran(num):
                albaranes.append(num)

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
    bases_iva = _extraer_bases_iva_factura(file)

    albaranes = []
    gastos = []
    ajustes_comerciales = []

    albaranes = _extraer_albaranes_factura(
        df,
        tokens_fin_bloque=["servicio", "gestion", "avantia", "ajuste comercial", "total compras", "totales", "bases"],
    )
    total_albaranes_factura = _sumar_total_albaranes_factura(df)
    if not albaranes:
        albaranes, total_albaranes_factura_crudo = _extraer_albaranes_factura_cruda(file)
        if total_albaranes_factura is None:
            total_albaranes_factura = total_albaranes_factura_crudo

    for _, row in df.iterrows():

        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if not valores:
            continue

        texto = " ".join(valores).lower()
        texto_normalizado = normalizar_texto(texto)

        if len(texto) < 5:
            continue

        if "iva servicios" in texto_normalizado or "total servicios" in texto_normalizado:
            continue

        # IMPORTE ROBUSTO
        importe = _importe_desde_fila(row)

        for col in df.columns:
            if importe is None and any(x in col for x in ["importe", "total", "base"]):
                try:
                    val = str(row[col]).replace(",", ".").replace("€", "").strip()
                    importe = float(val)
                    break
                except:
                    continue

        if importe is None:
            numeros = re.findall(r"-?\d+[.,]?\d*", texto)
            for num in reversed(numeros):
                importe = _numero_decimal(num)
                if importe is not None:
                    break

        if importe is None:
            continue

        texto_limpio = limpiar_texto(texto)

        if "servicio" in texto_normalizado:
            gastos.append({
                "tipo": "servicios",
                "concepto": texto_limpio,
                "importe": round(importe, 2)
            })

        elif "gestion" in texto_normalizado or "gesti" in texto_normalizado:
            gastos.append({
                "tipo": "gestion",
                "concepto": "gastos de gestión",
                "importe": round(importe, 2)
            })

        elif "avantia" in texto_normalizado:
            gastos.append({
                "tipo": "avantia",
                "concepto": "cuota avantia",
                "importe": round(importe, 2)
            })

        elif "ajuste comercial" in texto_normalizado:
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
        "bases_iva": bases_iva,
    }


# =========================
# FACTURA TRANSFER
# =========================

def analizar_factura_transfer(file):

    df = pd.read_excel(file)
    df.columns = [normalizar_nombre_columna(c) for c in df.columns]
    bases_iva = _extraer_bases_iva_factura(file)

    albaranes = []
    gastos = []
    abonos = []

    albaranes = _extraer_albaranes_factura(
        df,
        tokens_fin_bloque=["log", "abono", "laboratorio", "total compras", "totales", "bases"],
    )
    total_albaranes_factura = _sumar_total_albaranes_factura(df)
    if not albaranes:
        albaranes, total_albaranes_factura_crudo = _extraer_albaranes_factura_cruda(file)
        if total_albaranes_factura is None:
            total_albaranes_factura = total_albaranes_factura_crudo

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
        "bases_iva": bases_iva,
    }
