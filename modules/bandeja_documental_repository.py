from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from modules import bandeja_documental as bd


class BandejaDocumentalRepository:
    def __init__(self, db_path: str = "data/bandeja_documental.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS expedientes (
                    expediente_id TEXT PRIMARY KEY,
                    dedupe_key TEXT UNIQUE NOT NULL,
                    cliente TEXT NOT NULL,
                    farmacia TEXT NOT NULL,
                    email_remitente TEXT,
                    tipo_servicio TEXT NOT NULL,
                    periodo TEXT NOT NULL,
                    ano INTEGER NOT NULL,
                    fecha_recepcion TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    documentos_recibidos_json TEXT NOT NULL DEFAULT '[]',
                    documentos_faltantes_json TEXT NOT NULL DEFAULT '[]',
                    observaciones TEXT NOT NULL DEFAULT '',
                    ruta_almacenamiento TEXT NOT NULL DEFAULT '',
                    fecha_ultima_actualizacion TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documentos (
                    id_documento TEXT PRIMARY KEY,
                    expediente_id TEXT NOT NULL,
                    nombre_original TEXT NOT NULL,
                    nombre_normalizado TEXT NOT NULL,
                    tipo_documental TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    tamano_bytes INTEGER NOT NULL DEFAULT 0,
                    hash_archivo TEXT NOT NULL DEFAULT '',
                    fecha_recepcion TEXT NOT NULL,
                    origen TEXT NOT NULL,
                    estado_documento TEXT NOT NULL,
                    ruta_archivo TEXT NOT NULL DEFAULT '',
                    observaciones TEXT NOT NULL DEFAULT '',
                    fecha_eliminacion TEXT NOT NULL DEFAULT '',
                    motivo_eliminacion TEXT NOT NULL DEFAULT '',
                    reemplaza_documento_id TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(expediente_id) REFERENCES expedientes(expediente_id)
                );

                CREATE TABLE IF NOT EXISTS historial_eventos (
                    id_evento INTEGER PRIMARY KEY AUTOINCREMENT,
                    expediente_id TEXT NOT NULL,
                    fecha TEXT NOT NULL,
                    tipo_evento TEXT NOT NULL,
                    usuario_origen TEXT NOT NULL,
                    detalle TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS errores_ingestion (
                    id_error INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TEXT NOT NULL,
                    origen TEXT NOT NULL,
                    asunto TEXT NOT NULL DEFAULT '',
                    email_remitente TEXT NOT NULL DEFAULT '',
                    nombre_archivo TEXT NOT NULL DEFAULT '',
                    motivo_error TEXT NOT NULL,
                    detalle TEXT NOT NULL DEFAULT '',
                    resuelto INTEGER NOT NULL DEFAULT 0,
                    expediente_id_relacionado TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS emails_procesados (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL DEFAULT '',
                    expediente_id TEXT NOT NULL DEFAULT '',
                    asunto TEXT NOT NULL DEFAULT '',
                    remitente_email TEXT NOT NULL DEFAULT '',
                    fecha_recepcion TEXT NOT NULL DEFAULT '',
                    fecha_procesado TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    resultado_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_expedientes_estado ON expedientes(estado);
                CREATE INDEX IF NOT EXISTS idx_expedientes_farmacia ON expedientes(farmacia);
                CREATE INDEX IF NOT EXISTS idx_documentos_expediente ON documentos(expediente_id);
                CREATE INDEX IF NOT EXISTS idx_documentos_expediente_hash ON documentos(expediente_id, hash_archivo);
                CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails_procesados(message_id);
                CREATE INDEX IF NOT EXISTS idx_errores_resuelto ON errores_ingestion(resuelto);
                """
            )
            self._ensure_column(conn, "documentos", "fecha_eliminacion", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "documentos", "motivo_eliminacion", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "documentos", "reemplaza_documento_id", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_expediente(self, expediente: Dict[str, object]) -> str:
        now = bd.ahora_iso()
        expediente = dict(expediente)
        expediente["fecha_ultima_actualizacion"] = now
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT expediente_id FROM expedientes WHERE dedupe_key = ?",
                (expediente["dedupe_key"],),
            ).fetchone()
            expediente_id = str(existing["expediente_id"]) if existing else str(expediente["expediente_id"])
            expediente["expediente_id"] = expediente_id
            conn.execute(
                """
                INSERT INTO expedientes (
                    expediente_id, dedupe_key, cliente, farmacia, email_remitente, tipo_servicio,
                    periodo, ano, fecha_recepcion, estado, documentos_recibidos_json,
                    documentos_faltantes_json, observaciones, ruta_almacenamiento,
                    fecha_ultima_actualizacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(expediente_id) DO UPDATE SET
                    cliente=excluded.cliente,
                    email_remitente=excluded.email_remitente,
                    estado=excluded.estado,
                    documentos_recibidos_json=excluded.documentos_recibidos_json,
                    documentos_faltantes_json=excluded.documentos_faltantes_json,
                    observaciones=excluded.observaciones,
                    ruta_almacenamiento=excluded.ruta_almacenamiento,
                    fecha_ultima_actualizacion=excluded.fecha_ultima_actualizacion
                """,
                (
                    expediente_id,
                    expediente["dedupe_key"],
                    expediente["cliente"],
                    expediente["farmacia"],
                    expediente.get("email_remitente", ""),
                    expediente["tipo_servicio"],
                    expediente["periodo"],
                    expediente["ano"],
                    expediente["fecha_recepcion"],
                    expediente["estado"],
                    json.dumps(expediente.get("documentos_recibidos", []), ensure_ascii=False),
                    json.dumps(expediente.get("documentos_faltantes", []), ensure_ascii=False),
                    expediente.get("observaciones", ""),
                    expediente.get("ruta_almacenamiento", ""),
                    now,
                ),
            )
            return expediente_id

    def update_expediente_fields(self, expediente_id: str, **fields: object) -> None:
        if not fields:
            return
        fields["fecha_ultima_actualizacion"] = bd.ahora_iso()
        converted = {}
        for key, value in fields.items():
            if key == "documentos_recibidos":
                converted["documentos_recibidos_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "documentos_faltantes":
                converted["documentos_faltantes_json"] = json.dumps(value, ensure_ascii=False)
            else:
                converted[key] = value
        assignments = ", ".join(f"{key}=?" for key in converted)
        values = list(converted.values()) + [expediente_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE expedientes SET {assignments} WHERE expediente_id=?", values)

    def get_expediente(self, expediente_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM expedientes WHERE expediente_id=?", (expediente_id,)).fetchone()
        return self._row_to_expediente(row) if row else None

    def list_expedientes(self) -> List[Dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM expedientes ORDER BY fecha_ultima_actualizacion DESC").fetchall()
        return [self._row_to_expediente(row) for row in rows]

    def _row_to_expediente(self, row: sqlite3.Row) -> Dict[str, object]:
        data = dict(row)
        data["documentos_recibidos"] = json.loads(data.pop("documentos_recibidos_json") or "[]")
        data["documentos_faltantes"] = json.loads(data.pop("documentos_faltantes_json") or "[]")
        return data

    def save_documento(self, documento: Dict[str, object]) -> str:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documentos (
                    id_documento, expediente_id, nombre_original, nombre_normalizado,
                    tipo_documental, extension, tamano_bytes, hash_archivo, fecha_recepcion,
                    origen, estado_documento, ruta_archivo, observaciones, fecha_eliminacion,
                    motivo_eliminacion, reemplaza_documento_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_documento) DO UPDATE SET
                    estado_documento=excluded.estado_documento,
                    observaciones=excluded.observaciones,
                    fecha_eliminacion=excluded.fecha_eliminacion,
                    motivo_eliminacion=excluded.motivo_eliminacion,
                    reemplaza_documento_id=excluded.reemplaza_documento_id
                """,
                (
                    documento["id_documento"],
                    documento["expediente_id"],
                    documento["nombre_original"],
                    documento["nombre_normalizado"],
                    documento["tipo_documental"],
                    documento["extension"],
                    int(documento.get("tamano_bytes", 0)),
                    documento.get("hash_archivo", ""),
                    documento["fecha_recepcion"],
                    documento.get("origen", "manual"),
                    documento.get("estado_documento", bd.EstadoDocumento.RECIBIDO.value),
                    documento.get("ruta_archivo", ""),
                    documento.get("observaciones", ""),
                    documento.get("fecha_eliminacion", ""),
                    documento.get("motivo_eliminacion", ""),
                    documento.get("reemplaza_documento_id", ""),
                ),
            )
        return str(documento["id_documento"])

    def list_documentos(self, expediente_id: Optional[str] = None, include_deleted: bool = True) -> List[Dict[str, object]]:
        sql = "SELECT * FROM documentos"
        params: List[object] = []
        clauses = []
        if expediente_id:
            clauses.append("expediente_id=?")
            params.append(expediente_id)
        if not include_deleted:
            clauses.append("estado_documento != ?")
            params.append(bd.EstadoDocumento.ELIMINADO.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY fecha_recepcion DESC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_documento_by_hash(self, expediente_id: str, hash_archivo: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM documentos
                WHERE expediente_id=? AND hash_archivo=? AND estado_documento != ?
                LIMIT 1
                """,
                (expediente_id, hash_archivo, bd.EstadoDocumento.ELIMINADO.value),
            ).fetchone()
        return dict(row) if row else None

    def get_documento_activo_por_tipo(self, expediente_id: str, tipo_documental: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM documentos
                WHERE expediente_id=? AND tipo_documental=? AND estado_documento=?
                ORDER BY fecha_recepcion DESC
                LIMIT 1
                """,
                (expediente_id, tipo_documental, bd.EstadoDocumento.RECIBIDO.value),
            ).fetchone()
        return dict(row) if row else None

    def update_documento_estado(self, id_documento: str, estado: str, observaciones: str = "", **extra: object) -> None:
        fields = {"estado_documento": estado}
        if observaciones:
            fields["observaciones"] = observaciones
        fields.update(extra)
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE documentos SET {assignments} WHERE id_documento=?",
                list(fields.values()) + [id_documento],
            )

    def add_evento(self, expediente_id: str, tipo_evento: str, detalle: str, usuario_origen: str = "sistema") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO historial_eventos (expediente_id, fecha, tipo_evento, usuario_origen, detalle)
                VALUES (?, ?, ?, ?, ?)
                """,
                (expediente_id, bd.ahora_iso(), tipo_evento, usuario_origen, detalle),
            )

    def list_eventos(self, expediente_id: str) -> List[Dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM historial_eventos WHERE expediente_id=? ORDER BY fecha DESC, id_evento DESC",
                (expediente_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_error_ingestion(
        self,
        origen: str,
        motivo_error: str,
        detalle: str = "",
        asunto: str = "",
        email_remitente: str = "",
        nombre_archivo: str = "",
        expediente_id_relacionado: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO errores_ingestion (
                    fecha, origen, asunto, email_remitente, nombre_archivo, motivo_error,
                    detalle, resuelto, expediente_id_relacionado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    bd.ahora_iso(),
                    origen,
                    asunto,
                    email_remitente,
                    nombre_archivo,
                    motivo_error,
                    detalle,
                    expediente_id_relacionado,
                ),
            )
            return int(cur.lastrowid)

    def list_errores(self, expediente_id: Optional[str] = None, solo_pendientes: bool = False) -> List[Dict[str, object]]:
        clauses = []
        params: List[object] = []
        if expediente_id:
            clauses.append("expediente_id_relacionado=?")
            params.append(expediente_id)
        if solo_pendientes:
            clauses.append("resuelto=0")
        sql = "SELECT * FROM errores_ingestion"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY fecha DESC, id_error DESC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def count_errores_pendientes(self, expediente_id: Optional[str] = None) -> int:
        return len(self.list_errores(expediente_id=expediente_id, solo_pendientes=True))

    def marcar_error_resuelto(self, id_error: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE errores_ingestion SET resuelto=1 WHERE id_error=?", (id_error,))

    def get_email_procesado(self, message_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM emails_procesados WHERE message_id=?", (message_id,)).fetchone()
        return dict(row) if row else None

    def save_email_procesado(self, data: Dict[str, object]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO emails_procesados (
                    message_id, thread_id, expediente_id, asunto, remitente_email,
                    fecha_recepcion, fecha_procesado, estado, resultado_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (
                    data["message_id"],
                    data.get("thread_id", ""),
                    data.get("expediente_id", ""),
                    data.get("asunto", ""),
                    data.get("remitente_email", ""),
                    data.get("fecha_recepcion", ""),
                    bd.ahora_iso(),
                    data.get("estado", ""),
                    json.dumps(data.get("resultado", {}), ensure_ascii=False),
                ),
            )

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            return {
                "expedientes": conn.execute("SELECT COUNT(*) FROM expedientes").fetchone()[0],
                "documentos": conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0],
                "errores": conn.execute("SELECT COUNT(*) FROM errores_ingestion").fetchone()[0],
                "errores_pendientes": conn.execute("SELECT COUNT(*) FROM errores_ingestion WHERE resuelto=0").fetchone()[0],
                "emails_procesados": conn.execute("SELECT COUNT(*) FROM emails_procesados").fetchone()[0],
            }
