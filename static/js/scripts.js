// scripts.js
// Inicializar escáner QR (para /scan)
const scannerState = {
    stream: null,
    active: false,
    detecting: false,
    detector: null,
    lastStatusAt: 0
};

function initializeQRScanner() {
    console.log("Inicializando escáner QR");
    const video = document.getElementById('qr-video');
    const canvas = document.getElementById('qr-canvas');
    const resultContainer = document.getElementById('qr-result');
    if (!video || !canvas || !resultContainer) {
        console.error("Elementos qr-video, qr-canvas o qr-result no encontrados");
        if (resultContainer) {
            resultContainer.innerHTML = `<p class="text-danger">Error: Elementos de la pagina no encontrados</p>`;
        }
        return;
    }

    const restartButton = document.getElementById('restart-scanner-btn');
    if (restartButton) {
        restartButton.addEventListener('click', () => startQRScanner(video, canvas, resultContainer));
    }

    const manualForm = document.getElementById('manual-qr-form');
    if (manualForm) {
        manualForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const manualInput = document.getElementById('manual_qr_data');
            const qrData = manualInput ? manualInput.value.trim() : '';
            if (!qrData) {
                resultContainer.innerHTML = `<p class="text-danger">Captura un token o matricula.</p>`;
                return;
            }
            registerAttendance(qrData, () => {
                if (manualInput) manualInput.value = '';
            });
        });
    }

    startQRScanner(video, canvas, resultContainer);
}

function stopQRScanner() {
    scannerState.active = false;
    if (scannerState.stream) {
        scannerState.stream.getTracks().forEach(track => track.stop());
        scannerState.stream = null;
    }
}

async function startQRScanner(video, canvas, resultContainer) {
    stopQRScanner();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        resultContainer.innerHTML = `<p class="text-danger">El navegador no soporta acceso a la camara. Usa captura manual.</p>`;
        return;
    }

    const constraints = [
        { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } },
        { video: true }
    ];

    let lastError = null;
    for (const constraint of constraints) {
        try {
            scannerState.stream = await navigator.mediaDevices.getUserMedia(constraint);
            break;
        } catch (error) {
            lastError = error;
        }
    }

    if (!scannerState.stream) {
        console.error("Error al acceder a la camara:", lastError);
        resultContainer.innerHTML = `<p class="text-danger">No se pudo abrir la camara: ${escapeHtml(lastError ? lastError.message : 'permiso denegado')}. Usa captura manual.</p>`;
        return;
    }

    try {
        video.srcObject = scannerState.stream;
        await video.play();
        scannerState.active = true;
        resultContainer.innerHTML = `<p class="text-info">Camara lista. Acerca el QR al recuadro.</p>`;
        scanQRCode(video, canvas, resultContainer);
    } catch (error) {
        console.error("Error al reproducir video:", error);
        resultContainer.innerHTML = `<p class="text-danger">Error al reproducir video: ${escapeHtml(error.message)}.</p>`;
        stopQRScanner();
    }
}

function scanQRCode(video, canvas, resultContainer) {
    console.log("Iniciando escaneo de QR");
    const context = canvas.getContext('2d');

    async function scan() {
        if (!scannerState.active) return;

        if (video.readyState === video.HAVE_ENOUGH_DATA && !scannerState.detecting) {
            scannerState.detecting = true;
            try {
                const qrData = await detectQRData(video, canvas, context);
                if (qrData) {
                    console.log("QR detectado:", qrData);
                    scannerState.active = false;
                    resultContainer.innerHTML = `<p class="text-info">Codigo QR detectado. Registrando...</p>`;
                    registerAttendance(qrData, () => {
                        resultContainer.innerHTML = `<p class="text-info">Listo para escanear otro codigo.</p>`;
                        scannerState.active = true;
                        requestAnimationFrame(scan);
                    });
                    scannerState.detecting = false;
                    return;
                }

                const now = Date.now();
                if (now - scannerState.lastStatusAt > 800) {
                    resultContainer.innerHTML = `<p class="text-info">Escaneando...</p>`;
                    scannerState.lastStatusAt = now;
                }
            } catch (e) {
                console.error("Error al procesar QR:", e);
                resultContainer.innerHTML = `<p class="text-danger">No se pudo leer el QR: ${escapeHtml(e.message)}.</p>`;
            } finally {
                scannerState.detecting = false;
            }
        }
        requestAnimationFrame(scan);
    }
    scan();
}

