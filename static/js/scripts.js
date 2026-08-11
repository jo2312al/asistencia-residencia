// scripts.js
// Inicializar escáner QR (para /scan)
const scannerState = {
    stream: null,
    active: false,
    detecting: false,
    detector: null,
    lastStatusAt: 0,
    feedbackTimer: null
};

function initializeQRScanner() {
    console.log("Inicializando escaner QR");
    const elements = getScannerElements();
    if (!elements) return;
    bindScannerControls(elements);
    startQRScanner(elements);
}

function getScannerElements() {
    const video = document.getElementById('qr-video');
    const canvas = document.getElementById('qr-canvas');
    const result = document.getElementById('qr-result');
    const restart = document.getElementById('restart-scanner-btn');
    const pause = document.getElementById('pause-scanner-btn');
    const manualForm = document.getElementById('manual-qr-form');
    const manualInput = document.getElementById('manual_qr_data');
    const pill = document.getElementById('scanner-pill');
    const history = document.getElementById('scan-history-list');
    const count = document.getElementById('scan-count');
    const feedback = document.getElementById('scanner-feedback');
    if (!video || !canvas || !result) return null;
    return { video, canvas, result, restart, pause, manualForm, manualInput, pill, history, count, feedback };
}

function bindScannerControls(elements) {
    if (elements.restart) elements.restart.addEventListener('click', () => startQRScanner(elements));
    if (elements.pause) elements.pause.addEventListener('click', () => toggleScannerPause(elements));
    if (elements.manualForm) bindManualScan(elements);
}

function bindManualScan(elements) {
    elements.manualForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const qrData = elements.manualInput ? elements.manualInput.value.trim() : '';
        if (!qrData) return renderScannerStatus(elements.result, 'danger', 'Captura un token o matricula.');
        registerAttendance(qrData, () => clearManualInput(elements.manualInput));
    });
}

function stopQRScanner() {
    scannerState.active = false;
    if (scannerState.stream) {
        scannerState.stream.getTracks().forEach(track => track.stop());
        scannerState.stream = null;
    }
}

async function startQRScanner(elements) {
    stopQRScanner();
    setScannerPill(elements, 'idle', 'Abriendo camara');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        return renderScannerStatus(elements.result, 'danger', 'El navegador no soporta acceso a la camara. Usa captura manual.');
    }
    scannerState.stream = await openCameraStream();
    if (!scannerState.stream) return showCameraError(elements.result);
    await playScannerVideo(elements);
}

async function openCameraStream() {
    const constraints = [
        { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } },
        { video: true }
    ];

    let lastError = null;
    for (const constraint of constraints) {
        try {
            return await navigator.mediaDevices.getUserMedia(constraint);
        } catch (error) {
            lastError = error;
        }
    }
    scannerState.lastCameraError = lastError;
    return null;
}

async function playScannerVideo(elements) {
    try {
        elements.video.srcObject = scannerState.stream;
        await elements.video.play();
        scannerState.active = true;
        updatePauseButton(elements, false);
        setScannerPill(elements, 'ready', 'Escaneando');
        renderScannerStatus(elements.result, 'info', 'Camara lista. Acerca el QR al recuadro.');
        scanQRCode(elements);
    } catch (error) {
        console.error("Error al reproducir video:", error);
        renderScannerStatus(elements.result, 'danger', `Error al reproducir video: ${error.message}.`);
        stopQRScanner();
    }
}

function showCameraError(resultContainer) {
    const error = scannerState.lastCameraError;
    console.error("Error al acceder a la camara:", error);
    renderScannerStatus(resultContainer, 'danger', `No se pudo abrir la camara: ${error ? error.message : 'permiso denegado'}. Usa captura manual.`);
}

function scanQRCode(elements) {
    console.log("Iniciando escaneo de QR");
    const context = elements.canvas.getContext('2d');

    async function scan() {
        if (!scannerState.active) return;
        if (canReadFrame(elements.video)) await readScannerFrame(elements, context, scan);
        requestAnimationFrame(scan);
    }
    scan();
}

