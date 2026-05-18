import streamlit as st
import pandas as pd
import re
import importlib
import unicodedata

from modules.ingestion import load_excel
from modules.parser import parse_sections
from modules.classification import normalize_columns, clasificar_especialidad_cara
from modules.analytics import analizar_factura_bidafarma, analizar_factura_transfer
import modules.bitransfer as bitransfer
import modules.servicios as servicios
import modules.avantia as avantia
import modules.faceta as faceta
import modules.condiciones_bidafarma as condiciones_bidafarma
import modules.maestro_laboratorios as maestro_laboratorios
import modules.nomenclator_aemps as nomenclator_aemps
import modules.ventas as ventas
import modules.reporting as reporting
import modules.equivalencias_efg as equivalencias_efg

bitransfer = importlib.reload(bitransfer)
servicios = importlib.reload(servicios)
avantia = importlib.reload(avantia)
faceta = importlib.reload(faceta)
condiciones_bidafarma = importlib.reload(condiciones_bidafarma)
maestro_laboratorios = importlib.reload(maestro_laboratorios)
nomenclator_aemps = importlib.reload(nomenclator_aemps)
ventas = importlib.reload(ventas)
reporting = importlib.reload(reporting)
equivalencias_efg = importlib.reload(equivalencias_efg)

DEBUG_PASSWORD = "CAMBIAR_CLAVE"
try:
    APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")
except Exception:
    APP_PASSWORD = ""
MODO_DEBUG = False

PROVEEDORES_BASE = {
    "cofares": "cofares",
    "alliance": "alliance",
    "hefame": "hefame",
}

SECCIONES = [
    "bidafarma",
    "cofares",
    "alliance",
    "hefame",
    "Facturas laboratorios",
    "Ventas farmacia",
    "Stock",
    "Resumen",
]

# =========================
# NORMALIZADOR GLOBAL
# =========================

def normalizar_albaran(valor):
    valor = str(valor).lower().strip()
    match = re.match(r"^[a-z]{0,3}-?\d+$", valor)

    if match:
        return re.sub(r"[^\d]", "", valor)

    return valor


def _guardar_dataset(clave, df):
    st.session_state[clave] = df


def _mostrar_dataframe_debug(df, mensaje="Vista completa oculta por privacidad."):
    if MODO_DEBUG:
        st.dataframe(df)
    else:
        st.caption(f"{mensaje} Activa MODO_DEBUG para verla.")


def _verificar_acceso_app():
    if st.session_state.get("app_authenticated"):
        return

    _, login_col, _ = st.columns([1, 1.2, 1])
    with login_col:
        with st.form("login_app"):
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar")

        if submitted and password == APP_PASSWORD:
            st.session_state["app_authenticated"] = True
            st.rerun()

        st.warning("Acceso restringido")
        st.stop()


def _asegurar_maestros_en_sesion():
    return


def _obtener_maestro_laboratorios():
    _asegurar_maestros_en_sesion()

    manual_df = st.session_state.get("maestro_laboratorios_df")
    ministerio_df = st.session_state.get("maestro_ministerio_df")
    aemps_df = st.session_state.get("maestro_medicamentos_aemps_df")

    piezas = []
    if manual_df is not None and not manual_df.empty:
        piezas.append(manual_df.copy())
    if ministerio_df is not None and not ministerio_df.empty:
        piezas.append(ministerio_df.copy())
    if aemps_df is not None and not aemps_df.empty:
        piezas.append(aemps_df.copy())

    if not piezas:
        return None

    combinado = pd.concat(piezas, ignore_index=True)
    combinado = combinado.drop_duplicates(subset=["cn"], keep="first").reset_index(drop=True)
    return combinado


def _enriquecer_con_maestro(df):
    maestro_df = _obtener_maestro_laboratorios()
    if maestro_df is None or maestro_df.empty:
        return df
    return maestro_laboratorios.enriquecer_con_laboratorio(df, maestro_df)


def _render_uploader_equivalencias_efg():
    equivalencias_file = st.file_uploader(
        "Equivalencias EFG",
        type=["xlsx"],
        key="equivalencias_efg_file",
        help=(
            "Sube la base BASE_EQUIVALENCIAS_EFG_NOMENCLATOR_v2_LOGICA_DINAMICA.xlsx. "
            "Solo identifica grupos, opciones y laboratorios EFG disponibles; no fija laboratorio recomendado."
        ),
    )

    if equivalencias_file:
        try:
            efg_data = equivalencias_efg.leer_base_equivalencias_efg(equivalencias_file)
            st.session_state["tabla_equivalencias_efg"] = efg_data["tabla_equivalencias_efg"]
            st.session_state["grupos_homogeneos_efg"] = efg_data["grupos_homogeneos"]
            st.session_state["opciones_por_grupo_efg"] = efg_data["opciones_por_grupo"]
            st.session_state["resumen_equivalencias_efg"] = efg_data["resumen"]
            st.session_state["equivalencias_efg_cargadas"] = True
        except ValueError as error:
            st.error(f"No se pudo leer la base de equivalencias EFG: {error}")