async function detectQRData(video, canvas, context) {
    if ('BarcodeDetector' in window) {
        try {
            if (!scannerState.detector) {
                scannerState.detector = new BarcodeDetector({ formats: ['qr_code'] });
            }
            const codes = await scannerState.detector.detect(video);
            if (codes.length) {
                return codes[0].rawValue;
            }
        } catch (error) {
            scannerState.detector = null;
        }
    }

    if (typeof jsQR !== 'function') {
        throw new Error('No cargo la libreria de lectura QR');
    }

    canvas.height = video.videoHeight;
    canvas.width = video.videoWidth;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
    const code = jsQR(imageData.data, imageData.width, imageData.height);
    return code ? code.data : null;
}

function registerAttendance(qrData, afterClose) {
    console.log("Registrando asistencia para QR:", qrData);
    const eventSelector = document.getElementById('event_id');
    const eventId = eventSelector ? eventSelector.value : '';
    const eventTypeSelector = document.getElementById('attendance_event_type');
    const eventType = eventTypeSelector ? eventTypeSelector.value : 'entrada';
    fetch('/register_attendance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qr_data: qrData, event_id: eventId, event_type: eventType })
    })
    .then(response => {
        return readJsonResponse(response);
    })
    .then(data => {
        console.log("Respuesta de /register_attendance:", data);
        if (data.success) {
            showScannerModal(`<p class="text-success">${escapeHtml(data.message)}</p>`, afterClose);
        } else {
            showScannerModal(`<p class="text-danger">${escapeHtml(data.error || 'No se pudo registrar')}</p>`, afterClose);
        }
    })
    .catch(error => {
        console.error("Error en fetch /register_attendance:", error);
        showScannerModal(`<p class="text-danger">Error: ${escapeHtml(error.message)}</p>`, afterClose);
    });
}

