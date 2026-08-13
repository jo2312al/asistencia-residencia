import importlib
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import database


RAIZ = Path(__file__).resolve().parents[1]


class PruebasBlueprints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("app", None)
        cls.parche = patch.object(database.GestorBaseDatos, "__init__", return_value=None)
        cls.parche.start()
        cls.modulo = importlib.import_module("app")
        cls.app = cls.modulo.app

    @classmethod
    def tearDownClass(cls):
        cls.parche.stop()

    def test_se_registran_las_51_rutas(self):
        reglas = [r for r in self.app.url_map.iter_rules() if r.endpoint != "static"]
        self.assertEqual(len(reglas), 51)

    def test_rutas_estan_distribuidas_en_siete_blueprints(self):
        espacios = {
            regla.endpoint.split(".", 1)[0]
            for regla in self.app.url_map.iter_rules()
            if regla.endpoint != "static"
        }
        self.assertEqual(
            espacios,
            {"acceso", "usuarios", "eventos", "participantes", "configuracion", "asistencia", "reportes"},
        )

    def test_todos_los_url_for_de_plantillas_existen(self):
        endpoints = set(self.app.view_functions)
        usados = set()
        patron = re.compile(r"url_for\(['\"]([^'\"]+)")
        for plantilla in (RAIZ / "templates").glob("*.html"):
            usados.update(patron.findall(plantilla.read_text(encoding="utf-8")))
        self.assertEqual(usados - endpoints, set())

    def test_conserva_urls_y_metodos_clave(self):
        reglas = {
            (r.rule, frozenset(r.methods - {"HEAD", "OPTIONS"}))
            for r in self.app.url_map.iter_rules()
            if r.endpoint != "static"
        }
        esperadas = {
            ("/login", frozenset({"GET", "POST"})),
            ("/events", frozenset({"GET"})),
            ("/events", frozenset({"POST"})),
            ("/register_attendance", frozenset({"POST"})),
            ("/generate_report", frozenset({"POST"})),
        }
        self.assertTrue(esperadas.issubset(reglas))


if __name__ == "__main__":
    unittest.main()
