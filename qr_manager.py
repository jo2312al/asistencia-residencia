import os

import qrcode


class GestorQR:
    def __init__(self, qr_dir='static/qr_codes'):
        """Realiza internamente la operación init."""
        self.qr_dir = qr_dir
        if not os.path.exists(qr_dir):
            os.makedirs(qr_dir)

    def generar_desde_datos(self, qr_data, filename=None):
        """Ejecuta la operación generar desde datos y devuelve el resultado correspondiente."""
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        seguro_nombre_archivo = filename or qr_data
        qr_path = os.path.join(self.qr_dir, f"{seguro_nombre_archivo}.png")
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img.save(qr_path)
        return qr_path.replace('\\', '/')

    def generar(self, matricula):
        """Ejecuta la operación generar y devuelve el resultado correspondiente."""
        return self.generar_desde_datos(matricula, matricula)


# Compatibilidad con la API y el nombre históricos.
GestorQR.generate_qr_data = GestorQR.generar_desde_datos
GestorQR.generar_qr = GestorQR.generar
QRManager = GestorQR