function showScannerModal(html, afterClose) {
    const modalElement = document.getElementById('qrModal');
    const modalMessage = document.getElementById('modal-message');
    if (!modalElement || !modalMessage || typeof bootstrap === 'undefined') {
        const resultContainer = document.getElementById('qr-result');
        if (resultContainer) resultContainer.innerHTML = html;
        if (typeof afterClose === 'function') afterClose();
        return;
    }

    modalMessage.innerHTML = html;
    const qrModal = new bootstrap.Modal(modalElement);
    modalElement.addEventListener('hidden.bs.modal', function () {
        if (typeof afterClose === 'function') afterClose();
    }, { once: true });
    qrModal.show();
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

async function readJsonResponse(response) {
    const text = await response.text();
    let payload = null;
    try {
        payload = text ? JSON.parse(text) : {};
    } catch (error) {
        const message = text.trim() || `HTTP ${response.status}`;
        throw new Error(message.slice(0, 500));
    }
    if (!response.ok) {
        throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
    }
    return payload;
}

function normalizeFieldType(fieldType) {
    const allowedTypes = ['text', 'email', 'number', 'date', 'tel'];
    return allowedTypes.includes(fieldType) ? fieldType : 'text';
}

function normalizeFieldName(name) {
    return String(name || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .trim()
        .toLowerCase();
}

async function loadEventFields(eventId) {
    const container = document.getElementById('event-fields-container');
    if (!container) return;

    container.innerHTML = '';
    container.classList.add('d-none');
    if (!eventId) return;

    try {
        const response = await fetch(`/event_fields/${eventId}`);
        const result = await readJsonResponse(response);
        if (!result.success || !result.fields.length) return;
        const baseFieldNames = new Set(['nombre', 'apellido paterno', 'apellido materno', 'matricula', 'carrera']);
        const fields = result.fields.filter((field) => !baseFieldNames.has(normalizeFieldName(field.name)));
        if (!fields.length) return;

        const title = document.createElement('h3');
        title.className = 'h6 mb-3';
        title.textContent = 'Campos adicionales del evento';
        container.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'row g-3';
        container.appendChild(grid);

        fields.forEach((field) => {
            const group = document.createElement('div');
            group.className = 'col-md-6';

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
            grid.appendChild(group);
        });
        container.classList.remove('d-none');
    } catch (error) {
        console.error("Error cargando campos del evento:", error);
    }
}

async function loadEventProjects(eventId, selectId) {
    const projectSelect = document.getElementById(selectId);
    if (!projectSelect) return;

    projectSelect.innerHTML = '<option value="">Sin proyecto</option>';
    if (!eventId) {
        projectSelect.innerHTML = '<option value="">Seleccione primero un evento</option>';
        return;
    }

    try {
        const response = await fetch(`/event_projects/${eventId}`);
        const result = await readJsonResponse(response);
        if (!result.success) return;
        if (!result.projects.length) {
            projectSelect.innerHTML = '<option value="">Este evento no tiene proyectos</option>';
            return;
        }
        projectSelect.innerHTML = '<option value="">Sin proyecto</option>';
        result.projects.forEach((project) => {
            const option = document.createElement('option');
            option.value = project.id;
            option.textContent = project.name;
            projectSelect.appendChild(option);
        });
    } catch (error) {
        console.error("Error cargando proyectos del evento:", error);
    }
}

function initializeEventRegistrationLoader() {
    const eventSelect = document.getElementById('event_id');
    const excelEventSelect = document.getElementById('event_id_excel');

    if (eventSelect) {
        eventSelect.addEventListener('change', function() {
            loadEventFields(this.value);
            loadEventProjects(this.value, 'project_id');
        });
        loadEventFields(eventSelect.value);
        loadEventProjects(eventSelect.value, 'project_id');
    }

    if (excelEventSelect) {
        excelEventSelect.addEventListener('change', function() {
            loadEventProjects(this.value, 'project_id_excel');
        });
        loadEventProjects(excelEventSelect.value, 'project_id_excel');
    }
}

function initializeProjectFieldsLoader() {
    initializeEventRegistrationLoader();
    const projectSelect = document.getElementById('project_id');
    if (!projectSelect || document.getElementById('event_id')) return;

    projectSelect.addEventListener('change', function() {
        loadEventFields(this.value);
    });
    loadEventFields(projectSelect.value);
}

function initializeAppNavbar() {
    const menuButton = document.querySelector('[data-menu-toggle]');
    const menu = document.querySelector('[data-nav-menu]');
    const submenuButtons = document.querySelectorAll('[data-submenu-toggle]');

    function closeSubmenus(exceptGroup) {
        document.querySelectorAll('.app-nav-group.is-open').forEach((group) => {
            if (group !== exceptGroup) {
                group.classList.remove('is-open');
                group.querySelector('[data-submenu-toggle]')?.setAttribute('aria-expanded', 'false');
            }
        });
    }

    if (menuButton && menu) {
        menuButton.addEventListener('click', function() {
            const isOpen = menu.classList.toggle('is-open');
            this.setAttribute('aria-expanded', String(isOpen));
            if (!isOpen) closeSubmenus();
        });
    }

    submenuButtons.forEach((button) => {
        button.addEventListener('click', function(event) {
            event.stopPropagation();
            const group = this.closest('.app-nav-group');
            if (!group) return;
            const willOpen = !group.classList.contains('is-open');
            closeSubmenus(group);
            group.classList.toggle('is-open', willOpen);
            this.setAttribute('aria-expanded', String(willOpen));
        });
    });

    document.addEventListener('click', function(event) {
        if (!event.target.closest('.app-navbar')) {
            closeSubmenus();
        }
    });

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            closeSubmenus();
            if (menu && menuButton && window.matchMedia('(max-width: 991.98px)').matches) {
                menu.classList.remove('is-open');
                menuButton.setAttribute('aria-expanded', 'false');
            }
        }
    });
}

