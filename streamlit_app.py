import streamlit as st
import pandas as pd
import re
import importlib
import unicodedata
import io
import html
import json
from pathlib import Path

from modules.ingestion import load_excel
from modules.parser import parse_sections
from modules.classification import normalize_columns, clasificar_especialidad_cara
from modules.parafarmacia import detectar_parafarmacia_financiada, normalizar_columnas_nomenclator
from modules.analytics import analizar_factura_bidafarma, analizar_factura_transfer
import modules.bitransfer as bitransfer
import modules.servicios as servicios
import modules.avantia as avantia
import modules.faceta as faceta
import modules.condiciones_proveedor_b as condiciones_proveedor_b
import modules.maestro_laboratorios as maestro_laboratorios
import modules.nomenclator_aemps as nomenclator_aemps
import modules.ventas as ventas
import modules.reporting as reporting
import modules.equivalencias_efg as equivalencias_efg
import modules.ai_advisor as ai_advisor
import modules.club_analysis as club_analysis
import modules.distributor_analysis as distributor_analysis
import modules.parafarmacia as parafarmacia
import modules.transfer_manual_mapping as transfer_manual_mapping
import modules.maestros_persistentes as maestros_persistentes
import modules.bandeja_documental as bandeja_documental
import modules.preanalisis_documental as preanalisis_documental
from modules.bandeja_documental_inbox import AdjuntoEntrante, EmailEntrante, procesar_email_entrante
from modules.bandeja_documental_repository import BandejaDocumentalRepository
from modules.bandeja_documental_service import BandejaDocumentalService

bitransfer = importlib.reload(bitransfer)
servicios = importlib.reload(servicios)
avantia = importlib.reload(avantia)
faceta = importlib.reload(faceta)
condiciones_proveedor_b = importlib.reload(condiciones_proveedor_b)
maestro_laboratorios = importlib.reload(maestro_laboratorios)
nomenclator_aemps = importlib.reload(nomenclator_aemps)
ventas = importlib.reload(ventas)
reporting = importlib.reload(reporting)
equivalencias_efg = importlib.reload(equivalencias_efg)
ai_advisor = importlib.reload(ai_advisor)
club_analysis = importlib.reload(club_analysis)
distributor_analysis = importlib.reload(distributor_analysis)
parafarmacia = importlib.reload(parafarmacia)
transfer_manual_mapping = importlib.reload(transfer_manual_mapping)
maestros_persistentes = importlib.reload(maestros_persistentes)
bandeja_documental = importlib.reload(bandeja_documental)
preanalisis_documental = importlib.reload(preanalisis_documental)

try:
    APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")
except Exception:
    APP_PASSWORD = ""
MAX_UPLOAD_MB = 50

PROVEEDORES_BASE = {
    "cofares": "cofares",
    "alliance": "alliance",
    "hefame": "hefame",
}

