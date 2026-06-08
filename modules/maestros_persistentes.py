import io
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1] / "data" / "base_maestra"
METADATA_FILE = BASE_DIR / "metadata.json"

DEFINICIONES = {
    "ministerio": {"base": "nomenclator_ministerio", "extensiones": {"xlsx"}},
    "manual": {"base": "base_manual_cn_laboratorio", "extensiones": {"xlsx"}},
    "aemps": {"base": "nomenclator_aemps", "extensiones": {"zip", "xml"}},
    "efg": {"base": "equivalencias_efg", "extensiones": {"xlsx"}},
}

NOMBRES_INCLUIDOS = {
    "ministerio": "Nomenclator facturacion Ministerio incluido",
    "manual": "Base manual CN/laboratorio incluida",
    "aemps": "Nomenclator AEMPS incluido",
    "efg": "Equivalencias EFG incluidas",
}


class ArchivoPersistido(io.BytesIO):
    def __init__(self, contenido, nombre):
        super().__init__(contenido)
        self.name = nombre
        self.size = len(contenido)


def _asegurar_directorio():
    BASE_DIR.mkdir(parents=True, exist_ok=True)


def _leer_metadata():
    if not METADATA_FILE.exists():
        return {}
    try:
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _guardar_metadata(metadata):
    _asegurar_directorio()
    METADATA_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _extension(nombre):
    texto = str(nombre or "")
    return texto.rsplit(".", 1)[-1].lower() if "." in texto else ""


def _ruta_para(clave, extension):
    definicion = DEFINICIONES[clave]
    return BASE_DIR / f"{definicion['base']}.{extension}"


def obtener_metadata(clave=None):
    metadata = _leer_metadata()
    if clave is None:
        for clave_definida in DEFINICIONES:
            info = metadata.get(clave_definida)
            ruta = BASE_DIR / info.get("filename", "") if info else None
            if not info or ruta is None or not ruta.exists():
                descubierto = _descubrir_archivo(clave_definida)
                if descubierto:
                    metadata[clave_definida] = descubierto
                else:
                    metadata.pop(clave_definida, None)
        return {k: v for k, v in metadata.items() if v}
    info = metadata.get(clave)
    ruta = BASE_DIR / info.get("filename", "") if info else None
    if info and ruta is not None and ruta.exists():
        return info
    return _descubrir_archivo(clave)


def _descubrir_archivo(clave):
    if clave not in DEFINICIONES:
        return None
    definicion = DEFINICIONES[clave]
    for extension in definicion["extensiones"]:
        ruta = _ruta_para(clave, extension)
        if ruta.exists() and ruta.is_file():
            stat = ruta.stat()
            return {
                "filename": ruta.name,
                "original_name": ruta.name,
                "label": NOMBRES_INCLUIDOS.get(clave, ruta.name),
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "incluido_en_codigo": True,
            }
    return None


def guardar_archivo(clave, uploaded_file):
    if clave not in DEFINICIONES:
        raise ValueError(f"Tipo de maestro no soportado: {clave}")

    nombre = str(getattr(uploaded_file, "name", ""))
    extension = _extension(nombre)
    extensiones = DEFINICIONES[clave]["extensiones"]
    if extension not in extensiones:
        raise ValueError(f"Formato no permitido para {clave}: .{extension}")

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if hasattr(uploaded_file, "getvalue"):
        contenido = uploaded_file.getvalue()
    else:
        contenido = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    _asegurar_directorio()
    eliminar_archivo(clave)
    ruta = _ruta_para(clave, extension)
    ruta.write_bytes(contenido)

    metadata = _leer_metadata()
    metadata[clave] = {
        "filename": ruta.name,
        "original_name": nombre or ruta.name,
        "size": len(contenido),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _guardar_metadata(metadata)
    return metadata[clave]


def abrir_archivo(clave):
    info = obtener_metadata(clave)
    if not info:
        return None
    ruta = BASE_DIR / info.get("filename", "")
    if not ruta.exists() or not ruta.is_file():
        return None
    contenido = ruta.read_bytes()
    return ArchivoPersistido(contenido, info.get("original_name") or ruta.name)


def eliminar_archivo(clave):
    metadata = _leer_metadata()
    info = metadata.pop(clave, None)
    if info:
        ruta = BASE_DIR / info.get("filename", "")
        if ruta.exists() and ruta.is_file():
            ruta.unlink()

    if clave in DEFINICIONES:
        base = DEFINICIONES[clave]["base"]
        for ruta in BASE_DIR.glob(f"{base}.*"):
            if ruta.is_file():
                ruta.unlink()

    _guardar_metadata(metadata)


def hay_archivo(clave):
    info = obtener_metadata(clave)
    if not info:
        return False
    ruta = BASE_DIR / info.get("filename", "")
    return ruta.exists() and ruta.is_file()