function initializeHelpTooltips() {
    if (!window.bootstrap) return;
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((element) => {
        new bootstrap.Tooltip(element);
    });
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
                const result = await readJsonResponse(response);
                console.log("Respuesta de /generate_qr:", result);
                const resultContainer = document.getElementById('qr-result');
                if (result.success) {
                    resultContainer.innerHTML = '';
                    const downloadContainer = document.getElementById('register-success-download');
                    if (downloadContainer) {
                        downloadContainer.innerHTML = `<a href="${result.qr_path}" download>Descargar QR</a>`;
                    }
                    const successModalElement = document.getElementById('registerSuccessModal');
                    if (successModalElement && window.bootstrap) {
                        const successModal = new bootstrap.Modal(successModalElement);
                        successModal.show();
                    } else {
                        resultContainer.innerHTML = `<p class="text-success">Usuario registrado. <a href="${result.qr_path}" download>Descargar QR</a></p>`;
                    }
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

function initializeRegisterSuccessModal() {
    const button = document.getElementById('register-another-btn');
    const form = document.getElementById('register-form');
    const modalElement = document.getElementById('registerSuccessModal');
    if (!button || !form || !modalElement) return;

    button.addEventListener('click', function() {
        const selectedEvent = document.getElementById('event_id')?.value || '';
        const selectedProject = document.getElementById('project_id')?.value || '';
        form.reset();
        const eventSelect = document.getElementById('event_id');
        if (eventSelect && selectedEvent) {
            eventSelect.value = selectedEvent;
            loadEventFields(selectedEvent);
            loadEventProjects(selectedEvent, 'project_id').then(() => {
                const projectSelect = document.getElementById('project_id');
                if (projectSelect && selectedProject) projectSelect.value = selectedProject;
            });
        }
        document.getElementById('qr-result').innerHTML = '';
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) modal.hide();
        document.getElementById('first_name')?.focus();
    });
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
                const result = await readJsonResponse(response);
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

function initializeExcelPreview() {
    const button = document.getElementById('preview-excel-btn');
    const form = document.getElementById('excel-form');
    const resultContainer = document.getElementById('excel-preview-result');
    if (!button || !form || !resultContainer) return;

    button.addEventListener('click', async function() {
        const formData = new FormData(form);
        try {
            const response = await fetch('/preview_excel', {
                method: 'POST',
                body: formData
            });
            const result = await readJsonResponse(response);
            if (!result.success) {
                resultContainer.innerHTML = `<p class="text-danger">Error: ${escapeHtml(result.error)}</p>`;
                return;
            }
            const formatLabel = result.format === 'event_format' ? 'Formato de evento detectado.' : 'Columnas base completas.';
            const missing = result.missing.length ? `<p class="text-danger">Faltan columnas: ${result.missing.map(escapeHtml).join(', ')}</p>` : `<p class="text-success">${formatLabel}</p>`;
            const columns = result.columns.map(escapeHtml).join(', ');
            resultContainer.innerHTML = `${missing}<p class="small text-muted mb-1">Filas detectadas: ${result.row_count}</p><p class="small text-muted">Columnas: ${columns}</p>`;
        } catch (error) {
            resultContainer.innerHTML = `<p class="text-danger">Error: ${escapeHtml(error.message)}</p>`;
        }
    });
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
                const result = await readJsonResponse(response);
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
    initializeAppNavbar();
    initializeHelpTooltips();
    if (document.getElementById('qr-video')) {
        initializeQRScanner();
    }
    if (document.getElementById('register-form')) {
        initializeProjectFieldsLoader();
        initializeGenerateQRForm();
        initializeRegisterSuccessModal();
    }
    if (document.getElementById('excel-form')) {
        initializeExcelUploadForm();
        initializeExcelPreview();
    }
    if (document.getElementById('report-form')) {
        initializeReportForm();
    }
});