def _render_base_maestra_laboratorios():
    _asegurar_maestros_en_sesion()

    st.subheader("🧬 Base maestra CN / laboratorio")
    st.caption(
        "Usaremos como base principal el Nomenclátor de facturación del Ministerio. "
        "La base manual servirá para completar huecos y AEMPS quedará como apoyo para medicamentos."
    )

    col_ministerio, col_manual, col_aemps, col_efg = st.columns(4)

    with col_efg:
        _render_uploader_equivalencias_efg()

    with col_ministerio:
        ministerio_file = st.file_uploader(
            "Nomenclátor facturación Ministerio",
            type=["xls", "xlsx", "csv"],
            key="maestro_ministerio_file",
            help=(
                "Sube aquí el nomenclátor de facturación del Ministerio. "
                "Será la base maestra principal porque incluye código nacional, descripción, laboratorio y tipo."
            ),
        )

        if ministerio_file:
            try:
                ministerio_df = maestro_laboratorios.leer_maestro_laboratorios(ministerio_file)
                ministerio_df["fuente_maestro"] = "ministerio_facturacion"
                if "tipo_producto" not in ministerio_df.columns:
                    ministerio_df["tipo_producto"] = None
                st.session_state["maestro_ministerio_df"] = ministerio_df
                st.session_state["maestro_ministerio_nombre"] = ministerio_file.name
            except ValueError as error:
                st.error(f"No se pudo leer el nomenclátor del Ministerio: {error}")

    with col_manual:
        maestro_file = st.file_uploader(
            "Base maestra manual CN / laboratorio",
            type=["xls", "xlsx", "csv"],
            key="maestro_cn_laboratorio_file",
            help=(
                "Esta base manual nos servirá para completar fuentes no cubiertas por AEMPS, "
                "como parafarmacia u otros códigos propios."
            ),
        )

        if maestro_file:
            try:
                maestro_df = maestro_laboratorios.leer_maestro_laboratorios(maestro_file)
                maestro_df["fuente_maestro"] = "manual"
                st.session_state["maestro_laboratorios_df"] = maestro_df
                st.session_state["maestro_laboratorios_nombre"] = maestro_file.name
            except ValueError as error:
                st.error(f"No se pudo leer la base maestra manual: {error}")

    with col_aemps:
        nomenclator_file = st.file_uploader(
            "Nomenclátor AEMPS medicamentos",
            type=["zip", "xml"],
            key="nomenclator_aemps_file",
            help=(
                "Sube aquí el zip oficial del Nomenclátor AEMPS de medicamentos. "
                "Lo convertiremos automáticamente a la base maestra CN / laboratorio para medicamentos."
            ),
        )

        if nomenclator_file:
            try:
                nomenclator_df = nomenclator_aemps.leer_nomenclator_aemps(nomenclator_file)
                st.session_state["maestro_medicamentos_aemps_df"] = nomenclator_df
                st.session_state["maestro_medicamentos_aemps_nombre"] = nomenclator_file.name
            except ValueError as error:
                st.error(f"No se pudo leer el Nomenclátor AEMPS: {error}")

    ministerio_df = st.session_state.get("maestro_ministerio_df")
    manual_df = st.session_state.get("maestro_laboratorios_df")
    aemps_df = st.session_state.get("maestro_medicamentos_aemps_df")
    tabla_equivalencias_efg = st.session_state.get("tabla_equivalencias_efg")
    grupos_homogeneos_efg = st.session_state.get("grupos_homogeneos_efg")
    opciones_por_grupo_efg = st.session_state.get("opciones_por_grupo_efg")
    resumen_equivalencias_efg = st.session_state.get("resumen_equivalencias_efg")
    maestro_df = _obtener_maestro_laboratorios()

    if ministerio_df is not None and not ministerio_df.empty:
        m0, m1, m2, m3 = st.columns(4)
        m0.metric("CN Ministerio", ministerio_df["cn"].nunique())
        m1.metric("Labs Ministerio", ministerio_df["laboratorio_maestro"].nunique())
        tipo_ministerio = (
            ministerio_df["tipo_producto"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique()
            if "tipo_producto" in ministerio_df.columns
            else 0
        )
        m2.metric("Tipos Ministerio", tipo_ministerio)
        desc_ministerio = (
            ministerio_df["descripcion_maestra"].fillna("").astype(str).str.strip().ne("").sum()
            if "descripcion_maestra" in ministerio_df.columns
            else 0
        )
        m3.metric("CN con descripción", desc_ministerio)
        st.caption(
            f"Nomenclátor principal activo: {st.session_state.get('maestro_ministerio_nombre', 'nomenclátor cargado')}"
        )

    if aemps_df is not None and not aemps_df.empty:
        a1, a2, a3 = st.columns(3)
        a1.metric("Medicamentos AEMPS", aemps_df["cn"].nunique())
        a2.metric("Labs AEMPS", aemps_df["laboratorio_maestro"].nunique())
        cn_con_laboratorio = aemps_df["laboratorio_maestro"].fillna("").astype(str).str.strip().ne("").sum()
        a3.metric("CN con laboratorio", cn_con_laboratorio)
        st.caption(
            f"Nomenclátor activo: {st.session_state.get('maestro_medicamentos_aemps_nombre', 'nomenclátor cargado')}"
        )
        if cn_con_laboratorio < len(aemps_df):
            st.warning(
                "Hay códigos nacionales del Nomenclátor sin laboratorio resuelto. "
                "Los seguiremos cargando, pero conviene revisar si el formato del zip ha cambiado."
            )
        else:
            st.success(
                "El zip del Nomenclátor AEMPS se ha cargado correctamente y los laboratorios se han resuelto."
            )

    if manual_df is not None and not manual_df.empty:
        b1, b2 = st.columns(2)
        b1.metric("Registros manuales", manual_df["cn"].nunique())
        b2.metric("Labs manuales", manual_df["laboratorio_maestro"].nunique())
        st.caption(
            f"Base manual activa: {st.session_state.get('maestro_laboratorios_nombre', 'base manual cargada')}"
        )

    if resumen_equivalencias_efg:
        st.subheader("Equivalencias EFG")
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Productos", resumen_equivalencias_efg.get("productos", 0))
        e2.metric("Grupos homogéneos", resumen_equivalencias_efg.get("grupos_homogeneos", 0))
        e3.metric("Opciones EFG", resumen_equivalencias_efg.get("opciones_efg", 0))
        e4.metric("Marcas con alternativa", resumen_equivalencias_efg.get("marcas_con_alternativa_efg", 0))
        st.info(
            "Base EFG cargada como tabla maestra neutra. No contiene laboratorio recomendado fijo; "
            "la recomendación se calculará dinámicamente con datos reales de cada farmacia."
        )
        if MODO_DEBUG:
            if tabla_equivalencias_efg is not None and not tabla_equivalencias_efg.empty:
                st.caption("Preview tabla_equivalencias_efg")
                st.dataframe(tabla_equivalencias_efg.head(20))
            if grupos_homogeneos_efg is not None and not grupos_homogeneos_efg.empty:
                st.caption("Preview grupos_homogeneos")
                st.dataframe(grupos_homogeneos_efg.head(20))
            if opciones_por_grupo_efg is not None and not opciones_por_grupo_efg.empty:
                st.caption("Preview opciones_por_grupo")
                st.dataframe(opciones_por_grupo_efg.head(20))

    if maestro_df is not None and not maestro_df.empty:
        m1, m2 = st.columns(2)
        m1.metric("Códigos nacionales totales", maestro_df["cn"].nunique())
        m2.metric("Laboratorios totales", maestro_df["laboratorio_maestro"].dropna().nunique())
        st.caption("Vista previa de la base maestra combinada")
        st.dataframe(maestro_df.head(20))
    else:
        st.info(
            "Todavía no hay base cargada. Lo ideal es empezar por el nomenclátor de facturación del Ministerio, "
            "y luego completar con la base manual o con AEMPS si hace falta."
        )


def _leer_albaranes_genericos(uploaded_files, proveedor, tipo_compra):
    dfs = []

    if not uploaded_files:
        return dfs

    for uploaded_file in uploaded_files:
        df_temp = normalize_columns(load_excel(uploaded_file))
        df_temp.columns = [c.lower().strip() for c in df_temp.columns]
        df_temp["proveedor"] = proveedor
        df_temp["tipo_compra"] = tipo_compra

        col_albaran = next((c for c in df_temp.columns if "albaran" in c), None)
        if col_albaran:
            df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

        df_temp = parse_sections(df_temp)
        df_temp = _enriquecer_con_maestro(df_temp)
        df_temp = clasificar_especialidad_cara(df_temp)
        dfs.append(df_temp)

    return dfs


def _mostrar_vistas_albaranes(df):
    if df is None:
        return

    for tipo in ["goteo", "transfer"]:
        df_tipo = df[df["tipo_compra"] == tipo].copy()

        if df_tipo.empty:
            continue

        titulo = "📦 Goteo" if tipo == "goteo" else "🚚 Transfer"
        st.header(f"{titulo}")

        _mostrar_dataframe_debug(df_tipo, "Albaranes completos ocultos por privacidad.")

        if "tipo" in df_tipo.columns:
            mask_faceta = df_tipo.apply(
                lambda row: faceta.es_linea_faceta(row.get("tipo"), row.get("descripcion")),
                axis=1,
            )
            df_tipo = df_tipo[~mask_faceta].copy()

        df_tipo["bruto"] = pd.to_numeric(df_tipo["bruto"], errors="coerce").fillna(0.0)
        df_tipo["neto"] = pd.to_numeric(df_tipo["neto"], errors="coerce").fillna(0.0)
        df_tipo["unidades"] = pd.to_numeric(df_tipo["unidades"], errors="coerce").fillna(0.0)
        df_tipo["es_abono"] = df_tipo["neto"] < 0
        abonos = df_tipo[df_tipo["es_abono"]]
        compras = df_tipo[~df_tipo["es_abono"]]

        total_bruto = compras["bruto"].sum()
        total_neto = compras["neto"].sum()
        total_abonos = abonos["neto"].sum()

        descuento = (total_bruto - total_neto) / total_bruto * 100 if total_bruto else 0

        c1, c2, c3, c4, c5, c6 = st.columns(6)

        c1.metric("Líneas", len(df_tipo))
        c2.metric("Unidades", int(df_tipo["unidades"].sum()))
        c3.metric("Bruto", f"{total_bruto:.1f} €")
        c4.metric("Neto", f"{total_neto:.1f} €")
        c5.metric("Desc %", round(descuento, 2))
        c6.metric("Abonos", f"{abs(total_abonos):.1f} €")

        _mostrar_resumen_especialidad_cara(compras)


def _mostrar_resumen_especialidad_cara(df):
    if df is None or df.empty or "es_especialidad_cara" not in df.columns:
        return

    especialidad_cara = df[df["es_especialidad_cara"]].copy()
    if especialidad_cara.empty:
        return

    for columna in ["bruto", "neto", "descuento_especialidad_cara_euros"]:
        if columna in especialidad_cara.columns:
            especialidad_cara[columna] = pd.to_numeric(especialidad_cara[columna], errors="coerce").fillna(0.0)
        else:
            especialidad_cara[columna] = 0.0

    lineas = len(especialidad_cara)
    bruto_total = especialidad_cara["bruto"].sum()
    neto_total = especialidad_cara["neto"].sum()
    descuento_total = especialidad_cara["descuento_especialidad_cara_euros"].sum()
    descuento_medio = descuento_total / lineas if lineas else 0.0

    st.subheader("Especialidad cara / RDL 4/2010")
    ec1, ec2, ec3, ec4, ec5 = st.columns(5)
    ec1.metric("Líneas", lineas)
    ec2.metric("Bruto", f"{bruto_total:.2f} €")
    ec3.metric("Neto", f"{neto_total:.2f} €")
    ec4.metric("Descuento €", f"{descuento_total:.2f} €")
    ec5.metric("Desc. medio/línea", f"{descuento_medio:.2f} €")

    columnas_debug = [
        "cn",
        "descripcion",
        "proveedor",
        "tipo_compra",
        "iva",
        "unidades",
        "bruto",
        "neto",
        "bruto_unitario",
        "neto_unitario",
        "tipo_especialidad_cara",
        "descuento_especialidad_cara_euros",
        "base_iva4_total",
        "base_iva4_especialidad_cara",
        "base_iva4_sujeta_ajuste",
    ]
    columnas_debug = [col for col in columnas_debug if col in especialidad_cara.columns]
    if MODO_DEBUG:
        st.caption("Líneas detectadas como especialidad cara")
        st.dataframe(especialidad_cara[columnas_debug])


def _guardar_analisis_distribuidora(proveedor_id, analisis):
    analisis_actuales = st.session_state.get("analisis_distribuidora", {})
    if not isinstance(analisis_actuales, dict):
        analisis_actuales = {}
    analisis_actuales[proveedor_id] = analisis
    st.session_state["analisis_distribuidora"] = analisis_actuales


def _mostrar_analisis_distribuidora(analisis):
    if not analisis or not analisis.get("ok"):
        st.warning((analisis or {}).get("mensaje", "No hay análisis disponible."))
        return

    resumen = analisis.get("resumen", {})
    st.subheader(f"Análisis distribuidora · {analisis.get('proveedor', '')}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Compra bruta", f"{resumen.get('compra_bruta_total', 0):.2f} €")
    c2.metric("Compra neta", f"{resumen.get('compra_neta_total', 0):.2f} €")
    c3.metric("Abonos", f"{resumen.get('abonos_totales', 0):.2f} €")
    descuento = resumen.get("descuento_medio_general")
    c4.metric("Desc. medio", "-" if descuento is None else f"{descuento:.2f}%")

    periodo = resumen.get("periodo")
    if periodo:
        st.caption(f"Periodo analizado: {periodo.get('desde')} a {periodo.get('hasta')}")

    desglose = analisis.get("desglose", pd.DataFrame())
    if desglose is not None and not desglose.empty:
        st.caption("Desglose por tipo de compra")
        st.dataframe(desglose)

    cargos = analisis.get("cargos", pd.DataFrame())
    if cargos is not None and not cargos.empty:
        st.caption("Cargos detectados")
        st.dataframe(cargos)

    especialidad_cara = analisis.get("especialidad_cara", pd.DataFrame())
    if especialidad_cara is not None and not especialidad_cara.empty:
        st.caption("Especialidad cara / RDL 4/2010")
        st.dataframe(especialidad_cara)

    top_impacto = analisis.get("top_impacto", pd.DataFrame())
    if top_impacto is not None and not top_impacto.empty:
        st.caption("Top impacto coste aparente vs coste real")
        st.dataframe(top_impacto)
    else:
        st.info("Top impacto pendiente: no hay costes imputados suficientes para calcular diferencias.")


def _serie_numerica(df, columna):
    if df is None or columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index if df is not None else None)
    return pd.to_numeric(df[columna], errors="coerce").fillna(0.0)


def _descuento_pct(bruto_total, coste_total):
    if bruto_total <= 0:
        return 0.0
    return round((1 - (coste_total / bruto_total)) * 100, 2)


def _normalizar_texto_match(valor):
    if pd.isna(valor):
        return ""
    texto = str(valor).strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _tokens_significativos_laboratorio(laboratorio):
    texto = _normalizar_texto_match(laboratorio)
    if not texto:
        return []

    stopwords = {
        "laboratorio",
        "laboratorios",
        "pharma",
        "farma",
        "iberica",
        "iberico",
        "grupo",
        "medical",
        "medic",
        "medica",
        "medico",
        "productos",
        "producto",
        "sociedad",
        "anonima",
        "limitada",
        "slu",
        "sluu",
        "sl",
        "sa",
        "s",
        "l",
        "u",
        "a",
        "de",
        "del",
        "la",
        "el",
    }

    tokens = [token for token in texto.split() if token not in stopwords and len(token) >= 4]
    return tokens


def _detectar_laboratorios_bonificados(df_transfer, abonos_transfer):
    if (
        df_transfer is None
        or df_transfer.empty
        or abonos_transfer is None
        or abonos_transfer.empty
        or "laboratorio_maestro" not in df_transfer.columns
    ):
        return {"laboratorios": [], "detalle": pd.DataFrame()}

    labs = (
        df_transfer["laboratorio_maestro"]
        .dropna()
        .astype(str)
        .str.strip()
    )
    labs = labs[labs != ""].drop_duplicates().tolist()
    firmas = []
    for lab in labs:
        lab_norm = _normalizar_texto_match(lab)
        tokens = _tokens_significativos_laboratorio(lab)
        firmas.append(
            {
                "laboratorio": lab,
                "normalizado": lab_norm,
                "tokens": tokens,
            }
        )
    firmas = sorted(
        firmas,
        key=lambda item: (len(item["tokens"]), len(item["normalizado"])),
        reverse=True,
    )

    registros = []
    laboratorios_detectados_total = set()
    for _, fila in abonos_transfer.iterrows():
        concepto = str(fila.get("concepto", "")).strip()
        concepto_norm = _normalizar_texto_match(concepto)
        importe = float(fila.get("importe", 0) or 0)

        labs_detectados = []
        concepto_tokens = set(concepto_norm.split())
        for firma in firmas:
            lab = firma["laboratorio"]
            lab_norm = firma["normalizado"]
            tokens = firma["tokens"]

            if lab_norm and lab_norm in concepto_norm:
                labs_detectados.append(lab)
                continue

            if tokens and any(token in concepto_tokens for token in tokens):
                labs_detectados.append(lab)

        laboratorios_detectados_total.update(labs_detectados)
        registros.append(
            {
                "concepto": concepto,
                "importe": round(importe, 2),
                "laboratorios_detectados": " | ".join(labs_detectados),
            }
        )

    detalle = pd.DataFrame(registros)
    laboratorios = sorted(laboratorios_detectados_total)

    return {"laboratorios": laboratorios, "detalle": detalle}


