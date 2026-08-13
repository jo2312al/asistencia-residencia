import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aplicacion.servicio_qr import ServicioCredencialesQR
from attendance_manager import AttendanceManager, GestorAsistencia
from database import DatabaseManager, GestorBaseDatos
from qr_manager import GestorQR, QRManager


class PruebasCompatibilidad(unittest.TestCase):
    def test_aliases_historicos_apuntan_a_clases_en_espanol(self):
        self.assertIs(AttendanceManager, GestorAsistencia)
        self.assertIs(DatabaseManager, GestorBaseDatos)
        self.assertIs(QRManager, GestorQR)

    def test_fachada_conserva_metodos_de_todos_los_repositorios(self):
        gestor = GestorBaseDatos.__new__(GestorBaseDatos)
        for metodo in (
            "get_user", "get_credential_by_token", "get_event",
            "exportar_reporte_final_evento_pdf", "get_student_by_id",
        ):
            self.assertTrue(callable(getattr(gestor, metodo)))


class PruebasGestorAsistencia(unittest.TestCase):
    def test_reporte_agrega_filtros_en_orden(self):
        gestor = GestorAsistencia(Mock())
        consulta, parametros = gestor._attendance_report_query(
            "2026-08-01", "2026-08-12", 7
        )
        self.assertIn("s.project_id = %s", consulta)
        self.assertTrue(consulta.endswith("ORDER BY a.timestamp DESC"))
        self.assertEqual(parametros, ["2026-08-01", "2026-08-12", 7])

    def test_credencial_inactiva_no_intenta_registrar(self):
        repositorio = Mock()
        repositorio.obtener_credencial_por_token.return_value = {
            "credential_status": "inactive",
            "participant_status": "active",
            "legacy_student_id": "alumno-1",
        }
        gestor = GestorAsistencia(repositorio)
        self.assertEqual(
            gestor.register_attendance_by_qr_data("token"), "Credencial inactiva"
        )
        repositorio.connect.assert_not_called()


class PruebasServicioQR(unittest.TestCase):
    def test_reutiliza_qr_existente_y_no_lo_regenera(self):
        repositorio = Mock()
        generador = Mock()
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "token.png"
            ruta.write_bytes(b"qr")
            servicio = ServicioCredencialesQR(repositorio, generador)
            resultado = servicio.asegurar_credencial(
                {"token": "token", "qr_path": str(ruta)}
            )
        self.assertEqual(resultado, str(ruta))
        generador.generate_qr_data.assert_not_called()
        repositorio.update_credential_qr_path.assert_not_called()

    def test_controla_trabajos_duplicados(self):
        servicio = ServicioCredencialesQR(Mock(), Mock())
        self.assertTrue(servicio.marcar_enviado("a"))
        self.assertFalse(servicio.marcar_enviado("a"))
        servicio.desmarcar_enviado("a")
        self.assertTrue(servicio.marcar_enviado("a"))


if __name__ == "__main__":
    unittest.main()
