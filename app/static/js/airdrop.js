// ==============================================================================
// Airutil Smart AirDrop Radar & File Upload Controller (Phase 7 Signature Interaction)
// Ripple animations, SVG circular progress rings, course detection feedback & mobile pickers
// ==============================================================================

(function() {
    'use strict';

    document.addEventListener('DOMContentLoaded', () => {
        const dropZone = document.getElementById('airdrop-zone');
        const fileInput = document.getElementById('file-upload-input');
        const uploadProgressList = document.getElementById('upload-progress-list');

        if (!dropZone || !fileInput) return;

        // ----------------------------------------------------------------------
        // Drag and Drop Visual Feedback
        // ----------------------------------------------------------------------
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.add('drag-active');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.remove('drag-active');
            }, false);
        });

        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files && files.length > 0) {
                handleFilesUpload(files);
            }
        });

        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length > 0) {
                handleFilesUpload(fileInput.files);
            }
        });

        // ----------------------------------------------------------------------
        // File Upload Handler with Progress Ring & Auto-Sort Toast
        // ----------------------------------------------------------------------
        function handleFilesUpload(fileList) {
            Array.from(fileList).forEach(file => {
                uploadSingleFile(file);
            });
        }

        function uploadSingleFile(file) {
            const fileId = 'up_' + Math.random().toString(36).substring(2, 9);
            const itemEl = createProgressItemElement(fileId, file.name, file.size);
            if (uploadProgressList) uploadProgressList.appendChild(itemEl);

            const formData = new FormData();
            formData.append('file', file);
            formData.append('csrf_token', window.csrfToken || '');

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/files/upload', true);
            xhr.setRequestHeader('Accept', 'application/json');

            // Track upload progress
            xhr.upload.addEventListener('progress', (e) => {
                if (e.lengthComputable) {
                    const pct = Math.round((e.loaded / e.total) * 100);
                    updateProgressRing(itemEl, pct);
                }
            });

            xhr.addEventListener('load', () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    try {
                        const res = JSON.parse(xhr.responseText);
                        updateSuccessState(itemEl, res);
                        if (window.showToast) {
                            const dest = `${res.course || 'General'} / ${res.category || 'Documents'}`;
                            window.showToast(`✓ Sorted '${file.name}' into ${dest}`, 'success', 4000);
                        }
                    } catch (e) {
                        updateSuccessState(itemEl, { original_name: file.name, course: 'General', category: 'Documents' });
                    }
                    // Refresh file list if on files page
                    setTimeout(() => {
                        if (window.location.pathname.startsWith('/files')) {
                            window.location.reload();
                        }
                    }, 1200);
                } else {
                    let errMsg = 'Upload failed';
                    try {
                        const err = JSON.parse(xhr.responseText);
                        errMsg = err.error || errMsg;
                    } catch (e) {}
                    updateErrorState(itemEl, errMsg);
                    if (window.showToast) window.showToast(`Upload failed: ${errMsg}`, 'danger', 4000);
                }
            });

            xhr.addEventListener('error', () => {
                updateErrorState(itemEl, 'Network transmission error');
                if (window.showToast) window.showToast('Network error during upload', 'danger');
            });

            xhr.send(formData);
        }

        function createProgressItemElement(id, name, size) {
            const div = document.createElement('div');
            div.className = 'upload-progress-item';
            div.id = id;

            const sizeHuman = (size / (1024 * 1024)).toFixed(1) + ' MB';
            div.innerHTML = `
                <div class="upload-item-left">
                    <svg class="progress-ring" width="36" height="36" viewBox="0 0 36 36">
                        <circle class="progress-ring-bg" cx="18" cy="18" r="14" stroke-width="3"></circle>
                        <circle class="progress-ring-fill" cx="18" cy="18" r="14" stroke-width="3" stroke-dasharray="88" stroke-dashoffset="88"></circle>
                    </svg>
                    <div class="upload-item-info">
                        <div class="upload-item-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                        <div class="upload-item-sub">${sizeHuman} • <span class="upload-status-text">Uploading...</span></div>
                    </div>
                </div>
                <div class="upload-item-result"></div>
            `;
            return div;
        }

        function updateProgressRing(itemEl, percent) {
            const fill = itemEl.querySelector('.progress-ring-fill');
            const status = itemEl.querySelector('.upload-status-text');
            if (fill) {
                const circumference = 88; // 2 * PI * 14
                const offset = circumference - (percent / 100) * circumference;
                fill.style.strokeDashoffset = offset;
            }
            if (status) {
                status.textContent = `${percent}%`;
            }
        }

        function updateSuccessState(itemEl, fileData) {
            const ring = itemEl.querySelector('.progress-ring');
            if (ring) {
                ring.innerHTML = `
                    <circle cx="18" cy="18" r="14" fill="var(--success)" />
                    <text x="18" y="22" font-size="14" fill="#ffffff" text-anchor="middle" font-weight="bold">✓</text>
                `;
            }
            const status = itemEl.querySelector('.upload-status-text');
            if (status) status.textContent = 'Uploaded';

            const resultBox = itemEl.querySelector('.upload-item-result');
            if (resultBox) {
                const destination = `${fileData.course || 'General'} / ${fileData.category || 'Documents'}`;
                resultBox.innerHTML = `<span class="badge badge-success" style="font-size:0.75rem;">📁 ${escapeHtml(destination)}</span>`;
            }
        }

        function updateErrorState(itemEl, message) {
            const ring = itemEl.querySelector('.progress-ring');
            if (ring) {
                ring.innerHTML = `
                    <circle cx="18" cy="18" r="14" fill="var(--danger)" />
                    <text x="18" y="22" font-size="14" fill="#ffffff" text-anchor="middle" font-weight="bold">✕</text>
                `;
            }
            const status = itemEl.querySelector('.upload-status-text');
            if (status) {
                status.style.color = 'var(--danger)';
                status.textContent = message;
            }
        }

        function escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }
    });
})();
