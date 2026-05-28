import unicodedata

import pandas as pd


ESCALADOS_CLUBES_B = {
    "normal": {
        "NORMON": [(0, 0.0), (1001, 0.20), (2001, 0.40), (3001, 0.45), (4001, 0.50)],
        "STADA": [(0, 0.0), (650, 0.20), (1400, 0.35), (2400, 0.45), (3400, 0.50)],
        "TEVA": [],
        "KERN": [(0, 0.0), (650, 0.20), (1200, 0.40), (2200, 0.45), (3400, 0.50)],
        "CINFA": [(0, 0.0), (999, 0.10), (3500, 0.30), (40000, 0.45)],
        "NEURAXPHARM": [(0, 0.10), (150, 0.30)],
        "VIATRIS": [(0, 0.0), (1000, 0.35)],
    },
    "avantia": {
        "NORMON": [(0, 0.0), (1001, 0.20), (2001, 0.40), (3001, 0.45), (4001, 0.50)],
        "STADA": [(0, 0.0), (650, 0.20), (1200, 0.35), (2200, 0.45), (3200, 0.50)],
        "TEVA": [],
        "KERN": [(0, 0.0), (650, 0.20), (1200, 0.40), (2000, 0.45), (3400, 0.50), (4000, 0.52)],
        "CINFA": [],
        "NEURAXPHARM": [(0, 0.10), (150, 0.30)],
        "VIATRIS": [(0, 0.0), (1000, 0.35)],
    },
}

LAB_ALIASES = {
    "NORMON": "NORMON",
    "LABORATORIOS NORMON": "NORMON",
    "STADA": "STADA",
    "TEVA": "TEVA",
    "TEVA PHARMA": "TEVA",
    "KERN": "KERN",
    "KERN PHARMA": "KERN",
    "CINFA": "CINFA",
    "NEURAXPHARM": "NEURAXPHARM",
    "NEURAX PHARM": "NEURAXPHARM",
    "NEURAS PHARMA": "NEURAXPHARM",
    "NEURAS": "NEURAXPHARM",
    "VIATRIS": "VIATRIS",
}


def _normalizar_texto(valor):
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return texto.upper().strip()


def _normalizar_columna(columna):
    texto = _normalizar_texto(columna).lower()
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


def _buscar_columna(df, opciones):
    columnas = {_normalizar_columna(col): col for col in df.columns}
    for opcion in opciones:
        encontrada = columnas.get(_normalizar_columna(opcion))
        if encontrada is not None:
            return encontrada
    return None


def normalizar_laboratorio(valor):
    texto = _normalizar_texto(valor)
    if not texto:
        return None
    for alias, canonico in LAB_ALIASES.items():
        if alias in texto:
            return canonico
    return None


def detectar_modo_clubes(df_compras):
    if df_compras is None or df_compras.empty:
        return "normal"
    texto = " ".join(
        df_compras[col].astype(str).str.lower().str.cat(sep=" ")
        for col in df_compras.columns
        if any(token in _normalizar_columna(col) for token in ["descripcion", "seccion", "tipo", "categoria"])
    )
    return "avantia" if "avantia" in texto else "normal"


def _tramo_actual_y_siguiente(escalados, compra):
    escalados = sorted(escalados or [], key=lambda item: float(item[0]))
    if not escalados:
        return 0.0, 0.0, None, 0.0

    actual = escalados[0]
    siguiente = None
    for tramo in escalados:
        if compra >= float(tramo[0]):
            actual = tramo
        elif siguiente is None:
            siguiente = tramo
            break

    objetivo_actual, pct_actual = float(actual[0]), float(actual[1])
    objetivo_siguiente = None if siguiente is None else float(siguiente[0])
    pct_siguiente = pct_actual if siguiente is None else float(siguiente[1])
    return objetivo_actual, pct_actual, objetivo_siguiente, pct_siguiente


def generar_prevision_escalados(df_compras, modo=None):
    if df_compras is None or df_compras.empty:
        return pd.DataFrame()

    modo = (modo or detectar_modo_clubes(df_compras)).lower()
    if modo not in ESCALADOS_CLUBES_B:
        modo = "normal"

    df = df_compras.copy()
    columna_lab = _buscar_columna(df, ["laboratorio_maestro", "laboratorio", "lab", "proveedor_laboratorio"])
    if columna_lab is None:
        return pd.DataFrame()

    laboratorio = df[columna_lab].map(normalizar_laboratorio)
    bruto = _serie_numerica(df, "bruto").abs()
    tipo_compra = df.get("tipo_compra", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()
    seccion = df.get("seccion_albaran", pd.Series("", index=df.index)).astype(str).str.lower().str.strip()

    texto_club = pd.Series("", index=df.index, dtype="object")
    for columna in df.columns:
        nombre = _normalizar_columna(columna)
        if any(token in nombre for token in ["categoria", "descuento", "cargo", "dc", "descripcion", "observacion", "seccion"]):
            texto_club = texto_club + " " + df[columna].astype(str).str.lower()

    es_club_goteo = tipo_compra.eq("goteo") & texto_club.str.contains("club", na=False)
    suma_escalado = es_club_goteo | tipo_compra.isin(["transfer", "bitransfer"]) | seccion.isin(["transfer", "bitransfer"])
    base_liquidable = es_club_goteo

    filas = []
    for lab, escalados in ESCALADOS_CLUBES_B[modo].items():
        mask_lab = laboratorio.eq(lab)
        compra_real = float(bruto[mask_lab & suma_escalado].sum())
        base_liq = float(bruto[mask_lab & base_liquidable].sum())
        objetivo_actual, pct_actual, objetivo_siguiente, pct_siguiente = _tramo_actual_y_siguiente(escalados, compra_real)
        diferencia = 0.0 if objetivo_siguiente is None else max(0.0, objetivo_siguiente - compra_real)
        filas.append(
            {
                "laboratorio": lab,
                "grupo": f"club_{lab.lower()}",
                "modo": modo,
                "compra_real": round(compra_real, 2),
                "base_liquidable": round(base_liq, 2),
                "tramo_actual": round(objetivo_actual, 2),
                "porcentaje_liquidacion": round(pct_actual * 100, 2),
                "liquidacion_esperada": round(base_liq * pct_actual, 2),
                "siguiente_tramo": None if objetivo_siguiente is None else round(objetivo_siguiente, 2),
                "porcentaje_siguiente": round(pct_siguiente * 100, 2),
                "diferencia_para_siguiente_tramo": round(diferencia, 2),
                "liquidacion_actual": 0.0,
                "liquidacion_potencial": round(base_liq * pct_siguiente, 2),
                "fuente": "master_clubes_vida_pharma",
            }
        )

    return pd.DataFrame(filas)