def _analisis_transfer_logistica(df_transfer, resultado_transfer):
    if df_transfer is None or df_transfer.empty or not resultado_transfer:
        return None

    detalle = df_transfer.copy()
    detalle["bruto"] = _serie_numerica(detalle, "bruto")
    detalle["neto"] = _serie_numerica(detalle, "neto")
    detalle["unidades"] = _serie_numerica(detalle, "unidades")
    if "albaran" in detalle.columns:
        detalle["albaran"] = detalle["albaran"].apply(normalizar_albaran)
    else:
        detalle["albaran"] = None

    detalle = detalle[detalle["bruto"] != 0].copy()
    if detalle.empty:
        return None

    abonos_transfer = resultado_transfer.get("abonos", pd.DataFrame()).copy()
    deteccion = _detectar_laboratorios_bonificados(detalle, abonos_transfer)
    laboratorios_bonificados = deteccion["laboratorios"]

    albaranes_bonificados = set()
    if laboratorios_bonificados and "laboratorio_maestro" in detalle.columns:
        albaranes_bonificados = set(
            detalle.loc[
                detalle["laboratorio_maestro"].astype(str).isin(laboratorios_bonificados),
                "albaran",
            ]
            .dropna()
            .astype(str)
            .tolist()
        )

    detalle["tiene_bonificacion_logistica"] = detalle["albaran"].astype(str).isin(albaranes_bonificados)
    detalle["cargo_transfer_base"] = 0.0
    detalle["cargo_transfer_iva"] = 0.0
    detalle["cargo_transfer_total"] = 0.0
    detalle["neto_con_cargo_transfer"] = detalle["neto"]
    detalle["abono_logistico_laboratorio"] = 0.0

    mask_elegible = (detalle["neto"] > 0) & (~detalle["tiene_bonificacion_logistica"])
    if mask_elegible.any():
        detalle.loc[mask_elegible, "cargo_transfer_base"] = (
            detalle.loc[mask_elegible, "bruto"].abs() * 0.017
        )
        detalle.loc[mask_elegible, "cargo_transfer_iva"] = (
            detalle.loc[mask_elegible, "cargo_transfer_base"] * 0.21
        )
        detalle.loc[mask_elegible, "cargo_transfer_total"] = (
            detalle.loc[mask_elegible, "cargo_transfer_base"]
            + detalle.loc[mask_elegible, "cargo_transfer_iva"]
        )
        detalle.loc[mask_elegible, "neto_con_cargo_transfer"] = (
            detalle.loc[mask_elegible, "neto"] + detalle.loc[mask_elegible, "cargo_transfer_total"]
        )

    bonificados = detalle["tiene_bonificacion_logistica"] & detalle["neto"].gt(0)
    if bonificados.any():
        detalle.loc[bonificados, "abono_logistico_laboratorio"] = (
            detalle.loc[bonificados, "bruto"].abs() * 0.017
        )
        detalle.loc[bonificados, "neto_con_cargo_transfer"] = detalle.loc[bonificados, "neto"]

    detalle["cargo_transfer_base"] = detalle["cargo_transfer_base"].round(4)
    detalle["cargo_transfer_iva"] = detalle["cargo_transfer_iva"].round(4)
    detalle["cargo_transfer_total"] = detalle["cargo_transfer_total"].round(4)
    detalle["neto_con_cargo_transfer"] = detalle["neto_con_cargo_transfer"].round(4)
    detalle["abono_logistico_laboratorio"] = detalle["abono_logistico_laboratorio"].round(4)

    base_elegible = float(detalle.loc[mask_elegible, "bruto"].abs().sum())
    base_teorica = float(detalle["bruto"].abs().sum())
    cargo_base_teorico = float(detalle["cargo_transfer_base"].sum())
    cargo_iva_teorico = float(detalle["cargo_transfer_iva"].sum())
    cargo_total_teorico = float(detalle["cargo_transfer_total"].sum())

    resumen_factura = resultado_transfer.get("resumen_logistica", {}) or {}
    base_factura = float(resumen_factura.get("base", 0) or 0)
    iva_factura = float(resumen_factura.get("iva", 0) or 0)
    total_factura = float(resumen_factura.get("total", 0) or 0)

    return {
        "detalle": detalle,
        "abonos_detectados": deteccion["detalle"],
        "resumen": {
            "laboratorios_bonificados": laboratorios_bonificados,
            "albaranes_bonificados": sorted(albaranes_bonificados),
            "base_teorica_total": round(base_teorica, 2),
            "base_elegible": round(base_elegible, 2),
            "cargo_base_teorico": round(cargo_base_teorico, 2),
            "cargo_iva_teorico": round(cargo_iva_teorico, 2),
            "cargo_total_teorico": round(cargo_total_teorico, 2),
            "base_factura": round(base_factura, 2),
            "iva_factura": round(iva_factura, 2),
            "total_factura": round(total_factura, 2),
            "diferencia_base": round(base_factura - cargo_base_teorico, 2),
            "diferencia_total": round(total_factura - cargo_total_teorico, 2),
            "lineas_elegibles": int(mask_elegible.sum()),
        },
    }


def _lineas_elegibles_goteo_puro(df):
    if df is None or df.empty:
        return pd.DataFrame()

    detalle = df.copy()
    detalle["bruto"] = _serie_numerica(detalle, "bruto")
    detalle["neto"] = _serie_numerica(detalle, "neto")
    detalle["iva"] = _serie_numerica(detalle, "iva")
    detalle["descripcion"] = detalle.get("descripcion", "").astype(str)
    descripcion_norm = detalle["descripcion"].str.lower()
    no_especialidad_cara = ~detalle.get(
        "es_especialidad_cara",
        pd.Series(False, index=detalle.index),
    ).fillna(False).astype(bool)

    mask = (
        detalle["tipo_compra"].eq("goteo")
        & detalle["seccion_albaran"].isin(["especialidad", "parafarmacia"])
        & no_especialidad_cara
        & ~detalle["neto"].lt(0)
        & ~descripcion_norm.str.contains("club", na=False)
        & ~descripcion_norm.str.contains("avantia", na=False)
        & ~descripcion_norm.str.contains("bitransfer|bittransfer", na=False)
    )

    return detalle[mask].copy()


def _analisis_ajuste_comercial_bidafarma(df, ajustes_comerciales, df_faceta=None):
    if df is None or df.empty or ajustes_comerciales is None or ajustes_comerciales.empty:
        return None

    if faceta.hay_cargo_tarifa(df_faceta):
        return None

    df_base = df.copy()
    df_base["bruto"] = _serie_numerica(df_base, "bruto")
    df_base["neto"] = _serie_numerica(df_base, "neto")
    df_base["iva"] = _serie_numerica(df_base, "iva")
    descripcion_norm = df_base.get("descripcion", "").astype(str).str.lower()
    no_especialidad_cara = ~df_base.get(
        "es_especialidad_cara",
        pd.Series(False, index=df_base.index),
    ).fillna(False).astype(bool)

    mask_elegible = (
        df_base["tipo_compra"].eq("goteo")
        & df_base["iva"].eq(4)
        & df_base["seccion_albaran"].eq("especialidad")
        & no_especialidad_cara
        & (df_base["bruto"].abs() <= 96)
        & df_base["bruto"].ne(0)
        & ~descripcion_norm.str.contains("club", na=False)
        & ~descripcion_norm.str.contains("avantia", na=False)
        & ~descripcion_norm.str.contains("bitransfer|bittransfer", na=False)
    )

    elegibles = df_base[mask_elegible].copy()
    if elegibles.empty:
        return None

    base_compras = float(elegibles.loc[elegibles["bruto"] > 0, "bruto"].sum())
    base_abonos = float(elegibles.loc[elegibles["bruto"] < 0, "bruto"].sum())
    base_aplicacion = base_compras + base_abonos
    if base_aplicacion <= 0:
        return None

    descuento_total = abs(float(ajustes_comerciales["importe"].sum()))
    if descuento_total <= 0:
        return None

    descuento_pct = (descuento_total / base_aplicacion) * 100
    detalle = elegibles[elegibles["bruto"] > 0].copy()
    if detalle.empty:
        return None

    detalle["descuento_ajuste_comercial"] = detalle["bruto"] * (descuento_pct / 100)
    detalle["neto_con_ajuste_comercial"] = (
        detalle["neto"] - detalle["descuento_ajuste_comercial"]
    )
    detalle["descuento_ajuste_comercial"] = detalle["descuento_ajuste_comercial"].round(4)
    detalle["neto_con_ajuste_comercial"] = detalle["neto_con_ajuste_comercial"].round(4)

    return {
        "detalle": detalle,
        "resumen": {
            "descuento_total": round(descuento_total, 2),
            "base_aplicacion": round(base_aplicacion, 2),
            "base_compras": round(base_compras, 2),
            "base_abonos": round(base_abonos, 2),
            "descuento_pct": round(descuento_pct, 2),
            "lineas_afectadas": len(detalle),
        },
    }


def _analisis_cargo_adicional_gestion(df, importe_cargo):
    if df is None or df.empty or importe_cargo is None or abs(float(importe_cargo)) <= 0.05:
        return None

    detalle = _lineas_elegibles_goteo_puro(df)
    if detalle.empty:
        return None

    base_aplicacion = float(detalle["bruto"].abs().sum())
    if base_aplicacion <= 0:
        return None

    cargo_total = abs(float(importe_cargo))
    detalle["cargo_gestion_adicional"] = (
        detalle["bruto"].abs() / base_aplicacion
    ) * cargo_total
    detalle["neto_con_gestion_adicional"] = (
        detalle["neto"] + detalle["cargo_gestion_adicional"]
    )
    detalle["cargo_gestion_adicional"] = detalle["cargo_gestion_adicional"].round(4)
    detalle["neto_con_gestion_adicional"] = detalle["neto_con_gestion_adicional"].round(4)

    return {
        "detalle": detalle,
        "resumen": {
            "cargo_total": round(cargo_total, 2),
            "base_cargo": round(cargo_total * 0.076, 2),
            "base_aplicacion": round(base_aplicacion, 2),
            "lineas_afectadas": len(detalle),
        },
    }


def _detectar_penalizacion_bajo_consumo(condicion, diferencia_gestion):
    if not condicion or abs(float(diferencia_gestion or 0)) <= 0.05:
        return None

    reglas_gestion = condicion.get("gestion", {})
    if not reglas_gestion.get("penalizacion_bajo_consumo"):
        return None

    importe_penalizacion = float(reglas_gestion.get("importe_penalizacion_bajo_consumo", 0.0) or 0.0)
    if abs(float(diferencia_gestion) - importe_penalizacion) > 0.05:
        return None

    return {
        "importe": round(importe_penalizacion, 2),
        "umbral_consumo": reglas_gestion.get("umbral_consumo"),
    }


