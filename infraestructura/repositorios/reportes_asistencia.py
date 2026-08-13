import hashlib
import os
import re
import secrets
import unicodedata
import uuid
from datetime import datetime
from html import escape

import pandas as pd
import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import generate_password_hash

class RepositorioReportesAsistenciaMixin:
    """Consultas y exportaciones operativas de registros de asistencia."""

    def exportar_asistencia_a_excel(self, project_id, start_date, end_date):
        """Ejecuta la operación exportar asistencia a excel y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name, a.timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(a.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(a.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'attendance_report_{timestamp}.xlsx').replace('\\', '/')

        workbook = xlsxwriter.Workbook(report_path)
        worksheet = workbook.add_worksheet()
        worksheet.write('A1', 'Reporte de Asistencias - AsisTec')
        headers = ['Matrícula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Fecha/Hora']
        for col, header in enumerate(headers):
            worksheet.write(1, col, header)
        for row_idx, row in enumerate(report_data, 2):
            worksheet.write(row_idx, 0, row[0])
            worksheet.write(row_idx, 1, row[1])
            worksheet.write(row_idx, 2, row[2])
            worksheet.write(row_idx, 3, row[3])
            worksheet.write(row_idx, 4, row[4])
            worksheet.write(row_idx, 5, row[5] or 'Sin proyecto')
            worksheet.write(row_idx, 6, row[6].strftime('%Y-%m-%d %H:%M:%S'))
        workbook.close()

        return report_path

    def obtener_evento_asistencia_reporte(self, event_id, start_date=None, end_date=None, project_id=None):
        """Ejecuta la operación obtener evento asistencia reporte y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera,
                   p.name, e.name, ae.event_type, ae.timestamp
            FROM attendance_events ae
            LEFT JOIN credentials cdr ON ae.credential_id = cdr.id
            LEFT JOIN participants part ON ae.participant_id = part.id
            LEFT JOIN students s ON part.legacy_student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            LEFT JOIN events e ON ae.event_id = e.id
            WHERE ae.event_id = %s
        """
        params = [event_id]
        if start_date:
            query += " AND DATE(ae.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(ae.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        query += " ORDER BY ae.timestamp DESC"
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()
        return report_data

    def exportar_evento_asistencia_a_excel(self, event_id, project_id, start_date, end_date):
        """Ejecuta la operación exportar evento asistencia a excel y devuelve el resultado correspondiente."""
        report_data = self.obtener_evento_asistencia_reporte(event_id, start_date, end_date, project_id)
        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'event_report_{timestamp}.xlsx').replace('\\', '/')

        workbook = xlsxwriter.Workbook(report_path)
        worksheet = workbook.add_worksheet()
        worksheet.write('A1', 'Reporte de Asistencias por Evento')
        headers = ['Matricula', 'Nombre', 'Apellido P', 'Apellido M', 'Carrera', 'Proyecto', 'Evento', 'Tipo', 'Fecha/Hora']
        for col, header in enumerate(headers):
            worksheet.write(1, col, header)
        for row_idx, row in enumerate(report_data, 2):
            for col_idx, value in enumerate(row):
                worksheet.write(row_idx, col_idx, value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else value)
        workbook.close()
        return report_path

    def exportar_evento_asistencia_a_pdf(self, event_id, project_id, start_date, end_date):
        """Ejecuta la operación exportar evento asistencia a pdf y devuelve el resultado correspondiente."""
        report_data = self.obtener_evento_asistencia_reporte(event_id, start_date, end_date, project_id)
        if not report_data:
            raise ValueError("No hay datos para el reporte")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        report_path = os.path.join(report_dir, f'event_report_{timestamp}.pdf').replace('\\', '/')

        doc = self._documento_pdf_asistencia(report_path)
        elements = self._evento_asistencia_pdf_elements(report_data, project_id, start_date, end_date)
        doc.build(elements)
        return report_path

    def _documento_pdf_asistencia(self, report_path):
        """Realiza internamente la operación attendance pdf doc."""
        return SimpleDocTemplate(
            report_path, pagesize=landscape(letter),
            leftMargin=0.35 * inch, rightMargin=0.35 * inch,
            topMargin=0.35 * inch, bottomMargin=0.35 * inch,
        )

    def _evento_asistencia_pdf_elements(self, report_data, project_id, start_date, end_date):
        """Realiza internamente la operación event attendance pdf elements."""
        styles = self._estilos_pdf_asistencia()
        filters = self._evento_reporte_filtros(report_data, project_id, start_date, end_date)
        table = self._tabla_pdf_asistencia(self._evento_reporte_tabla_datos(report_data), self._evento_reporte_widths())
        return self._encabezado_pdf_asistencia("Reporte de asistencias por evento", len(report_data), filters, styles) + [table]

    def _estilos_pdf_asistencia(self):
        """Realiza internamente la operación attendance pdf styles."""
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=20, leading=24, textColor=colors.HexColor("#08223c"), alignment=0))
        styles.add(ParagraphStyle("ReportSubtitle", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#475569")))
        styles.add(ParagraphStyle("ReportMeta", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#475569")))
        styles.add(ParagraphStyle("ReportCell", parent=styles["Normal"], fontSize=6.8, leading=8, textColor=colors.HexColor("#0f172a")))
        styles.add(ParagraphStyle("ReportHeader", parent=styles["Normal"], fontSize=7, leading=8, textColor=colors.white, alignment=1))
        styles.add(ParagraphStyle("MetricLabel", parent=styles["Normal"], fontSize=7, leading=8, textColor=colors.HexColor("#047c70"), alignment=1))
        styles.add(ParagraphStyle("MetricValue", parent=styles["Normal"], fontSize=15, leading=18, textColor=colors.HexColor("#08223c"), alignment=1))
        return styles

    def _evento_reporte_filtros(self, report_data, project_id, start_date, end_date):
        """Realiza internamente la operación event report filters."""
        return {
            "Evento": report_data[0][6] or "Sin evento",
            "Proyecto": self._seleccionado_proyecto_nombre(report_data, project_id),
            "Fechas": self._fecha_range_label(start_date, end_date),
        }

    def _seleccionado_proyecto_nombre(self, report_data, project_id):
        """Realiza internamente la operación selected project name."""
        if not project_id:
            return "Todos"
        return report_data[0][5] or f"Proyecto {project_id}"

    def _fecha_range_label(self, start_date, end_date):
        """Realiza internamente la operación date range label."""
        if start_date and end_date:
            return f"{start_date} a {end_date}"
        return start_date or end_date or "Todas"

    def _encabezado_pdf_asistencia(self, title, total, filters, styles):
        """Realiza internamente la operación attendance pdf header."""
        meta = " | ".join(f"{key}: {value}" for key, value in filters.items())
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        return [
            Paragraph(title, styles["Title"]),
            Paragraph(f"Total de registros: {total} | Generado: {generated}", styles["ReportMeta"]),
            Paragraph(meta, styles["ReportMeta"]),
            Spacer(1, 0.12 * inch),
        ]

    def _evento_reporte_tabla_datos(self, report_data):
        """Realiza internamente la operación event report table data."""
        headers = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Evento", "Tipo", "Fecha/Hora"]
        rows = [self._evento_reporte_fila(row) for row in report_data]
        return [headers] + rows

    def _evento_reporte_fila(self, row):
        """Realiza internamente la operación event report row."""
        return [
            row[0], row[1], row[2], row[3], row[4], row[5] or "Sin proyecto",
            row[6] or "Sin evento", row[7] or "entrada", self._formatear_reporte_fecha_hora(row[8]),
        ]

    def _formatear_reporte_fecha_hora(self, value):
        """Realiza internamente la operación format report datetime."""
        return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else value

    def _tabla_pdf_asistencia(self, datos, widths):
        """Realiza internamente la operación attendance pdf table."""
        styles = self._estilos_pdf_asistencia()
        table_data = [[self._pdf_celda(value, styles["ReportHeader" if row == 0 else "ReportCell"]) for value in line] for row, line in enumerate(datos)]
        table = Table(table_data, colWidths=widths, repeatRows=1)
        table.setStyle(self._tabla_pdf_asistencia_style())
        return table

    def _pdf_celda(self, value, style):
        """Realiza internamente la operación pdf cell."""
        return Paragraph(escape(str(value or "")), style)

    def _evento_reporte_widths(self):
        """Realiza internamente la operación event report widths."""
        return [value * inch for value in [0.78, 0.9, 0.85, 0.85, 1.25, 1.15, 1.15, 0.72, 1.05]]

    def _tabla_pdf_asistencia_style(self):
        """Realiza internamente la operación attendance pdf table style."""
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#08223c")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dbe4ee")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])

    def exportar_asistencia_a_pdf(self, project_id, start_date, end_date):
        """Ejecuta la operación exportar asistencia a pdf y devuelve el resultado correspondiente."""
        conn = self.conectar()
        c = conn.cursor()
        query = """
            SELECT s.matricula, s.first_name, s.last_name_p, s.last_name_m, s.carrera, p.name, a.timestamp
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            LEFT JOIN projects p ON s.project_id = p.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND DATE(a.timestamp) >= %s"
            params.append(start_date)
        if end_date:
            query += " AND DATE(a.timestamp) <= %s"
            params.append(end_date)
        if project_id:
            query += " AND s.project_id = %s"
            params.append(project_id)
        c.execute(query, params)
        report_data = c.fetchall()
        conn.close()

        if not report_data:
            raise ValueError("No hay datos para el reporte")

        report_path = self._asistencia_reporte_ruta("attendance_report", "pdf")
        doc = self._documento_pdf_asistencia(report_path)
        elements = self._heredado_asistencia_pdf_elements(report_data, project_id, start_date, end_date)
        doc.build(elements)
        return report_path

    def _asistencia_reporte_ruta(self, prefix, extension):
        """Realiza internamente la operación attendance report path."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_dir = 'static/reports'
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
        return os.path.join(report_dir, f'{prefix}_{timestamp}.{extension}').replace('\\', '/')

    def _heredado_asistencia_pdf_elements(self, report_data, project_id, start_date, end_date):
        """Realiza internamente la operación legacy attendance pdf elements."""
        styles = self._estilos_pdf_asistencia()
        filters = {"Proyecto": project_id or "Todos", "Fechas": self._fecha_range_label(start_date, end_date)}
        table = self._tabla_pdf_asistencia(self._heredado_reporte_tabla_datos(report_data), self._heredado_reporte_widths())
        return self._encabezado_pdf_asistencia("Reporte de asistencias - AsisTec", len(report_data), filters, styles) + [table]

    def _heredado_reporte_tabla_datos(self, report_data):
        """Realiza internamente la operación legacy report table data."""
        headers = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Fecha/Hora"]
        rows = [self._heredado_reporte_fila(row) for row in report_data]
        return [headers] + rows

    def _heredado_reporte_fila(self, row):
        """Realiza internamente la operación legacy report row."""
        return [row[0], row[1], row[2], row[3], row[4], row[5] or "Sin proyecto", self._formatear_reporte_fecha_hora(row[6])]

    def _heredado_reporte_widths(self):
        """Realiza internamente la operación legacy report widths."""
        return [value * inch for value in [0.9, 1.05, 1.0, 1.0, 1.55, 1.45, 1.15]]

# Alias temporales para compatibilidad con la API anterior.
RepositorioReportesAsistenciaMixin.export_attendance_to_excel = RepositorioReportesAsistenciaMixin.exportar_asistencia_a_excel
RepositorioReportesAsistenciaMixin.export_attendance_to_pdf = RepositorioReportesAsistenciaMixin.exportar_asistencia_a_pdf
RepositorioReportesAsistenciaMixin.export_event_attendance_to_excel = RepositorioReportesAsistenciaMixin.exportar_evento_asistencia_a_excel
RepositorioReportesAsistenciaMixin.export_event_attendance_to_pdf = RepositorioReportesAsistenciaMixin.exportar_evento_asistencia_a_pdf
RepositorioReportesAsistenciaMixin.get_event_attendance_report = RepositorioReportesAsistenciaMixin.obtener_evento_asistencia_reporte

# Alias temporales para compatibilidad con la API anterior.
RepositorioReportesAsistenciaMixin._attendance_report_path = RepositorioReportesAsistenciaMixin._asistencia_reporte_ruta
RepositorioReportesAsistenciaMixin._date_range_label = RepositorioReportesAsistenciaMixin._fecha_range_label
RepositorioReportesAsistenciaMixin._event_attendance_pdf_elements = RepositorioReportesAsistenciaMixin._evento_asistencia_pdf_elements
RepositorioReportesAsistenciaMixin._event_report_filters = RepositorioReportesAsistenciaMixin._evento_reporte_filtros
RepositorioReportesAsistenciaMixin._event_report_row = RepositorioReportesAsistenciaMixin._evento_reporte_fila
RepositorioReportesAsistenciaMixin._event_report_table_data = RepositorioReportesAsistenciaMixin._evento_reporte_tabla_datos
RepositorioReportesAsistenciaMixin._event_report_widths = RepositorioReportesAsistenciaMixin._evento_reporte_widths
RepositorioReportesAsistenciaMixin._format_report_datetime = RepositorioReportesAsistenciaMixin._formatear_reporte_fecha_hora
RepositorioReportesAsistenciaMixin._legacy_attendance_pdf_elements = RepositorioReportesAsistenciaMixin._heredado_asistencia_pdf_elements
RepositorioReportesAsistenciaMixin._legacy_report_row = RepositorioReportesAsistenciaMixin._heredado_reporte_fila
RepositorioReportesAsistenciaMixin._legacy_report_table_data = RepositorioReportesAsistenciaMixin._heredado_reporte_tabla_datos
RepositorioReportesAsistenciaMixin._legacy_report_widths = RepositorioReportesAsistenciaMixin._heredado_reporte_widths
RepositorioReportesAsistenciaMixin._pdf_cell = RepositorioReportesAsistenciaMixin._pdf_celda
RepositorioReportesAsistenciaMixin._selected_project_name = RepositorioReportesAsistenciaMixin._seleccionado_proyecto_nombre
