// scripts.js
// Inicializar escáner QR (para /scan)
function initializeQRScanner() {
    console.log("Inicializando escáner QR");
    const video = document.getElementById('qr-video');
    const canvas = document.getElementById('qr-canvas');
    const resultContainer = document.getElementById('qr-result');
    if (!video || !canvas || !resultContainer) {
        console.error("Elementos qr-video, qr-canvas o qr-result no encontrados");
        resultContainer.innerHTML = `<p class="text-danger">Error: Elementos de la página no encontrados</p>`;
        return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        resultContainer.innerHTML = `<p class="text-danger">El navegador no soporta acceso a la cámara</p>`;
        return;
    }

    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
        .then(stream => {
            console.log("Cámara accedida correctamente");
            video.srcObject = stream;
            video.play().catch(err => {
                console.error("Error al reproducir video:", err);
                resultContainer.innerHTML = `<p class="text-danger">Error al reproducir video: ${err.message}</p>`;
            });
            scanQRCode(video, canvas, resultContainer);
        })
        .catch(err => {
            console.error("Error al acceder a la cámara:", err);
            resultContainer.innerHTML = `<p class="text-danger">Error al acceder a la cámara: ${err.message}</p>`;
        });
}

function scanQRCode(video, canvas, resultContainer) {
    console.log("Iniciando escaneo de QR");
    const context = canvas.getContext('2d');

    function scan() {
        if (video.readyState === video.HAVE_ENOUGH_DATA) {
            canvas.height = video.videoHeight;
            canvas.width = video.videoWidth;
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
            try {
                const code = jsQR(imageData.data, imageData.width, imageData.height);
                if (code) {
                    console.log("QR detectado:", code.data);
                    resultContainer.innerHTML = `<p class="text-info">Código QR detectado: ${code.data}</p>`;
                    registerAttendance(code.data);
                    return; // Detener escaneo tras lectura exitosa
                } else {
                    resultContainer.innerHTML = `<p class="text-info">Escaneando...</p>`;
                }
            } catch (e) {
                console.error("Error al procesar QR:", e);
            }
        }
        requestAnimationFrame(scan);
    }
    scan();
}

function registerAttendance(qrData) {
    console.log("Registrando asistencia para QR:", qrData);
    const eventSelector = document.getElementById('event_id');
    const eventId = eventSelector ? eventSelector.value : '';
    fetch('/register_attendance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qr_data: qrData, event_id: eventId })
    })
    .then(response => {
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return response.json();
    })
    .then(data => {
        console.log("Respuesta de /register_attendance:", data);
        const modalMessage = document.getElementById('modal-message');
        const qrModal = new bootstrap.Modal(document.getElementById('qrModal'));
        if (data.success) {
            modalMessage.innerHTML = `<p class="text-success">${data.message}</p>`;
        } else {
            modalMessage.innerHTML = `<p class="text-danger">${data.error}</p>`;
        }
        qrModal.show();
        // Recargar página al cerrar el modal
        document.getElementById('qrModal').addEventListener('hidden.bs.modal', function () {
            location.reload();
        }, { once: true });
    })
    .catch(error => {
        console.error("Error en fetch /register_attendance:", error);
        const modalMessage = document.getElementById('modal-message');
        const qrModal = new bootstrap.Modal(document.getElementById('qrModal'));
        modalMessage.innerHTML = `<p class="text-danger">Error: ${error.message}</p>`;
        qrModal.show();
        // Recargar página al cerrar el modal
        document.getElementById('qrModal').addEventListener('hidden.bs.modal', function () {
            location.reload();
        }, { once: true });
    });
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function normalizeFieldType(fieldType) {
    const allowedTypes = ['text', 'email', 'number', 'date', 'tel'];
    return allowedTypes.includes(fieldType) ? fieldType : 'text';
}