function canReadFrame(video) {
    return video.readyState === video.HAVE_ENOUGH_DATA && !scannerState.detecting;
}

async function readScannerFrame(elements, context, scan) {
    scannerState.detecting = true;
    try {
        const qrData = await detectQRData(elements.video, elements.canvas, context);
        qrData ? onQRDetected(qrData, elements, scan) : showScanningPulse(elements.result);
    } catch (error) {
        console.error("Error al procesar QR:", error);
        renderScannerStatus(elements.result, 'danger', `No se pudo leer el QR: ${error.message}.`);
    } finally {
        scannerState.detecting = false;
    }
}

function onQRDetected(qrData, elements, scan) {
    console.log("QR detectado:", qrData);
    scannerState.active = false;
    setScannerPill(elements, 'busy', 'Registrando');
    renderScannerStatus(elements.result, 'info', 'Codigo QR detectado. Registrando...');
    registerAttendance(qrData, () => resumeScanner(elements.result, scan));
}

function resumeScanner(resultContainer, scan) {
    renderScannerStatus(resultContainer, 'info', 'Listo para escanear otro codigo.');
    setScannerPill(getScannerElements(), 'ready', 'Escaneando');
    scannerState.active = true;
    requestAnimationFrame(scan);
}

function showScanningPulse(resultContainer) {
    const now = Date.now();
    if (now - scannerState.lastStatusAt <= 800) return;
    renderScannerStatus(resultContainer, 'info', 'Escaneando...');
    scannerState.lastStatusAt = now;
}

async function detectQRData(video, canvas, context) {
    return await detectWithBarcodeDetector(video) || detectWithJsQR(video, canvas, context);
}

async function detectWithBarcodeDetector(video) {
    if (!('BarcodeDetector' in window)) return null;
    try {
        if (!scannerState.detector) scannerState.detector = new BarcodeDetector({ formats: ['qr_code'] });
        const codes = await scannerState.detector.detect(video);
        return codes.length ? codes[0].rawValue : null;
    } catch (error) {
        scannerState.detector = null;
        return null;
    }
}

function detectWithJsQR(video, canvas, context) {
    if (typeof jsQR !== 'function') throw new Error('No cargo la libreria de lectura QR');
    const imageData = drawVideoFrame(video, canvas, context);
    const code = jsQR(imageData.data, imageData.width, imageData.height);
    return code ? code.data : null;
}

function drawVideoFrame(video, canvas, context) {
    canvas.height = video.videoHeight;
    canvas.width = video.videoWidth;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return context.getImageData(0, 0, canvas.width, canvas.height);
}

function renderScannerStatus(container, type, message) {
    container.className = `scanner-status scanner-status-${type}`;
    container.innerHTML = escapeHtml(message);
}

function clearManualInput(input) {
    if (input) input.value = '';
}

function registerAttendance(qrData, afterClose) {
    console.log("Registrando asistencia para QR:", qrData);
    fetch('/register_attendance', buildAttendanceRequest(qrData))
        .then(response => readJsonResponse(response))
        .then(data => handleAttendanceResult(data, afterClose))
        .catch(error => handleAttendanceError(error, afterClose));
}

function buildAttendanceRequest(qrData) {
    const eventSelector = document.getElementById('event_id');
    const eventTypeSelector = document.getElementById('attendance_event_type');
    const payload = {
        qr_data: qrData,
        event_id: eventSelector ? eventSelector.value : '',
        event_type: eventTypeSelector ? eventTypeSelector.value : 'entrada'
    };
    return {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    };
}

function handleAttendanceResult(data, afterClose) {
    console.log("Respuesta de /register_attendance:", data);
    const type = attendanceFeedbackType(data);
    const message = data.success ? data.message : (data.error || 'No se pudo registrar');
    showScannerFeedback(type, message, afterClose);
}

function attendanceFeedbackType(data) {
    if (data.success) return 'success';
    return data.duplicate ? 'warning' : 'danger';
}

function handleAttendanceError(error, afterClose) {
    console.error("Error en fetch /register_attendance:", error);
    showScannerFeedback('danger', `Error: ${error.message}`, afterClose);
}

