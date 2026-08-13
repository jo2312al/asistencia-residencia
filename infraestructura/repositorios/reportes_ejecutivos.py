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

class RepositorioReportesEjecutivosMixin:
    """Indicadores y reporte final consolidado de un evento."""

    def resumen_ejecutivo_evento(self, event_id):
        """Ejecuta la operación resumen ejecutivo evento y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            c = conn.cursor(dictionary=True)
            participantes = self._estadisticas_participantes_evento(c, event_id)
            asistencias = self._estadisticas_asistencia_evento(c, event_id)
            hora_pico = self._hora_pico_evento(c, event_id)
            return self._armar_resumen_ejecutivo(participantes, asistencias, hora_pico)
        finally:
            conn.close()

    def _estadisticas_participantes_evento(self, cursor, event_id):
        """Realiza internamente la operación estadisticas participantes evento."""
        cursor.execute(self._consulta_estadisticas_participantes(), (event_id,))
        return cursor.fetchone()

    def _consulta_estadisticas_participantes(self):
        """Realiza internamente la operación consulta estadisticas participantes."""
        return """SELECT COUNT(*) AS total,
                         SUM(participant_type = 'alumno') AS alumnos,
                         SUM(participant_type = 'asesor') AS asesores
                  FROM participants WHERE event_id = %s AND status = 'active'"""

    def _estadisticas_asistencia_evento(self, cursor, event_id):
        """Realiza internamente la operación estadisticas asistencia evento."""
        cursor.execute("""SELECT COUNT(*) AS registros, COUNT(DISTINCT participant_id) AS presentes
                          FROM attendance_events WHERE event_id = %s""", (event_id,))
        return cursor.fetchone()

    def _armar_resumen_ejecutivo(self, participantes, asistencias, hora_pico):
        """Realiza internamente la operación armar resumen ejecutivo."""
        total = participantes["total"] or 0
        presentes = asistencias["presentes"] or 0
        return {
            "participantes": total,
            "asistencias": asistencias["registros"] or 0,
            "presentes": presentes,
            "pendientes": max(total - presentes, 0),
            "porcentaje": round((presentes / total) * 100, 1) if total else 0,
            "alumnos": participantes["alumnos"] or 0,
            "asesores": participantes["asesores"] or 0,
            "hora_pico": hora_pico or "Sin registros",
        }

    def _hora_pico_evento(self, cursor, event_id):
        """Realiza internamente la operación hora pico evento."""
        cursor.execute("""SELECT HOUR(timestamp) AS hora, COUNT(*) AS total
                          FROM attendance_events WHERE event_id = %s
                          GROUP BY HOUR(timestamp) ORDER BY total DESC LIMIT 1""", (event_id,))
        row = cursor.fetchone()
        return f"{int(row['hora']):02d}:00" if row and row["hora"] is not None else None

    def proyectos_con_asistencia(self, event_id):
        """Ejecuta la operación proyectos con asistencia y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            c = conn.cursor(dictionary=True)
            c.execute(self._consulta_proyectos_con_asistencia(), (event_id, event_id, event_id))
            return c.fetchall()
        finally:
            conn.close()

    def _consulta_proyectos_con_asistencia(self):
        """Realiza internamente la operación consulta proyectos con asistencia."""
        return """SELECT pr.id, pr.name,
                         COUNT(DISTINCT p.id) AS participantes,
                         COUNT(DISTINCT ae.participant_id) AS presentes,
                         COUNT(ae.id) AS registros
                  FROM projects pr
                  LEFT JOIN participants p ON p.project_id = pr.id AND p.event_id = %s AND p.status = 'active'
                  LEFT JOIN attendance_events ae ON ae.participant_id = p.id AND ae.event_id = %s
                  WHERE pr.event_id = %s
                  GROUP BY pr.id, pr.name
                  ORDER BY presentes DESC, pr.name"""

    def detalle_asistencia_proyecto(self, event_id, project_id):
        """Ejecuta la operación detalle asistencia proyecto y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            c = conn.cursor(dictionary=True)
            c.execute(self._consulta_detalle_asistencia_proyecto(), (event_id, project_id, event_id))
            return c.fetchall()
        finally:
            conn.close()

    def _consulta_detalle_asistencia_proyecto(self):
        """Realiza internamente la operación consulta detalle asistencia proyecto."""
        return """SELECT p.full_name, p.participant_type, s.matricula, s.carrera,
                         MAX(ae.timestamp) AS ultima_asistencia, COUNT(ae.id) AS registros
                  FROM participants p
                  LEFT JOIN students s ON p.legacy_student_id = s.id
                  LEFT JOIN attendance_events ae ON ae.participant_id = p.id AND ae.event_id = %s
                  WHERE p.project_id = %s AND p.event_id = %s AND p.status = 'active'
                  GROUP BY p.id, p.full_name, p.participant_type, s.matricula, s.carrera
                  ORDER BY ultima_asistencia IS NULL, p.participant_type, p.full_name"""

    def ultima_asistencia_participante(self, participant_id, event_id=None, event_type=None):
        """Ejecuta la operación ultima asistencia participante y devuelve el resultado correspondiente."""
        conn = self.conectar()
        try:
            c = conn.cursor(dictionary=True)
            query, params = self._consulta_ultima_asistencia(participant_id, event_id, event_type)
            c.execute(query, params)
            return c.fetchone()
        finally:
            conn.close()

    def _consulta_ultima_asistencia(self, participant_id, event_id, event_type):
        """Realiza internamente la operación consulta ultima asistencia."""
        query = """SELECT ae.timestamp, ae.event_type, e.name AS event_name
                   FROM attendance_events ae LEFT JOIN events e ON ae.event_id = e.id
                   WHERE ae.participant_id = %s"""
        params = [participant_id]
        if event_id:
            query += " AND ae.event_id = %s"
            params.append(event_id)
        if event_type:
            query += " AND ae.event_type = %s"
            params.append(event_type)
        return query + " ORDER BY ae.timestamp DESC LIMIT 1", params

    def exportar_reporte_final_evento_pdf(self, event_id):
        """Ejecuta la operación exportar reporte final evento pdf y devuelve el resultado correspondiente."""
        evento = self.obtener_evento(event_id)
        resumen = self.resumen_ejecutivo_evento(event_id)
        proyectos = self.proyectos_con_asistencia(event_id)
        datos = self.obtener_evento_asistencia_reporte(event_id)
        ruta = self._asistencia_reporte_ruta("reporte_final_evento", "pdf")
        self._crear_reporte_final_pdf(ruta, evento, resumen, proyectos, datos)
        return ruta

    def exportar_reporte_final_evento_excel(self, event_id):
        """Ejecuta la operación exportar reporte final evento excel y devuelve el resultado correspondiente."""
        evento = self.obtener_evento(event_id)
        resumen = self.resumen_ejecutivo_evento(event_id)
        proyectos = self.proyectos_con_asistencia(event_id)
        datos = self.obtener_evento_asistencia_reporte(event_id)
        ruta = self._asistencia_reporte_ruta("reporte_final_evento", "xlsx")
        self._crear_reporte_final_excel(ruta, evento, resumen, proyectos, datos)
        return ruta

    def _crear_reporte_final_pdf(self, ruta, evento, resumen, proyectos, datos):
        """Realiza internamente la operación crear reporte final pdf."""
        doc = self._attendance_pdf_doc(ruta)
        estilos = self._attendance_pdf_styles()
        elementos = self._reporte_final_elementos(evento, resumen, proyectos, datos, estilos)
        doc.build(elementos)

    def _reporte_final_elementos(self, evento, resumen, proyectos, datos, estilos):
        """Realiza internamente la operación reporte final elementos."""
        elementos = self._portada_reporte_final(evento, resumen, estilos)
        elementos += self._seccion_proyectos_final(proyectos, estilos)
        elementos += self._seccion_detalle_final(datos, estilos)
        elementos.append(self._bloque_firmas_final(estilos))
        return elementos

    def _portada_reporte_final(self, evento, resumen, estilos):
        """Realiza internamente la operación portada reporte final."""
        return [
            self._encabezado_reporte_final(evento, estilos),
            Spacer(1, 0.16 * inch),
            self._tabla_metricas_reporte(resumen, estilos),
            Spacer(1, 0.22 * inch),
        ]

    def _encabezado_reporte_final(self, evento, estilos):
        """Realiza internamente la operación encabezado reporte final."""
        nombre = escape(str(evento[1] if evento else "Evento"))
        generado = datetime.now().strftime("%Y-%m-%d %H:%M")
        titulo = Paragraph("Reporte final oficial", estilos["ReportTitle"])
        meta = Paragraph(f"AsisTec | Evento: {nombre} | Generado: {generado}", estilos["ReportSubtitle"])
        tabla = Table([[titulo], [meta]], colWidths=[10 * inch])
        tabla.setStyle(self._estilo_banda_reporte_final())
        return tabla

    def _estilo_banda_reporte_final(self):
        """Realiza internamente la operación estilo banda reporte final."""
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eefdf5")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#10a779")),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ])

    def _tabla_metricas_reporte(self, resumen, estilos):
        """Realiza internamente la operación tabla metricas reporte."""
        metricas = self._metricas_reporte_final(resumen, estilos)
        tabla = Table([metricas], colWidths=[1.65 * inch] * 6)
        tabla.setStyle(self._estilo_metricas_reporte())
        return tabla

    def _metricas_reporte_final(self, resumen, estilos):
        """Realiza internamente la operación metricas reporte final."""
        datos = [("Participantes", resumen["participantes"]), ("Presentes", resumen["presentes"]), ("Pendientes", resumen["pendientes"]), ("Asistencia", f"{resumen['porcentaje']}%"), ("Registros", resumen["asistencias"]), ("Hora pico", resumen["hora_pico"])]
        return [self._celda_metrica_reporte(label, value, estilos) for label, value in datos]

    def _celda_metrica_reporte(self, label, value, estilos):
        """Realiza internamente la operación celda metrica reporte."""
        return [Paragraph(str(label), estilos["MetricLabel"]), Paragraph(str(value), estilos["MetricValue"])]

    def _estilo_metricas_reporte(self):
        """Realiza internamente la operación estilo metricas reporte."""
        return TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe4ee")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dbe4ee")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])

    def _seccion_proyectos_final(self, proyectos, estilos):
        """Realiza internamente la operación seccion proyectos final."""
        return [Paragraph("Resumen por proyecto", estilos["Heading2"]), self._tabla_resumen_proyectos(proyectos), Spacer(1, 0.18 * inch)]

    def _tabla_resumen_proyectos(self, proyectos):
        """Realiza internamente la operación tabla resumen proyectos."""
        filas = [["Proyecto", "Participantes", "Presentes", "Pendientes", "% asistencia"]]
        filas += [self._fila_resumen_proyecto(proyecto) for proyecto in proyectos]
        tabla = Table(filas, colWidths=[3.4 * inch, 1.15 * inch, 1.05 * inch, 1.05 * inch, 1.15 * inch], repeatRows=1)
        tabla.setStyle(self._attendance_pdf_table_style())
        return tabla

    def _seccion_detalle_final(self, datos, estilos):
        """Realiza internamente la operación seccion detalle final."""
        titulo = Paragraph("Detalle de asistencia", estilos["Heading2"])
        if not datos:
            return [titulo, Paragraph("Sin registros de asistencia capturados.", estilos["ReportMeta"])]
        tabla = self._attendance_pdf_table(self._evento_reporte_tabla_datos(datos), self._evento_reporte_widths())
        return [titulo, tabla, Spacer(1, 0.18 * inch)]

    def _bloque_firmas_final(self, estilos):
        """Realiza internamente la operación bloque firmas final."""
        fila = [["Responsable del evento", "Coordinacion", "Validacion"]]
        tabla = Table(fila, colWidths=[2.45 * inch] * 3)
        tabla.setStyle(self._estilo_firmas_final())
        return tabla

    def _estilo_firmas_final(self):
        """Realiza internamente la operación estilo firmas final."""
        return TableStyle([
            ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.HexColor("#64748b")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#475569")),
            ("TOPPADDING", (0, 0), (-1, -1), 18),
        ])

    def _fila_resumen_proyecto(self, proyecto):
        """Realiza internamente la operación fila resumen proyecto."""
        participantes = proyecto["participantes"] or 0
        presentes = proyecto["presentes"] or 0
        porcentaje = round((presentes / participantes) * 100, 1) if participantes else 0
        return [proyecto["name"], participantes, presentes, max(participantes - presentes, 0), f"{porcentaje}%"]

    def _crear_reporte_final_excel(self, ruta, evento, resumen, proyectos, datos):
        """Realiza internamente la operación crear reporte final excel."""
        workbook = xlsxwriter.Workbook(ruta)
        self._hoja_resumen_final(workbook, evento, resumen, proyectos)
        self._hoja_asistencia_final(workbook, datos)
        workbook.close()

    def _hoja_resumen_final(self, workbook, evento, resumen, proyectos):
        """Realiza internamente la operación hoja resumen final."""
        hoja = workbook.add_worksheet("Resumen")
        hoja.write("A1", "Reporte final oficial - AsisTec")
        hoja.write("A2", evento[1] if evento else "Evento")
        for fila, item in enumerate(resumen.items(), 4):
            hoja.write(fila, 0, item[0])
            hoja.write(fila, 1, item[1])
        self._escribir_proyectos_final(hoja, proyectos)

    def _escribir_proyectos_final(self, hoja, proyectos):
        """Realiza internamente la operación escribir proyectos final."""
        inicio = 14
        encabezados = ["Proyecto", "Participantes", "Presentes", "Registros"]
        for col, encabezado in enumerate(encabezados):
            hoja.write(inicio, col, encabezado)
        for fila, proyecto in enumerate(proyectos, inicio + 1):
            hoja.write(fila, 0, proyecto["name"])
            hoja.write(fila, 1, proyecto["participantes"])
            hoja.write(fila, 2, proyecto["presentes"])
            hoja.write(fila, 3, proyecto["registros"])

    def _hoja_asistencia_final(self, workbook, datos):
        """Realiza internamente la operación hoja asistencia final."""
        hoja = workbook.add_worksheet("Asistencia")
        encabezados = ["Matricula", "Nombre", "Apellido P", "Apellido M", "Carrera", "Proyecto", "Evento", "Tipo", "Fecha/Hora"]
        for col, encabezado in enumerate(encabezados):
            hoja.write(0, col, encabezado)
        for fila, row in enumerate(datos, 1):
            for col, valor in enumerate(row):
                hoja.write(fila, col, self._formatear_reporte_fecha_hora(valor) if col == 8 else valor)