async function loadProjectFields(projectId) {
    const container = document.getElementById('project-fields-container');
    if (!container) return;

    container.innerHTML = '';
    container.classList.add('d-none');
    if (!projectId) return;

    try {
        const response = await fetch(`/project_fields/${projectId}`);
        const result = await response.json();
        if (!result.success || !result.fields.length) return;

        const title = document.createElement('h3');
        title.className = 'h6 mb-3';
        title.textContent = 'Campos adicionales del proyecto';
        container.appendChild(title);

        result.fields.forEach((field) => {
            const group = document.createElement('div');
            group.className = 'mb-3';

            const label = document.createElement('label');
            label.className = 'form-label';
            label.setAttribute('for', `field_${field.id}`);
            label.innerHTML = `${escapeHtml(field.name)}${field.is_required ? ' <span class="text-danger">*</span>' : ''}`;

            const input = document.createElement('input');
            input.className = 'form-control';
            input.type = normalizeFieldType(field.field_type);
            input.id = `field_${field.id}`;
            input.name = `field_${field.id}`;
            input.required = Boolean(field.is_required);

            group.appendChild(label);
            group.appendChild(input);
            container.appendChild(group);
        });
        container.classList.remove('d-none');
    } catch (error) {
        console.error("Error cargando campos del proyecto:", error);
    }
}

function initializeProjectFieldsLoader() {
    const projectSelect = document.getElementById('project_id');
    if (!projectSelect) return;

    projectSelect.addEventListener('change', function() {
        loadProjectFields(this.value);
    });
    loadProjectFields(projectSelect.value);
}

// Formulario de registro (para /register)
function initializeGenerateQRForm() {
    console.log("Inicializando formulario de generación de QR");
    const form = document.getElementById('register-form');
    if (form) {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            console.log("Formulario de registro enviado");
            const formData = new FormData(this);
            try {
                const response = await fetch('/generate_qr', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                console.log("Respuesta de /generate_qr:", result);
                const resultContainer = document.getElementById('qr-result');
                if (result.success) {
                    resultContainer.innerHTML = `<p class="text-success">QR generado. <a href="${result.qr_path}" download>Descargar QR</a></p>`;
                } else {
                    resultContainer.innerHTML = `<p class="text-danger">Error: ${result.error}</p>`;
                }
            } catch (error) {
                console.error("Error en fetch /generate_qr:", error);
                document.getElementById('qr-result').innerHTML = `<p class="text-danger">Error: ${error.message}</p>`;
            }
        });
    }
}

// Formulario de carga de Excel (para /register)
function initializeExcelUploadForm() {
    console.log("Inicializando formulario de carga de Excel");
    const form = document.getElementById('excel-form');
    if (form) {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            console.log("Formulario de Excel enviado");
            const formData = new FormData(this);
            try {
                const response = await fetch('/upload_excel', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                console.log("Respuesta de /upload_excel:", result);
                const resultContainer = document.getElementById('excel-result');
                if (result.success) {
                    resultContainer.innerHTML = `<p class="text-success">${result.message}</p>`;
                } else {
                    resultContainer.innerHTML = `<p class="text-danger">Error: ${result.error}</p>`;
                }
            } catch (error) {
                console.error("Error en fetch /upload_excel:", error);
                document.getElementById('excel-result').innerHTML = `<p class="text-danger">Error: ${error.message}</p>`;
            }
        });
    }
}

// Formulario de reportes (para /dashboard o /reports)
function initializeReportForm() {
    console.log("Inicializando formulario de reportes");
    const form = document.getElementById('report-form');
    if (form) {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            console.log("Formulario de reporte enviado");
            const formData = new FormData(this);
            try {
                const response = await fetch('/generate_report', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                console.log("Respuesta de /generate_report:", result);
                const resultContainer = document.getElementById('report-result');
                if (result.success) {
                    resultContainer.innerHTML = `<p class="text-success">Reporte generado: <a href="${result.report_path}" download>Descargar</a></p>`;
                } else {
                    resultContainer.innerHTML = `<p class="text-danger">Error: ${result.error}</p>`;
                }
            } catch (error) {
                console.error("Error en fetch /generate_report:", error);
                document.getElementById('report-result').innerHTML = `<p class="text-danger">Error: ${error.message}</p>`;
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', function() {
    console.log("DOM cargado, inicializando scripts");
    if (document.getElementById('qr-video')) {
        initializeQRScanner();
    }
    if (document.getElementById('register-form')) {
        initializeProjectFieldsLoader();
        initializeGenerateQRForm();
    }
    if (document.getElementById('excel-form')) {
        initializeExcelUploadForm();
    }
    if (document.getElementById('report-form')) {
        initializeReportForm();
    }
    // Estilo dinámico para tecn-blue
    const elements = document.getElementsByClassName('tecn-blue');
    for (let element of elements) {
        element.style.color = '#003087';
    }
});