function toggleScannerPause(elements) {
    scannerState.active = !scannerState.active;
    updatePauseButton(elements, !scannerState.active);
    setScannerPill(elements, scannerState.active ? 'ready' : 'idle', scannerState.active ? 'Escaneando' : 'Pausado');
    renderScannerStatus(elements.result, 'info', scannerState.active ? 'Escaner reanudado.' : 'Escaner pausado.');
    if (scannerState.active) scanQRCode(elements);
}

function updatePauseButton(elements, paused) {
    if (elements.pause) elements.pause.textContent = paused ? 'Reanudar' : 'Pausar';
}

function setScannerPill(elements, type, text) {
    if (!elements || !elements.pill) return;
    elements.pill.className = `scanner-pill scanner-pill-${type}`;
    elements.pill.textContent = text;
}

function showScannerFeedback(type, message, afterClose) {
    const elements = getScannerElements();
    if (elements) renderScannerStatus(elements.result, type, message);
    if (elements) addScanHistory(elements, type, message);
    if (elements) showScannerOverlay(elements, type, message);
    notifyScanResult(type);
    window.setTimeout(() => finishScannerFeedback(afterClose), 1800);
}

function finishScannerFeedback(afterClose) {
    if (typeof afterClose === 'function') afterClose();
}

function notifyScanResult(type) {
    if (navigator.vibrate) navigator.vibrate(type === 'success' ? 80 : [80, 60, 80]);
}

function showScannerOverlay(elements, type, message) {
    if (!elements.feedback) return;
    elements.feedback.className = `scanner-feedback scanner-feedback-${type}`;
    elements.feedback.innerHTML = overlayFeedbackHtml(type, message);
    window.clearTimeout(scannerState.feedbackTimer);
    scannerState.feedbackTimer = window.setTimeout(() => hideScannerOverlay(elements), 1200);
}

function overlayFeedbackHtml(type, message) {
    const title = scannerFeedbackTitle(type);
    return `<strong>${title}</strong><span>${escapeHtml(message)}</span>`;
}

function scannerFeedbackTitle(type) {
    if (type === 'success') return 'Asistencia tomada';
    if (type === 'warning') return 'Registro existente';
    return 'Revisar escaneo';
}

function hideScannerOverlay(elements) {
    if (elements.feedback) elements.feedback.className = 'scanner-feedback scanner-feedback-hidden';
}

function addScanHistory(elements, type, message) {
    if (!elements.history) return;
    elements.history.prepend(buildScanHistoryItem(type, message));
    trimScanHistory(elements.history);
    updateScanCount(elements);
}

function buildScanHistoryItem(type, message) {
    const item = document.createElement('div');
    item.className = `scanner-history-item scanner-history-${type}`;
    item.innerHTML = `<strong>${new Date().toLocaleTimeString()}</strong><span>${escapeHtml(message)}</span>`;
    return item;
}

function trimScanHistory(history) {
    while (history.children.length > 6) history.removeChild(history.lastElementChild);
}

function updateScanCount(elements) {
    if (elements.count) elements.count.textContent = String(elements.history.children.length);
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

async function postForm(url, form) {
    const response = await fetch(url, {
        method: 'POST',
        body: new FormData(form)
    });
    return readJsonResponse(response);
}

function renderElementHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) element.innerHTML = html;
}

function renderElementStatus(elementId, type, message) {
    renderElementHtml(elementId, `<p class="text-${type}">${escapeHtml(message)}</p>`);
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

    resetDynamicContainer(container);
    if (!eventId) return;

    try {
        const fields = await fetchAdditionalEventFields(eventId);
        if (!fields.length) return;
        renderEventFields(container, fields);
        container.classList.remove('d-none');
    } catch (error) {
        console.error("Error cargando campos del evento:", error);
    }
}

function resetDynamicContainer(container) {
    container.innerHTML = '';
    container.classList.add('d-none');
}

async function fetchAdditionalEventFields(eventId) {
    const response = await fetch(`/event_fields/${eventId}`);
    const result = await readJsonResponse(response);
    if (!result.success || !result.fields.length) return [];
    return result.fields.filter(isAdditionalField);
}