def _resumen_bidafarma(
    df,
    analisis_faceta=None,
    resumen_bitransfer=None,
    analisis_avantia=None,
    analisis_ajuste=None,
    analisis_cargo_adicional=None,
    analisis_transfer=None,
):
    if df is None or df.empty:
        return None

    df_resumen = df.copy()
    if "tipo" in df_resumen.columns:
        mask_faceta = df_resumen.apply(
            lambda row: faceta.es_linea_faceta(row.get("tipo"), row.get("descripcion")),
            axis=1,
        )
        df_resumen = df_resumen[~mask_faceta].copy()

    df_resumen["bruto"] = _serie_numerica(df_resumen, "bruto")
    df_resumen["neto"] = _serie_numerica(df_resumen, "neto")
    df_resumen["unidades"] = _serie_numerica(df_resumen, "unidades")

    lineas_resumen = df_resumen.copy()
    if lineas_resumen.empty:
        return None

    descripcion_norm = lineas_resumen.get("descripcion", "").astype(str).str.lower().str.strip()
    seccion_norm = lineas_resumen.get("seccion_albaran", "").astype(str).str.lower().str.strip()
    tipo_compra_norm = lineas_resumen.get("tipo_compra", "").astype(str).str.lower().str.strip()

    mask_bitransfer = seccion_norm.eq("bitransfer")
    mask_club = seccion_norm.eq("club")
    mask_avantia = seccion_norm.eq("avantia") | descripcion_norm.str.contains("avantia", na=False)
    mask_especialidad_cara = lineas_resumen.get(
        "es_especialidad_cara",
        pd.Series(False, index=lineas_resumen.index),
    ).fillna(False).astype(bool)
    mask_goteo_puro = (
        tipo_compra_norm.eq("goteo")
        & seccion_norm.isin(["especialidad", "parafarmacia"])
        & ~mask_bitransfer
        & ~mask_club
        & ~mask_avantia
        & ~mask_especialidad_cara
    )
    mask_especialidad_normal = (
        mask_goteo_puro
        & seccion_norm.eq("especialidad")
        & lineas_resumen["bruto"].abs().le(96)
    )
    mask_transfer = tipo_compra_norm.eq("transfer")

    resumen_bloques = []

    def _sumar_columna_real(bloque, columna):
        serie = _serie_numerica(bloque, columna)
        positivos = float(serie[serie > 0].sum())
        negativos = float(serie[serie < 0].sum())
        return positivos + negativos

    def agregar_bloque(nombre, mask, coste_extra=0.0):
        bloque = lineas_resumen[mask].copy()
        if bloque.empty:
            return None

        bruto = _sumar_columna_real(bloque, "bruto")
        neto = _sumar_columna_real(bloque, "neto")
        unidades = _sumar_columna_real(bloque, "unidades")
        coste_real = neto + coste_extra
        descuento = _descuento_pct(bruto, coste_real)
        resumen_bloques.append({
            "bloque": nombre,
            "lineas": len(bloque),
            "unidades": round(unidades, 2),
            "bruto_compra": round(bruto, 2),
            "neto_inicial": round(neto, 2),
            "coste_ajustado": round(coste_real, 2),
            "descuento_medio_pct": descuento,
            "descuento_total_euros": None,
            "descuento_medio_euros": None,
        })
        return {"bruto": bruto, "neto": neto, "coste": coste_real, "descuento": descuento}

    def agregar_bloque_especialidad_cara():
        bloque = lineas_resumen[mask_especialidad_cara].copy()
        if bloque.empty:
            return None

        bruto = _sumar_columna_real(bloque, "bruto")
        neto = _sumar_columna_real(bloque, "neto")
        unidades = _sumar_columna_real(bloque, "unidades")
        descuento_total = _sumar_columna_real(bloque, "descuento_especialidad_cara_euros")
        descuento_medio = descuento_total / unidades if unidades else 0.0
        resumen_bloques.append({
            "bloque": "Especialidad cara",
            "lineas": len(bloque),
            "unidades": round(unidades, 2),
            "bruto_compra": round(bruto, 2),
            "neto_inicial": round(neto, 2),
            "coste_ajustado": round(neto, 2),
            "descuento_medio_pct": None,
            "descuento_total_euros": round(descuento_total, 2),
            "descuento_medio_euros": round(descuento_medio, 2),
        })
        return {
            "bruto": bruto,
            "neto": neto,
            "coste": neto,
            "descuento_medio_euros": descuento_medio,
        }

    bloque_goteo_puro = agregar_bloque(
        "Goteo puro",
        mask_goteo_puro,
        coste_extra=(
            (0.0 if not analisis_faceta else analisis_faceta["resumen"]["margen_tramo_fijo_total"])
            + (0.0 if not analisis_cargo_adicional else analisis_cargo_adicional["resumen"]["cargo_total"])
            - (0.0 if not analisis_ajuste else analisis_ajuste["resumen"]["descuento_total"])
        ),
    )
    bloque_especialidad = agregar_bloque(
        "Especialidad normal",
        mask_especialidad_normal,
        coste_extra=(
            (0.0 if not analisis_faceta else float(
                analisis_faceta["detalle_tramo_fijo"]
                .loc[analisis_faceta["detalle_tramo_fijo"]["seccion_albaran"] == "especialidad", "cargo_faceta_tramo_fijo"]
                .sum()
            ))
            + (0.0 if not analisis_cargo_adicional else float(
                analisis_cargo_adicional["detalle"]
                .loc[analisis_cargo_adicional["detalle"]["seccion_albaran"] == "especialidad", "cargo_gestion_adicional"]
                .sum()
            ))
            - (0.0 if not analisis_ajuste else analisis_ajuste["resumen"]["descuento_total"])
        ),
    )
    bloque_especialidad_cara = agregar_bloque_especialidad_cara()
    bloque_parafarmacia = agregar_bloque(
        "Parafarmacia normal",
        mask_goteo_puro & seccion_norm.eq("parafarmacia"),
        coste_extra=(
            (0.0 if not analisis_faceta else float(
                analisis_faceta["detalle_tramo_fijo"]
                .loc[analisis_faceta["detalle_tramo_fijo"]["seccion_albaran"] == "parafarmacia", "cargo_faceta_tramo_fijo"]
                .sum()
            ))
            + (0.0 if not analisis_cargo_adicional else float(
                analisis_cargo_adicional["detalle"]
                .loc[analisis_cargo_adicional["detalle"]["seccion_albaran"] == "parafarmacia", "cargo_gestion_adicional"]
                .sum()
            ))
        ),
    )
    bloque_bitransfer = agregar_bloque(
        "Bitransfer",
        mask_bitransfer,
        coste_extra=0.0 if not resumen_bitransfer else (
            resumen_bitransfer["coste_real_total_compras"] - resumen_bitransfer["importe_neto_compras"]
        ),
    )
    bloque_transfer = agregar_bloque(
        "Transfer",
        mask_transfer,
        coste_extra=0.0 if not analisis_transfer else float(
            analisis_transfer["detalle"]["cargo_transfer_total"].sum()
        ),
    )
    bloque_club = agregar_bloque(
        "Clubes",
        mask_club,
        coste_extra=0.0 if not analisis_faceta or analisis_faceta["detalle_liquidaciones"].empty else float(
            analisis_faceta["detalle_liquidaciones"]["liquidacion_faceta_linea"].sum()
        ),
    )
    bloque_avantia = agregar_bloque(
        "Avantia",
        mask_avantia,
        coste_extra=0.0 if not analisis_avantia else float(analisis_avantia["resumen"]["coste_total_avantia"] - analisis_avantia["resumen"]["cuota_avantia"] - lineas_resumen[mask_avantia]["neto"].sum()),
    )

    total_bidafarma_bruto = float(lineas_resumen["bruto"].sum())

    resumen_textual = []
    if bloque_goteo_puro:
        descuento_inicial_goteo = _descuento_pct(bloque_goteo_puro["bruto"], bloque_goteo_puro["neto"])
        if analisis_faceta and analisis_faceta["resumen"]["margen_tramo_fijo_total"] > 0:
            resumen_textual.append(
                f"Hay un cargo de tramo fijo de {analisis_faceta['resumen']['margen_tramo_fijo_total']:.2f} € "
                f"que reduce el descuento medio del goteo puro desde {descuento_inicial_goteo:.2f}% "
                f"hasta {bloque_goteo_puro['descuento']:.2f}%."
            )
        if analisis_ajuste:
            resumen_textual.append(
                f"Se ha aplicado un ajuste comercial de {analisis_ajuste['resumen']['descuento_total']:.2f} € "
                f"sobre una base elegible de {analisis_ajuste['resumen']['base_aplicacion']:.2f} €, "
                f"equivalente a un {analisis_ajuste['resumen']['descuento_pct']:.2f}%."
            )
        if analisis_cargo_adicional:
            resumen_textual.append(
                f"Hay un gasto adicional de gestión de {analisis_cargo_adicional['resumen']['cargo_total']:.2f} € "
                f"repartido sobre una base elegible de {analisis_cargo_adicional['resumen']['base_aplicacion']:.2f} €."
            )

    if analisis_faceta and not analisis_faceta["resumen_liquidaciones"].empty:
        for _, fila in analisis_faceta["resumen_liquidaciones"].iterrows():
            resumen_textual.append(
                f"Se ha detectado {fila['concepto']} por {fila['importe_liquidacion']:.2f} €, "
                f"equivalente a un {fila['pct_liquidacion']:.2f}% sobre una base de {fila['base_liquidacion']:.2f} €."
            )

    return {
        "tabla": pd.DataFrame(resumen_bloques),
        "resumen_textual": resumen_textual,
        "metricas": {
            "total_bidafarma_bruto": round(total_bidafarma_bruto, 2),
            "goteo_puro_descuento_real": None if not bloque_goteo_puro else bloque_goteo_puro["descuento"],
            "bitransfer_descuento_real": None if not bloque_bitransfer else bloque_bitransfer["descuento"],
            "transfer_descuento_real": None if not bloque_transfer else bloque_transfer["descuento"],
            "club_descuento_real": None if not bloque_club else bloque_club["descuento"],
            "avantia_descuento_real": None if not bloque_avantia else bloque_avantia["descuento"],
        },
    }


def _render_subida_albaranes_base(nombre_proveedor, proveedor_id):
    st.header("1️⃣ Subida de albaranes")

    col1, col2 = st.columns(2)

    with col1:
        uploaded_files = st.file_uploader(
            f"📦 Albaranes {nombre_proveedor} (goteo)",
            type=["xlsx"],
            accept_multiple_files=True,
            key=f"{proveedor_id}_albaranes_goteo",
        )

    with col2:
        uploaded_transfer = st.file_uploader(
            f"🚚 Albaranes {nombre_proveedor} TRANSFER",
            type=["xlsx"],
            accept_multiple_files=True,
            key=f"{proveedor_id}_albaranes_transfer",
        )

    dfs = []
    dfs.extend(_leer_albaranes_genericos(uploaded_files, proveedor_id, "goteo"))
    dfs.extend(_leer_albaranes_genericos(uploaded_transfer, proveedor_id, "transfer"))

    df = pd.concat(dfs, ignore_index=True) if dfs else None
    _guardar_dataset(f"df_{proveedor_id}", df)
    _mostrar_vistas_albaranes(df)

    return df


def render_proveedor_base(nombre_proveedor, proveedor_id):
    df = _render_subida_albaranes_base(nombre_proveedor, proveedor_id)

    st.header("2️⃣ Facturas")

    factura_normal = st.file_uploader(
        "Factura NORMAL",
        type=["xlsx"],
        key=f"{proveedor_id}_factura_normal",
    )
    factura_transfer = st.file_uploader(
        "Factura TRANSFER",
        type=["xlsx"],
        key=f"{proveedor_id}_factura_transfer",
    )

    st.session_state[f"factura_normal_{proveedor_id}"] = "cargada" if factura_normal else None
    st.session_state[f"factura_transfer_{proveedor_id}"] = "cargada" if factura_transfer else None

    if factura_normal:
        st.success(f"Factura NORMAL de {nombre_proveedor} cargada.")
    if factura_transfer:
        st.success(f"Factura TRANSFER de {nombre_proveedor} cargada.")

    if df is None:
        st.warning("Sube archivos")
        return

    st.header("3️⃣ Análisis")
    if st.button("Generar análisis distribuidora", key=f"generar_analisis_{proveedor_id}"):
        analisis = reporting.generar_analisis_distribuidora(
            df,
            proveedor=proveedor_id,
        )
        _guardar_analisis_distribuidora(proveedor_id, analisis)

    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get(proveedor_id)
    if analisis_guardado:
        _mostrar_analisis_distribuidora(analisis_guardado)


def render_facturas_laboratorios():
    st.header("Facturas de laboratorios")
    archivos = st.file_uploader(
        "Sube facturas de laboratorios",
        type=["xlsx"],
        accept_multiple_files=True,
        key="facturas_laboratorios_excel",
    )
    st.session_state["facturas_laboratorios"] = ["cargado"] * len(archivos) if archivos else []

    if archivos:
        st.success(f"{len(archivos)} archivo(s) de laboratorios cargado(s).")
        if MODO_DEBUG:
            st.dataframe(pd.DataFrame({"archivo": [archivo.name for archivo in archivos]}))
    else:
        st.info("Sube aquí los Excel de facturas de laboratorios. Más adelante añadiremos su lectura específica.")


    if st.button("Generar análisis laboratorios", key="generar_analisis_laboratorios"):
        st.session_state["analisis_laboratorios"] = {
            "ok": False,
            "mensaje": "Pendiente de implementación: análisis de facturas de laboratorio.",
        }

    analisis_laboratorios = st.session_state.get("analisis_laboratorios")
    if analisis_laboratorios:
        st.info(analisis_laboratorios["mensaje"])


