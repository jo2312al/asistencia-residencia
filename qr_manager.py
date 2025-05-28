import qrcode
import os

class QRManager:
    def __init__(self, qr_dir='static/qr_codes'):
        self.qr_dir = qr_dir
        if not os.path.exists(qr_dir):
            os.makedirs(qr_dir)

    def generate_qr(self, matricula):
        qr_data = matricula  # Solo la matrícula
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_path = os.path.join(self.qr_dir, f"{matricula}.png")  # Usar matrícula en el nombre del archivo
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img.save(qr_path)
        return qr_path.replace('\\', '/')  # Normaliza para URLs