function isAdditionalField(field) {
    const base = new Set(['nombre', 'apellido paterno', 'apellido materno', 'matricula', 'carrera']);
    return !base.has(normalizeFieldName(field.name));
}

function renderEventFields(container, fields) {
    container.appendChild(buildSectionTitle('Campos adicionales del evento'));
    const grid = document.createElement('div');
    grid.className = 'row g-3';
    fields.forEach((field) => grid.appendChild(buildEventFieldGroup(field)));
    container.appendChild(grid);
}

function buildSectionTitle(text) {
    const title = document.createElement('h3');
    title.className = 'h6 mb-3';
    title.textContent = text;
    return title;
}

function buildEventFieldGroup(field) {
    const group = document.createElement('div');
    group.className = 'col-md-6';
    group.appendChild(buildEventFieldLabel(field));
    group.appendChild(buildEventFieldInput(field));
    return group;
}

function buildEventFieldLabel(field) {
    const label = document.createElement('label');
    label.className = 'form-label';
    label.setAttribute('for', `field_${field.id}`);
    label.innerHTML = `${escapeHtml(field.name)}${field.is_required ? ' <span class="text-danger">*</span>' : ''}`;
    return label;
}

function buildEventFieldInput(field) {
    const input = document.createElement('input');
    input.className = 'form-control';
    input.type = normalizeFieldType(field.field_type);
    input.id = `field_${field.id}`;
    input.name = `field_${field.id}`;
    input.required = Boolean(field.is_required);
    return input;
}

async function loadEventProjects(eventId, selectId) {
    const projectSelect = document.getElementById(selectId);
    if (!projectSelect) return;

    resetProjectSelect(projectSelect, eventId);
    if (!eventId) return;

    try {
        const projects = await fetchEventProjects(eventId);
        renderProjectOptions(projectSelect, projects);
    } catch (error) {
        console.error("Error cargando proyectos del evento:", error);
    }
}

function resetProjectSelect(projectSelect, eventId) {
    const label = eventId ? 'Sin proyecto' : 'Seleccione primero un evento';
    projectSelect.innerHTML = `<option value="">${label}</option>`;
}

async function fetchEventProjects(eventId) {
    const response = await fetch(`/event_projects/${eventId}`);
    const result = await readJsonResponse(response);
    return result.success ? result.projects : [];
}

function renderProjectOptions(projectSelect, projects) {
    if (!projects.length) {
        projectSelect.innerHTML = '<option value="">Este evento no tiene proyectos</option>';
        return;
    }
    projectSelect.innerHTML = '<option value="">Sin proyecto</option>';
    projects.forEach((project) => projectSelect.appendChild(buildProjectOption(project)));
}

function buildProjectOption(project) {
    const option = document.createElement('option');
    option.value = project.id;
    option.textContent = project.name;
    return option;
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
    bindMainMenu(menuButton, menu);
    bindSubmenus();
    bindNavbarCloseHandlers(menuButton, menu);
}

function closeSubmenus(exceptGroup) {
    document.querySelectorAll('.app-nav-group.is-open').forEach((group) => closeSubmenu(group, exceptGroup));
}

function closeSubmenu(group, exceptGroup) {
    if (group === exceptGroup) return;
    group.classList.remove('is-open');
    group.querySelector('[data-submenu-toggle]')?.setAttribute('aria-expanded', 'false');
}

function bindMainMenu(menuButton, menu) {
    if (!menuButton || !menu) return;
    menuButton.addEventListener('click', function() {
        const isOpen = menu.classList.toggle('is-open');
        this.setAttribute('aria-expanded', String(isOpen));
        if (!isOpen) closeSubmenus();
    });
}

function bindSubmenus() {
    document.querySelectorAll('[data-submenu-toggle]').forEach((button) => {
        button.addEventListener('click', toggleSubmenu);
    });
}