def render_ventas_farmacia():
    st.header("Ventas farmacia")
    archivos = st.file_uploader(
        "Sube ventas de la farmacia",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key="ventas_farmacia_excel",
    )
    st.session_state["ventas_farmacia"] = ["cargado"] * len(archivos) if archivos else []

    if archivos:
        ventas_normalizadas = []
        for archivo in archivos:
            try:
                ventas_normalizadas.append(ventas.normalizar_ventas_erp(ventas.leer_tabla(archivo)))
            except ValueError as error:
                st.error(f"No se pudo leer un archivo de ventas: {error}")

        if ventas_normalizadas:
            df_ventas = pd.concat(ventas_normalizadas, ignore_index=True)
            _guardar_dataset("ventas_farmacia_df", df_ventas)

            dataframes_compras = [
                st.session_state.get("df_bidafarma"),
                st.session_state.get("df_cofares"),
                st.session_state.get("df_alliance"),
                st.session_state.get("df_hefame"),
            ]
            costes_reales = ventas.coste_medio_real_por_cn(dataframes_compras)
            analisis, discordancia, no_fiable = ventas.analizar_margen_real(df_ventas, costes_reales)
            _guardar_dataset("analisis_ventas_margen_real_df", analisis)

            st.success(f"{len(archivos)} archivo(s) de ventas cargado(s).")

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("CN ventas", int(df_ventas["cn"].nunique()))
            v2.metric("Unidades vendidas", f"{df_ventas['unidades_vendidas'].sum():.0f}")
            v3.metric("Venta neta", f"{df_ventas['venta_neta'].sum():.2f} €")
            v4.metric("CN con coste real", int(analisis["tiene_compras_reales"].sum()) if not analisis.empty else 0)

            columnas = [
                "cn",
                "descripcion",
                "unidades_vendidas",
                "unidades_compradas_periodo",
                "unidades_sin_compra_periodo",
                "venta_neta",
                "coste_medio_real",
                "coste_real_total_vendido",
                "margen_real_pct",
                "margen_erp",
                "diferencia_margen_erp_vs_real",
                "coste_erp_no_fiable",
                "motivo_coste_erp_no_fiable",
            ]

            st.subheader("Margen real por CN")
            st.dataframe(analisis[[col for col in columnas if col in analisis.columns]])

            st.subheader("Ranking de discordancia válida ERP vs margen real")
            if not discordancia.empty:
                st.dataframe(discordancia[[col for col in columnas if col in discordancia.columns]].head(50))
            else:
                st.info("No hay discordancias válidas: faltan costes ERP fiables, márgenes ERP válidos o compras reales.")

            st.subheader("Artículos con coste ERP no fiable")
            if not no_fiable.empty:
                st.dataframe(no_fiable[[col for col in columnas if col in no_fiable.columns]])
            else:
                st.success("No se han detectado costes/márgenes ERP no fiables.")
    else:
        st.info(
            "Sube aquí las ventas del ERP. El margen real se calculará con el coste medio real de compras, "
            "y el margen ERP se usará solo como comparación."
        )

    if st.button("Generar análisis ventas", key="generar_analisis_ventas"):
        st.session_state["analisis_ventas"] = {
            "ok": False,
            "mensaje": "Pendiente de implementación: cruce compras vs ventas.",
        }

    analisis_ventas = st.session_state.get("analisis_ventas")
    if analisis_ventas:
        st.info(analisis_ventas["mensaje"])


def _normalizar_stock_farmacia(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["cn", "descripcion", "unidades_stock", "caducidad", "ultima_compra"])

    trabajo = df.copy()
    trabajo.columns = [str(c).strip().lower() for c in trabajo.columns]

    def buscar_columna(*patrones):
        for columna in trabajo.columns:
            nombre = str(columna).strip().lower()
            if all(patron in nombre for patron in patrones):
                return columna
        return None

    col_cn = buscar_columna("codigo", "nacional") or buscar_columna("código", "nacional") or buscar_columna("cn")
    col_descripcion = buscar_columna("descripcion") or buscar_columna("descripción") or buscar_columna("producto")
    col_unidades = buscar_columna("unidad") or buscar_columna("stock") or buscar_columna("existencia")
    col_caducidad = buscar_columna("caduc")
    col_ultima_compra = buscar_columna("ultima", "compra") or buscar_columna("última", "compra") or buscar_columna("fecha", "compra")

    resultado = pd.DataFrame()
    resultado["cn"] = trabajo[col_cn] if col_cn else None
    resultado["descripcion"] = trabajo[col_descripcion] if col_descripcion else None
    resultado["unidades_stock"] = pd.to_numeric(trabajo[col_unidades], errors="coerce") if col_unidades else None
    resultado["caducidad"] = pd.to_datetime(trabajo[col_caducidad], errors="coerce") if col_caducidad else pd.NaT
    resultado["ultima_compra"] = pd.to_datetime(trabajo[col_ultima_compra], errors="coerce") if col_ultima_compra else pd.NaT

    if "cn" in resultado.columns:
        resultado["cn"] = (
            resultado["cn"]
            .astype(str)
            .str.strip()
            .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        )

    if "descripcion" in resultado.columns:
        resultado["descripcion"] = resultado["descripcion"].astype(str).str.strip()

    resultado = resultado.dropna(how="all")
    resultado = _enriquecer_con_maestro(resultado)
    return resultado.reset_index(drop=True)



def render_stock():
    st.header("Stock")
    archivo = st.file_uploader(
        "Sube el stock de la farmacia",
        type=["xlsx", "xls", "csv"],
        key="stock_farmacia_excel",
    )

    df_stock = None
    if archivo:
        try:
            df_stock = _normalizar_stock_farmacia(load_excel(archivo))
            _guardar_dataset("stock_farmacia_df", df_stock)
            st.session_state["stock_farmacia"] = "cargado"
        except ValueError as error:
            st.error(f"No se pudo leer el stock de la farmacia: {error}")
            return

    if df_stock is None:
        df_stock = st.session_state.get("stock_farmacia_df")

    if df_stock is not None and not df_stock.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Referencias", int(df_stock["cn"].dropna().nunique()) if "cn" in df_stock.columns else len(df_stock))
        c2.metric("Unidades stock", int(df_stock["unidades_stock"].fillna(0).sum()) if "unidades_stock" in df_stock.columns else 0)
        c3.metric("Con caducidad", int(df_stock["caducidad"].notna().sum()) if "caducidad" in df_stock.columns else 0)
        c4.metric("Con última compra", int(df_stock["ultima_compra"].notna().sum()) if "ultima_compra" in df_stock.columns else 0)

        columnas = [
            col for col in [
                "cn",
                "descripcion",
                "laboratorio_maestro",
                "tipo_producto",
                "unidades_stock",
                "caducidad",
                "ultima_compra",
            ]
            if col in df_stock.columns
        ]
        st.dataframe(df_stock[columnas])
        st.info(
            "Esta subida queda preparada para el siguiente paso: análisis ABCD, artículos con menor rotación, "
            "stock parado y productos próximos a caducar."
        )
    else:
        st.info(
            "Sube aquí el Excel de stock con código nacional, descripción, unidades, caducidad y última compra."
        )


    if st.button("Generar análisis stock", key="generar_analisis_stock"):
        st.session_state["analisis_stock"] = {
            "ok": False,
            "mensaje": "Pendiente de implementación: análisis de stock.",
        }

    analisis_stock = st.session_state.get("analisis_stock")
    if analisis_stock:
        st.info(analisis_stock["mensaje"])


def render_resumen():
    st.header("Resumen")

    filas = []
    for nombre, proveedor_id in {"bidafarma": "bidafarma", **PROVEEDORES_BASE}.items():
        df_proveedor = st.session_state.get(f"df_{proveedor_id}")
        filas.append({
            "seccion": nombre,
            "lineas_albaranes": 0 if df_proveedor is None else len(df_proveedor),
            "factura_normal": "cargada" if st.session_state.get(f"factura_normal_{proveedor_id}") else "",
            "factura_transfer": "cargada" if st.session_state.get(f"factura_transfer_{proveedor_id}") else "",
        })

    filas.append({
        "seccion": "Facturas laboratorios",
        "lineas_albaranes": len(st.session_state.get("facturas_laboratorios", [])),
        "factura_normal": "",
        "factura_transfer": "",
    })
    filas.append({
        "seccion": "Ventas farmacia",
        "lineas_albaranes": len(st.session_state.get("ventas_farmacia", [])),
        "factura_normal": "",
        "factura_transfer": "",
    })
    stock_df = st.session_state.get("stock_farmacia_df")
    filas.append({
        "seccion": "Stock",
        "lineas_albaranes": 0 if stock_df is None else len(stock_df),
        "factura_normal": "cargado" if st.session_state.get("stock_farmacia") else "",
        "factura_transfer": "",
    })

    st.dataframe(pd.DataFrame(filas))
    st.info("Este resumen queda preparado como punto de salida. En los siguientes pasos añadiremos los indicadores y la descarga Excel final.")


    st.header("Resumen final de auditoría")
    if st.button("Generar resumen final", key="generar_resumen_final_auditoria"):
        analisis_distribuidora = st.session_state.get("analisis_distribuidora", {})
        resumen_final = reporting.generar_resumen_final(
            analisis_distribuidoras=list(analisis_distribuidora.values()) if isinstance(analisis_distribuidora, dict) else analisis_distribuidora,
            analisis_laboratorios=st.session_state.get("analisis_laboratorios"),
            analisis_ventas=st.session_state.get("analisis_ventas"),
            analisis_stock=st.session_state.get("analisis_stock"),
        )
        st.session_state["resumen_final_auditoria"] = resumen_final

    resumen_final = st.session_state.get("resumen_final_auditoria")
    if resumen_final and resumen_final.get("ok"):
        distribuidoras = resumen_final.get("distribuidoras", pd.DataFrame())
        if not distribuidoras.empty:
            st.caption("Consolidado temporal de distribuidoras")
            st.dataframe(distribuidoras)
    else:
        st.info("Genera primero los análisis individuales.")


