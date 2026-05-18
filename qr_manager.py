import os

import qrcode


class QRManager:
    def __init__(self, qr_dir='static/qr_codes'):
        self.qr_dir = qr_dir
        if not os.path.exists(qr_dir):
            os.makedirs(qr_dir)

    def generate_qr_data(self, qr_data, filename=None):
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        safe_filename = filename or qr_data
        qr_path = os.path.join(self.qr_dir, f"{safe_filename}.png")
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img.save(qr_path)
        return qr_path.replace('\\', '/')

    def generate_qr(self, matricula):
        return self.generate_qr_data(matricula, matricula)
