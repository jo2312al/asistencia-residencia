import mysql.connector
from mysql.connector import Error
import pandas as pd
import uuid
import os
import re
import hashlib
import secrets
import unicodedata
from datetime import datetime
from html import escape
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import xlsxwriter
from werkzeug.security import generate_password_hash

from infraestructura.repositorios.estudiantes import RepositorioEstudiantesMixin
from infraestructura.repositorios.eventos import RepositorioEventosMixin
from infraestructura.repositorios.participantes import RepositorioParticipantesMixin
from infraestructura.repositorios.reportes_asistencia import RepositorioReportesAsistenciaMixin
from infraestructura.repositorios.reportes_ejecutivos import RepositorioReportesEjecutivosMixin
from infraestructura.repositorios.usuarios import RepositorioUsuariosMixin

class GestorBaseDatos(
    RepositorioUsuariosMixin,
    RepositorioParticipantesMixin,
    RepositorioEventosMixin,
    RepositorioReportesEjecutivosMixin,
    RepositorioReportesAsistenciaMixin,
    RepositorioEstudiantesMixin,
):
    VALID_ROLES = {'adminsuperior', 'admin', 'staff', 'guest'}
    BASE_REGISTRATION_FIELD_NAMES = {'nombre', 'apellido paterno', 'apellido materno', 'matricula', 'carrera'}

    def __init__(self, db_config):
        """Realiza internamente la operaciÃ³n init."""
        self.db_config = db_config
        self.pool = self._crear_grupo_conexiones()
        self.inicializar_bd()

    def _crear_grupo_conexiones(self):
        """Realiza internamente la operaciÃ³n crear grupo conexiones."""
        pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        return mysql.connector.pooling.MySQLConnectionPool(
            pool_name=f"asistec_{os.getpid()}",
            pool_size=pool_size,
            **self.db_config
        )

    def conectar(self):
        """Ejecuta la operaciÃ³n conectar y devuelve el resultado correspondiente."""
        return self.pool.get_connection()

    def inicializar_bd(self):
        """Ejecuta la operaciÃ³n inicializar bd y devuelve el resultado correspondiente."""
        conexion = self.conectar()
        try:
            cursor = conexion.cursor()
            self._crear_tablas_base(cursor)
            self._crear_tablas_campos(cursor)
            self._crear_tablas_operacion(cursor)
            self._migrar_esquema(cursor)
            conexion.commit()
        finally:
            try:
                conexion.close()
            except Error:
                pass

    def _crear_tablas_base(self, cursor):
        """Realiza internamente la operaciÃ³n crear tablas base."""
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            username VARCHAR(50) PRIMARY KEY,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'guest')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            event_id INT NULL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            description TEXT,
            start_datetime DATETIME NULL,
            end_datetime DATETIME NULL,
            location VARCHAR(255),
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            event_type VARCHAR(50) NOT NULL DEFAULT 'general',
            duplicate_policy VARCHAR(50) NOT NULL DEFAULT 'once_per_day',
            created_at DATETIME NULL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS students (
            id VARCHAR(36) PRIMARY KEY,
            first_name VARCHAR(50) NOT NULL,
            last_name_p VARCHAR(50) NOT NULL,
            last_name_m VARCHAR(50) NOT NULL,
            matricula VARCHAR(20) UNIQUE NOT NULL,
            carrera VARCHAR(100) NOT NULL,
            event_id INT NULL,
            project_id INT,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (project_id) REFERENCES projects(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id INT AUTO_INCREMENT PRIMARY KEY,
            student_id VARCHAR(36),
            timestamp DATETIME,
            FOREIGN KEY (student_id) REFERENCES students(id))''')

    def _crear_tablas_campos(self, cursor):
        """Realiza internamente la operaciÃ³n crear tablas campos."""
        cursor.execute('''CREATE TABLE IF NOT EXISTS project_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            project_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            field_type VARCHAR(50) NOT NULL DEFAULT 'text',
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            display_order INT NOT NULL DEFAULT 0,
            created_at DATETIME NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS event_fields (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT NOT NULL,
            name VARCHAR(100) NOT NULL,
            field_type VARCHAR(50) NOT NULL DEFAULT 'text',
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            display_order INT NOT NULL DEFAULT 0,
            created_at DATETIME NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS participants (
            id VARCHAR(36) PRIMARY KEY,
            full_name VARCHAR(150) NOT NULL,
            email VARCHAR(255),
            phone VARCHAR(50),
            participant_type VARCHAR(30) NOT NULL DEFAULT 'alumno',
            event_id INT NULL,
            project_id INT,
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            legacy_student_id VARCHAR(36) UNIQUE,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (legacy_student_id) REFERENCES students(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS participant_field_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            field_id INT NOT NULL,
            value TEXT,
            created_at DATETIME NULL,
            updated_at DATETIME NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (field_id) REFERENCES project_fields(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS participant_event_field_values (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            field_id INT NOT NULL,
            value TEXT,
            created_at DATETIME NULL,
            updated_at DATETIME NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (field_id) REFERENCES event_fields(id))''')

    def _crear_tablas_operacion(self, cursor):
        """Realiza internamente la operaciÃ³n crear tablas operacion."""
        cursor.execute('''CREATE TABLE IF NOT EXISTS credentials (
            id VARCHAR(36) PRIMARY KEY,
            participant_id VARCHAR(36) NOT NULL,
            token VARCHAR(80) UNIQUE NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            qr_path VARCHAR(255),
            digital_url VARCHAR(500),
            sent_status VARCHAR(30) NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS email_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT NULL,
            recipient VARCHAR(255) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            status VARCHAR(30) NOT NULL,
            error TEXT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS attendance_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            participant_id VARCHAR(36),
            credential_id VARCHAR(36),
            legacy_attendance_id INT,
            event_type VARCHAR(50) NOT NULL DEFAULT 'entrada',
            timestamp DATETIME NOT NULL,
            event_id INT NULL,
            FOREIGN KEY (participant_id) REFERENCES participants(id),
            FOREIGN KEY (credential_id) REFERENCES credentials(id),
            FOREIGN KEY (legacy_attendance_id) REFERENCES attendance(id),
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS event_templates (
            event_id INT PRIMARY KEY,
            email_subject VARCHAR(255),
            email_body TEXT,
            credential_style VARCHAR(50) NOT NULL DEFAULT 'standard',
            logo_filename VARCHAR(255),
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_event_permissions (
            username VARCHAR(50) NOT NULL,
            event_id INT NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (username, event_id),
            FOREIGN KEY (username) REFERENCES users(username),
            FOREIGN KEY (event_id) REFERENCES events(id))''')

    def _migrar_esquema(self, cursor):
        """Realiza internamente la operaciÃ³n migrar esquema."""
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'users'
            AND COLUMN_NAME = 'role'
        """, (self.db_config['database'],))
        has_role_column = cursor.fetchone()[0] > 0
        if not has_role_column:
            cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'guest'")
        self._asegurar_columna(cursor, 'events', 'description', 'TEXT')
        self._asegurar_columna(cursor, 'events', 'start_datetime', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'events', 'end_datetime', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'events', 'location', 'VARCHAR(255)')
        self._asegurar_columna(cursor, 'events', 'status', "VARCHAR(30) NOT NULL DEFAULT 'active'")
        self._asegurar_columna(cursor, 'events', 'event_type', "VARCHAR(50) NOT NULL DEFAULT 'general'")
        self._asegurar_columna(cursor, 'events', 'duplicate_policy', "VARCHAR(50) NOT NULL DEFAULT 'once_per_day'")
        self._asegurar_columna(cursor, 'events', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'projects', 'event_id', 'INT NULL')
        self._asegurar_proyecto_evento_unico_indice(cursor)
        self._asegurar_columna(cursor, 'students', 'event_id', 'INT NULL')
        self._asegurar_columna(cursor, 'participants', 'event_id', 'INT NULL')
        self._asegurar_columna(cursor, 'participants', 'legacy_student_id', 'VARCHAR(36)')
        self._asegurar_columna(cursor, 'participants', 'participant_type', "VARCHAR(30) NOT NULL DEFAULT 'alumno'")
        self._asegurar_columna(cursor, 'project_fields', 'field_type', "VARCHAR(50) NOT NULL DEFAULT 'text'")
        self._asegurar_columna(cursor, 'project_fields', 'display_order', 'INT NOT NULL DEFAULT 0')
        self._asegurar_columna(cursor, 'project_fields', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participant_field_values', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participant_field_values', 'updated_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participant_event_field_values', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participant_event_field_values', 'updated_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participants', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'participants', 'updated_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'credentials', 'qr_path', 'VARCHAR(255)')
        self._asegurar_columna(cursor, 'credentials', 'digital_url', 'VARCHAR(500)')
        self._asegurar_columna(cursor, 'credentials', 'sent_status', "VARCHAR(30) NOT NULL DEFAULT 'pending'")
        self._asegurar_columna(cursor, 'credentials', 'created_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'credentials', 'updated_at', 'DATETIME NULL')
        self._asegurar_columna(cursor, 'attendance_events', 'credential_id', 'VARCHAR(36)')
        self._asegurar_columna(cursor, 'attendance_events', 'legacy_attendance_id', 'INT')
        self._asegurar_columna(cursor, 'attendance_events', 'event_id', 'INT NULL')
        self._asegurar_rendimiento_indices(cursor)


    def _asegurar_columna(self, cursor, table_name, column_name, definition):
        """Realiza internamente la operaciÃ³n asegurar columna."""
        if self._columna_exists(cursor, table_name, column_name):
            return
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _asegurar_proyecto_evento_unico_indice(self, cursor):
        """Realiza internamente la operaciÃ³n asegurar proyecto evento unico indice."""
        cursor.execute("""
            SELECT INDEX_NAME
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'projects'
              AND NON_UNIQUE = 0
              AND INDEX_NAME <> 'PRIMARY'
            GROUP BY INDEX_NAME
            HAVING GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) = 'name'
        """, (self.db_config['database'],))
        for (index_name,) in cursor.fetchall():
            cursor.execute(f"ALTER TABLE projects DROP INDEX `{index_name}`")

        cursor.execute("""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'projects'
              AND INDEX_NAME = 'uq_projects_name_event'
        """, (self.db_config['database'],))
        if not cursor.fetchone()[0]:
            cursor.execute("CREATE UNIQUE INDEX uq_projects_name_event ON projects (name, event_id)")

    def _asegurar_rendimiento_indices(self, cursor):
        """Realiza internamente la operaciÃ³n asegurar rendimiento indices."""
        indexes = [
            ('students', 'idx_students_event_project', 'event_id, project_id'),
            ('students', 'idx_students_event_matricula', 'event_id, matricula'),
            ('students', 'idx_students_event_last_name', 'event_id, last_name_p, first_name'),
            ('participants', 'idx_participants_legacy_student_fast', 'legacy_student_id'),
            ('participants', 'idx_participants_event_project', 'event_id, project_id'),
            ('credentials', 'idx_credentials_participant_created', 'participant_id, created_at'),
            ('credentials', 'idx_credentials_token_fast', 'token'),
            ('attendance_events', 'idx_attendance_event_participant_type', 'event_id, participant_id, event_type, timestamp'),
            ('participant_field_values', 'idx_pfv_participant_field', 'participant_id, field_id'),
            ('participant_event_field_values', 'idx_pefv_participant_field', 'participant_id, field_id'),
            ('projects', 'idx_projects_event_name_fast', 'event_id, name'),
        ]
        for table_name, index_name, columns in indexes:
            self._asegurar_indice(cursor, table_name, index_name, columns)

    def _asegurar_indice(self, cursor, table_name, index_name, columns):
        """Realiza internamente la operaciÃ³n asegurar indice."""
        if self._indice_exists(cursor, table_name, index_name):
            return
        try:
            cursor.execute(f"CREATE INDEX {index_name} ON {table_name} ({columns})")
        except Error as error:
            if getattr(error, "errno", None) != 1061:
                raise

    def _indice_exists(self, cursor, table_name, index_name):
        """Realiza internamente la operaciÃ³n index exists."""
        cursor.execute("""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
        """, (self.db_config['database'], table_name, index_name))
        return cursor.fetchone()[0] > 0

    def _columna_exists(self, cursor, table_name, column_name):
        """Realiza internamente la operaciÃ³n column exists."""
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = %s
            AND COLUMN_NAME = %s
        """, (self.db_config['database'], table_name, column_name))
        return cursor.fetchone()[0] > 0

    def _obtener_columna_datos_tipo(self, cursor, table_name, column_name):
        """Realiza internamente la operaciÃ³n obtener columna datos tipo."""
        cursor.execute("""
            SELECT DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = %s
            AND COLUMN_NAME = %s
        """, (self.db_config['database'], table_name, column_name))
        row = cursor.fetchone()
        return row[0] if row else None


# Alias temporal para integraciones que aÃºn importan el nombre anterior.
DatabaseManager = GestorBaseDatos

# Alias temporales para compatibilidad con la API anterior.
GestorBaseDatos._create_pool = GestorBaseDatos._crear_grupo_conexiones
GestorBaseDatos._ensure_column = GestorBaseDatos._asegurar_columna
GestorBaseDatos._ensure_index = GestorBaseDatos._asegurar_indice
GestorBaseDatos._ensure_performance_indexes = GestorBaseDatos._asegurar_rendimiento_indices
GestorBaseDatos._ensure_project_event_unique_index = GestorBaseDatos._asegurar_proyecto_evento_unico_indice
GestorBaseDatos._get_column_data_type = GestorBaseDatos._obtener_columna_datos_tipo
GestorBaseDatos.connect = GestorBaseDatos.conectar
GestorBaseDatos.init_db = GestorBaseDatos.inicializar_bd

# Alias temporales para compatibilidad con la API anterior.
GestorBaseDatos._column_exists = GestorBaseDatos._columna_exists
GestorBaseDatos._index_exists = GestorBaseDatos._indice_exists