def render_vida_pharma():
    df = None
    faceta_frames = []
    analisis_faceta = None
    analisis_avantia = None
    analisis_ajuste = None
    analisis_cargo_adicional = None
    resumen_conciliacion_bitransfer = None
    condicion_detectada = None
    resultado_factura_normal = None
    resultado_factura_transfer = None

    # =========================
    # 1. ALBARANES
    # =========================

    st.header("1️⃣ Subida de albaranes")

    col1, col2 = st.columns(2)

    with col1:
        uploaded_files = st.file_uploader(
            "📦 Albaranes BIDAFARMA (goteo)",
            type=["xlsx"],
            accept_multiple_files=True,
            key="bidafarma_albaranes_goteo"
        )

    with col2:
        uploaded_transfer = st.file_uploader(
            "🚚 Albaranes TRANSFER",
            type=["xlsx"],
            accept_multiple_files=True,
            key="transfer"
        )

    dfs = []

    # GOTE0
    if uploaded_files:
        for uploaded_file in uploaded_files:
            df_faceta_temp = faceta.leer_albaran_faceta_v(uploaded_file)
            if df_faceta_temp is not None:
                faceta_frames.append(df_faceta_temp)
                continue

            if hasattr(uploaded_file, "seek"):
                uploaded_file.seek(0)

            df_temp = normalize_columns(load_excel(uploaded_file))
            df_temp.columns = [c.lower().strip() for c in df_temp.columns]

            df_temp["proveedor"] = "bidafarma"
            df_temp["tipo_compra"] = "goteo"

            col_albaran = next((c for c in df_temp.columns if "albaran" in c), None)

            if col_albaran:
                df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

            df_temp = parse_sections(df_temp)
            df_temp = _enriquecer_con_maestro(df_temp)
            df_temp = clasificar_especialidad_cara(df_temp)
            dfs.append(df_temp)

    # TRANSFER
    if uploaded_transfer:
        for uploaded_file in uploaded_transfer:
            df_temp = normalize_columns(load_excel(uploaded_file))
            df_temp.columns = [c.lower().strip() for c in df_temp.columns]

            df_temp["proveedor"] = "bidafarma"
            df_temp["tipo_compra"] = "transfer"

            col_albaran = next((c for c in df_temp.columns if "albaran" in c), None)

            if col_albaran:
                df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

            df_temp = parse_sections(df_temp)
            df_temp = _enriquecer_con_maestro(df_temp)
            df_temp = clasificar_especialidad_cara(df_temp)
            dfs.append(df_temp)

    if dfs:
        df = pd.concat(dfs, ignore_index=True)

    _guardar_dataset("df_bidafarma", df)
    df_faceta_bidafarma = pd.concat(faceta_frames, ignore_index=True) if faceta_frames else pd.DataFrame()
    if df is not None:
        df_faceta_lineas = faceta.extraer_faceta_desde_lineas(df)
        if not df_faceta_lineas.empty:
            df_faceta_bidafarma = pd.concat([df_faceta_bidafarma, df_faceta_lineas], ignore_index=True)
            df_faceta_bidafarma = df_faceta_bidafarma.drop_duplicates(subset=["concepto", "importe"], keep="last")
    _guardar_dataset("df_faceta_bidafarma", df_faceta_bidafarma)
    condicion_detectada = condiciones_bidafarma.detectar_condicion(df, df_faceta_bidafarma)

    # =========================
    # VISTAS
    # =========================

    if df is not None:
        _mostrar_vistas_albaranes(df)

    if condicion_detectada:
        st.subheader("🧭 Condición detectada")
        if MODO_DEBUG:
            c1, c2 = st.columns(2)
            c1.metric("Nombre", condicion_detectada["nombre"])
            c2.metric("Acrónimo", condicion_detectada["acronimo"])
        else:
            st.info("Condición detectada.")

    if not df_faceta_bidafarma.empty:
        analisis_faceta = faceta.analizar_faceta_v(df, df_faceta_bidafarma) if df is not None else None

        if analisis_faceta:
            resumen_faceta = analisis_faceta["resumen"]
            hay_cargo_tarifa = abs(resumen_faceta["margen_tramo_fijo_total"]) > 0.0001
            titulo_tarifa = "Albarán TP 74"
            if condicion_detectada and MODO_DEBUG:
                if hay_cargo_tarifa:
                    titulo_tarifa = f"Tarifa {condicion_detectada['acronimo']} · {condicion_detectada['nombre']}"
                else:
                    titulo_tarifa = f"Liquidaciones · {condicion_detectada['nombre']} ({condicion_detectada['acronimo']})"
            st.header(f"🧾 {titulo_tarifa}")

            if hay_cargo_tarifa:
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("Cargo tarifa", f"{resumen_faceta['margen_tramo_fijo_total']:.2f} €")
                f2.metric("Base tramo fijo", f"{resumen_faceta['base_tramo_fijo']:.2f} €")
                f3.metric("Base de aplicación", f"{resumen_faceta['base_aplicacion']:.2f} €")
                f4.metric("Liquidaciones", f"{resumen_faceta['liquidaciones_total']:.2f} €")
                st.caption("Conceptos detectados en albaranes TP 74")
                _mostrar_dataframe_debug(
                    analisis_faceta["conceptos"][
                        [col for col in ["fecha", "hora", "tp", "concepto", "importe"] if col in analisis_faceta["conceptos"].columns]
                    ],
                    "Conceptos completos del albarán TP 74 ocultos por privacidad.",
                )

            if hay_cargo_tarifa and not analisis_faceta["detalle_tramo_fijo"].empty:
                st.caption("Imputación margen tramo fijo sobre goteo elegible")
                st.dataframe(
                    analisis_faceta["detalle_tramo_fijo"][
                        [
                            col for col in [
                                "cn",
                                "descripcion",
                                "seccion_albaran",
                                "unidades",
                                "bruto",
                                "neto",
                                "cargo_faceta_tramo_fijo",
                                "neto_con_faceta_tramo_fijo",
                            ]
                            if col in analisis_faceta["detalle_tramo_fijo"].columns
                        ]
                    ]
                )

            if not analisis_faceta["resumen_liquidaciones"].empty:
                st.caption("Resumen de liquidaciones detectadas")
                st.dataframe(analisis_faceta["resumen_liquidaciones"])

            if not analisis_faceta["detalle_liquidaciones"].empty:
                st.caption("Imputación de liquidaciones por club/laboratorio")
                st.dataframe(
                    analisis_faceta["detalle_liquidaciones"][
                        [
                            col for col in [
                                "grupo_liquidacion",
                                "cn",
                                "descripcion",
                                "unidades",
                                "bruto",
                                "neto",
                                "pct_liquidacion",
                                "liquidacion_faceta_linea",
                                "neto_con_liquidacion",
                            ]
                            if col in analisis_faceta["detalle_liquidaciones"].columns
                        ]
                    ]
                )
        else:
            _mostrar_dataframe_debug(
                df_faceta_bidafarma,
                "Albaranes TP 74 completos ocultos por privacidad.",
            )
            st.info("Se ha detectado un albarán TP 74, pero todavía no hay líneas de compra goteo sobre las que imputar cargos o liquidaciones.")

    # =========================
    # 2. FACTURAS
    # =========================

    if df is not None:

        st.header("2️⃣ Facturas")

        # -------------------------
        # FACTURA NORMAL
        # -------------------------
        factura_normal = st.file_uploader("Factura NORMAL", type=["xlsx"], key="bidafarma_factura_normal")
        st.session_state["factura_normal_bidafarma"] = "cargada" if factura_normal else None

        resultado = None

        if factura_normal:

            resultado = analizar_factura_bidafarma(factura_normal)
            resultado_factura_normal = resultado

            df_goteo = df[df["tipo_compra"] == "goteo"]

            albaranes_factura = set(resultado["albaranes"])
            albaranes_df = set(df_goteo["albaran"].apply(normalizar_albaran))

            faltan = albaranes_df - albaranes_factura
            sobran = albaranes_factura - albaranes_df

            if not faltan and not sobran:
                st.success("✅ Albaranes NORMAL conciliados")
            else:
                if faltan:
                    if MODO_DEBUG:
                        st.error(f"Faltan: {faltan}")
                    else:
                        st.error(f"Faltan {len(faltan)} albaranes NORMAL por conciliar.")
                if sobran:
                    if MODO_DEBUG:
                        st.warning(f"Sobran: {sobran}")
                    else:
                        st.warning(f"Sobran {len(sobran)} albaranes NORMAL en factura.")

            st.subheader("💸 Gastos factura normal")
            st.dataframe(resultado["gastos"])

            ajustes_comerciales = resultado.get("ajustes_comerciales", pd.DataFrame())
            permite_ajuste = condicion_detectada is None or condicion_detectada["ajuste_comercial_factura"]
            analisis_ajuste = (
                _analisis_ajuste_comercial_bidafarma(df, ajustes_comerciales, df_faceta_bidafarma)
                if permite_ajuste else None
            )

            if analisis_ajuste:
                resumen_ajuste = analisis_ajuste["resumen"]
                st.subheader("📉 Ajuste comercial en factura")

                ac1, ac2, ac3, ac4 = st.columns(4)
                ac1.metric("Descuento factura", f"{resumen_ajuste['descuento_total']:.2f} €")
                ac2.metric("Base aplicación", f"{resumen_ajuste['base_aplicacion']:.2f} €")
                ac3.metric("Descuento %", f"{resumen_ajuste['descuento_pct']:.2f}%")
                ac4.metric("Líneas afectadas", resumen_ajuste["lineas_afectadas"])

                st.caption("Imputación del ajuste comercial sobre especialidad IVA 4 elegible")
                st.dataframe(
                    analisis_ajuste["detalle"][
                        [
                            col for col in [
                                "cn",
                                "descripcion",
                                "bruto",
                                "neto",
                                "descuento_ajuste_comercial",
                                "neto_con_ajuste_comercial",
                            ]
                            if col in analisis_ajuste["detalle"].columns
                        ]
                    ]
                )

            analisis_servicios = servicios.analizar_gastos_servicios(df, resultado["gastos"], condicion_detectada)

            if analisis_servicios and analisis_servicios["resumen"]["servicios_factura"] > 0:
                st.subheader("🧾 Imputación gastos por servicios")

                resumen_servicios = analisis_servicios["resumen"]

                s1, s2, s3, s4, s5, s6 = st.columns(6)
                s1.metric("Avantia", "Sí" if resumen_servicios["tiene_avantia"] else "No")
                s2.metric("Cargo bidanatural", f"{resumen_servicios['cargo_pct_vida_natural']:.1f}%")
                s3.metric("Servicios factura", f"{resumen_servicios['servicios_factura']:.2f} €")
                s4.metric("bidanatural", f"{resumen_servicios['cargo_vida_natural']:.2f} €")
                s5.metric("Dif. servicios", f"{resumen_servicios['diferencia_servicios']:.2f} €")
                s6.metric("Devoluciones", f"{resumen_servicios['cargo_devoluciones']:.2f} €")

                if abs(resumen_servicios["diferencia_servicios"]) <= 0.05:
                    st.success("Los servicios de factura cuadran con el cargo calculado de bidanatural.")
                elif resumen_servicios["diferencia_servicios"] > 0:
                    if resumen_servicios.get("imputa_devoluciones", True):
                        st.warning(
                            "Hay importe de servicios no cubierto por bidanatural. "
                            "Se imputa como posible cargo por devoluciones sobre abonos."
                        )
                    else:
                        st.warning(
                            "Hay importe de servicios no cubierto por bidanatural. "
                            "La tarifa detectada no imputa devoluciones por defecto; queda como diferencia a revisar."
                        )
                    if resumen_servicios.get("devoluciones_cuadran"):
                        st.success(
                            "La diferencia de servicios coincide exactamente con el cargo calculado "
                            "por devoluciones/abonos."
                        )
                else:
                    st.warning(
                        "El cargo calculado de bidanatural supera el importe de servicios de factura. "
                        "Revisa las líneas con observación B o la condición Avantia."
                    )

                if not analisis_servicios["detalle"].empty:
                    st.caption("Resumen detallado de líneas afectadas por servicios")
                    st.dataframe(analisis_servicios["detalle"])

                if not analisis_servicios["imputacion_devoluciones"].empty:
                    st.caption("Imputación de devoluciones a compras del mismo código nacional")
                    st.dataframe(analisis_servicios["imputacion_devoluciones"])

                if not analisis_servicios["pendiente_otros_gastos"].empty:
                    st.caption("Devoluciones pendientes para imputar más adelante como otros gastos")
                    st.dataframe(analisis_servicios["pendiente_otros_gastos"])

            resumen = resultado.get("resumen_costes")

            if resumen:
                st.subheader("💰 Coste total factura normal")

                col1, col2, col3 = st.columns(3)

                col1.metric("Base", f"{resumen['base']} €")
                col2.metric("IVA (21%)", f"{resumen['iva']} €")
                col3.metric("TOTAL", f"{resumen['total']} €")

            hay_avantia_detectada = avantia.hay_avantia(df, resultado["gastos"])

            if hay_avantia_detectada:
                st.subheader("🧾 Desglose Avantia")

                excel_avantia = st.file_uploader(
                    "Cuadro rentabilidad Avantia",
                    type=["xlsx"],
                    key="avantia_rentabilidad_excel"
                )

                if excel_avantia:
                    try:
                        cargos_avantia = avantia.leer_cuadro_rentabilidad_avantia(excel_avantia)
                        analisis_avantia = avantia.analizar_avantia(df, resultado["gastos"], cargos_avantia)

                        if analisis_avantia:
                            resumen_avantia = analisis_avantia["resumen"]

                            a1, a2, a3, a4, a5, a6 = st.columns(6)
                            a1.metric("Gasto esp.", f"{resumen_avantia['cargo_especialidad']:.2f} €")
                            a2.metric("Gasto paraf.", f"{resumen_avantia['cargo_parafarmacia']:.2f} €")
                            a3.metric("Bonif. esp.", f"{resumen_avantia['bonificacion_especialidad']:.2f} €")
                            a4.metric("Bonif. paraf.", f"{resumen_avantia['bonificacion_parafarmacia']:.2f} €")
                            a5.metric("Cuota Avantia", f"{resumen_avantia['cuota_avantia']:.2f} €")
                            a6.metric("Coste total", f"{resumen_avantia['coste_total_avantia']:.2f} €")

                            if not analisis_avantia["cargos"].empty:
                                st.caption("Cargos detectados en cuadro rentabilidad Avantia")
                                st.dataframe(analisis_avantia["cargos"])

                            if not analisis_avantia["detalle"].empty:
                                st.caption("Resumen detallado de artículos Avantia")
                                st.dataframe(analisis_avantia["detalle"])
                            else:
                                st.info(
                                    "Se ha detectado Avantia, pero no hay líneas de albarán con Avantia "
                                    "en la descripción para imputar cargos."
                                )

                    except ValueError as error:
                        st.error(f"No se pudo leer el cuadro rentabilidad Avantia: {error}")
                else:
                    st.info(
                        "Se ha detectado Avantia por factura o albaranes. "
                        "Sube el cuadro rentabilidad Avantia para calcular los gastos de especialidad/parafarmacia "
                        "y prorratear la cuota."
                    )

            # BITRANSFER
            df_bida = df[df["proveedor"] == "bidafarma"]

            hay_bitransfer = False
            if "seccion_albaran" in df_bida.columns:
                hay_bitransfer = (df_bida["seccion_albaran"] == "bitransfer").any()

            hay_gestion = False
            if resultado and not resultado["gastos"].empty:
                hay_gestion = (resultado["gastos"]["tipo"] == "gestion").any()

            if hay_bitransfer and hay_gestion:

                st.subheader("🔍 Desglose gastos gestión Bitransfer")

                col_consumos, col_compras = st.columns(2)

                with col_consumos:
                    excel_consumos_bitransfer = st.file_uploader(
                        "Cuadro resumen de consumos",
                        type=["xlsx"],
                        key="bitransfer_consumos_excel"
                    )

                with col_compras:
                    excel_compras_bitransfer = st.file_uploader(
                        "Listado de compras BitTransfer",
                        type=["xlsx"],
                        key="bitransfer_compras_excel"
                    )

                resumen_consumos = None
                df_bt_compras = None

                if excel_consumos_bitransfer:
                    try:
                        resumen_consumos = bitransfer.leer_cuadro_resumen_consumos(excel_consumos_bitransfer)

                        st.subheader("📊 Cuadro resumen de consumos normalizado")

                        if not resumen_consumos["bitransfer"].empty:
                            st.caption("Bloque BitTransfer")
                            st.dataframe(resumen_consumos["bitransfer"])

                        if not resumen_consumos["plataformas"].empty:
                            st.caption("Bloque plataformas")
                            st.dataframe(resumen_consumos["plataformas"])

                    except ValueError as error:
                        st.error(f"No se pudo leer el cuadro resumen de consumos: {error}")

                if excel_compras_bitransfer:
                    try:
                        df_bt_compras = bitransfer.leer_listado_compras_bitransfer(excel_compras_bitransfer)
                        df_bt_compras = _enriquecer_con_maestro(df_bt_compras)

                        st.subheader("📋 Listado de compras BitTransfer normalizado")

                        c1, c2, c3 = st.columns(3)
                        c1.metric("Códigos nacionales", df_bt_compras["cn"].nunique())
                        c2.metric("Unidades", int(df_bt_compras["cantidad"].fillna(0).sum()))
                        c3.metric("Importe neto", f"{df_bt_compras['importe_neto'].sum():.2f} €")

                        _mostrar_dataframe_debug(
                            df_bt_compras,
                            "Listado completo de compras BitTransfer oculto por privacidad.",
                        )

                    except ValueError as error:
                        st.error(f"No se pudo leer el listado de compras BitTransfer: {error}")

                if resumen_consumos is not None and df_bt_compras is not None:
                    try:
                        df_bt_conciliado, resumen_conciliacion = bitransfer.conciliar_bitransfer_consumos(
                            df_bt_compras,
                            resumen_consumos
                        )
                        resumen_conciliacion_bitransfer = resumen_conciliacion

                        st.subheader("✅ Conciliación BitTransfer")

                        c1, c2, c3, c4, c5, c6 = st.columns(6)
                        c1.metric("Bruto resumen", f"{resumen_conciliacion['venta_bruta_resumen']:.2f} €")
                        c2.metric("Bruto compras", f"{resumen_conciliacion['venta_bruta_compras']:.2f} €")
                        c3.metric("Diferencia bruto", f"{resumen_conciliacion['diferencia_venta_bruta']:.2f} €")
                        c4.metric("Cargo resumen", f"{resumen_conciliacion['cargo_resumen']:.2f} €")
                        c5.metric("Cargo teórico", f"{resumen_conciliacion['cargo_teorico_compras']:.2f} €")
                        c6.metric("Dif. cargo", f"{resumen_conciliacion['diferencia_cargo']:.2f} €")

                        if analisis_avantia:
                            gestion_factura = float(resultado["gastos"].loc[
                                resultado["gastos"]["tipo"] == "gestion",
                                "importe"
                            ].sum())
                            cargo_bitransfer = resumen_conciliacion["cargo_resumen"]
                            cargo_avantia = analisis_avantia["resumen"]["cargo_total"]
                            gestion_calculada = cargo_bitransfer + cargo_avantia
                            diferencia_gestion = gestion_factura - gestion_calculada

                            st.subheader("🧮 Conciliación gastos de gestión")

                            g1, g2, g3, g4 = st.columns(4)
                            g1.metric("Gestión factura", f"{gestion_factura:.2f} €")
                            g2.metric("BitTransfer", f"{cargo_bitransfer:.2f} €")
                            g3.metric("Avantia", f"{cargo_avantia:.2f} €")
                            g4.metric("Diferencia", f"{diferencia_gestion:.2f} €")

                            if abs(diferencia_gestion) > 0.05:
                                st.warning(
                                    "Los gastos de gestión no cuadran exactamente con BitTransfer + Avantia. "
                                    "Revisa que el cuadro de consumos y el cuadro rentabilidad Avantia correspondan al mismo periodo."
                                )

                        if abs(resumen_conciliacion["diferencia_venta_bruta"]) <= 0.05:
                            st.success("La venta bruta del resumen cuadra con el listado de compras BitTransfer.")
                        else:
                            st.warning(
                                "La venta bruta no cuadra todavía. "
                                "Revisa si el listado de compras contiene exactamente los productos del resumen."
                            )

                        st.caption(
                            "Detalle unitario: PBL, descuento, importe neto unitario, "
                            "cargo teórico unitario y coste real unitario."
                        )
                        st.dataframe(df_bt_conciliado)

                        plataformas = resumen_consumos["plataformas"]
                        if not plataformas.empty:
                            st.subheader("🧩 Listados de productos de plataformas")
                            st.info(
                                "El cuadro resumen contiene plataformas o grupos adicionales. "
                                "Sube aquí el Excel de productos de cada plataforma para poder prorratear cuotas "
                                "y aplicar su cargo específico en el siguiente paso."
                            )

                            for indice, plataforma in plataformas.iterrows():
                                nombre_plataforma = str(plataforma["plataforma"])
                                cargo_pct = plataforma.get("cargo_pct")
                                cuota = plataforma.get("cuota")

                                st.markdown(
                                    f"**{nombre_plataforma}**"
                                    f" · Cargo: {cargo_pct if pd.notna(cargo_pct) else 0:.2f}%"
                                    f" · Cuota: {cuota if pd.notna(cuota) else 0:.2f} €"
                                )

                                excel_plataforma = st.file_uploader(
                                    f"Listado de productos {nombre_plataforma}",
                                    type=["xlsx"],
                                    key=f"plataforma_{indice}_excel"
                                )

                                if excel_plataforma:
                                    try:
                                        df_plataforma = bitransfer.leer_listado_compras_bitransfer(excel_plataforma)
                                        df_plataforma = _enriquecer_con_maestro(df_plataforma)
                                        _mostrar_dataframe_debug(
                                            df_plataforma,
                                            f"Listado completo de productos de {nombre_plataforma} oculto por privacidad.",
                                        )
                                    except ValueError as error:
                                        st.error(
                                            f"No se pudo leer el listado de productos de {nombre_plataforma}: {error}"
                                        )

                    except ValueError as error:
                        st.error(f"No se pudo conciliar BitTransfer: {error}")

            if resultado is not None and not resultado["gastos"].empty:
                gestion_factura = float(resultado["gastos"].loc[
                    resultado["gastos"]["tipo"] == "gestion",
                    "importe"
                ].sum())
                cargo_bitransfer = (
                    0.0 if not resumen_conciliacion_bitransfer else resumen_conciliacion_bitransfer["cargo_resumen"]
                )
                cargo_avantia = 0.0 if not analisis_avantia else analisis_avantia["resumen"]["cargo_total"]
                gestion_calculada = cargo_bitransfer + cargo_avantia
                diferencia_gestion = gestion_factura - gestion_calculada

                if gestion_factura > 0 and (cargo_bitransfer > 0 or cargo_avantia > 0 or condicion_detectada):
                    st.subheader("🧮 Conciliación global gastos de gestión")

                    g1, g2, g3, g4 = st.columns(4)
                    g1.metric("Gestión factura", f"{gestion_factura:.2f} €")
                    g2.metric("BitTransfer", f"{cargo_bitransfer:.2f} €")
                    g3.metric("Avantia", f"{cargo_avantia:.2f} €")
                    g4.metric("Diferencia", f"{diferencia_gestion:.2f} €")

                    penalizacion_bajo_consumo = _detectar_penalizacion_bajo_consumo(
                        condicion_detectada,
                        diferencia_gestion,
                    )
                    if penalizacion_bajo_consumo:
                        st.info(
                            "La diferencia de gestión coincide con la penalización por bajo consumo "
                            f"({penalizacion_bajo_consumo['importe']:.2f} €)."
                        )

                if (
                    condicion_detectada
                    and condicion_detectada["cargo_adicional_gestion"]
                    and diferencia_gestion > 0.05
                ):
                    analisis_cargo_adicional = _analisis_cargo_adicional_gestion(df, diferencia_gestion)

                    if analisis_cargo_adicional:
                        st.warning(
                            "Los gastos de gestión incluyen un cargo adicional no explicado por BitTransfer/Avantia. "
                            "Se reparte como franquicia sobre el goteo elegible."
                        )
                        resumen_cargo_adicional = analisis_cargo_adicional["resumen"]
                        ca1, ca2, ca3, ca4 = st.columns(4)
                        ca1.metric("Cargo adicional", f"{resumen_cargo_adicional['cargo_total']:.2f} €")
                        ca2.metric("Base tramo fijo", f"{resumen_cargo_adicional['base_cargo']:.2f} €")
                        ca3.metric("Base de aplicación", f"{resumen_cargo_adicional['base_aplicacion']:.2f} €")
                        ca4.metric("Líneas afectadas", resumen_cargo_adicional["lineas_afectadas"])

                        st.caption("Imputación del cargo adicional de gestión")
                        st.dataframe(
                            analisis_cargo_adicional["detalle"][
                                [
                                    col for col in [
                                        "cn",
                                        "descripcion",
                                        "seccion_albaran",
                                        "bruto",
                                        "neto",
                                        "cargo_gestion_adicional",
                                        "neto_con_gestion_adicional",
                                    ]
                                    if col in analisis_cargo_adicional["detalle"].columns
                                ]
                            ]
                        )

        # -------------------------
        # FACTURA TRANSFER
        # -------------------------
        analisis_transfer = None
        factura_transfer = st.file_uploader("Factura TRANSFER", type=["xlsx"], key="bidafarma_factura_transfer")
        st.session_state["factura_transfer_bidafarma"] = "cargada" if factura_transfer else None

        if factura_transfer:

            resultado_transfer = analizar_factura_transfer(factura_transfer)
            resultado_factura_transfer = resultado_transfer

            df_transfer = df[df["tipo_compra"] == "transfer"]

            albaranes_factura = set(resultado_transfer["albaranes"])
            albaranes_df = set(df_transfer["albaran"].apply(normalizar_albaran))

            faltan = albaranes_df - albaranes_factura
            sobran = albaranes_factura - albaranes_df

            if not faltan and not sobran:
                st.success("✅ Albaranes TRANSFER conciliados")
            else:
                if faltan:
                    if MODO_DEBUG:
                        st.error(f"Faltan en transfer: {faltan}")
                    else:
                        st.error(f"Faltan {len(faltan)} albaranes TRANSFER por conciliar.")
                if sobran:
                    if MODO_DEBUG:
                        st.warning(f"Sobran en transfer: {sobran}")
                    else:
                        st.warning(f"Sobran {len(sobran)} albaranes TRANSFER en factura.")

            st.subheader("🚚 Servicios logísticos")
            st.dataframe(resultado_transfer["gastos"])

            st.subheader("🏭 Abonos laboratorios")
            st.dataframe(resultado_transfer["abonos"])

            resumen = resultado_transfer.get("resumen_logistica")

            if resumen:
                st.subheader("💰 Coste total logística")

                col1, col2, col3 = st.columns(3)

                col1.metric("Base", f"{resumen['base']} €")
                col2.metric("IVA (21%)", f"{resumen['iva']} €")
                col3.metric("TOTAL", f"{resumen['total']} €")

            analisis_transfer = _analisis_transfer_logistica(df_transfer, resultado_transfer)
            if analisis_transfer:
                resumen_transfer = analisis_transfer["resumen"]

                st.subheader("🧮 Imputación logística transfer")

                t1, t2, t3, t4, t5 = st.columns(5)
                t1.metric("Base elegible", f"{resumen_transfer['base_elegible']:.2f} €")
                t2.metric("Cargo teórico 1,7%", f"{resumen_transfer['cargo_base_teorico']:.2f} €")
                t3.metric("IVA teórico 21%", f"{resumen_transfer['cargo_iva_teorico']:.2f} €")
                t4.metric("Total teórico", f"{resumen_transfer['cargo_total_teorico']:.2f} €")
                t5.metric("Líneas con cargo", resumen_transfer["lineas_elegibles"])

                t6, t7, t8, t9 = st.columns(4)
                t6.metric("Base factura", f"{resumen_transfer['base_factura']:.2f} €")
                t7.metric("IVA factura", f"{resumen_transfer['iva_factura']:.2f} €")
                t8.metric("Total factura", f"{resumen_transfer['total_factura']:.2f} €")
                t9.metric("Dif. total", f"{resumen_transfer['diferencia_total']:.2f} €")

                if resumen_transfer["laboratorios_bonificados"]:
                    st.caption(
                        "Laboratorios bonificados detectados: "
                        + ", ".join(resumen_transfer["laboratorios_bonificados"])
                    )
                if resumen_transfer["albaranes_bonificados"]:
                    st.caption(
                        "Albaranes sin cargo por bonificación logística: "
                        + ", ".join(map(str, resumen_transfer["albaranes_bonificados"]))
                    )

                if abs(resumen_transfer["diferencia_total"]) <= 0.05:
                    st.success(
                        "El cargo teórico de transfer, incluyendo IVA, cuadra con la factura."
                    )
                else:
                    st.warning(
                        "El cálculo teórico de transfer no cuadra todavía con la factura. "
                        "Revisa si falta algún laboratorio por reconocer en la base maestra "
                        "o si la factura incluye una bonificación logística adicional."
                    )

                if not analisis_transfer["abonos_detectados"].empty:
                    st.caption("Abonos de factura y laboratorios detectados")
                    st.dataframe(analisis_transfer["abonos_detectados"])

                st.caption(
                    "Detalle de líneas transfer: solo se aplica cargo al transfer real, "
                    "sin abonos y excluyendo albaranes bonificados."
                )
                st.dataframe(
                    analisis_transfer["detalle"][
                        [
                            col for col in [
                                "albaran",
                                "cn",
                                "descripcion",
                                "laboratorio_maestro",
                                "bruto",
                                "neto",
                                "tiene_bonificacion_logistica",
                                "abono_logistico_laboratorio",
                                "cargo_transfer_base",
                                "cargo_transfer_iva",
                                "cargo_transfer_total",
                                "neto_con_cargo_transfer",
                            ]
                            if col in analisis_transfer["detalle"].columns
                        ]
                    ]
                )

    # =========================
    # INICIO
    # =========================

    if df is None:
        st.warning("Sube archivos")
        return

    st.header("📌 Resumen Bidafarma")
    analisis_faceta_final = faceta.analizar_faceta_v(df, df_faceta_bidafarma) if not df_faceta_bidafarma.empty else None
    resumen_final = _resumen_bidafarma(
        df,
        analisis_faceta=analisis_faceta_final,
        resumen_bitransfer=resumen_conciliacion_bitransfer,
        analisis_avantia=analisis_avantia,
        analisis_ajuste=analisis_ajuste,
        analisis_cargo_adicional=analisis_cargo_adicional,
        analisis_transfer=analisis_transfer,
    )

    if resumen_final:
        metricas = resumen_final["metricas"]
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Compra total Bidafarma", f"{metricas['total_bidafarma_bruto']:.2f} €")
        r2.metric(
            "Desc. real goteo puro",
            "-" if metricas["goteo_puro_descuento_real"] is None else f"{metricas['goteo_puro_descuento_real']:.2f}%",
        )
        r3.metric(
            "Desc. real Bitransfer",
            "-" if metricas["bitransfer_descuento_real"] is None else f"{metricas['bitransfer_descuento_real']:.2f}%",
        )
        r4.metric(
            "Desc. real Transfer",
            "-" if metricas["transfer_descuento_real"] is None else f"{metricas['transfer_descuento_real']:.2f}%",
        )

        if resumen_final["resumen_textual"]:
            for texto in resumen_final["resumen_textual"]:
                st.info(texto)

        if not resumen_final["tabla"].empty:
            st.caption("Resumen de compras y descuentos reales por bloque")
            st.dataframe(resumen_final["tabla"])

    st.header("Generación de informe")
    if st.button("Generar análisis distribuidora", key="generar_analisis_bidafarma"):
        analisis = reporting.generar_analisis_distribuidora(
            df,
            proveedor="bidafarma",
            resultado_factura_normal=resultado_factura_normal,
            resultado_factura_transfer=resultado_factura_transfer,
            analisis_faceta=analisis_faceta_final,
            analisis_avantia=analisis_avantia,
            resumen_bitransfer=resumen_conciliacion_bitransfer,
            analisis_transfer=analisis_transfer,
        )
        _guardar_analisis_distribuidora("bidafarma", analisis)

    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get("bidafarma")
    if analisis_guardado:
        _mostrar_analisis_distribuidora(analisis_guardado)