SECCIONES = [
    "Bandeja documental",
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
    if pd.isna(valor):
        return ""

    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if float(valor).is_integer():
            return str(int(valor))

    texto = str(valor).lower().strip()
    if not texto:
        return ""

    texto = re.sub(r"\.0+$", "", texto)
    if re.fullmatch(r"[a-z.\s-]*\d[\d.\s-]*", texto):
        return re.sub(r"\D", "", texto)

    grupos = re.findall(r"\d+", texto)
    grupos_largos = [grupo for grupo in grupos if len(grupo) >= 4]
    if grupos_largos:
        return grupos_largos[-1]

    return re.sub(r"\D", "", texto) or texto


def _normalizar_nombre_columna(columna):
    texto = str(columna).lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip()


def _buscar_columna_albaran(columnas):
    tokens_excluir = ["total", "importe", "base", "iva", "recargo", "fecha"]
    for col in columnas:
        nombre = _normalizar_nombre_columna(col)
        if "albar" in nombre and not any(token in nombre for token in tokens_excluir):
            return col
    return None


def _guardar_dataset(clave, df):
    st.session_state[clave] = df


def _inyectar_estilos_dashboard():
    st.markdown(
        """
        <style>
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 14px;
            margin: 12px 0 22px 0;
        }
        .kpi-card {
            border: 1px solid rgba(148, 163, 184, 0.24);
            background: rgba(31, 41, 55, 0.52);
            border-radius: 8px;
            padding: 16px 18px;
            min-height: 96px;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.22);
        }
        .kpi-label {
            color: rgba(226, 232, 240, 0.82);
            font-size: 0.82rem;
            font-weight: 650;
            margin-bottom: 10px;
            line-height: 1.2;
        }
        .kpi-value {
            color: #ffffff;
            font-size: 1.88rem;
            font-weight: 700;
            line-height: 1.05;
            letter-spacing: 0;
            white-space: nowrap;
        }
        .kpi-note {
            color: rgba(148, 163, 184, 0.88);
            font-size: 0.76rem;
            margin-top: 8px;
        }
        .dashboard-table-wrap {
            border: 1px solid rgba(148, 163, 184, 0.24);
            background: rgba(15, 23, 42, 0.50);
            border-radius: 8px;
            margin: 10px 0 24px 0;
            max-height: 520px;
            overflow: auto;
        }
        .dashboard-table {
            width: 100%;
            min-width: 760px;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.86rem;
        }
        .dashboard-table thead th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: rgba(31, 41, 55, 0.98);
            color: rgba(226, 232, 240, 0.92);
            font-weight: 700;
            text-align: left;
            padding: 12px 14px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.26);
            white-space: nowrap;
        }
        .dashboard-table tbody td {
            color: #ffffff;
            padding: 11px 14px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.14);
            border-right: 1px solid rgba(148, 163, 184, 0.10);
            white-space: nowrap;
        }
        .dashboard-table tbody tr:nth-child(even) {
            background: rgba(31, 41, 55, 0.34);
        }
        .dashboard-table tbody tr:hover {
            background: rgba(59, 130, 246, 0.14);
        }
        .dashboard-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .dashboard-table tbody td:first-child {
            font-weight: 700;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _tarjetas_metricas(metricas):
    tarjetas = []
    for metrica in metricas:
        etiqueta = html.escape(str(metrica.get("label", "")))
        valor = html.escape(str(metrica.get("value", "")))
        nota = html.escape(str(metrica.get("note", "")))
        nota_html = f'<div class="kpi-note">{nota}</div>' if nota else ""
        tarjetas.append(
            f'<div class="kpi-card"><div class="kpi-label">{etiqueta}</div>'
            f'<div class="kpi-value">{valor}</div>{nota_html}</div>'
        )
    st.markdown('<div class="kpi-grid">' + "".join(tarjetas) + "</div>", unsafe_allow_html=True)


def _nombre_columna_tabla(columna):
    texto = str(columna).strip().replace("_", " ")
    equivalencias = {
        "cn": "CN",
        "iva": "IVA",
        "pvpiva": "PVP IVA",
        "pvp iva": "PVP IVA",
        "re": "RE",
        "d c": "D/C",
        "dc": "D/C",
    }
    normalizado = _normalizar_nombre_columna(texto).replace(" ", "_")
    if normalizado in {"cn", "iva", "pvpiva", "pvp_iva", "re", "dc", "d_c"}:
        return equivalencias.get(normalizado.replace("_", " "), equivalencias.get(normalizado, texto.upper()))
    return texto[:1].upper() + texto[1:]


def _formatear_valor_tabla(valor):
    try:
        es_vacio = pd.isna(valor)
    except (TypeError, ValueError):
        es_vacio = False
    if isinstance(es_vacio, (list, tuple)):
        es_vacio = False
    if hasattr(es_vacio, "any"):
        es_vacio = False
    if valor is None or es_vacio:
        return "-"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        numero = float(valor)
        if abs(numero - round(numero)) < 0.000001:
            return str(int(round(numero)))
        return f"{numero:.2f}".rstrip("0").rstrip(".")
    return str(valor)


def _mostrar_tabla_dashboard(df, max_filas=None, renombrar_columnas=True):
    if df is None:
        return
    tabla = pd.DataFrame(df).copy()
    if tabla.empty:
        st.info("No hay datos para mostrar.")
        return
    if max_filas is not None:
        tabla = tabla.head(max_filas).copy()
    if renombrar_columnas:
        tabla.columns = [_nombre_columna_tabla(col) for col in tabla.columns]
    tabla = tabla.apply(lambda columna: columna.map(_formatear_valor_tabla))
    html_tabla = tabla.to_html(index=False, escape=True, border=0, classes="dashboard-table")
    st.markdown(f'<div class="dashboard-table-wrap">{html_tabla}</div>', unsafe_allow_html=True)


def _mostrar_dataframe_completo(df):
    _mostrar_tabla_dashboard(df, renombrar_columnas=False)


def _vista_compras_ligera(df):
    if df is None or df.empty:
        return df

    def buscar_columna(candidatas):
        columnas_norm = {_normalizar_nombre_columna(col).replace(" ", "_"): col for col in df.columns}
        for candidata in candidatas:
            normalizada = _normalizar_nombre_columna(candidata).replace(" ", "_")
            if normalizada in columnas_norm:
                return columnas_norm[normalizada]
        for col in df.columns:
            nombre = _normalizar_nombre_columna(col).replace(" ", "_")
            if any(token in nombre for token in candidatas):
                return col
        return None

    columna_descuento_cargo = buscar_columna([
        "d/c",
        "dc",
        "descuento_cargo",
        "descuento cargo",
        "dto_cargo",
        "dto/cargo",
    ])
    columna_descripcion = buscar_columna([
        "descripcion",
        "descripción",
        "descripcion_articulo",
        "descripcion articulo",
        "descripción artículo",
        "articulo",
        "artículo",
        "producto",
    ])
    columna_coste_total = buscar_columna([
        "coste_total_iva_re",
        "coste_total_con_iva_re",
        "coste_total_con_iva_y_re",
        "total_iva_re",
        "total_con_iva_re",
        "coste_total",
    ])
    columna_pvpiva = buscar_columna([
        "pvpiva",
        "pvp_iva",
        "pvp iva",
        "pvp con iva",
        "pvp_con_iva",
    ])

    columnas_preferidas = [
        "fecha",
        "albaran",
        "cn",
        columna_descripcion,
        "seccion_albaran",
        "tipo_compra",
        "categoria",
        "unidades",
        "bruto",
        columna_descuento_cargo,
        "neto",
        "iva",
        columna_coste_total,
        "pvp",
        columna_pvpiva,
        "laboratorio_maestro",
        "tipo_producto",
        "es_especialidad_cara",
        "es_parafarmacia_financiada",
        "tipo_parafarmacia",
        "fuente_deteccion_parafarmacia_financiada",
    ]
    columnas = []
    for col in columnas_preferidas:
        if col in df.columns and col not in columnas:
            columnas.append(col)
    if not columnas:
        return df
    return df.loc[:, columnas].copy()


def _mostrar_error_procesamiento(mensaje, error=None):
    if error is not None:
        st.error(f"{mensaje}: {error}")
    else:
        st.error(mensaje)


def _verificar_acceso_app():
    if st.session_state.get("app_authenticated"):
        return

    _, login_col, _ = st.columns([1, 1.2, 1])
    with login_col:
        with st.form("login_app"):
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar")

        if submitted and APP_PASSWORD and password == APP_PASSWORD:
            st.session_state["app_authenticated"] = True
            st.rerun()

        st.warning("Acceso restringido")
        st.stop()


def _validar_archivo_subido(uploaded_file, etiqueta="archivo", extensiones=("xlsx",), max_mb=MAX_UPLOAD_MB):
    if uploaded_file is None:
        return False

    nombre = str(getattr(uploaded_file, "name", ""))
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if extension not in extensiones:
        st.error(f"{etiqueta}: formato no permitido. Sube únicamente archivos {', '.join('.' + ext for ext in extensiones)}.")
        return False

    tamano = getattr(uploaded_file, "size", None)
    if tamano is not None and tamano > max_mb * 1024 * 1024:
        st.error(f"{etiqueta}: archivo demasiado grande. Límite máximo: {max_mb} MB.")
        return False

    return True


def _filtrar_archivos_validos(uploaded_files, etiqueta="archivo", extensiones=("xlsx",), max_mb=MAX_UPLOAD_MB):
    if not uploaded_files:
        return []
    return [
        uploaded_file
        for uploaded_file in uploaded_files
        if _validar_archivo_subido(uploaded_file, etiqueta, extensiones=extensiones, max_mb=max_mb)
    ]


def _aplicar_clasificaciones_transversales(df, df_nomenclator=None):
    df = clasificar_especialidad_cara(df)
    return detectar_parafarmacia_financiada(df, df_nomenclator=df_nomenclator)


def _mostrar_tarjeta_parafarmacia_financiada(df):
    if df is None or df.empty or "es_parafarmacia_financiada" not in df.columns:
        return

    mask = df["es_parafarmacia_financiada"].fillna(False).astype(bool)
    if not mask.any():
        return

    parte = df[mask].copy()
    bruto = pd.to_numeric(parte.get("bruto", 0), errors="coerce").fillna(0).sum()
    neto = pd.to_numeric(parte.get("neto", 0), errors="coerce").fillna(0).sum()
    st.success(
        "Parafarmacia financiada detectada · "
        f"Líneas: {int(mask.sum())} · Bruto: {bruto:.2f} € · Neto: {neto:.2f} €"
    )


def _archivo_a_bytes(uploaded_file):
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    contenido = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    nombre = str(getattr(uploaded_file, "name", "archivo"))
    return contenido, nombre


def _archivo_desde_bytes(contenido, nombre):
    archivo = io.BytesIO(contenido)
    archivo.name = nombre
    archivo.size = len(contenido)
    return archivo


@st.cache_data(show_spinner=False)
def _leer_equivalencias_efg_cache(contenido, nombre):
    return equivalencias_efg.leer_base_equivalencias_efg(_archivo_desde_bytes(contenido, nombre))


@st.cache_data(show_spinner=False)
def _leer_maestro_ministerio_cache(contenido, nombre):
    archivo = _archivo_desde_bytes(contenido, nombre)
    ministerio_df = maestro_laboratorios.leer_maestro_laboratorios(archivo)
    ministerio_df["fuente_maestro"] = "ministerio_facturacion"
    if "tipo_producto" not in ministerio_df.columns:
        ministerio_df["tipo_producto"] = None
    archivo.seek(0)
    try:
        nomenclator_df = normalizar_columnas_nomenclator(pd.read_excel(archivo))
    except ValueError:
        nomenclator_df = None
    return ministerio_df, nomenclator_df


@st.cache_data(show_spinner=False)
def _leer_maestro_manual_cache(contenido, nombre):
    maestro_df = maestro_laboratorios.leer_maestro_laboratorios(_archivo_desde_bytes(contenido, nombre))
    maestro_df["fuente_maestro"] = "manual"
    return maestro_df


@st.cache_data(show_spinner=False)
def _leer_nomenclator_aemps_cache(contenido, nombre):
    return nomenclator_aemps.leer_nomenclator_aemps(_archivo_desde_bytes(contenido, nombre))


def _procesar_equivalencias_efg(equivalencias_file, persistir=False):
    contenido, nombre = _archivo_a_bytes(equivalencias_file)
    efg_data = _leer_equivalencias_efg_cache(contenido, nombre)
    st.session_state["tabla_equivalencias_efg"] = efg_data["tabla_equivalencias_efg"]
    st.session_state["grupos_homogeneos_efg"] = efg_data["grupos_homogeneos"]
    st.session_state["opciones_por_grupo_efg"] = efg_data["opciones_por_grupo"]
    st.session_state["resumen_equivalencias_efg"] = efg_data["resumen"]
    st.session_state["equivalencias_efg_cargadas"] = True
    if persistir:
        maestros_persistentes.guardar_archivo("efg", equivalencias_file)


def _procesar_maestro_ministerio(ministerio_file, persistir=False):
    contenido, nombre = _archivo_a_bytes(ministerio_file)
    ministerio_df, nomenclator_df = _leer_maestro_ministerio_cache(contenido, nombre)
    st.session_state["maestro_ministerio_df"] = ministerio_df
    st.session_state["maestro_ministerio_nombre"] = "cargado"
    st.session_state["nomenclator_parafarmacia_financiada_df"] = nomenclator_df
    if persistir:
        maestros_persistentes.guardar_archivo("ministerio", ministerio_file)


def _procesar_maestro_manual(maestro_file, persistir=False):
    contenido, nombre = _archivo_a_bytes(maestro_file)
    maestro_df = _leer_maestro_manual_cache(contenido, nombre)
    st.session_state["maestro_laboratorios_df"] = maestro_df
    st.session_state["maestro_laboratorios_nombre"] = "cargado"
    if persistir:
        maestros_persistentes.guardar_archivo("manual", maestro_file)


def _procesar_nomenclator_aemps(nomenclator_file, persistir=False):
    contenido, nombre = _archivo_a_bytes(nomenclator_file)
    nomenclator_df = _leer_nomenclator_aemps_cache(contenido, nombre)
    st.session_state["maestro_medicamentos_aemps_df"] = nomenclator_df
    st.session_state["maestro_medicamentos_aemps_nombre"] = "cargado"
    if persistir:
        maestros_persistentes.guardar_archivo("aemps", nomenclator_file)


def _limpiar_maestro_en_sesion(clave):
    claves_por_tipo = {
        "ministerio": [
            "maestro_ministerio_df",
            "maestro_ministerio_nombre",
            "nomenclator_parafarmacia_financiada_df",
        ],
        "manual": ["maestro_laboratorios_df", "maestro_laboratorios_nombre"],
        "aemps": ["maestro_medicamentos_aemps_df", "maestro_medicamentos_aemps_nombre"],
        "efg": [
            "tabla_equivalencias_efg",
            "grupos_homogeneos_efg",
            "opciones_por_grupo_efg",
            "resumen_equivalencias_efg",
            "equivalencias_efg_cargadas",
        ],
    }
    for clave_sesion in claves_por_tipo.get(clave, []):
        st.session_state.pop(clave_sesion, None)
    st.session_state.pop("_maestro_laboratorios_huella", None)
    st.session_state.pop("_maestro_laboratorios_combinado", None)


def _mostrar_estado_maestro_persistente(clave, etiqueta):
    info = maestros_persistentes.obtener_metadata(clave)
    if not info:
        st.caption("Sin base guardada.")
        return

    st.success(f"Base guardada: {info.get('original_name', etiqueta)}")
    st.caption(f"Actualizada: {info.get('updated_at', '-')}")
    if st.button(f"Eliminar {etiqueta}", key=f"eliminar_maestro_{clave}"):
        maestros_persistentes.eliminar_archivo(clave)
        _limpiar_maestro_en_sesion(clave)
        st.rerun()


def _asegurar_maestros_en_sesion():
    if "tabla_equivalencias_efg" not in st.session_state and maestros_persistentes.hay_archivo("efg"):
        try:
            _procesar_equivalencias_efg(maestros_persistentes.abrir_archivo("efg"), persistir=False)
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo cargar la base EFG guardada.", error)

    if "maestro_ministerio_df" not in st.session_state and maestros_persistentes.hay_archivo("ministerio"):
        try:
            _procesar_maestro_ministerio(maestros_persistentes.abrir_archivo("ministerio"), persistir=False)
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo cargar el nomenclátor del Ministerio guardado.", error)

    if "maestro_laboratorios_df" not in st.session_state and maestros_persistentes.hay_archivo("manual"):
        try:
            _procesar_maestro_manual(maestros_persistentes.abrir_archivo("manual"), persistir=False)
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo cargar la base manual guardada.", error)

    if "maestro_medicamentos_aemps_df" not in st.session_state and maestros_persistentes.hay_archivo("aemps"):
        try:
            _procesar_nomenclator_aemps(maestros_persistentes.abrir_archivo("aemps"), persistir=False)
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo cargar el Nomenclátor AEMPS guardado.", error)


def _obtener_maestro_laboratorios():
    _asegurar_maestros_en_sesion()

    manual_df = st.session_state.get("maestro_laboratorios_df")
    ministerio_df = st.session_state.get("maestro_ministerio_df")
    aemps_df = st.session_state.get("maestro_medicamentos_aemps_df")
    huella = tuple(
        (
            id(df),
            len(df) if df is not None else 0,
            tuple(df.columns) if df is not None and not df.empty else (),
        )
        for df in (manual_df, ministerio_df, aemps_df)
    )
    if st.session_state.get("_maestro_laboratorios_huella") == huella:
        return st.session_state.get("_maestro_laboratorios_combinado")

    piezas = []
    if manual_df is not None and not manual_df.empty:
        piezas.append(manual_df.copy())
    if ministerio_df is not None and not ministerio_df.empty:
        piezas.append(ministerio_df.copy())
    if aemps_df is not None and not aemps_df.empty:
        piezas.append(aemps_df.copy())

    if not piezas:
        st.session_state["_maestro_laboratorios_huella"] = huella
        st.session_state["_maestro_laboratorios_combinado"] = None
        return None

    combinado = pd.concat(piezas, ignore_index=True)
    combinado = combinado.drop_duplicates(subset=["cn"], keep="first").reset_index(drop=True)
    st.session_state["_maestro_laboratorios_huella"] = huella
    st.session_state["_maestro_laboratorios_combinado"] = combinado
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
        if not _validar_archivo_subido(equivalencias_file, "Equivalencias EFG"):
            equivalencias_file = None
    if equivalencias_file:
        try:
            _procesar_equivalencias_efg(equivalencias_file, persistir=True)
            st.success("Base EFG guardada para próximos accesos.")
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo leer la base de equivalencias EFG.", error)

    _mostrar_estado_maestro_persistente("efg", "Equivalencias EFG")


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
            type=["xlsx"],
            key="maestro_ministerio_file",
            help=(
                "Sube aquí el nomenclátor de facturación del Ministerio. "
                "Será la base maestra principal porque incluye código nacional, descripción, laboratorio y tipo."
            ),
        )

        if ministerio_file:
            if not _validar_archivo_subido(ministerio_file, "Nomenclátor facturación Ministerio"):
                ministerio_file = None
        if ministerio_file:
            try:
                _procesar_maestro_ministerio(ministerio_file, persistir=True)
                st.success("Nomenclátor del Ministerio guardado para próximos accesos.")
            except ValueError as error:
                _mostrar_error_procesamiento("No se pudo leer el nomenclátor del Ministerio.", error)
        _mostrar_estado_maestro_persistente("ministerio", "nomenclátor Ministerio")

    with col_manual:
        maestro_file = st.file_uploader(
            "Base maestra manual CN / laboratorio",
            type=["xlsx"],
            key="maestro_cn_laboratorio_file",
            help=(
                "Esta base manual nos servirá para completar fuentes no cubiertas por AEMPS, "
                "como parafarmacia u otros códigos propios."
            ),
        )

        if maestro_file:
            if not _validar_archivo_subido(maestro_file, "Base maestra manual"):
                maestro_file = None
        if maestro_file:
            try:
                _procesar_maestro_manual(maestro_file, persistir=True)
                st.success("Base manual guardada para próximos accesos.")
            except ValueError as error:
                _mostrar_error_procesamiento("No se pudo leer la base maestra manual.", error)
        _mostrar_estado_maestro_persistente("manual", "base manual")

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
            if not _validar_archivo_subido(
                nomenclator_file,
                "Nomenclátor AEMPS",
                extensiones=("zip", "xml"),
            ):
                nomenclator_file = None
        if nomenclator_file:
            try:
                _procesar_nomenclator_aemps(nomenclator_file, persistir=True)
                st.success("Nomenclátor AEMPS guardado para próximos accesos.")
            except ValueError as error:
                _mostrar_error_procesamiento("No se pudo leer el Nomenclátor AEMPS.", error)
        _mostrar_estado_maestro_persistente("aemps", "nomenclátor AEMPS")

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
        mostrar_tablas_efg = st.checkbox(
            "Mostrar tablas completas EFG",
            value=False,
            key="mostrar_tablas_completas_efg",
        )
        if mostrar_tablas_efg:
            if tabla_equivalencias_efg is not None and not tabla_equivalencias_efg.empty:
                st.caption("Tabla equivalencias EFG")
                st.dataframe(tabla_equivalencias_efg)
            if grupos_homogeneos_efg is not None and not grupos_homogeneos_efg.empty:
                st.caption("Grupos homogéneos")
                st.dataframe(grupos_homogeneos_efg)
            if opciones_por_grupo_efg is not None and not opciones_por_grupo_efg.empty:
                st.caption("Opciones por grupo")
                st.dataframe(opciones_por_grupo_efg)

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

    for uploaded_file in _filtrar_archivos_validos(uploaded_files, f"Albaranes {proveedor} {tipo_compra}"):
        df_temp = normalize_columns(load_excel(uploaded_file))
        df_temp.columns = [c.lower().strip() for c in df_temp.columns]
        df_temp["proveedor"] = proveedor
        df_temp["tipo_compra"] = tipo_compra

        col_albaran = _buscar_columna_albaran(df_temp.columns)
        if col_albaran:
            df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

        df_temp = parse_sections(df_temp)
        df_temp = _enriquecer_con_maestro(df_temp)
        df_temp = _aplicar_clasificaciones_transversales(
            df_temp,
            st.session_state.get("nomenclator_parafarmacia_financiada_df"),
        )
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
        total_bruto = df_tipo["bruto"].sum()
        total_neto = df_tipo["neto"].sum()
        total_abonos = abonos["neto"].sum()

        descuento = (total_bruto - total_neto) / total_bruto * 100 if total_bruto else 0

        bases_iva = _calcular_bases_iva_albaranes(df_tipo)
        _tarjetas_metricas([
            {"label": "Líneas", "value": len(df_tipo)},
            {"label": "Unidades", "value": int(df_tipo["unidades"].sum())},
            {"label": "Bruto", "value": f"{total_bruto:.1f} €"},
            {"label": "Neto", "value": f"{total_neto:.1f} €"},
            {"label": "Desc %", "value": f"{descuento:.2f}%"},
            {"label": "Abonos", "value": f"{abs(total_abonos):.1f} €"},
            {"label": "Base IVA 4%", "value": f"{bases_iva['base_iva_4']:.2f} €"},
            {"label": "Base IVA 10%", "value": f"{bases_iva['base_iva_10']:.2f} €"},
            {"label": "Base IVA 21%", "value": f"{bases_iva['base_iva_21']:.2f} €"},
        ])

        etiqueta = "Ver detalle compras goteo" if tipo == "goteo" else "Ver detalle compras transfer"
        with st.expander(etiqueta, expanded=False):
            _mostrar_dataframe_completo(_vista_compras_ligera(df_tipo))


def _guardar_analisis_distribuidora(proveedor_id, analisis):
    analisis_actuales = st.session_state.get("analisis_distribuidora", {})
    if not isinstance(analisis_actuales, dict):
        analisis_actuales = {}
    analisis_actuales[proveedor_id] = analisis
    st.session_state["analisis_distribuidora"] = analisis_actuales


def _mostrar_analisis_clubes(analisis_clubes):
    if not analisis_clubes or not analisis_clubes.get("ok"):
        mensaje = (analisis_clubes or {}).get("mensaje")
        if mensaje:
            st.info(mensaje)
        return

    st.subheader("Análisis de clubes y escalados")
    _tarjetas_metricas([
        {"label": "Compra total club", "value": f"{analisis_clubes.get('compra_total_club', 0):.2f} €"},
        {"label": "Compra sin liquidación", "value": f"{analisis_clubes.get('compra_sin_liquidacion', 0):.2f} €"},
        {"label": "% sin liquidación", "value": f"{analisis_clubes.get('pct_club_sin_liquidacion', 0):.2f}%"},
        {
            "label": "Pérdida estimada vs condición",
            "value": f"{analisis_clubes.get('perdida_vs_descuento_habitual', 0):.2f} €",
        },
    ])

    descuento_ref = analisis_clubes.get("descuento_habitual_referencia_pct")
    if descuento_ref is not None:
        metodo_ref = analisis_clubes.get("descuento_habitual_referencia_metodo")
        if metodo_ref == "descuento_real_simulado_especialidad_con_clubes":
            st.caption(
                "Referencia usada: descuento real simulado de especialidad "
                f"{float(descuento_ref):.2f}% incorporando clubes sin liquidación y diluyendo cargos."
            )
        else:
            st.caption(f"Referencia usada: descuento habitual de especialidad {float(descuento_ref):.2f}%.")

    for alerta in analisis_clubes.get("alertas", []):
        st.warning(alerta)

    oportunidades = analisis_clubes.get("oportunidades_siguiente_tramo", pd.DataFrame())
    if oportunidades is not None and not oportunidades.empty:
        st.caption("Oportunidades cercanas a siguiente tramo")
        _mostrar_tabla_dashboard(oportunidades)

    escalados = analisis_clubes.get("escalados", pd.DataFrame())
    if escalados is not None and not escalados.empty:
        st.caption("Detalle de escalados")
        _mostrar_tabla_dashboard(escalados)

    detalle = analisis_clubes.get("detalle_club", pd.DataFrame())
    if detalle is not None and not detalle.empty:
        st.caption("Detalle de líneas club")
        _mostrar_tabla_dashboard(detalle)


def _mostrar_tarjeta_condicion_comercial(condicion_detectada=None, analisis_faceta=None):
    if not condicion_detectada and not analisis_faceta:
        st.info("ℹ️ No se han detectado condiciones comerciales específicas en los albaranes cargados.")
        st.caption("Puedes continuar con la subida de facturas.")
        return

    resumen_faceta = (analisis_faceta or {}).get("resumen", {})
    tipo_74 = resumen_faceta.get("tipo_albaran_74")
    tipo_condicion = {
        1: "liquidación club",
        2: "margen tramo fijo",
        3: "tramo cero / ajuste escala",
    }.get(tipo_74, tipo_74 or (condicion_detectada or {}).get("acronimo"))
    cargo_total = float(resumen_faceta.get("margen_tramo_fijo_total", 0) or 0)
    lineas_afectadas = resumen_faceta.get("lineas_tramo_fijo")

    st.success("✅ Condición comercial detectada")
    st.caption("Se ha detectado una condición aplicable en los albaranes cargados.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Tipo", tipo_condicion or "condición aplicable")
    c2.metric("Franquicia detectada", f"{cargo_total:.2f} €" if cargo_total else "-")
    c3.metric("Líneas afectadas", "-" if lineas_afectadas is None else lineas_afectadas)
    st.caption("Puedes continuar con la subida de facturas. El detalle técnico se incluirá en el informe generado.")


def _mostrar_condiciones_en_informe(analisis):
    condiciones = (analisis or {}).get("condiciones_comerciales") or {}
    resumen = condiciones.get("resumen", pd.DataFrame())
    detalles = condiciones.get("detalles", {})

    if resumen is None or resumen.empty:
        return

    st.subheader("Condiciones comerciales y franquicias")
    _mostrar_tabla_dashboard(resumen)

    if detalles:
        with st.expander("Ver detalle técnico de condiciones", expanded=False):
            for nombre, detalle in detalles.items():
                if detalle is not None and not detalle.empty:
                    st.caption(nombre.replace("_", " ").title())
                    _mostrar_tabla_dashboard(detalle)


def _mostrar_analisis_distribuidora(analisis):
    if not analisis or not analisis.get("ok"):
        st.warning((analisis or {}).get("mensaje", "No hay análisis disponible."))
        return

    resumen = analisis.get("resumen", {})
    st.subheader(f"Análisis distribuidora · {analisis.get('proveedor', '')}")

    gastos_resumen = analisis.get("gastos_resumen", {})
    descuento = resumen.get("descuento_medio_general")
    volumen = analisis.get("volumen_compra", {})
    descuentos = analisis.get("descuentos_reales", {})
    _tarjetas_metricas([
        {"label": "Compra bruta", "value": f"{resumen.get('compra_bruta_total', 0):.2f} €"},
        {"label": "Compra neta", "value": f"{resumen.get('compra_neta_total', 0):.2f} €"},
        {"label": "Gastos totales", "value": f"{gastos_resumen.get('total_gastos', 0):.2f} €"},
        {"label": "Desc. medio", "value": "-" if descuento is None else f"{descuento:.2f}%"},
        {
            "label": "Compra mensual",
            "value": "-" if volumen.get("compra_total_mensual") is None else f"{volumen.get('compra_total_mensual'):.2f} €",
        },
        {"label": "Abonos", "value": f"{abs(float(resumen.get('abonos_totales', 0) or 0)):.2f} €"},
        {"label": "% gastos/compra", "value": f"{gastos_resumen.get('pct_gastos_sobre_compra', 0):.2f}%"},
        {
            "label": "Goteo aparente",
            "value": "-" if descuentos.get("goteo_aparente_pct") is None else f"{descuentos.get('goteo_aparente_pct'):.2f}%",
        },
        {
            "label": "Goteo real",
            "value": "-" if descuentos.get("goteo_real_pct") is None else f"{descuentos.get('goteo_real_pct'):.2f}%",
        },
    ])

    periodo = resumen.get("periodo")
    if periodo:
        st.caption(f"Periodo analizado: {periodo.get('desde')} a {periodo.get('hasta')}")

    perdida_puntos = descuentos.get("perdida_puntos_goteo")
    if perdida_puntos is not None and perdida_puntos > 0:
        st.info(f"El goteo pierde {perdida_puntos:.2f} puntos tras cargos frente al descuento aparente.")

    desglose = analisis.get("desglose_por_tipo", analisis.get("desglose", pd.DataFrame()))
    if desglose is not None and not desglose.empty:
        st.caption("Desglose por tipo de compra")
        _mostrar_tabla_dashboard(desglose)

    cargos = analisis.get("gastos_ocultos", analisis.get("cargos", pd.DataFrame()))
    if cargos is not None and not cargos.empty:
        st.caption("Gastos y costes ocultos")
        _mostrar_tabla_dashboard(cargos)

    especialidad_cara_resumen = analisis.get("especialidad_cara_resumen", {})
    if especialidad_cara_resumen and especialidad_cara_resumen.get("lineas_detectadas", 0) > 0:
        st.subheader("Especialidad cara / RDL 4/2010")
        _tarjetas_metricas([
            {"label": "Líneas", "value": especialidad_cara_resumen.get("lineas_detectadas", 0)},
            {"label": "Unidades", "value": f"{especialidad_cara_resumen.get('unidades', 0):.2f}"},
            {"label": "Bruto total", "value": f"{especialidad_cara_resumen.get('bruto_total', 0):.2f} €"},
            {"label": "Neto total", "value": f"{especialidad_cara_resumen.get('neto_total', 0):.2f} €"},
            {
                "label": "Descuento total",
                "value": f"{especialidad_cara_resumen.get('descuento_total_euros', 0):.2f} €",
            },
            {
                "label": "Desc. medio/línea",
                "value": f"{especialidad_cara_resumen.get('descuento_medio_linea_euros', 0):.2f} €",
            },
            {"label": "Base IVA4 total", "value": f"{especialidad_cara_resumen.get('base_iva4_total', 0):.2f} €"},
            {
                "label": "Base sujeta ajuste",
                "value": f"{especialidad_cara_resumen.get('base_iva4_sujeta_ajuste', 0):.2f} €",
            },
        ])
        _mostrar_tabla_dashboard(pd.DataFrame([{
            "base_iva4_total": especialidad_cara_resumen.get("base_iva4_total", 0),
            "base_iva4_especialidad_cara": especialidad_cara_resumen.get("base_iva4_especialidad_cara", 0),
            "base_iva4_sujeta_ajuste": especialidad_cara_resumen.get("base_iva4_sujeta_ajuste", 0),
        }]))

    parafarmacia_financiada = analisis.get("parafarmacia_financiada", {}) or {}
    resumen_para_fin = parafarmacia_financiada.get("resumen", {})
    if resumen_para_fin and resumen_para_fin.get("lineas_detectadas", 0) > 0:
        st.subheader("Parafarmacia financiada")
        _tarjetas_metricas([
            {"label": "Líneas", "value": resumen_para_fin.get("lineas_detectadas", 0)},
            {"label": "Unidades", "value": f"{resumen_para_fin.get('unidades', 0):.2f}"},
            {"label": "Bruto total", "value": f"{resumen_para_fin.get('bruto_total', 0):.2f} €"},
            {"label": "Neto total", "value": f"{resumen_para_fin.get('neto_total', 0):.2f} €"},
            {"label": "Descuento total", "value": f"{resumen_para_fin.get('descuento_total_euros', 0):.2f} €"},
            {"label": "Desc. medio", "value": f"{resumen_para_fin.get('descuento_medio_euros', 0):.2f} €"},
            {"label": "% compra total", "value": f"{resumen_para_fin.get('porcentaje_sobre_compra_total', 0):.2f}%"},
            {"label": "% parafarmacia", "value": f"{resumen_para_fin.get('porcentaje_sobre_parafarmacia_total', 0):.2f}%"},
        ])
        _mostrar_tabla_dashboard(pd.DataFrame([{
            "base_parafarmacia_total": resumen_para_fin.get("base_parafarmacia_total", 0),
            "base_parafarmacia_financiada": resumen_para_fin.get("base_parafarmacia_financiada", 0),
            "base_parafarmacia_no_financiada": resumen_para_fin.get("base_parafarmacia_no_financiada", 0),
            "base_parafarmacia_sujeta_condiciones": resumen_para_fin.get("base_parafarmacia_sujeta_condiciones", 0),
        }]))
        top_labs = parafarmacia_financiada.get("top_laboratorios", pd.DataFrame())
        if top_labs is not None and not top_labs.empty:
            st.caption("Top laboratorios parafarmacia financiada")
            _mostrar_tabla_dashboard(top_labs)
        top_cn = parafarmacia_financiada.get("top_cn", pd.DataFrame())
        if top_cn is not None and not top_cn.empty:
            st.caption("Top CN parafarmacia financiada")
            _mostrar_tabla_dashboard(top_cn)

    operativa = analisis.get("operativa_proveedor", {})
    if operativa:
        st.caption("Operativa proveedor")
        _mostrar_tabla_dashboard(pd.DataFrame([operativa]))

    _mostrar_analisis_clubes(analisis.get("clubes"))

    imputaciones_transfer = analisis.get("imputaciones_transfer_manuales", pd.DataFrame())
    if imputaciones_transfer is not None and not imputaciones_transfer.empty:
        st.subheader("Imputaciones manuales de abonos transfer")
        _mostrar_tabla_dashboard(imputaciones_transfer)

    _mostrar_condiciones_en_informe(analisis)

    top_impacto = analisis.get("top_impacto", pd.DataFrame())
    if top_impacto is not None and not top_impacto.empty:
        st.caption("Top impacto coste aparente vs coste real")
        _mostrar_tabla_dashboard(top_impacto)
    else:
        st.info(analisis.get("top_impacto_mensaje") or "Top impacto pendiente: no hay costes imputados suficientes para calcular diferencias.")

    diagnostico = analisis.get("diagnostico", {})
    if diagnostico:
        for alerta in diagnostico.get("alertas", []):
            st.warning(alerta)
        for oportunidad in diagnostico.get("oportunidades", []):
            st.info(oportunidad)


def _descuento_goteo_real_desde_resumen(resumen_bidafarma=None, analisis_distribuidora=None):
    if resumen_bidafarma:
        descuento = (resumen_bidafarma.get("metricas") or {}).get("goteo_puro_descuento_real")
        if descuento is not None:
            return descuento

    desglose = (analisis_distribuidora or {}).get("desglose", pd.DataFrame())
    if desglose is not None and not desglose.empty and "bloque" in desglose.columns:
        fila = desglose[desglose["bloque"].astype(str).str.lower().eq("goteo_puro")]
        if not fila.empty and "descuento_real_final_pct" in fila.columns:
            return fila["descuento_real_final_pct"].iloc[0]
    return None


def _render_bloque_clubes(proveedor_id, df, descuento_goteo_real=None):
    df_club = club_analysis.detectar_compras_club(df)
    if df_club.empty:
        return None

    st.subheader("Análisis de clubes y escalados")
    st.caption("Se han detectado líneas de club o selección genéricos.")
    documento_clubes = st.file_uploader(
        "Sube documento de clubes / selección genéricos",
        type=["xlsx"],
        key=f"{proveedor_id}_clubes_escalados",
    )

    df_documento = None
    if documento_clubes:
        if _validar_archivo_subido(documento_clubes, "Documento de clubes / selección genéricos"):
            try:
                df_documento = load_excel(documento_clubes)
            except Exception as error:
                _mostrar_error_procesamiento("No se pudo leer el documento de clubes / selección genéricos.", error)

    analisis_clubes = club_analysis.analizar_clubes(
        df,
        df_escalados=df_documento,
        df_liquidaciones=df_documento,
        proveedor=proveedor_id,
        descuento_goteo_real=descuento_goteo_real,
    )
    _mostrar_analisis_clubes(analisis_clubes)
    return analisis_clubes


def _obtener_analisis_distribuidora_principal():
    analisis_distribuidora = st.session_state.get("analisis_distribuidora", {})
    if isinstance(analisis_distribuidora, dict):
        for analisis in analisis_distribuidora.values():
            if analisis and analisis.get("ok"):
                return analisis
    elif analisis_distribuidora and analisis_distribuidora.get("ok"):
        return analisis_distribuidora
    return None


def _mostrar_lista_recomendaciones(titulo, elementos):
    elementos = elementos or []
    if not elementos:
        return
    st.markdown(f"**{titulo}**")
    for elemento in elementos[:5]:
        st.write(f"- {elemento}")


def render_recomendaciones_ia():
    analisis = _obtener_analisis_distribuidora_principal()
    resumen_final = st.session_state.get("resumen_final_auditoria")
    if not analisis and not resumen_final:
        return

    st.header("Recomendaciones asistidas por IA")
    st.caption(
        "Se generan con datos agregados y anonimizados. No se envían facturas, albaranes completos ni documentos originales."
    )

    if st.button("Generar recomendaciones IA", key="generar_recomendaciones_ia"):
        if not analisis:
            st.warning("Genera primero un análisis de distribuidora para alimentar las recomendaciones.")
            return

        contexto = st.session_state.get("contexto_farmacia", {})
        analisis_ventas = st.session_state.get("analisis_ventas")
        analisis_stock = st.session_state.get("analisis_stock")

        recomendaciones = ai_advisor.generar_recomendaciones_ia(
            contexto,
            analisis,
            analisis_ventas=analisis_ventas,
            analisis_stock=analisis_stock,
        )
        st.session_state["recomendaciones_ia"] = recomendaciones

    recomendaciones = st.session_state.get("recomendaciones_ia")
    if not recomendaciones:
        return

    st.subheader("Resumen ejecutivo")
    st.info(recomendaciones.get("resumen_ejecutivo", "Sin resumen disponible."))

    col1, col2 = st.columns(2)
    with col1:
        _mostrar_lista_recomendaciones("Oportunidades", recomendaciones.get("oportunidades"))
        _mostrar_lista_recomendaciones("Recomendaciones de pedido", recomendaciones.get("recomendaciones_pedido"))
    with col2:
        _mostrar_lista_recomendaciones("Riesgos detectados", recomendaciones.get("riesgos_detectados"))
        _mostrar_lista_recomendaciones("Acciones prioritarias", recomendaciones.get("acciones_prioritarias"))

    _mostrar_lista_recomendaciones("Recomendaciones de negociación", recomendaciones.get("recomendaciones_negociacion"))
    _mostrar_lista_recomendaciones("Advertencias", recomendaciones.get("advertencias"))


def _serie_numerica(df, columna):
    if df is None or columna not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index if df is not None else None)
    return pd.to_numeric(df[columna], errors="coerce").fillna(0.0)


def _buscar_columna_por_tokens(df, tokens_obligatorios, tokens_excluir=None):
    if df is None:
        return None
    tokens_excluir = tokens_excluir or []
    for columna in df.columns:
        nombre = _normalizar_nombre_columna(columna)
        if all(token in nombre for token in tokens_obligatorios) and not any(token in nombre for token in tokens_excluir):
            return columna
    return None


def _calcular_bases_iva_albaranes(df):
    bases = {
        "base_iva_4": 0.0,
        "base_iva_10": 0.0,
        "base_iva_21": 0.0,
    }
    if df is None or df.empty:
        return bases

    col_iva = "iva" if "iva" in df.columns else _buscar_columna_por_tokens(df, ["iva"], ["pvp", "precio"])
    if col_iva is None or "neto" not in df.columns:
        return bases

    trabajo = df.copy()
    trabajo["_iva_base_validacion"] = pd.to_numeric(trabajo[col_iva], errors="coerce").fillna(-1)
    trabajo["_neto_base_validacion"] = pd.to_numeric(trabajo["neto"], errors="coerce").fillna(0.0)

    for tipo_iva in [4, 10, 21]:
        mask = trabajo["_iva_base_validacion"].sub(tipo_iva).abs().le(0.01)
        bases[f"base_iva_{tipo_iva}"] = round(float(trabajo.loc[mask, "_neto_base_validacion"].sum()), 2)

    return bases


def _mostrar_metricas_bases_iva(bases, prefijo="Base"):
    b1, b2, b3 = st.columns(3)
    b1.metric(f"{prefijo} IVA 4%", f"{float(bases.get('base_iva_4', 0) or 0):.2f} €")
    b2.metric(f"{prefijo} IVA 10%", f"{float(bases.get('base_iva_10', 0) or 0):.2f} €")
    b3.metric(f"{prefijo} IVA 21%", f"{float(bases.get('base_iva_21', 0) or 0):.2f} €")


def _mostrar_validacion_economica_factura(df_albaranes, resultado_factura, etiqueta="factura"):
    if df_albaranes is None or resultado_factura is None:
        return

    total_albaranes = round(float(_serie_numerica(df_albaranes, "neto").sum()), 2)
    total_factura = resultado_factura.get("total_albaranes_factura")
    bases_albaranes = _calcular_bases_iva_albaranes(df_albaranes)
    bases_factura = resultado_factura.get("bases_iva") or {}

    st.subheader("Validación bases imponibles e IVA")
    f1, f2 = st.columns(2)
    f1.metric("Total neto albaranes", f"{total_albaranes:.2f} €")
    f2.metric(
        "Total neto factura",
        "-" if total_factura is None else f"{float(total_factura):.2f} €",
    )

    st.caption("Bases detectadas en albaranes")
    _mostrar_metricas_bases_iva(bases_albaranes, prefijo="Albaranes")
    st.caption("Bases detectadas en factura")
    _mostrar_metricas_bases_iva(bases_factura, prefijo="Factura")

    validaciones = []

    def agregar_validacion(nombre, valor_albaranes, valor_factura):
        if valor_factura is None:
            validaciones.append({
                "validacion": nombre,
                "albaranes": round(float(valor_albaranes or 0), 2),
                "factura": None,
                "diferencia": None,
                "estado": "No disponible en factura",
            })
            return
        diferencia = round(float(valor_albaranes or 0) - float(valor_factura or 0), 2)
        validaciones.append({
            "validacion": nombre,
            "albaranes": round(float(valor_albaranes or 0), 2),
            "factura": round(float(valor_factura or 0), 2),
            "diferencia": diferencia,
            "estado": "Validado" if abs(diferencia) <= 0.05 else "Diferencia detectada",
        })

    agregar_validacion("Total neto", total_albaranes, total_factura)
    agregar_validacion("Base IVA 4%", bases_albaranes["base_iva_4"], bases_factura.get("base_iva_4"))
    agregar_validacion("Base IVA 10%", bases_albaranes["base_iva_10"], bases_factura.get("base_iva_10"))
    agregar_validacion("Base IVA 21%", bases_albaranes["base_iva_21"], bases_factura.get("base_iva_21"))

    df_validaciones = pd.DataFrame(validaciones)
    diferencias = df_validaciones[
        df_validaciones["estado"].eq("Diferencia detectada")
    ].copy()

    if diferencias.empty:
        st.success(f"✔ Validado: los totales y bases IVA de {etiqueta} cuadran con los albaranes.")
    else:
        detalle = "; ".join(
            f"{fila['validacion']}: {fila['diferencia']:.2f} €"
            for _, fila in diferencias.iterrows()
        )
        st.error(f"⚠ Diferencia detectada en {etiqueta}: {detalle}")

    st.dataframe(df_validaciones)


def _mostrar_validacion_economica_factura(df_albaranes, resultado_factura, etiqueta="factura"):
    if df_albaranes is None or resultado_factura is None:
        return

    total_albaranes = round(float(_serie_numerica(df_albaranes, "neto").sum()), 2)
    total_factura = resultado_factura.get("total_albaranes_factura")
    bases_albaranes = _calcular_bases_iva_albaranes(df_albaranes)
    bases_factura = resultado_factura.get("bases_iva") or {}

    validaciones = []

    def agregar_validacion(nombre, valor_albaranes, valor_factura):
        if valor_factura is None:
            validaciones.append({
                "validacion": nombre,
                "albaranes": round(float(valor_albaranes or 0), 2),
                "factura": None,
                "diferencia": None,
                "estado": "No disponible en factura",
            })
            return
        diferencia = round(float(valor_albaranes or 0) - float(valor_factura or 0), 2)
        validaciones.append({
            "validacion": nombre,
            "albaranes": round(float(valor_albaranes or 0), 2),
            "factura": round(float(valor_factura or 0), 2),
            "diferencia": diferencia,
            "estado": "Validado" if abs(diferencia) <= 0.05 else "Diferencia detectada",
        })

    agregar_validacion("Total neto", total_albaranes, total_factura)
    agregar_validacion("Base IVA 4%", bases_albaranes["base_iva_4"], bases_factura.get("base_iva_4"))
    agregar_validacion("Base IVA 10%", bases_albaranes["base_iva_10"], bases_factura.get("base_iva_10"))
    agregar_validacion("Base IVA 21%", bases_albaranes["base_iva_21"], bases_factura.get("base_iva_21"))

    df_validaciones = pd.DataFrame(validaciones)
    diferencias = df_validaciones[df_validaciones["estado"].eq("Diferencia detectada")].copy()

    if diferencias.empty:
        st.success(f"Validado: totales y bases IVA de {etiqueta} cuadran con los albaranes.")
        return

    detalle = "; ".join(
        f"{fila['validacion']}: {fila['diferencia']:.2f} €"
        for _, fila in diferencias.iterrows()
    )
    st.error(f"Diferencia detectada en {etiqueta}: {detalle}")
    st.dataframe(df_validaciones)


def _descuento_pct(bruto_total, coste_total):
    if bruto_total <= 0:
        return 0.0
    return round((1 - (coste_total / bruto_total)) * 100, 2)


def _calcular_gastos_plataformas(resumen_consumos):
    if not resumen_consumos:
        return 0.0

    plataformas = resumen_consumos.get("plataformas")
    if plataformas is None or plataformas.empty:
        return 0.0

    total = 0.0
    if "cargo_eur" in plataformas.columns:
        total += pd.to_numeric(plataformas["cargo_eur"], errors="coerce").fillna(0).sum()

    pendientes = plataformas.copy()
    if "cargo_eur" in pendientes.columns:
        pendientes = pendientes[pd.to_numeric(pendientes["cargo_eur"], errors="coerce").fillna(0) == 0]

    if {"venta_bruta", "cargo_pct"}.issubset(pendientes.columns):
        total += (
            pd.to_numeric(pendientes["venta_bruta"], errors="coerce").fillna(0)
            * (pd.to_numeric(pendientes["cargo_pct"], errors="coerce").fillna(0) / 100)
        ).sum()

    if "cuota" in plataformas.columns:
        total += pd.to_numeric(plataformas["cuota"], errors="coerce").fillna(0).sum()

    return round(float(total), 2)


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
    for indice, fila in abonos_transfer.reset_index(drop=True).iterrows():
        concepto = str(fila.get("concepto", "")).strip()
        concepto_norm = _normalizar_texto_match(concepto)
        importe = float(fila.get("importe", 0) or 0)
        id_abono = transfer_manual_mapping._id_abono(indice, concepto, importe)

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
                "id_abono": id_abono,
                "concepto": concepto,
                "importe": round(importe, 2),
                "laboratorios_detectados": " | ".join(labs_detectados),
            }
        )

    detalle = pd.DataFrame(registros)
    laboratorios = sorted(laboratorios_detectados_total)

    return {"laboratorios": laboratorios, "detalle": detalle}


def _analisis_transfer_logistica(df_transfer, resultado_transfer, imputaciones_manuales=None):
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

    detalle["bonificado_transfer_auto"] = detalle["albaran"].astype(str).isin(albaranes_bonificados)
    detalle = transfer_manual_mapping.aplicar_imputaciones_manuales_transfer(detalle, imputaciones_manuales)
    detalle["bonificado_transfer_manual"] = detalle.get(
        "bonificado_transfer_manual",
        pd.Series(False, index=detalle.index),
    ).fillna(False).astype(bool)
    detalle["tiene_bonificacion_logistica"] = (
        detalle["bonificado_transfer_auto"] | detalle["bonificado_transfer_manual"]
    )
    detalle["motivo_bonificacion_transfer"] = ""
    detalle.loc[detalle["bonificado_transfer_auto"], "motivo_bonificacion_transfer"] = "auto_laboratorio"
    detalle.loc[detalle["bonificado_transfer_manual"], "motivo_bonificacion_transfer"] = "manual"
    detalle.loc[
        detalle["bonificado_transfer_auto"] & detalle["bonificado_transfer_manual"],
        "motivo_bonificacion_transfer",
    ] = "auto_laboratorio + manual"
    detalle["aplica_cargo_logistico_transfer"] = ~detalle["tiene_bonificacion_logistica"]
    detalle["cargo_transfer_base"] = 0.0
    detalle["cargo_transfer_iva"] = 0.0
    detalle["cargo_transfer_total"] = 0.0
    detalle["neto_con_cargo_transfer"] = detalle["neto"]
    detalle["abono_logistico_laboratorio"] = 0.0

    mask_elegible = (detalle["neto"] > 0) & (detalle["aplica_cargo_logistico_transfer"])
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
            "albaranes_bonificados_manual": sorted(
                detalle.loc[detalle["bonificado_transfer_manual"], "albaran"].dropna().astype(str).unique().tolist()
            ),
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
        "imputaciones_manuales": transfer_manual_mapping.generar_resumen_imputaciones_transfer(imputaciones_manuales),
    }


def _render_imputacion_manual_transfer(df_transfer, resultado_transfer, asociaciones_auto):
    pendientes = transfer_manual_mapping.detectar_abonos_transfer_no_asociados(
        resultado_transfer,
        asociaciones_auto=asociaciones_auto,
    )
    if not pendientes:
        return st.session_state.get("imputaciones_transfer_manuales", [])

    st.subheader("Abonos transfer pendientes de imputación manual")
    if df_transfer is None or df_transfer.empty:
        st.warning("Hay abonos transfer pendientes, pero no hay albaranes transfer cargados para imputarlos.")
        return st.session_state.get("imputaciones_transfer_manuales", [])

    selector = transfer_manual_mapping.preparar_albaranes_transfer_para_selector(df_transfer)
    if selector.empty:
        st.warning("No se han encontrado albaranes transfer válidos para la imputación manual.")
        return st.session_state.get("imputaciones_transfer_manuales", [])

    imputaciones = list(st.session_state.get("imputaciones_transfer_manuales", []))
    imputaciones_por_id = {imp.get("id_abono"): imp for imp in imputaciones}
    etiquetas = dict(zip(selector["albaran"], selector["etiqueta"]))
    opciones = selector["albaran"].astype(str).tolist()

    for abono in pendientes:
        id_abono = abono["id_abono"]
        existente = imputaciones_por_id.get(id_abono, {})
        with st.expander(f"Abono: {abono['concepto']} · {abono['importe_abono']:.2f} €", expanded=True):
            seleccion = st.multiselect(
                "Selecciona albaranes afectados",
                options=opciones,
                default=[alb for alb in existente.get("albaranes_asociados", []) if alb in opciones],
                format_func=lambda alb: etiquetas.get(alb, str(alb)),
                key=f"transfer_manual_{id_abono}",
            )

            validacion = transfer_manual_mapping.calcular_validacion_abono_manual(
                abono["importe_abono"],
                seleccion,
                df_transfer,
            )

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Base seleccionada", f"{validacion['base_manual']:.2f} €")
            v2.metric("Cargo teórico 1,7%", f"{validacion['cargo_teorico_1_7']:.2f} €")
            v3.metric("Importe abono", f"{validacion['importe_abono']:.2f} €")
            v4.metric("Diferencia", f"{validacion['diferencia']:.2f} €")

            if validacion["usa_neto_fallback"]:
                st.warning("Algún albarán no tiene bruto válido; se ha usado neto como base de respaldo.")

            if validacion["estado_validacion"] == "cuadra":
                st.success("El abono coincide con el 1,7% de los albaranes seleccionados.")
            else:
                st.warning("El abono no coincide exactamente con el 1,7% de los albaranes seleccionados.")

            if st.button("Confirmar imputación manual", key=f"confirmar_transfer_manual_{id_abono}"):
                nueva = {
                    **abono,
                    "estado_asociacion": "asociado_manual",
                    "albaranes_asociados": seleccion,
                    **validacion,
                }
                imputaciones = [imp for imp in imputaciones if imp.get("id_abono") != id_abono]
                imputaciones.append(nueva)
                st.session_state["imputaciones_transfer_manuales"] = imputaciones
                st.success("Imputación manual guardada temporalmente.")
                st.rerun()

    return st.session_state.get("imputaciones_transfer_manuales", [])


def _lineas_elegibles_goteo_puro(df):
    if df is None or df.empty:
        return pd.DataFrame()

    detalle = df.copy()
    detalle["bruto"] = _serie_numerica(detalle, "bruto")
    detalle["neto"] = _serie_numerica(detalle, "neto")
    detalle["iva"] = _serie_numerica(detalle, "iva")
    serie_vacia = pd.Series("", index=detalle.index)
    detalle["descripcion"] = detalle.get("descripcion", serie_vacia).astype(str)
    descripcion_norm = detalle["descripcion"].str.lower()
    no_especialidad_cara = ~detalle.get(
        "es_especialidad_cara",
        pd.Series(False, index=detalle.index),
    ).fillna(False).astype(bool)
    no_parafarmacia_financiada = ~detalle.get(
        "es_parafarmacia_financiada",
        pd.Series(False, index=detalle.index),
    ).fillna(False).astype(bool)
    tipo_compra_norm = detalle.get("tipo_compra", serie_vacia).astype(str).str.lower().str.strip()
    seccion_norm = detalle.get("seccion_albaran", serie_vacia).astype(str).str.lower().str.strip()

    mask = (
        tipo_compra_norm.eq("goteo")
        & seccion_norm.isin(["especialidad", "parafarmacia"])
        & no_especialidad_cara
        & no_parafarmacia_financiada
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
    serie_vacia = pd.Series("", index=df_base.index)
    descripcion_norm = df_base.get("descripcion", serie_vacia).astype(str).str.lower()
    no_especialidad_cara = ~df_base.get(
        "es_especialidad_cara",
        pd.Series(False, index=df_base.index),
    ).fillna(False).astype(bool)
    tipo_compra_norm = df_base.get("tipo_compra", serie_vacia).astype(str).str.lower().str.strip()
    seccion_norm = df_base.get("seccion_albaran", serie_vacia).astype(str).str.lower().str.strip()

    mask_elegible = (
        tipo_compra_norm.eq("goteo")
        & df_base["iva"].eq(4)
        & seccion_norm.eq("especialidad")
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

    serie_vacia = pd.Series("", index=lineas_resumen.index)
    descripcion_norm = lineas_resumen.get("descripcion", serie_vacia).astype(str).str.lower().str.strip()
    seccion_norm = lineas_resumen.get("seccion_albaran", serie_vacia).astype(str).str.lower().str.strip()
    tipo_compra_norm = lineas_resumen.get("tipo_compra", serie_vacia).astype(str).str.lower().str.strip()
    texto_club = pd.Series("", index=lineas_resumen.index, dtype="object")
    for columna in lineas_resumen.columns:
        nombre = _normalizar_nombre_columna(columna)
        if any(token in nombre for token in ["categoria", "descuento", "cargo", "dc"]):
            texto_club = texto_club + " " + lineas_resumen[columna].astype(str).str.lower()

    mask_bitransfer = seccion_norm.eq("bitransfer")
    mask_club = seccion_norm.eq("club") | descripcion_norm.str.contains("club", na=False) | texto_club.str.contains("club", na=False)
    mask_avantia = seccion_norm.eq("avantia") | descripcion_norm.str.contains("avantia", na=False)
    mask_especialidad_cara = lineas_resumen.get(
        "es_especialidad_cara",
        pd.Series(False, index=lineas_resumen.index),
    ).fillna(False).astype(bool)
    mask_parafarmacia_financiada = lineas_resumen.get(
        "es_parafarmacia_financiada",
        pd.Series(False, index=lineas_resumen.index),
    ).fillna(False).astype(bool)
    mask_goteo_puro = (
        tipo_compra_norm.eq("goteo")
        & seccion_norm.isin(["especialidad", "parafarmacia"])
        & ~mask_bitransfer
        & ~mask_club
        & ~mask_avantia
        & ~mask_especialidad_cara
        & ~mask_parafarmacia_financiada
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

    def agregar_bloque_parafarmacia_financiada():
        bloque = lineas_resumen[mask_parafarmacia_financiada].copy()
        if bloque.empty:
            return None

        bruto = _sumar_columna_real(bloque, "bruto")
        neto = _sumar_columna_real(bloque, "neto")
        unidades = _sumar_columna_real(bloque, "unidades")
        descuento_total = _sumar_columna_real(bloque, "descuento_parafarmacia_financiada_euros")
        descuento_medio = descuento_total / unidades if unidades else 0.0
        resumen_bloques.append({
            "bloque": "Parafarmacia financiada",
            "lineas": len(bloque),
            "unidades": round(unidades, 2),
            "bruto_compra": round(bruto, 2),
            "neto_inicial": round(neto, 2),
            "coste_ajustado": round(neto, 2),
            "descuento_medio_pct": _descuento_pct(bruto, neto),
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
    bloque_parafarmacia_financiada = agregar_bloque_parafarmacia_financiada()
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
        coste_extra=0.0 if not analisis_avantia else float(analisis_avantia["resumen"]["coste_total_avantia"]),
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
    _mostrar_tarjeta_parafarmacia_financiada(df)

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
    if factura_normal and not _validar_archivo_subido(factura_normal, f"Factura normal {nombre_proveedor}"):
        factura_normal = None
    if factura_transfer and not _validar_archivo_subido(factura_transfer, f"Factura transfer {nombre_proveedor}"):
        factura_transfer = None

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
    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get(proveedor_id)
    descuento_goteo_real = _descuento_goteo_real_desde_resumen(analisis_distribuidora=analisis_guardado)
    analisis_clubes = _render_bloque_clubes(proveedor_id, df, descuento_goteo_real=descuento_goteo_real)

    if st.button("Generar análisis distribuidora", key=f"generar_analisis_{proveedor_id}"):
        analisis = distributor_analysis.generar_analisis_distribuidora(
            df,
            proveedor=proveedor_id,
            analisis_clubes=analisis_clubes,
        )
        if analisis_clubes and analisis_clubes.get("ok") and descuento_goteo_real is None:
            descuento_calculado = _descuento_goteo_real_desde_resumen(analisis_distribuidora=analisis)
            analisis["clubes"] = club_analysis.analizar_clubes(
                df,
                df_escalados=analisis_clubes.get("escalados"),
                df_liquidaciones=analisis_clubes.get("escalados"),
                proveedor=proveedor_id,
                descuento_goteo_real=descuento_calculado,
                desglose=analisis.get("desglose_por_tipo"),
            )
        _guardar_analisis_distribuidora(proveedor_id, analisis)

    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get(proveedor_id)
    if analisis_guardado:
        _mostrar_analisis_distribuidora(analisis_guardado)
        render_recomendaciones_ia()


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
        archivos = _filtrar_archivos_validos(archivos, "Facturas de laboratorios")
    if archivos:
        st.success(f"{len(archivos)} archivo(s) de laboratorios cargado(s).")
        st.dataframe(pd.DataFrame({"archivo": [f"archivo_{idx + 1}" for idx, _ in enumerate(archivos)]}))
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
        type=["xlsx"],
        accept_multiple_files=True,
        key="ventas_farmacia_excel",
    )
    st.session_state["ventas_farmacia"] = ["cargado"] * len(archivos) if archivos else []

    if archivos:
        archivos = _filtrar_archivos_validos(archivos, "Ventas farmacia")
    if archivos:
        ventas_normalizadas = []
        for archivo in archivos:
            try:
                ventas_normalizadas.append(ventas.normalizar_ventas_erp(ventas.leer_tabla(archivo)))
            except ValueError as error:
                _mostrar_error_procesamiento("No se pudo leer un archivo de ventas.", error)

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
        type=["xlsx"],
        key="stock_farmacia_excel",
    )

    df_stock = None
    if archivo:
        if not _validar_archivo_subido(archivo, "Stock farmacia"):
            archivo = None
    if archivo:
        try:
            df_stock = _normalizar_stock_farmacia(load_excel(archivo))
            _guardar_dataset("stock_farmacia_df", df_stock)
            st.session_state["stock_farmacia"] = "cargado"
        except ValueError as error:
            _mostrar_error_procesamiento("No se pudo leer el stock de la farmacia.", error)
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

    render_recomendaciones_ia()


def render_vida_pharma():
    df = None
    faceta_frames = []
    analisis_faceta = None
    analisis_avantia = None
    analisis_ajuste = None
    analisis_cargo_adicional = None
    analisis_clubes = None
    analisis_faceta_final = None
    resumen_conciliacion_bitransfer = None
    resumen_consumos_bitransfer = None
    resumen_final = None
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
        for uploaded_file in _filtrar_archivos_validos(uploaded_files, "Albaranes Bidafarma goteo"):
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

            col_albaran = _buscar_columna_albaran(df_temp.columns)

            if col_albaran:
                df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

            df_temp = parse_sections(df_temp)
            df_temp = _enriquecer_con_maestro(df_temp)
            df_temp = _aplicar_clasificaciones_transversales(
                df_temp,
                st.session_state.get("nomenclator_parafarmacia_financiada_df"),
            )
            dfs.append(df_temp)

    # TRANSFER
    if uploaded_transfer:
        for uploaded_file in _filtrar_archivos_validos(uploaded_transfer, "Albaranes Bidafarma transfer"):
            df_temp = normalize_columns(load_excel(uploaded_file))
            df_temp.columns = [c.lower().strip() for c in df_temp.columns]

            df_temp["proveedor"] = "bidafarma"
            df_temp["tipo_compra"] = "transfer"

            col_albaran = _buscar_columna_albaran(df_temp.columns)

            if col_albaran:
                df_temp["albaran"] = df_temp[col_albaran].apply(normalizar_albaran)

            df_temp = parse_sections(df_temp)
            df_temp = _enriquecer_con_maestro(df_temp)
            df_temp = _aplicar_clasificaciones_transversales(
                df_temp,
                st.session_state.get("nomenclator_parafarmacia_financiada_df"),
            )
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
    condicion_detectada = condiciones_proveedor_b.detectar_condicion(df, df_faceta_bidafarma)

    # =========================
    # VISTAS
    # =========================

    if df is not None:
        _mostrar_vistas_albaranes(df)
        _mostrar_tarjeta_parafarmacia_financiada(df)

    if not df_faceta_bidafarma.empty:
        analisis_faceta = faceta.analizar_faceta_v(df, df_faceta_bidafarma) if df is not None else None
    elif df is not None:
        analisis_faceta = None

    if df is not None:
        _mostrar_tarjeta_condicion_comercial(condicion_detectada, analisis_faceta)

    if not df_faceta_bidafarma.empty:
        if analisis_faceta:
            resumen_faceta = analisis_faceta["resumen"]
            hay_cargo_tarifa = abs(resumen_faceta["margen_tramo_fijo_total"]) > 0.0001
            if hay_cargo_tarifa:
                st.caption("La franquicia detectada se aplicará en los descuentos reales y aparecerá detallada en el informe generado.")
        else:
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
        if factura_normal and not _validar_archivo_subido(factura_normal, "Factura normal Bidafarma"):
            factura_normal = None
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
                    st.error(f"Faltan: {faltan}")
                if sobran:
                    st.warning(f"Sobran: {sobran}")

            total_factura_albaranes = resultado.get("total_albaranes_factura")
            if total_factura_albaranes is not None:
                st.metric("Total albaranes factura", f"{total_factura_albaranes:.2f} €")

            _mostrar_validacion_economica_factura(df_goteo, resultado, etiqueta="factura normal")

            if resultado["gastos"] is not None and not resultado["gastos"].empty:
                st.subheader("Gastos factura normal")
                st.dataframe(resultado["gastos"])

            ajustes_comerciales = resultado.get("ajustes_comerciales", pd.DataFrame())
            permite_ajuste = condicion_detectada is None or condicion_detectada["ajuste_comercial_factura"]
            analisis_ajuste = (
                _analisis_ajuste_comercial_bidafarma(df, ajustes_comerciales, df_faceta_bidafarma)
                if permite_ajuste else None
            )

            if analisis_ajuste:
                resumen_ajuste = analisis_ajuste["resumen"]
                st.info(
                    "Se ha detectado un ajuste comercial en factura "
                    f"por {resumen_ajuste['descuento_total']:.2f} € "
                    f"sobre {resumen_ajuste['lineas_afectadas']} líneas. "
                    "El detalle se incluirá en el informe generado."
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

            if resumen and abs(float(resumen.get("total", 0) or 0)) > 0.0001:
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
                    if not _validar_archivo_subido(excel_avantia, "Cuadro rentabilidad Avantia"):
                        excel_avantia = None
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

                            cargos_calculados_avantia = analisis_avantia.get(
                                "cargos_calculados",
                                pd.DataFrame(),
                            )
                            if not cargos_calculados_avantia.empty:
                                st.caption("Cargos calculados Avantia")
                                st.dataframe(cargos_calculados_avantia)

                            if not analisis_avantia["detalle"].empty:
                                st.caption("Resumen detallado de artículos Avantia")
                                st.dataframe(analisis_avantia["detalle"])
                            else:
                                st.info(
                                    "Se ha detectado Avantia, pero no hay líneas de albarán con Avantia "
                                    "en la descripción para imputar cargos."
                                )

                    except ValueError as error:
                        _mostrar_error_procesamiento("No se pudo leer el cuadro rentabilidad Avantia.", error)
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
                    if not _validar_archivo_subido(excel_consumos_bitransfer, "Cuadro resumen BitTransfer"):
                        excel_consumos_bitransfer = None
                if excel_consumos_bitransfer:
                    try:
                        resumen_consumos = bitransfer.leer_cuadro_resumen_consumos(excel_consumos_bitransfer)
                        resumen_consumos_bitransfer = resumen_consumos

                        st.subheader("📊 Cuadro resumen de consumos normalizado")

                        if not resumen_consumos["bitransfer"].empty:
                            st.caption("Bloque BitTransfer")
                            st.dataframe(resumen_consumos["bitransfer"])

                        if not resumen_consumos["plataformas"].empty:
                            st.caption("Bloque plataformas")
                            st.dataframe(resumen_consumos["plataformas"])

                    except ValueError as error:
                        _mostrar_error_procesamiento("No se pudo leer el cuadro resumen de consumos.", error)

                if excel_compras_bitransfer:
                    if not _validar_archivo_subido(excel_compras_bitransfer, "Listado compras BitTransfer"):
                        excel_compras_bitransfer = None
                if excel_compras_bitransfer:
                    try:
                        df_bt_compras = bitransfer.leer_listado_compras_bitransfer(excel_compras_bitransfer)
                        df_bt_compras = _enriquecer_con_maestro(df_bt_compras)

                        st.success("Listado de compras BitTransfer cargado. El detalle se mostrará en la conciliación.")

                    except ValueError as error:
                        _mostrar_error_procesamiento("No se pudo leer el listado de compras BitTransfer.", error)

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
                                    if not _validar_archivo_subido(excel_plataforma, f"Listado productos plataforma {indice}"):
                                        excel_plataforma = None
                                if excel_plataforma:
                                    try:
                                        df_plataforma = bitransfer.leer_listado_compras_bitransfer(excel_plataforma)
                                        df_plataforma = _enriquecer_con_maestro(df_plataforma)
                                        _mostrar_dataframe_completo(df_plataforma)
                                    except ValueError as error:
                                        st.error(
                                            f"No se pudo leer el listado de productos de {nombre_plataforma}: {error}"
                                        )

                    except ValueError as error:
                        _mostrar_error_procesamiento("No se pudo conciliar BitTransfer.", error)

            if resultado is not None and not resultado["gastos"].empty:
                gestion_factura = float(resultado["gastos"].loc[
                    resultado["gastos"]["tipo"] == "gestion",
                    "importe"
                ].sum())
                cargo_bitransfer = (
                    0.0 if not resumen_conciliacion_bitransfer else resumen_conciliacion_bitransfer["cargo_resumen"]
                )
                cargo_avantia = 0.0 if not analisis_avantia else analisis_avantia["resumen"]["cargo_total"]
                cargo_plataformas = _calcular_gastos_plataformas(resumen_consumos_bitransfer)
                gestion_calculada = cargo_bitransfer + cargo_avantia + cargo_plataformas
                diferencia_gestion = gestion_factura - gestion_calculada

                if gestion_factura > 0 and (
                    cargo_bitransfer > 0
                    or cargo_avantia > 0
                    or cargo_plataformas > 0
                    or condicion_detectada
                ):
                    st.subheader("🧮 Conciliación global gastos de gestión")

                    g1, g2, g3, g4, g5 = st.columns(5)
                    g1.metric("Gestión factura", f"{gestion_factura:.2f} €")
                    g2.metric("BitTransfer", f"{cargo_bitransfer:.2f} €")
                    g3.metric("Plataformas", f"{cargo_plataformas:.2f} €")
                    g4.metric("Avantia", f"{cargo_avantia:.2f} €")
                    g5.metric("Diferencia", f"{diferencia_gestion:.2f} €")

                    penalizacion_bajo_consumo = _detectar_penalizacion_bajo_consumo(
                        condicion_detectada,
                        diferencia_gestion,
                    )
                    if abs(diferencia_gestion) <= 0.05:
                        st.success(
                            "Los gastos de gestión de factura cuadran con BitTransfer, plataformas y Avantia."
                        )
                    elif cargo_bitransfer > 0 or cargo_avantia > 0 or cargo_plataformas > 0:
                        st.warning(
                            "Los gastos de gestión no cuadran exactamente con BitTransfer, plataformas y Avantia. "
                            "Revisa que los cuadros subidos correspondan al mismo periodo."
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
                            "Se reparte como franquicia sobre el goteo elegible y se detallará en el informe generado."
                        )
                        resumen_cargo_adicional = analisis_cargo_adicional["resumen"]
                        st.caption(
                            f"Franquicia adicional: {resumen_cargo_adicional['cargo_total']:.2f} € · "
                            f"Líneas afectadas: {resumen_cargo_adicional['lineas_afectadas']}"
                        )

            analisis_faceta_final = faceta.analizar_faceta_v(df, df_faceta_bidafarma) if not df_faceta_bidafarma.empty else None
            resumen_final = _resumen_bidafarma(
                df,
                analisis_faceta=analisis_faceta_final,
                resumen_bitransfer=resumen_conciliacion_bitransfer,
                analisis_avantia=analisis_avantia,
                analisis_ajuste=analisis_ajuste,
                analisis_cargo_adicional=analisis_cargo_adicional,
                analisis_transfer=None,
            )
            analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get("bidafarma")
            descuento_goteo_real = _descuento_goteo_real_desde_resumen(
                resumen_bidafarma=resumen_final,
                analisis_distribuidora=analisis_guardado,
            )
            analisis_clubes = _render_bloque_clubes("bidafarma", df, descuento_goteo_real=descuento_goteo_real)

        # -------------------------
        # FACTURA TRANSFER
        # -------------------------
        analisis_transfer = None
        factura_transfer = st.file_uploader("Factura TRANSFER", type=["xlsx"], key="bidafarma_factura_transfer")
        if factura_transfer and not _validar_archivo_subido(factura_transfer, "Factura transfer Bidafarma"):
            factura_transfer = None
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
                    st.error(f"Faltan en transfer: {faltan}")
                if sobran:
                    st.warning(f"Sobran en transfer: {sobran}")

            _mostrar_validacion_economica_factura(df_transfer, resultado_transfer, etiqueta="factura transfer")

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

            asociaciones_auto_transfer = _detectar_laboratorios_bonificados(
                df_transfer,
                resultado_transfer.get("abonos", pd.DataFrame()),
            )["detalle"]
            imputaciones_transfer_manuales = _render_imputacion_manual_transfer(
                df_transfer,
                resultado_transfer,
                asociaciones_auto_transfer,
            )

            analisis_transfer = _analisis_transfer_logistica(
                df_transfer,
                resultado_transfer,
                imputaciones_manuales=imputaciones_transfer_manuales,
            )
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

                if resumen_transfer.get("albaranes_bonificados_manual"):
                    st.caption(
                        "Albaranes sin cargo por imputación manual: "
                        + ", ".join(map(str, resumen_transfer["albaranes_bonificados_manual"]))
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

                if not analisis_transfer["imputaciones_manuales"].empty:
                    st.caption("Imputaciones manuales de abonos transfer")
                    st.dataframe(analisis_transfer["imputaciones_manuales"])

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
                                "bonificado_transfer_auto",
                                "bonificado_transfer_manual",
                                "motivo_bonificacion_transfer",
                                "aplica_cargo_logistico_transfer",
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

    st.divider()
    st.header("Generación de informe")
    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get("bidafarma")
    descuento_goteo_real = _descuento_goteo_real_desde_resumen(
        resumen_bidafarma=resumen_final,
        analisis_distribuidora=analisis_guardado,
    )
    if analisis_clubes is None:
        analisis_clubes = club_analysis.analizar_clubes(
            df,
            proveedor="bidafarma",
            descuento_goteo_real=descuento_goteo_real,
        )

    if st.button("Generar análisis distribuidora", key="generar_analisis_bidafarma"):
        analisis = distributor_analysis.generar_analisis_distribuidora(
            df,
            proveedor="bidafarma",
            resultado_factura_normal=resultado_factura_normal,
            resultado_factura_transfer=resultado_factura_transfer,
            analisis_faceta=analisis_faceta_final,
            analisis_avantia=analisis_avantia,
            resumen_bitransfer=resumen_conciliacion_bitransfer,
            analisis_transfer=analisis_transfer,
            analisis_clubes=analisis_clubes,
            condicion_detectada=condicion_detectada,
            analisis_ajuste=analisis_ajuste,
            analisis_cargo_adicional=analisis_cargo_adicional,
        )
        if analisis_clubes and analisis_clubes.get("ok") and descuento_goteo_real is None:
            descuento_calculado = _descuento_goteo_real_desde_resumen(analisis_distribuidora=analisis)
            analisis["clubes"] = club_analysis.analizar_clubes(
                df,
                df_escalados=analisis_clubes.get("escalados"),
                df_liquidaciones=analisis_clubes.get("escalados"),
                proveedor="bidafarma",
                descuento_goteo_real=descuento_calculado,
                desglose=analisis.get("desglose_por_tipo"),
            )
        _guardar_analisis_distribuidora("bidafarma", analisis)

    analisis_guardado = st.session_state.get("analisis_distribuidora", {}).get("bidafarma")
    if analisis_guardado:
        _mostrar_analisis_distribuidora(analisis_guardado)
        render_recomendaciones_ia()


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
    

def _bd_service():
    if "bandeja_documental_service" not in st.session_state:
        repo = BandejaDocumentalRepository("data/bandeja_documental.db")
        st.session_state["bandeja_documental_service"] = BandejaDocumentalService(repo=repo)
    return st.session_state["bandeja_documental_service"]


def _bd_estado_badge(estado):
    colores = {
        bandeja_documental.EstadoExpediente.ERROR_DOCUMENTAL.value: "#dc2626",
        bandeja_documental.EstadoExpediente.DOCUMENTACION_INCOMPLETA.value: "#dc2626",
        bandeja_documental.EstadoExpediente.PENDIENTE_DOCUMENTACION.value: "#ca8a04",
        bandeja_documental.EstadoExpediente.PENDIENTE_REVISION.value: "#ca8a04",
        bandeja_documental.EstadoExpediente.DOCUMENTACION_RECIBIDA.value: "#2563eb",
        bandeja_documental.EstadoExpediente.LISTO_ANALISIS.value: "#16a34a",
    }
    color = colores.get(estado, "#64748b")
    return (
        f"<span style='display:inline-block;background:{color};color:white;"
        "padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.82rem'>"
        f"{html.escape(str(estado))}</span>"
    )


def _preanalisis_badge(estado, valido=True):
    colores = {
        preanalisis_documental.EstadoPreanalisis.PREANALISIS_COMPLETADO.value: "#16a34a",
        preanalisis_documental.EstadoPreanalisis.PREANALISIS_WARNING.value: "#ca8a04",
        preanalisis_documental.EstadoPreanalisis.PREANALISIS_ERROR.value: "#dc2626",
        preanalisis_documental.EstadoPreanalisis.PREANALISIS_PENDIENTE.value: "#64748b",
    }
    color = colores.get(estado, "#64748b")
    etiqueta = estado if estado else ("valido" if valido else "error")
    return (
        f"<span style='display:inline-block;background:{color};color:white;"
        "padding:4px 10px;border-radius:8px;font-weight:700;font-size:0.78rem'>"
        f"{html.escape(str(etiqueta))}</span>"
    )


def _bd_format_bytes(num):
    try:
        num = float(num or 0)
    except Exception:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def render_bandeja_documental():
    service = _bd_service()
    repo = service.repo

    st.header("Bandeja documental")
    st.caption("Recepcion, organizacion y trazabilidad documental. Gmail y motor de analisis siguen desacoplados.")

    expedientes = repo.list_expedientes()
    documentos = repo.list_documentos()
    errores_pendientes = repo.count_errores_pendientes()
    hoy = bandeja_documental.ahora_iso()[:10]
    docs_hoy = [doc for doc in documentos if str(doc.get("fecha_recepcion", "")).startswith(hoy)]
    abiertos = [
        exp for exp in expedientes
        if exp["estado"] not in {
            bandeja_documental.EstadoExpediente.ANALIZADO.value,
            bandeja_documental.EstadoExpediente.LISTO_ANALISIS.value,
        }
    ]
    listos = [exp for exp in expedientes if exp["estado"] == bandeja_documental.EstadoExpediente.LISTO_ANALISIS.value]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Expedientes abiertos", len(abiertos))
    k2.metric("Listos para analisis", len(listos))
    k3.metric("Errores pendientes", errores_pendientes)
    k4.metric("Documentos hoy", len(docs_hoy))

    with st.expander("Crear expediente manual", expanded=False):
        c1, c2, c3 = st.columns([2, 1, 1])
        asunto = c1.text_input("Asunto normalizado", placeholder="ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL")
        cliente = c2.text_input("Cliente", placeholder="Cliente / farmacia")
        remitente = c3.text_input("Email remitente", placeholder="cliente@farmacia.com")
        if st.button("Crear o actualizar expediente", type="primary"):
            try:
                expediente_id = service.crear_expediente_desde_asunto(asunto, cliente, remitente)
                st.success(f"Expediente listo: {expediente_id}")
                st.rerun()
            except Exception as error:
                st.error(str(error))

    with st.expander("Simular email entrante", expanded=False):
        e1, e2 = st.columns(2)
        email_asunto = e1.text_input("Asunto email", key="bd_email_asunto", placeholder="ASESORIA_2T_2026_FARMACIA_SAN_MIGUEL")
        email_remitente = e2.text_input("Remitente", key="bd_email_remitente", placeholder="cliente@farmacia.com")
        e3, e4 = st.columns(2)
        message_id = e3.text_input("Message ID", key="bd_message_id", value=f"manual-{bandeja_documental.ahora_iso()}")
        thread_id = e4.text_input("Thread ID", key="bd_thread_id", value="thread-simulado")
        email_files = st.file_uploader(
            "Adjuntos simulados",
            type=["xlsx", "xls", "csv", "pdf", "zip"],
            accept_multiple_files=True,
            key="bd_email_files",
        )
        if st.button("Procesar email simulado"):
            email = EmailEntrante(
                message_id=message_id,
                thread_id=thread_id,
                asunto=email_asunto,
                remitente_email=email_remitente,
                adjuntos=[
                    AdjuntoEntrante(archivo.name, archivo.getvalue(), getattr(archivo, "type", ""))
                    for archivo in email_files
                ],
            )
            resultado = procesar_email_entrante(email, cliente_nombre=email_remitente, service=service)
            st.json(resultado.to_dict())
            st.rerun()

    st.subheader("Expedientes")
    f1, f2, f3 = st.columns([1, 1, 1])
    estados = ["Todos"] + sorted({str(exp["estado"]) for exp in expedientes})
    filtro_estado = f1.selectbox("Filtrar por estado", estados)
    busqueda = f2.text_input("Buscar farmacia")
    orden_desc = f3.checkbox("Ordenar por fecha reciente", value=True)

    expedientes_filtrados = expedientes
    if filtro_estado != "Todos":
        expedientes_filtrados = [exp for exp in expedientes_filtrados if exp["estado"] == filtro_estado]
    if busqueda:
        expedientes_filtrados = [
            exp for exp in expedientes_filtrados
            if busqueda.lower() in str(exp["farmacia"]).lower()
        ]
    expedientes_filtrados = sorted(
        expedientes_filtrados,
        key=lambda exp: str(exp.get("fecha_ultima_actualizacion", "")),
        reverse=orden_desc,
    )

    expediente_id = None
    if expedientes_filtrados:
        filas = []
        for exp in expedientes_filtrados:
            docs_exp = repo.list_documentos(exp["expediente_id"], include_deleted=False)
            filas.append(
                {
                    "ID expediente": exp["expediente_id"],
                    "Farmacia": exp["farmacia"],
                    "Cliente": exp["cliente"],
                    "Servicio": exp["tipo_servicio"],
                    "Periodo": f"{exp['periodo']} {exp['ano']}",
                    "Estado": exp["estado"],
                    "Fecha actualizacion": exp["fecha_ultima_actualizacion"],
                    "N documentos": len(docs_exp),
                    "Errores pendientes": repo.count_errores_pendientes(exp["expediente_id"]),
                }
            )
        st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
        opciones = {
            f"{exp['farmacia']} | {exp['tipo_servicio']} {exp['periodo']} {exp['ano']}": exp["expediente_id"]
            for exp in expedientes_filtrados
        }
        seleccionado = st.selectbox("Abrir detalle de expediente", list(opciones.keys()))
        expediente_id = opciones[seleccionado]
    else:
        st.info("No hay expedientes con estos filtros.")

    if expediente_id:
        exp = repo.get_expediente(expediente_id)
        docs = repo.list_documentos(expediente_id)
        errores = repo.list_errores(expediente_id)
        eventos = repo.list_eventos(expediente_id)

        st.divider()
        st.subheader("Detalle expediente")
        d1, d2, d3 = st.columns([2, 1, 1])
        d1.markdown(_bd_estado_badge(exp["estado"]), unsafe_allow_html=True)
        d1.write(f"**Farmacia:** {exp['farmacia']}  \n**Cliente:** {exp['cliente']}  \n**Email:** {exp.get('email_remitente', '')}")
        d2.write(f"**Servicio:** {exp['tipo_servicio']}  \n**Periodo:** {exp['periodo']}  \n**Ano:** {exp['ano']}")
        d3.write("**Bloques mínimos para análisis**")
        evaluacion_bloques = service.evaluar_bloques_documentales(expediente_id)
        d3.write(", ".join(evaluacion_bloques["bloques_obligatorios"]) or "Sin bloques")
        perfiles = [perfil.value for perfil in bandeja_documental.PerfilDocumental]
        perfil_actual = str(exp.get("perfil_documental", bandeja_documental.PerfilDocumental.GENERICO.value))
        perfil_sel = st.selectbox(
            "Perfil documental",
            perfiles,
            index=perfiles.index(perfil_actual) if perfil_actual in perfiles else perfiles.index(bandeja_documental.PerfilDocumental.GENERICO.value),
            help="Ajusta el checklist y el preanalisis sin modificar el flujo manual de calculo.",
        )
        if perfil_sel != perfil_actual:
            repo.update_expediente_fields(expediente_id, perfil_documental=perfil_sel)
            repo.add_evento(expediente_id, "perfil_documental_actualizado", f"{perfil_actual} -> {perfil_sel}", "usuario")
            service.recalcular_checklist_y_estado(expediente_id)
            st.rerun()

        if evaluacion_bloques["avisos"]:
            st.info(" | ".join(evaluacion_bloques["avisos"]))
        st.write("**Estado de bloques funcionales**")
        bcols = st.columns(3)
        for idx, bloque in enumerate([
            bandeja_documental.BloqueDocumental.COMPRAS_PROVEEDOR.value,
            bandeja_documental.BloqueDocumental.VENTAS.value,
            bandeja_documental.BloqueDocumental.STOCK.value,
        ]):
            completo = bool(evaluacion_bloques["bloques"].get(bloque))
            bcols[idx].metric(bloque.replace("_", " ").title(), "Completo" if completo else "Incompleto")
        st.caption(
            "Opcionales/detectables dentro de compras: albaranes embebidos, liquidaciones/abonos, facturas laboratorio y otros."
        )

        faltantes = evaluacion_bloques.get("bloques_faltantes", [])
        if faltantes:
            st.warning("Bloques mínimos faltantes: " + ", ".join(faltantes))
        else:
            st.success("Bloques mínimos completos.")

        a1, a2, a3 = st.columns(3)
        if a1.button("Recalcular checklist"):
            with st.spinner("Recalculando expediente..."):
                nuevo_estado = service.recalcular_checklist_y_estado(expediente_id)
            st.success(f"Estado actualizado: {nuevo_estado}")
            st.rerun()
        if a2.button("Marcar como Listo para analisis"):
            ok, motivos = service.marcar_listo_para_analisis(expediente_id)
            if ok:
                st.success("Expediente marcado como Listo para analisis.")
                st.rerun()
            else:
                st.error("No se puede marcar como listo: " + " | ".join(motivos))
        if a3.button("Generar preview payload"):
            try:
                st.session_state["bd_payload_preview"] = service.preparar_payload_para_analisis(expediente_id)
            except Exception as error:
                st.error(str(error))

        if st.session_state.get("bd_payload_preview", {}).get("expediente_id") == expediente_id:
            st.json(st.session_state["bd_payload_preview"])

        st.subheader("Subida de documentos")
        archivos = st.file_uploader(
            "Arrastra o selecciona archivos",
            type=["xlsx", "xls", "csv", "pdf", "zip"],
            accept_multiple_files=True,
            key=f"bd_uploader_{expediente_id}",
        )
        if st.button("Procesar archivos subidos", disabled=not archivos):
            with st.spinner("Guardando documentos y recalculando estado..."):
                resultado = service.registrar_subida_manual(
                    expediente_id,
                    [(archivo.name, archivo.getvalue()) for archivo in archivos],
                )
            if resultado["registrados"]:
                st.success("Registrados: " + ", ".join(resultado["registrados"]))
            if resultado["duplicados"]:
                st.warning("Duplicados: " + ", ".join(resultado["duplicados"]))
            if resultado["errores"]:
                st.error("Con errores: " + ", ".join(resultado["errores"]))
            st.rerun()

        st.subheader("Documentos")
        if docs:
            tabla_docs = pd.DataFrame(
                [
                    {
                        "ID": doc["id_documento"],
                        "Nombre original": doc["nombre_original"],
                        "Tipo": doc["tipo_documental"],
                        "Estado": doc["estado_documento"],
                        "Tamano": _bd_format_bytes(doc["tamano_bytes"]),
                        "Hash": str(doc["hash_archivo"])[:10],
                        "Fecha": doc["fecha_recepcion"],
                        "Origen": doc["origen"],
                        "Ruta": doc["ruta_archivo"],
                    }
                    for doc in docs
                ]
            )
            st.dataframe(tabla_docs, hide_index=True, use_container_width=True)
            doc_opciones = {f"{doc['nombre_original']} ({doc['estado_documento']})": doc["id_documento"] for doc in docs}
            doc_sel = st.selectbox("Documento para accion", list(doc_opciones.keys()))
            motivo_doc = st.text_input("Motivo / observacion documento", key=f"bd_motivo_doc_{expediente_id}")
            b1, b2, b3 = st.columns(3)
            if b1.button("Marcar incorrecto"):
                service.marcar_documento_incorrecto(expediente_id, doc_opciones[doc_sel], motivo_doc or "Marcado desde UI")
                st.rerun()
            if b2.button("Soft delete"):
                service.soft_delete_documento(expediente_id, doc_opciones[doc_sel], motivo_doc or "Eliminado desde UI")
                st.rerun()
            doc_actual = next((doc for doc in docs if doc["id_documento"] == doc_opciones[doc_sel]), None)
            if doc_actual and Path(str(doc_actual.get("ruta_archivo", ""))).exists():
                b3.download_button(
                    "Descargar archivo",
                    data=Path(str(doc_actual["ruta_archivo"])).read_bytes(),
                    file_name=doc_actual["nombre_original"],
                )
        else:
            st.info("Todavia no hay documentos registrados.")

        st.subheader("Preanalisis documental automatico")
        pre_exp = repo.get_preanalisis_expediente(expediente_id)
        p1, p2, p3, p4 = st.columns([1, 1, 1, 2])
        disabled_pre = not any(doc.get("estado_documento") == bandeja_documental.EstadoDocumento.RECIBIDO.value for doc in docs)
        if p1.button("Ejecutar preanalisis", disabled=disabled_pre):
            with st.spinner("Comprendiendo estructura documental..."):
                try:
                    resultado_pre = preanalisis_documental.ejecutar_preanalisis_expediente(expediente_id, repo=repo)
                    st.session_state["bd_preanalisis_resultado"] = resultado_pre.to_dict()
                    st.success(f"Preanalisis terminado: {resultado_pre.estado_preanalisis}")
                    st.rerun()
                except Exception as error:
                    st.error(str(error))
        if disabled_pre:
            p4.info("Carga al menos un documento recibido para ejecutar el preanalisis.")
        if pre_exp:
            p2.metric("Docs OK", pre_exp["documentos_ok"])
            p3.metric("Warnings", pre_exp["documentos_warning"])
            p4.markdown(_preanalisis_badge(pre_exp["estado_preanalisis"], pre_exp["valido_global"]), unsafe_allow_html=True)
            st.caption(f"Ultima ejecucion: {pre_exp['fecha_ejecucion']} | Perfil: {pre_exp.get('perfil_documental', 'GENERICO')}")
            if pre_exp.get("documentos_faltantes_perfil"):
                st.warning("Faltantes segun perfil: " + ", ".join(pre_exp["documentos_faltantes_perfil"]))
            if pre_exp["warnings_globales"]:
                st.warning("Warnings globales: " + " | ".join(pre_exp["warnings_globales"]))
            if pre_exp["errores_globales"]:
                st.error("Errores globales: " + " | ".join(pre_exp["errores_globales"]))

            pre_docs = repo.list_preanalisis_documentos(expediente_id)
            if pre_docs:
                tabla_pre = pd.DataFrame(
                    [
                        {
                            "Documento": item["nombre_archivo"],
                            "Esperado": item["tipo_documental_esperado"],
                            "Detectado": item["tipo_documental_detectado"],
                            "Confianza tipo": round(float(item["confianza_tipo"]), 2),
                            "Proveedor": item["proveedor_detectado"],
                            "Confianza proveedor": round(float(item["confianza_proveedor"]), 2),
                            "Subtipo": item.get("subtipo_documental", ""),
                            "PDF compuesto": bool(item.get("pdf_compuesto")),
                            "Factura": bool(item.get("contiene_factura")),
                            "Albaranes": bool(item.get("contiene_albaranes")),
                            "Paginas factura": item.get("paginas_factura", []),
                            "Paginas albaranes": item.get("paginas_albaranes", []),
                            "N albaranes": item.get("numero_albaranes_detectados", item.get("albaranes_detectados_count", 0)),
                            "Tipos albaran": " | ".join(item.get("tipos_albaran_detectados", [])),
                            "N factura": item.get("numero_factura", ""),
                            "Periodo": item.get("periodo_detectado", ""),
                            "Fechas": " | ".join(item.get("fechas_detectadas", [])),
                            "Liquidaciones": " | ".join(item.get("posibles_liquidaciones_detectadas", [])),
                            "Tipo 74": bool(item.get("contiene_tipo_74")),
                            "ZV/Zacofarva": bool(item.get("contiene_zv_zacofarva")),
                            "Liq. embebida": bool(item.get("posible_liquidacion_embebida")),
                            "Formato": item["formato_detectado"],
                            "Columnas": len(item["columnas_detectadas"]),
                            "Filas": item["numero_filas"],
                            "Hojas": ", ".join(item["hojas_detectadas"]),
                            "PDF paginas": item["pdf_paginas"],
                            "ZIP internos": len(item["zip_archivos_internos"]),
                            "Warnings": " | ".join(item["warnings"]),
                            "Errores": " | ".join(item["errores_detectados"]),
                            "Resumen": item["resumen"],
                        }
                        for item in pre_docs
                    ]
                )
                st.dataframe(tabla_pre, hide_index=True, use_container_width=True)
                with st.expander("Detalle tecnico de columnas/ZIP por documento", expanded=False):
                    for item in pre_docs:
                        st.write(f"**{item['nombre_archivo']}**")
                        if item["columnas_detectadas"]:
                            st.caption("Columnas: " + ", ".join(item["columnas_detectadas"][:80]))
                        if item["zip_archivos_internos"]:
                            st.caption("ZIP: " + ", ".join(item["zip_archivos_internos"][:80]))
                        if item.get("texto_extraido_resumido"):
                            st.caption("Texto PDF: " + item["texto_extraido_resumido"])
                        if item.get("clasificacion_paginas"):
                            st.dataframe(pd.DataFrame(item["clasificacion_paginas"]), hide_index=True, use_container_width=True)
        else:
            st.info("Aun no hay preanalisis guardado para este expediente.")

        o1, o2 = st.columns(2)
        with o1:
            st.subheader("Errores de ingestion")
            if errores:
                for error in errores:
                    estado_error = "resuelto" if error["resuelto"] else "pendiente"
                    st.write(f"**#{error['id_error']} {estado_error}** - {error['motivo_error']} - {error['nombre_archivo']}")
                    st.caption(error.get("detalle", ""))
                    if not error["resuelto"] and st.button(f"Marcar error {error['id_error']} resuelto"):
                        repo.marcar_error_resuelto(error["id_error"])
                        repo.add_evento(expediente_id, "error_ingestion_resuelto", f"Error {error['id_error']} resuelto", "usuario")
                        service.recalcular_checklist_y_estado(expediente_id)
                        st.rerun()
            else:
                st.info("Sin errores asociados.")
        with o2:
            st.subheader("Observaciones internas")
            nuevas_obs = st.text_area("Observaciones", value=str(exp.get("observaciones", "")), height=120)
            if st.button("Guardar observaciones"):
                repo.update_expediente_fields(expediente_id, observaciones=nuevas_obs)
                repo.add_evento(expediente_id, "observacion_anadida", nuevas_obs, "usuario")
                st.success("Observaciones guardadas.")
                st.rerun()

        st.subheader("Historial")
        if eventos:
            st.dataframe(pd.DataFrame(eventos), hide_index=True, use_container_width=True)
        else:
            st.info("Sin eventos todavia.")

        st.subheader("Exportacion y administracion tecnica")
        t1, t2, t3 = st.columns(3)
        export_data = service.exportar_expediente_json(expediente_id)
        t1.download_button(
            "Exportar JSON",
            data=json.dumps(export_data, ensure_ascii=False, indent=2),
            file_name=f"{expediente_id}.json",
            mime="application/json",
        )
        zip_path = service.exportar_expediente_zip(expediente_id)
        t2.download_button(
            "Exportar ZIP",
            data=zip_path.read_bytes(),
            file_name=zip_path.name,
            mime="application/zip",
        )
        if t3.button("Ejecutar diagnostico FS/DB"):
            st.session_state["bd_diagnostico"] = service.diagnostico_integridad()
        if "bd_diagnostico" in st.session_state:
            st.json(st.session_state["bd_diagnostico"])

    with st.expander("Panel tecnico global", expanded=False):
        stats = repo.stats()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("DB expedientes", stats["expedientes"])
        c2.metric("DB documentos", stats["documentos"])
        c3.metric("Errores", stats["errores"])
        c4.metric("Emails procesados", stats["emails_procesados"])
        c5.metric("Storage", _bd_format_bytes(service.storage_size_bytes()))
        st.metric("Preanalisis ejecutados", stats.get("preanalisis", 0))
        recientes = repo.list_preanalisis_recientes(limit=10)
        if recientes:
            st.write("**Ultimos preanalisis**")
            st.dataframe(pd.DataFrame(recientes), hide_index=True, use_container_width=True)
        pre_stats = repo.preanalisis_stats()
        s1, s2 = st.columns(2)
        with s1:
            st.write("**Proveedores mas detectados**")
            if pre_stats["proveedores_mas_detectados"]:
                st.dataframe(pd.DataFrame(pre_stats["proveedores_mas_detectados"]), hide_index=True, use_container_width=True)
            else:
                st.caption("Sin datos todavia.")
            st.write("**Tipos ambiguos frecuentes**")
            if pre_stats["tipos_ambiguos"]:
                st.dataframe(pd.DataFrame(pre_stats["tipos_ambiguos"]), hide_index=True, use_container_width=True)
            else:
                st.caption("Sin ambiguos registrados.")
        with s2:
            st.write("**Documentos con mas errores recientes**")
            if pre_stats["documentos_con_errores"]:
                st.dataframe(pd.DataFrame(pre_stats["documentos_con_errores"]), hide_index=True, use_container_width=True)
            else:
                st.caption("Sin errores de preanalisis.")
            st.metric("ZIPs corruptos detectados", pre_stats["zips_corruptos"])
        st.caption("Motor analitico y Gmail real no se ejecutan desde esta pantalla.")


st.set_page_config(layout="wide")
st.title("📊 Auditoría de Compras Farmacia")
_inyectar_estilos_dashboard()

_verificar_acceso_app()
_asegurar_maestros_en_sesion()

if st.button("Borrar datos cargados"):
    st.session_state.clear()
    st.rerun()

with st.sidebar:
    seccion_activa = st.radio(
        "Apartado",
        SECCIONES,
        label_visibility="visible",
    )

    with st.expander("Base maestra CN / laboratorio", expanded=False):
        _render_base_maestra_laboratorios()

    with st.expander("Contexto de farmacia", expanded=False):
        render_contexto_farmacia()

st.divider()

if seccion_activa == "Bandeja documental":
    render_bandeja_documental()
elif seccion_activa == "bidafarma":
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
