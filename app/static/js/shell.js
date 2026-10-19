// ==============================================================================
// Airutil Application Shell Controller (Phase 7 Premium UI/UX)
// Command Palette (Ctrl+K), Theme Switcher, Toast Notifications & Mobile Gestures
// ==============================================================================

(function() {
    'use strict';

    // --------------------------------------------------------------------------
    // 1. Theme Management (Dark Default, Light Override)
    // --------------------------------------------------------------------------
    const THEME_KEY = 'airutil_theme';

    function initTheme() {
        const savedTheme = localStorage.getItem(THEME_KEY);
        if (savedTheme) {
            document.documentElement.setAttribute('data-theme', savedTheme);
        } else {
            // Dark by default
            document.documentElement.setAttribute('data-theme', 'dark');
        }
        updateThemeIcon();
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem(THEME_KEY, next);
        updateThemeIcon();
        if (navigator.vibrate) navigator.vibrate(15);
        showToast(`Switched to ${next} theme`, 'info', 2000);
    }

    function updateThemeIcon() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const icon = document.getElementById('theme-icon');
        if (icon) {
            icon.textContent = current === 'dark' ? '☀️' : '🌙';
        }
    }

    // --------------------------------------------------------------------------
    // 2. Toast Notification System
    // --------------------------------------------------------------------------
    function showToast(message, type = 'info', duration = 3500) {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'toast-container';
            container.setAttribute('aria-live', 'polite');
            container.setAttribute('role', 'status');
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        
        const iconMap = {
            success: '✓',
            warning: '⚠️',
            danger: '✕',
            info: 'ℹ️'
        };

        toast.innerHTML = `
            <span class="toast-icon" aria-hidden="true">${iconMap[type] || '•'}</span>
            <span class="toast-message">${escapeHtml(message)}</span>
            <button type="button" class="toast-close" aria-label="Close notification">×</button>
        `;

        container.appendChild(toast);

        // Slide in
        requestAnimationFrame(() => {
            toast.classList.add('toast-visible');
        });

        // Close on button click
        toast.querySelector('.toast-close').addEventListener('click', () => {
            dismissToast(toast);
        });

        // Auto dismiss
        if (duration > 0) {
            setTimeout(() => {
                dismissToast(toast);
            }, duration);
        }

        if (navigator.vibrate && (type === 'danger' || type === 'warning')) {
            navigator.vibrate([20, 40]);
        }
    }

    function dismissToast(toast) {
        toast.classList.remove('toast-visible');
        toast.classList.add('toast-hiding');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // Expose global showToast
    window.showToast = showToast;

    // --------------------------------------------------------------------------
    // 3. Command Palette (Ctrl+K / Cmd+K)
    // --------------------------------------------------------------------------
    const COMMAND_ITEMS = [
        { title: 'Dashboard', category: 'Navigation', url: '/dashboard', icon: '📊', shortcut: 'G D' },
        { title: 'Files & Organizer', category: 'Navigation', url: '/files', icon: '📁', shortcut: 'G F' },
        { title: 'Semester Courses', category: 'Navigation', url: '/courses', icon: '📚', shortcut: 'G C' },
        { title: 'Password Vault', category: 'Navigation', url: '/vault', icon: '🔐', shortcut: 'G V' },
        { title: 'Clip Beam', category: 'Navigation', url: '/beam', icon: '⚡', shortcut: 'G B' },
        { title: 'OS Lab Primitives', category: 'Navigation', url: '/oslab', icon: '🧪', shortcut: 'G O' },
        { title: 'Docker Terminal', category: 'Navigation', url: '/terminal', icon: '💻', shortcut: 'G T' },
        { title: 'Account Settings', category: 'Navigation', url: '/settings', icon: '⚙️', shortcut: 'G S' },
        { title: 'Toggle Dark / Light Theme', category: 'Actions', action: toggleTheme, icon: '🌓', shortcut: 'T' },
        { title: 'Upload New File', category: 'Actions', url: '/files#upload', icon: '⬆️' },
        { title: 'Create Burn-on-Read Beam', category: 'Actions', url: '/beam', icon: '🔥' },
    ];

    let activeIndex = 0;
    let filteredItems = [...COMMAND_ITEMS];

    function openCommandPalette() {
        const modal = document.getElementById('command-palette-modal');
        if (!modal) return;
        modal.classList.add('active');
        const input = document.getElementById('command-palette-input');
        if (input) {
            input.value = '';
            input.focus();
        }
        filterCommands('');
        if (navigator.vibrate) navigator.vibrate(10);
    }

    function closeCommandPalette() {
        const modal = document.getElementById('command-palette-modal');
        if (modal) modal.classList.remove('active');
    }

    function filterCommands(query) {
        const q = query.toLowerCase().trim();
        if (!q) {
            filteredItems = [...COMMAND_ITEMS];
        } else {
            filteredItems = COMMAND_ITEMS.filter(item => 
                item.title.toLowerCase().includes(q) || item.category.toLowerCase().includes(q)
            );
        }
        activeIndex = 0;
        renderCommandList();
    }

    function renderCommandList() {
        const listEl = document.getElementById('command-palette-list');
        if (!listEl) return;

        if (filteredItems.length === 0) {
            listEl.innerHTML = '<div class="command-empty">No matching actions or pages found.</div>';
            return;
        }

        listEl.innerHTML = filteredItems.map((item, idx) => `
            <div class="command-item ${idx === activeIndex ? 'selected' : ''}" data-index="${idx}" role="option" aria-selected="${idx === activeIndex}">
                <span class="command-item-icon" aria-hidden="true">${item.icon}</span>
                <div class="command-item-text">
                    <span class="command-item-title">${escapeHtml(item.title)}</span>
                    <span class="command-item-cat">${escapeHtml(item.category)}</span>
                </div>
                ${item.shortcut ? `<kbd class="command-item-kbd">${item.shortcut}</kbd>` : ''}
            </div>
        `).join('');

        // Bind clicks
        listEl.querySelectorAll('.command-item').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.getAttribute('data-index'), 10);
                executeCommand(filteredItems[idx]);
            });
        });
    }

    function executeCommand(item) {
        if (!item) return;
        closeCommandPalette();
        if (item.action) {
            item.action();
        } else if (item.url) {
            window.location.href = item.url;
        }
    }

    // --------------------------------------------------------------------------
    // 4. Mobile Bottom Sheet Drawers
    // --------------------------------------------------------------------------
    function initBottomSheets() {
        document.querySelectorAll('.bottom-sheet-backdrop').forEach(sheet => {
            sheet.addEventListener('click', (e) => {
                if (e.target === sheet) {
                    sheet.classList.remove('active');
                }
            });
        });

        document.querySelectorAll('.close-bottom-sheet').forEach(btn => {
            btn.addEventListener('click', () => {
                const sheet = btn.closest('.bottom-sheet-backdrop');
                if (sheet) sheet.classList.remove('active');
            });
        });
    }

    // --------------------------------------------------------------------------
    // 5. Global Keyboard Shortcuts & Event Listeners
    // --------------------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', () => {
        initTheme();
        initBottomSheets();

        // Theme toggle button
        const themeBtn = document.getElementById('theme-toggle');
        if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

        // Command Palette Trigger Buttons
        document.querySelectorAll('.btn-command-palette').forEach(btn => {
            btn.addEventListener('click', openCommandPalette);
        });

        // Keyboard Shortcut: Ctrl+K or Cmd+K
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                const modal = document.getElementById('command-palette-modal');
                if (modal && modal.classList.contains('active')) {
                    closeCommandPalette();
                } else {
                    openCommandPalette();
                }
            } else if (e.key === 'Escape') {
                closeCommandPalette();
                document.querySelectorAll('.modal-backdrop.active, .bottom-sheet-backdrop.active').forEach(el => {
                    el.classList.remove('active');
                });
            }
        });

        // Command Palette Input Handlers
        const input = document.getElementById('command-palette-input');
        if (input) {
            input.addEventListener('input', (e) => {
                filterCommands(e.target.value);
            });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    if (filteredItems.length > 0) {
                        activeIndex = (activeIndex + 1) % filteredItems.length;
                        renderCommandList();
                    }
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    if (filteredItems.length > 0) {
                        activeIndex = (activeIndex - 1 + filteredItems.length) % filteredItems.length;
                        renderCommandList();
                    }
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    executeCommand(filteredItems[activeIndex]);
                }
            });
        }

        // Close Command Palette on Backdrop Click
        const paletteModal = document.getElementById('command-palette-modal');
        if (paletteModal) {
            paletteModal.addEventListener('click', (e) => {
                if (e.target === paletteModal) closeCommandPalette();
            });
        }

        // Register Service Worker for PWA
        if ('serviceWorker' in navigator && window.location.protocol.startsWith('http')) {
            navigator.serviceWorker.register('/static/sw.js').catch(err => {
                console.debug('ServiceWorker registration deferred:', err);
            });
        }
    });
})();