def render_contexto_farmacia():
    st.subheader("Contexto de farmacia")

    tipo_zona_opciones = [
        "urbana",
        "rural",
        "turística",
        "costa",
        "montaña",
        "barrio residencial",
        "zona hospitalaria",
        "centro de salud cercano",
        "otra",
    ]
    epoca_opciones = ["invierno", "primavera", "verano", "otoño"]
    campanas_opciones = [
        "gripe/resfriado",
        "alergia",
        "protección solar",
        "vuelta al cole",
        "Navidad",
        "Semana Santa",
        "turismo verano",
        "ola de calor",
        "dermocosmética",
        "ninguna",
    ]
    perfil_opciones = [
        "alta receta",
        "alta parafarmacia",
        "paciente crónico",
        "familias/pediatría",
        "turista",
        "dermocosmética",
        "farmacia de paso",
        "farmacia rural",
        "alto volumen",
    ]

    contexto_previo = st.session_state.get("contexto_farmacia", {})

    c1, c2 = st.columns(2)
    with c1:
        provincia_ciudad = st.text_input(
            "Provincia / ciudad",
            value=contexto_previo.get("provincia_ciudad", ""),
            key="contexto_provincia_ciudad",
        )
        tipo_zona = st.selectbox(
            "Tipo de zona",
            tipo_zona_opciones,
            index=tipo_zona_opciones.index(contexto_previo.get("tipo_zona", "urbana"))
            if contexto_previo.get("tipo_zona", "urbana") in tipo_zona_opciones else 0,
            key="contexto_tipo_zona",
        )
        epoca_ano = st.selectbox(
            "Época del año",
            epoca_opciones,
            index=epoca_opciones.index(contexto_previo.get("epoca_ano", "invierno"))
            if contexto_previo.get("epoca_ano", "invierno") in epoca_opciones else 0,
            key="contexto_epoca_ano",
        )

    with c2:
        campana_activa = st.multiselect(
            "Campaña activa",
            campanas_opciones,
            default=contexto_previo.get("campana_activa", ["ninguna"]),
            key="contexto_campana_activa",
        )
        perfil_farmacia = st.multiselect(
            "Perfil principal de farmacia",
            perfil_opciones,
            default=contexto_previo.get("perfil_farmacia", []),
            key="contexto_perfil_farmacia",
        )

    contexto = {
        "provincia_ciudad": provincia_ciudad.strip(),
        "tipo_zona": tipo_zona,
        "epoca_ano": epoca_ano,
        "campana_activa": campana_activa,
        "perfil_farmacia": perfil_farmacia,
    }
    st.session_state["contexto_farmacia"] = contexto

    resumen = pd.DataFrame([
        {"campo": "Provincia / ciudad", "valor": contexto["provincia_ciudad"] or "sin indicar"},
        {"campo": "Tipo de zona", "valor": contexto["tipo_zona"]},
        {"campo": "Época del año", "valor": contexto["epoca_ano"]},
        {"campo": "Campaña activa", "valor": ", ".join(contexto["campana_activa"]) or "sin indicar"},
        {"campo": "Perfil principal", "valor": ", ".join(contexto["perfil_farmacia"]) or "sin indicar"},
    ])
    st.caption("Resumen visual del contexto preparado para futuras propuestas de pedido")
    st.dataframe(resumen, hide_index=True)


st.set_page_config(layout="wide")
st.title("📊 Auditoría de Compras Farmacia")

_verificar_acceso_app()

clave_modo_auditor = st.sidebar.text_input("Clave modo auditor", type="password")
MODO_DEBUG = clave_modo_auditor == DEBUG_PASSWORD

if st.button("Borrar datos cargados"):
    st.session_state.clear()
    st.rerun()

with st.expander("Base maestra CN / laboratorio", expanded=False):
    _render_base_maestra_laboratorios()

with st.expander("Contexto de farmacia", expanded=False):
    render_contexto_farmacia()

seccion_activa = st.radio(
    "Selecciona el apartado de trabajo",
    SECCIONES,
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()

if seccion_activa == "bidafarma":
    render_vida_pharma()
elif seccion_activa in PROVEEDORES_BASE:
    render_proveedor_base(seccion_activa, PROVEEDORES_BASE[seccion_activa])
elif seccion_activa == "Facturas laboratorios":
    render_facturas_laboratorios()
elif seccion_activa == "Ventas farmacia":
    render_ventas_farmacia()
elif seccion_activa == "Stock":
    render_stock()
elif seccion_activa == "Resumen":
    render_resumen()
