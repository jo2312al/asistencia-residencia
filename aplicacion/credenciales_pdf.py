"""Renderizadores de credenciales PDF independientes de las rutas Flask."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def construir_credencial_tarjeta(student, styles, cell_width, cell_height):
    """Ejecuta la operación construir credencial tarjeta y devuelve el resultado correspondiente."""
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.webp")
    text_style = ParagraphStyle(
        "CredentialText",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        alignment=1,
    )
    name_style = ParagraphStyle(
        "CredentialName",
        parent=styles["Heading4"],
        fontSize=11,
        leading=13,
        alignment=1,
        textColor=colors.HexColor("#003087"),
    )
    label_style = ParagraphStyle(
        "CredentialLabel",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        alignment=1,
        textColor=colors.HexColor("#6c757d"),
    )

    if os.path.exists(logo_path):
        logo_cell = Image(logo_path, width=0.85 * inch, height=0.85 * inch)
    else:
        logo_cell = Paragraph("<b>AsisTec</b>", name_style)

    qr_image = Image(student["qr_path"], width=1.65 * inch, height=1.65 * inch)
    qr_image.hAlign = "CENTER"

    content = [
        [logo_cell],
        [Paragraph("CREDENCIAL DE ACCESO", label_style)],
        [Paragraph(student["name"], name_style)],
        [Paragraph(student["project_name"], text_style)],
        [Paragraph(f"Matricula: {student['matricula']}", text_style)],
        [Spacer(1, 0.12 * inch)],
        [qr_image],
        [Paragraph("Presenta este codigo para registrar asistencia", label_style)],
    ]
    card = Table(content, colWidths=[cell_width - 0.25 * inch])
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#f3f7fb")),
                ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#003087")),
                ("LINEBELOW", (0, 1), (-1, 1), 0.8, colors.HexColor("#d9e6f2")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    wrapper = Table([[card]], colWidths=[cell_width], rowHeights=[cell_height])
    wrapper.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return wrapper


def construir_horizontal_credencial_tarjeta(student, styles, card_width, card_height):
    """Ejecuta la operación construir horizontal credencial tarjeta y devuelve el resultado correspondiente."""
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo.webp")
    text_style = ParagraphStyle(
        "RectCredentialText",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#212529"),
    )
    name_style = ParagraphStyle(
        "RectCredentialName",
        parent=styles["Heading4"],
        fontSize=13,
        leading=15,
        textColor=colors.HexColor("#003087"),
    )
    label_style = ParagraphStyle(
        "RectCredentialLabel",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#6c757d"),
    )

    if os.path.exists(logo_path):
        logo_cell = Image(logo_path, width=0.75 * inch, height=0.75 * inch)
    else:
        logo_cell = Paragraph("<b>AsisTec</b>", name_style)

    qr_image = Image(student["qr_path"], width=1.25 * inch, height=1.25 * inch)
    info = Table(
        [
            [Paragraph("CREDENCIAL DE ACCESO", label_style)],
            [Paragraph(student["name"], name_style)],
            [Paragraph(student["project_name"], text_style)],
            [Paragraph(f"Matricula: {student['matricula']}", text_style)],
            [Paragraph("Presenta este codigo para registrar asistencia", label_style)],
        ],
        colWidths=[card_width - 1.85 * inch],
    )
    info.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    card = Table(
        [[logo_cell, info, qr_image]],
        colWidths=[0.65 * inch, card_width - 1.85 * inch, 1.2 * inch],
        rowHeights=[card_height],
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f7fb")),
                ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#003087")),
                ("LINEAFTER", (0, 0), (0, -1), 0.8, colors.HexColor("#d9e6f2")),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return card


def construir_estandar_credenciales_pdf(student_data, output):
    """Ejecuta la operación construir estandar credenciales pdf y devuelve el resultado correspondiente."""
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    page_width = letter[0]
    page_height = letter[1]
    margin = 0.5 * inch
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin
    cell_width = usable_width / 2
    cell_height = usable_height / 2.5

    for i in range(0, len(student_data), 4):
        students_chunk = student_data[i:i + 4]
        grid_data = [["", ""], ["", ""]]
        for j, student in enumerate(students_chunk):
            row = j // 2
            col = j % 2
            grid_data[row][col] = construir_credencial_tarjeta(student, styles, cell_width, cell_height)

        table = Table(grid_data, colWidths=[cell_width, cell_width], rowHeights=[cell_height, cell_height])
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(table)
        if i + 4 < len(student_data):
            elements.append(Spacer(1, 0.5 * inch))

    doc.build(elements)


def construir_horizontal_credenciales_pdf(student_data, output):
    """Ejecuta la operación construir horizontal credenciales pdf y devuelve el resultado correspondiente."""
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
    )
    elements = []
    styles = getSampleStyleSheet()

    page_width = letter[0]
    page_height = letter[1]
    margin = 0.45 * inch
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin
    card_width = usable_width / 2
    card_height = 1.55 * inch

    for i in range(0, len(student_data), 10):
        chunk = student_data[i:i + 10]
        rows = []
        for j in range(0, len(chunk), 2):
            left = construir_horizontal_credencial_tarjeta(chunk[j], styles, card_width - 0.08 * inch, card_height)
            right = construir_horizontal_credencial_tarjeta(chunk[j + 1], styles, card_width - 0.08 * inch, card_height) if j + 1 < len(chunk) else ""
            rows.append([left, right])

        table = Table(rows, colWidths=[card_width, card_width], rowHeights=[usable_height / 5] * len(rows))
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(table)
        if i + 10 < len(student_data):
            elements.append(Spacer(1, 0.2 * inch))

    doc.build(elements)

# Alias temporales para compatibilidad con la API anterior.
build_credential_card = construir_credencial_tarjeta
build_rectangular_credential_card = construir_horizontal_credencial_tarjeta
build_rectangular_credentials_pdf = construir_horizontal_credenciales_pdf
build_standard_credentials_pdf = construir_estandar_credenciales_pdf