function toggleSubmenu(event) {
    event.stopPropagation();
    const group = event.currentTarget.closest('.app-nav-group');
    if (!group) return;
    const willOpen = !group.classList.contains('is-open');
    closeSubmenus(group);
    group.classList.toggle('is-open', willOpen);
    event.currentTarget.setAttribute('aria-expanded', String(willOpen));
}

function bindNavbarCloseHandlers(menuButton, menu) {
    document.addEventListener('click', closeNavbarOnOutsideClick);
    document.addEventListener('keydown', (event) => closeNavbarOnEscape(event, menuButton, menu));
}

function closeNavbarOnOutsideClick(event) {
    if (!event.target.closest('.app-navbar')) closeSubmenus();
}

function closeNavbarOnEscape(event, menuButton, menu) {
    if (event.key !== 'Escape') return;
    closeSubmenus();
    if (isMobileNav(menuButton, menu)) closeMobileMenu(menuButton, menu);
}

function isMobileNav(menuButton, menu) {
    return menu && menuButton && window.matchMedia('(max-width: 991.98px)').matches;
}

function closeMobileMenu(menuButton, menu) {
    menu.classList.remove('is-open');
    menuButton.setAttribute('aria-expanded', 'false');
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
    if (!form) return;
    form.addEventListener('submit', (event) => submitGenerateQR(event, form));
}

async function submitGenerateQR(event, form) {
    event.preventDefault();
    try {
        const result = await postForm('/generate_qr', form);
        handleGenerateQRResult(result);
    } catch (error) {
        console.error("Error en fetch /generate_qr:", error);
        renderElementStatus('qr-result', 'danger', `Error: ${error.message}`);
    }
}

function handleGenerateQRResult(result) {
    if (!result.success) return renderElementStatus('qr-result', 'danger', `Error: ${result.error}`);
    renderElementHtml('qr-result', '');
    renderElementHtml('register-success-download', `<a href="${result.qr_path}" download>Descargar QR</a>`);
    showRegisterSuccessModal(result);
}

function showRegisterSuccessModal(result) {
    const modalElement = document.getElementById('registerSuccessModal');
    if (modalElement && window.bootstrap) return new bootstrap.Modal(modalElement).show();
    renderElementHtml('qr-result', `<p class="text-success">Usuario registrado. <a href="${result.qr_path}" download>Descargar QR</a></p>`);
}

function initializeRegisterSuccessModal() {
    const button = document.getElementById('register-another-btn');
    const form = document.getElementById('register-form');
    const modalElement = document.getElementById('registerSuccessModal');
    if (!button || !form || !modalElement) return;

    button.addEventListener('click', () => resetRegisterForm(form, modalElement));
}

function resetRegisterForm(form, modalElement) {
    const selected = getSelectedRegisterContext();
    form.reset();
    restoreRegisterContext(selected);
    renderElementHtml('qr-result', '');
    bootstrap.Modal.getInstance(modalElement)?.hide();
    document.getElementById('first_name')?.focus();
}

function getSelectedRegisterContext() {
    return {
        eventId: document.getElementById('event_id')?.value || '',
        projectId: document.getElementById('project_id')?.value || ''
    };
}

function restoreRegisterContext(selected) {
    const eventSelect = document.getElementById('event_id');
    if (!eventSelect || !selected.eventId) return;
    eventSelect.value = selected.eventId;
    loadEventFields(selected.eventId);
    loadEventProjects(selected.eventId, 'project_id').then(() => restoreProjectSelection(selected.projectId));
}

function restoreProjectSelection(projectId) {
    const projectSelect = document.getElementById('project_id');
    if (projectSelect && projectId) projectSelect.value = projectId;
}

// Formulario de carga de Excel (para /register)
function initializeExcelUploadForm() {
    console.log("Inicializando formulario de carga de Excel");
    const form = document.getElementById('excel-form');
    if (!form) return;
    form.addEventListener('submit', (event) => submitExcelUpload(event, form));
}

async function submitExcelUpload(event, form) {
    event.preventDefault();
    try {
        const result = await postForm('/upload_excel', form);
        renderUploadResult(result);
    } catch (error) {
        console.error("Error en fetch /upload_excel:", error);
        renderElementStatus('excel-result', 'danger', `Error: ${error.message}`);
    }
}

function renderUploadResult(result) {
    const type = result.success ? 'success' : 'danger';
    const message = result.success ? result.message : `Error: ${result.error}`;
    renderElementStatus('excel-result', type, message);
}

function initializeExcelPreview() {
    const button = document.getElementById('preview-excel-btn');
    const form = document.getElementById('excel-form');
    const resultContainer = document.getElementById('excel-preview-result');
    if (!button || !form || !resultContainer) return;

    button.addEventListener('click', () => submitExcelPreview(form, resultContainer));
}

async function submitExcelPreview(form, resultContainer) {
    try {
        const result = await postForm('/preview_excel', form);
        renderPreviewResult(resultContainer, result);
    } catch (error) {
        resultContainer.innerHTML = `<p class="text-danger">Error: ${escapeHtml(error.message)}</p>`;
    }
}

function renderPreviewResult(container, result) {
    if (!result.success) return renderElementStatus(container.id, 'danger', `Error: ${result.error}`);
    const missing = buildPreviewMissingHtml(result);
    const columns = result.columns.map(escapeHtml).join(', ');
    container.innerHTML = `${missing}<p class="small text-muted mb-1">Filas detectadas: ${result.row_count}</p><p class="small text-muted">Columnas: ${columns}</p>`;
}

function buildPreviewMissingHtml(result) {
    const ok = result.format === 'event_format' ? 'Formato de evento detectado.' : 'Columnas base completas.';
    if (!result.missing.length) return `<p class="text-success">${ok}</p>`;
    return `<p class="text-danger">Faltan columnas: ${result.missing.map(escapeHtml).join(', ')}</p>`;
}


function initializeReportProjectLoader() {
    const form = document.getElementById('report-form');
    const eventSelect = form ? form.querySelector('#event_id') : null;
    const projectSelect = form ? form.querySelector('#project_id') : null;
    if (!eventSelect || !projectSelect) return;
    eventSelect.addEventListener('change', () => loadEventProjects(eventSelect.value, 'project_id'));
    loadEventProjects(eventSelect.value, 'project_id');
}

// Formulario de reportes (para /dashboard o /reports)
function initializeReportForm() {
    console.log("Inicializando formulario de reportes");
    const form = document.getElementById('report-form');
    if (!form) return;
    form.addEventListener('submit', (event) => submitReport(event, form));
}

async function submitReport(event, form) {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    setReportLoading(button, true);
    renderReportLoading();
    try {
        const result = await postForm('/generate_report', form);
        renderReportResult(result);
    } catch (error) {
        console.error("Error en fetch /generate_report:", error);
        renderReportError(error.message);
    } finally {
        setReportLoading(button, false);
    }
}

function renderReportResult(result) {
    if (!result.success) return renderReportError(result.error);
    renderElementHtml('report-result', buildReportSuccessHtml(result.report_path));
}

function setReportLoading(button, loading) {
    if (!button) return;
    button.disabled = loading;
    button.textContent = loading ? (button.dataset.loadingText || 'Generando...') : 'Generar reporte';
}

function renderReportLoading() {
    renderElementHtml('report-result', '<div class="report-loading"><span class="spinner-border spinner-border-sm"></span><strong>Generando reporte...</strong><p>Preparando el archivo con los filtros seleccionados.</p></div>');
}

function renderReportError(message) {
    renderElementHtml('report-result', `<div class="report-alert report-alert-danger"><strong>No se pudo generar</strong><p>${escapeHtml(message || 'Error inesperado')}</p></div>`);
}

function buildReportSuccessHtml(path) {
    const label = path.endsWith('.xlsx') ? 'Excel' : 'PDF';
    return `<div class="report-alert report-alert-success"><span class="report-icon">${label}</span><div><strong>Reporte generado</strong><p>El archivo esta listo para descargar.</p><a class="btn btn-primary" href="${path}" download>Descargar reporte</a></div></div>`;
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
        initializeReportProjectLoader();
        initializeReportForm();
    }
});
