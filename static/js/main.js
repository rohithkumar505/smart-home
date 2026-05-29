// Bootstrap toast for in-app notifications (AJAX + optional boot list on dashboard)
function showAppToast(message, category) {
    const host = document.getElementById('globalToastStack');
    if (!host || typeof bootstrap === 'undefined') return;
    if (!message) return;
    const map = {
        info: 'text-bg-primary',
        success: 'text-bg-success',
        warning: 'text-bg-warning',
        danger: 'text-bg-danger',
    };
    const bg = map[category] || map.info;
    const wrap = document.createElement('div');
    wrap.className = 'toast align-items-center ' + bg + ' border-0 shadow-sm mb-0';
    wrap.setAttribute('role', 'alert');
    wrap.setAttribute('aria-live', 'polite');
    const inner = document.createElement('div');
    inner.className = 'd-flex';
    const body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = message;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
        category === 'warning' ? 'btn-close me-2 m-auto' : 'btn-close btn-close-white me-2 m-auto';
    btn.setAttribute('data-bs-dismiss', 'toast');
    btn.setAttribute('aria-label', 'Close');
    inner.appendChild(body);
    inner.appendChild(btn);
    wrap.appendChild(inner);
    host.appendChild(wrap);
    wrap.addEventListener('hidden.bs.toast', function () {
        wrap.remove();
    });
    const t = bootstrap.Toast.getOrCreateInstance(wrap, { autohide: true, delay: 6500 });
    t.show();
}
window.showAppToast = showAppToast;

// --- Real-time device status (polling + shared DOM apply) ---

/** @type {Set<string>} */
const _deviceToggleInFlight = new Set();

function getDeviceStatusFromDom(deviceId) {
    const cb = document.getElementById('switch-' + deviceId);
    if (cb) {
        return !!cb.checked;
    }
    const card = document.getElementById('card-' + deviceId);
    if (card) {
        if (card.classList.contains('device-dash-card')) {
            return card.classList.contains('device-dash-card--on');
        }
        if (card.classList.contains('device-card')) {
            return card.classList.contains('active');
        }
    }
    return null;
}

function pulseDeviceStatusCard(deviceId) {
    const card = document.getElementById('card-' + deviceId);
    if (!card) return;
    card.classList.remove('device-status--pulse');
    void card.offsetWidth;
    card.classList.add('device-status--pulse');
}

/**
 * @param {number|string} deviceId
 * @param {boolean} status
 * @param {{ fromRemote?: boolean }} [opts]
 */
function applyDeviceStatusToDom(deviceId, status, opts) {
    const o = opts || {};
    const checkbox = document.getElementById('switch-' + deviceId);
    const card = document.getElementById('card-' + deviceId);
    const statusEl = document.getElementById('status-' + deviceId);
    if (checkbox) {
        checkbox.checked = status;
    }
    if (card) {
        if (card.classList.contains('device-dash-card')) {
            card.classList.toggle('device-dash-card--on', status);
            card.classList.toggle('device-dash-card--off', !status);
        }
        if (card.classList.contains('device-card')) {
            card.classList.toggle('active', status);
        }
    }
    if (statusEl) {
        statusEl.textContent = status ? 'ON' : 'OFF';
        statusEl.classList.toggle('device-dash-card__power--on', status);
        statusEl.classList.toggle('device-dash-card__power--off', !status);
    }
    if (o.fromRemote) {
        pulseDeviceStatusCard(deviceId);
    }
}
window.applyDeviceStatusToDom = applyDeviceStatusToDom;

function updateOnDeviceCountLabels(onCount, totalCount) {
    const stat = document.getElementById('statActiveDevicesCount');
    if (stat) {
        stat.textContent = String(onCount);
    }
    const hero = document.getElementById('dashboardOnDevicesText');
    if (hero) {
        hero.textContent = String(onCount);
    }
    const shActive = document.getElementById('systemHealthActive');
    if (shActive) {
        shActive.textContent = String(onCount);
    }
    if (typeof totalCount === 'number' && !Number.isNaN(totalCount)) {
        const shTotal = document.getElementById('systemHealthTotal');
        if (shTotal) {
            shTotal.textContent = String(totalCount);
        }
        const ratio = document.getElementById('systemHealthOnRatio');
        if (ratio) {
            ratio.textContent = onCount + ' of ' + totalCount + ' on';
        }
    }
}

function updateSmartRecommendationsDOM(recs) {
    const container = document.getElementById('smart-recommendations-container');
    if (!container) return;

    if (!Array.isArray(recs) || recs.length === 0) {
        container.innerHTML = '<p class="text-muted small mb-0">Add devices to receive recommendations.</p>';
        return;
    }

    let html = '<ul class="list-group list-group-flush rounded-3 border small">';
    recs.forEach((r, i) => {
        const v = r.variant || 'info';
        let liClass = 'list-group-item d-flex gap-3 align-items-start border-0 ';
        if (v === 'warning') liClass += 'sm-rec--warn bg-warning bg-opacity-10 ';
        else if (v === 'info') liClass += 'sm-rec--info ';
        else if (v === 'success') liClass += 'sm-rec--ok border-success border-opacity-25 ';
        else liClass += 'sm-rec--neutral bg-light ';

        if (i === 0) liClass += 'rounded-top ';
        if (i === recs.length - 1) liClass += 'rounded-bottom ';

        let iconHtml = '<i class="bi bi-lightbulb text-secondary fs-4"></i>';
        if (r.icon === 'lightning-charge') iconHtml = '<i class="bi bi-lightning-charge-fill text-warning fs-4"></i>';
        else if (r.icon === 'graph-up-arrow') iconHtml = '<i class="bi bi-graph-up-arrow text-danger fs-4"></i>';
        else if (r.icon === 'power') iconHtml = '<i class="bi bi-power text-primary fs-4"></i>';
        else if (r.icon === 'moon') iconHtml = '<i class="bi bi-moon-stars text-info fs-4"></i>';
        else if (r.icon === 'snow') iconHtml = '<i class="bi bi-snow text-info fs-4"></i>';
        else if (r.icon === 'check2-circle') iconHtml = '<i class="bi bi-check2-circle text-success fs-4"></i>';
        else if (r.icon === 'heart') iconHtml = '<i class="bi bi-heart text-success fs-4"></i>';

        html += `<li class="${liClass}">
            <div class="flex-shrink-0 mt-0 pt-0">${iconHtml}</div>
            <div>
                <div class="fw-semibold">${r.title || 'Tip'}</div>
                <div class="text-muted mt-1" style="line-height:1.4;">${r.body || ''}</div>
            </div>
        </li>`;
    });
    html += '</ul>';
    container.innerHTML = html;
}

function syncDeviceStatusFromServer() {
    if (!document.getElementById('globalToastStack')) {
        return;
    }
    fetch('/api/devices/status', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
    })
        .then(function (r) {
            if (!r.ok) {
                throw new Error('status');
            }
            return r.json();
        })
        .then(function (data) {
            if (!data || !Array.isArray(data.devices)) {
                return;
            }
            for (const row of data.devices) {
                const id = row.id;
                const idKey = String(id);
                if (_deviceToggleInFlight.has(idKey)) {
                    continue;
                }
                const st = !!row.status;
                const have = getDeviceStatusFromDom(id);
                if (have === null || have === st) {
                    continue;
                }
                applyDeviceStatusToDom(id, st, { fromRemote: true });
            }
            if (typeof data.on_count === 'number') {
                const t =
                    typeof data.total_count === 'number' && !Number.isNaN(data.total_count)
                        ? data.total_count
                        : undefined;
                updateOnDeviceCountLabels(data.on_count, t);
            }
            if (data.smart_recommendations) {
                updateSmartRecommendationsDOM(data.smart_recommendations);
            }
        })
        .catch(function () {});
}

const DEVICE_POLL_ACTIVE_MS = 5000;
const DEVICE_POLL_IDLE_MS = 20000;

function getDevicePollIntervalMs() {
    return document.hidden ? DEVICE_POLL_IDLE_MS : DEVICE_POLL_ACTIVE_MS;
}

let _devicePollTimer = null;
function scheduleDeviceStatusPoll() {
    if (_devicePollTimer) {
        clearTimeout(_devicePollTimer);
    }
    _devicePollTimer = window.setTimeout(function () {
        syncDeviceStatusFromServer();
        scheduleDeviceStatusPoll();
    }, getDevicePollIntervalMs());
}

function initDeviceStatusLiveUpdates() {
    if (!document.getElementById('globalToastStack')) {
        return;
    }
    syncDeviceStatusFromServer();
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            syncDeviceStatusFromServer();
        }
        if (_devicePollTimer) {
            clearTimeout(_devicePollTimer);
            _devicePollTimer = null;
        }
        scheduleDeviceStatusPoll();
    });
    scheduleDeviceStatusPoll();
}


// Toggle Device Status via AJAX
function toggleDevice(deviceId) {
    const idKey = String(deviceId);
    const checkbox = document.getElementById('switch-' + idKey);
    const card = document.getElementById('card-' + idKey);
    const prevChecked = checkbox
        ? checkbox.checked
        : card
            ? card.classList.contains('device-dash-card--on') || card.classList.contains('active')
            : false;

    _deviceToggleInFlight.add(idKey);
    fetch('/toggle_device/' + encodeURIComponent(idKey), {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
    })
        .then((response) => response.json())
        .then((data) => {
            _deviceToggleInFlight.delete(idKey);
            if (data.success) {
                applyDeviceStatusToDom(deviceId, !!data.status);
                if (data.notification && data.notification.message) {
                    showAppToast(data.notification.message, data.notification.category || 'success');
                }
                window.setTimeout(syncDeviceStatusFromServer, 400);
            } else {
                if (checkbox) {
                    checkbox.checked = prevChecked;
                }
                alert('Failed to toggle device');
            }
        })
        .catch((error) => {
            console.error('Error:', error);
            _deviceToggleInFlight.delete(idKey);
            if (checkbox) {
                checkbox.checked = prevChecked;
            }
        });
}

// Process text command (Turn on light, Turn off fan, …)
document.getElementById('commandForm')?.addEventListener('submit', function (e) {
    e.preventDefault();
    const commandInput = document.getElementById('commandInput');
    const command = commandInput.value.trim();
    const responseDiv = document.getElementById('commandResponse');

    fetch('/command', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command: command }),
    })
        .then(function (response) {
            if (!response.ok) {
                throw new Error('Request failed');
            }
            return response.json();
        })
        .then(function (data) {
            if (!responseDiv) return;
            responseDiv.style.display = 'block';
            if (data.notification && data.notification.message) {
                showAppToast(
                    data.notification.message,
                    data.notification.category || (data.success ? 'success' : 'danger')
                );
            }
            if (data.success) {
                responseDiv.className = 'alert alert-success mt-3';
                responseDiv.textContent = data.message || 'Done.';
                if (data.changed === true) {
                    setTimeout(syncDeviceStatusFromServer, 200);
                }
            } else {
                responseDiv.className = 'alert alert-danger mt-3';
                responseDiv.textContent = data.message || 'Command could not be processed.';
            }
            commandInput.value = '';
        })
        .catch(function () {
            if (responseDiv) {
                responseDiv.style.display = 'block';
                responseDiv.className = 'alert alert-danger mt-3';
                responseDiv.textContent = 'Could not reach the server. Try again.';
            }
        });
});

// Notifications — mark read and update dashboard list / unread counter
function markRead(notifId) {
    fetch(`/notifications/read/${notifId}`, {
        method: 'POST',
    })
        .then((r) => r.json())
        .then((data) => {
            if (!data.success) return;
            const item = document.getElementById(`notif-${notifId}`);
            if (item) {
                item.classList.add('opacity-50');
                const badge = item.querySelector('[data-notif-read-badge]');
                if (badge) {
                    badge.textContent = 'Read';
                    badge.classList.remove('bg-primary');
                    badge.classList.add('bg-secondary');
                }
                const btn = item.querySelector('[data-mark-read]');
                if (btn) btn.remove();
            }
            const stat = document.getElementById('unreadNotifStat');
            if (stat) {
                let n = parseInt(stat.textContent, 10);
                if (isNaN(n)) n = 0;
                stat.textContent = Math.max(0, n - 1);
            }
            document.querySelectorAll('[data-unread-notif-badge]').forEach(function (el) {
                var n = parseInt(el.textContent, 10);
                if (isNaN(n)) n = 0;
                var next = Math.max(0, n - 1);
                if (next === 0) {
                    el.remove();
                } else {
                    el.textContent = next > 9 ? '9+' : String(next);
                }
            });
        });
}

// Auto-hide flash toasts (server-rendered flashed messages only)
setTimeout(function () {
    var host = document.getElementById('flashToastContainer');
    if (!host || typeof bootstrap === 'undefined') return;
    host.querySelectorAll('.toast').forEach(function (el) {
        var inst = bootstrap.Toast.getOrCreateInstance(el, { autohide: true, delay: 5000 });
        inst.hide();
    });
}, 5000);

function getUiTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function setUiTheme(mode) {
    var m = mode === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', m);
    document.documentElement.setAttribute('data-bs-theme', m === 'dark' ? 'dark' : 'light');
    try {
        localStorage.setItem('smUiTheme', m);
    } catch (e) {}
    document.querySelectorAll('#themeToggleNav i, #themeToggleGuest i, #themeToggleMore i').forEach(function (icon) {
        icon.className = m === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
    });
    document.querySelectorAll('#themeToggleNav, #themeToggleGuest, #themeToggleMore').forEach(function (btn) {
        if (!btn) return;
        btn.setAttribute('aria-label', m === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
        btn.setAttribute('title', m === 'dark' ? 'Light theme' : 'Dark theme');
    });
}

function toggleUiTheme() {
    setUiTheme(getUiTheme() === 'dark' ? 'light' : 'dark');
}

function initUiThemeControls() {
    setUiTheme(getUiTheme());
    ['themeToggleNav', 'themeToggleGuest', 'themeToggleMore'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('click', toggleUiTheme);
    });
}

var _dashboardLayoutSaveTimer = null;
function debouncedSaveDashboardLayout(grid) {
    if (_dashboardLayoutSaveTimer) {
        clearTimeout(_dashboardLayoutSaveTimer);
    }
    _dashboardLayoutSaveTimer = setTimeout(function () {
        _dashboardLayoutSaveTimer = null;
        var order = Array.from(grid.querySelectorAll('.dashboard-device-col')).map(function (c) {
            return parseInt(c.getAttribute('data-device-id'), 10);
        });
        if (!order.length) {
            return;
        }
        fetch('/api/dashboard/layout', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ order: order }),
        }).catch(function () {});
    }, 350);
}

function initDashboardDeviceDragDrop() {
    var grid = document.getElementById('dashboardDeviceGrid');
    if (!grid || !grid.getAttribute('data-dashboard-dnd')) {
        return;
    }
    var cols = grid.querySelectorAll('.dashboard-device-col');
    if (cols.length < 2) {
        return;
    }
    function clearOver() {
        var all = grid.querySelectorAll('.dashboard-device-col');
        for (var i = 0; i < all.length; i++) {
            all[i].classList.remove('dashboard-device-col--over');
        }
    }
    for (var c = 0; c < cols.length; c++) {
        (function (col) {
            col.addEventListener('dragstart', function (e) {
                if (e.target && e.target.closest) {
                    if (e.target.closest('button, input, label, a, [data-no-dnd]')) {
                        e.preventDefault();
                        return;
                    }
                }
                e.dataTransfer.setData('text/plain', col.getAttribute('data-device-id') || '');
                e.dataTransfer.effectAllowed = 'move';
                col.classList.add('dashboard-device-col--dragging');
            });
            col.addEventListener('dragend', function () {
                col.classList.remove('dashboard-device-col--dragging');
                clearOver();
            });
            col.addEventListener('dragover', function (e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                clearOver();
                col.classList.add('dashboard-device-col--over');
            });
            col.addEventListener('drop', function (e) {
                e.preventDefault();
                e.stopPropagation();
                clearOver();
                var raw = (e.dataTransfer.getData('text/plain') || '').trim();
                if (!raw) {
                    return;
                }
                var dragCol = grid.querySelector('.dashboard-device-col[data-device-id="' + raw + '"]');
                if (!dragCol || dragCol === col) {
                    return;
                }
                grid.insertBefore(dragCol, col);
                debouncedSaveDashboardLayout(grid);
            });
        })(cols[c]);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    initUiThemeControls();
    initDeviceStatusLiveUpdates();
    initDashboardDeviceDragDrop();
    initScrollReveal();
    init3dHeroTilt();
    initAiChat();
});

/* ── Scroll-reveal (IntersectionObserver) ────────────────────── */
function initScrollReveal() {
    // Auto-annotate common sections if not already marked
    var autoTargets = [
        { sel: '.stat-tile',            sr: 'fade-up',   delay: true },
        { sel: '.device-dash-card',     sr: 'zoom-in',   delay: true },
        { sel: '.voice-studio',         sr: 'fade-up',   delay: false },
        { sel: '.card.custom-card',     sr: 'fade-up',   delay: false },
        { sel: '.magic-reveal',         sr: 'fade-up',   delay: false },
        { sel: 'section[aria-labelledby]', sr: 'fade-up', delay: false },
        { sel: '.hero-3d-scene',        sr: 'zoom-in',   delay: false },
        { sel: '.log-item',             sr: 'fade-right', delay: true },
    ];

    autoTargets.forEach(function(cfg) {
        document.querySelectorAll(cfg.sel).forEach(function(el, i) {
            if (!el.hasAttribute('data-sr')) {
                el.setAttribute('data-sr', cfg.sr);
                if (cfg.delay) {
                    el.setAttribute('data-sr-delay', String((i % 6) + 1));
                }
            }
        });
    });

    if (!('IntersectionObserver' in window)) {
        // Fallback: just show everything
        document.querySelectorAll('[data-sr]').forEach(function(el) {
            el.classList.add('sr-visible');
        });
        return;
    }

    var observer = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add('sr-visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });

    document.querySelectorAll('[data-sr]').forEach(function(el) {
        observer.observe(el);
    });
}

/* ── 3-D hero mouse-tilt ──────────────────────────────────────── */
function init3dHeroTilt() {
    var scene = document.querySelector('.hero-3d-scene');
    if (!scene) return;
    var inner = scene.querySelector('.hero-3d-inner');
    if (!inner) return;

    var MAX_TILT = 7; // degrees
    var raf = null;
    var targetRX = 0, targetRY = 0;
    var currentRX = 0, currentRY = 0;

    scene.addEventListener('mousemove', function(e) {
        var rect = scene.getBoundingClientRect();
        var cx = rect.left + rect.width / 2;
        var cy = rect.top  + rect.height / 2;
        var dx = (e.clientX - cx) / (rect.width  / 2);
        var dy = (e.clientY - cy) / (rect.height / 2);
        targetRY =  dx * MAX_TILT;
        targetRX = -dy * MAX_TILT;
    });

    scene.addEventListener('mouseleave', function() {
        targetRX = 0;
        targetRY = 0;
    });

    function lerp(a, b, t) { return a + (b - a) * t; }

    function tick() {
        currentRX = lerp(currentRX, targetRX, 0.09);
        currentRY = lerp(currentRY, targetRY, 0.09);
        inner.style.transform =
            'rotateX(' + currentRX.toFixed(3) + 'deg) rotateY(' + currentRY.toFixed(3) + 'deg)';
        raf = requestAnimationFrame(tick);
    }
    tick();
}

/* ── AI Chat Panel ────────────────────────────────────────────── */
function initAiChat() {
    var form       = document.getElementById('aiChatForm');
    var input      = document.getElementById('aiChatInput');
    var messages   = document.getElementById('aiChatMessages');
    var clearBtn   = document.getElementById('aiClearBtn');
    var sendBtn    = document.getElementById('aiSendBtn');
    var micBtn     = document.getElementById('aiMicBtn');
    var speakerBtn = document.getElementById('aiSpeakerBtn');
    var wrap       = document.getElementById('aiAssistantWrap');
    var fab        = document.getElementById('aiAssistantFab');
    var closeBtn   = document.getElementById('aiAssistantCloseBtn');
    var panel      = document.getElementById('aiChatPanel');
    if (!form || !input || !messages) return;

    function setAssistantOpen(open) {
        if (!wrap) return;
        wrap.classList.toggle('ai-assistant-wrap--open', open);
        if (fab) fab.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (panel) panel.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (open) {
            updateVoiceHint();
            window.setTimeout(function () {
                input.focus();
            }, 300);
        }
    }

    function voiceSecureContextOk() {
        if (window.isSecureContext) return true;
        var host = window.location.hostname;
        return host === 'localhost' || host === '127.0.0.1';
    }

    function voiceHintMessage() {
        if (voiceSecureContextOk()) return '';
        var host = window.location.hostname;
        if (host === 'localhost' || host === '127.0.0.1') return '';
        var httpsPort = window.SM_VOICE_HTTPS_PORT || '5006';
        if (window.location.protocol === 'https:') return '';
        return (
            'For voice on phone use https://' + host + ':' + httpsPort +
            ' (accept certificate once), then allow the microphone.'
        );
    }

    function updateVoiceHint() {
        var hint = document.getElementById('aiVoiceHint');
        if (!hint) return;
        var msg = voiceHintMessage();
        if (msg) {
            hint.textContent = msg;
            hint.classList.remove('d-none');
        } else {
            hint.textContent = '';
            hint.classList.add('d-none');
        }
    }

    if (fab) {
        fab.addEventListener('click', function () {
            setAssistantOpen(!wrap.classList.contains('ai-assistant-wrap--open'));
        });
        bindAssistantFabLongPress(fab, function () {
            setAssistantOpen(true);
            startVoiceListening();
        });
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', function () {
            setAssistantOpen(false);
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && wrap && wrap.classList.contains('ai-assistant-wrap--open')) {
            setAssistantOpen(false);
        }
    });

    updateVoiceHint();

    // ── Text-to-Speech (Speech Synthesis API) ───────────────────
    var voiceEnabled = true;
    var currentSpeakBubble = null;
    var loadedVoices = [];

    function refreshSpeechVoices() {
        if (!window.speechSynthesis) return;
        loadedVoices = window.speechSynthesis.getVoices() || [];
    }

    refreshSpeechVoices();
    if (window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = refreshSpeechVoices;
    }

    /** iOS/Safari: unlock TTS from a user gesture so async replies can speak. */
    function unlockSpeechSynthesis() {
        if (!window.speechSynthesis) return;
        try {
            refreshSpeechVoices();
            var u = new SpeechSynthesisUtterance('\u200b');
            u.volume = 0.01;
            u.rate = 10;
            window.speechSynthesis.speak(u);
            window.speechSynthesis.cancel();
        } catch (e) {}
    }

    function stripMarkdown(text) {
        return (text || '')
            .replace(/\*\*(.+?)\*\*/g, '$1')
            .replace(/\*(.+?)\*/g, '$1')
            .replace(/<br>/gi, ' ')
            .replace(/[💡⚡🔌🏠🔢🕐✅🔴🌙ℹ️⚠️🤖👋🎙️📋]/gu, '')
            .replace(/•\s*/g, '')
            .trim();
    }

    function speakText(text, bubbleEl) {
        if (!voiceEnabled || !window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        var cleaned = stripMarkdown(text);
        if (!cleaned) return;
        refreshSpeechVoices();
        var utt = new SpeechSynthesisUtterance(cleaned);
        utt.lang = 'en-US';
        utt.rate = 1.05;
        utt.pitch = 1.0;
        utt.volume = 1.0;

        var preferred = loadedVoices.find(function (v) {
            return /google us english|samantha|alex|karen|daniel|zira/i.test(v.name) && v.lang.startsWith('en');
        }) || loadedVoices.find(function (v) {
            return v.lang.startsWith('en');
        });
        if (preferred) utt.voice = preferred;

        utt.onstart = function () {
            if (bubbleEl) bubbleEl.classList.add('ai-bubble--speaking');
        };
        utt.onend = utt.onerror = function () {
            if (bubbleEl) bubbleEl.classList.remove('ai-bubble--speaking');
            currentSpeakBubble = null;
        };
        currentSpeakBubble = bubbleEl;
        window.speechSynthesis.speak(utt);
    }

    // Speaker toggle button
    if (speakerBtn) {
        speakerBtn.addEventListener('click', function() {
            voiceEnabled = !voiceEnabled;
            if (!voiceEnabled) {
                window.speechSynthesis && window.speechSynthesis.cancel();
                speakerBtn.classList.remove('ai-speaker--on');
                speakerBtn.innerHTML = '<i class="bi bi-volume-mute-fill"></i>';
                speakerBtn.title = 'AI voice OFF — click to unmute';
            } else {
                speakerBtn.classList.add('ai-speaker--on');
                speakerBtn.innerHTML = '<i class="bi bi-volume-up-fill"></i>';
                speakerBtn.title = 'AI voice ON — click to mute';
            }
        });
    }

    // ── Voice Recognition (Web Speech API) ──────────────────────
    var SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null;
    var recognition = null;
    var isListening = false;
    var voiceListenPending = false;

    function resetMicUi() {
        isListening = false;
        voiceListenPending = false;
        if (!micBtn) return;
        micBtn.classList.remove('ai-mic--listening');
        micBtn.innerHTML = '<i class="bi bi-mic-fill"></i>';
        micBtn.title = 'Tap to speak — hold robot button for quick voice';
        input.placeholder = 'Ask anything or speak 🎙️…';
    }

    function tryStartRecognitionEngine() {
        if (!recognition) return;
        try {
            recognition.start();
        } catch (e) {
            var msg = String(e && e.message ? e.message : e);
            if (/already started|recognition/i.test(msg)) {
                try {
                    recognition.stop();
                } catch (e2) {}
                window.setTimeout(function () {
                    try {
                        recognition.start();
                    } catch (e3) {}
                }, 280);
            }
        }
    }

    function requestMicThenListen() {
        voiceListenPending = true;
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            navigator.mediaDevices
                .getUserMedia({ audio: true })
                .then(function (stream) {
                    stream.getTracks().forEach(function (t) {
                        t.stop();
                    });
                    if (voiceListenPending) tryStartRecognitionEngine();
                })
                .catch(function (err) {
                    voiceListenPending = false;
                    resetMicUi();
                    var denied = err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError');
                    appendBubble(
                        denied
                            ? '🔒 Microphone blocked. Allow mic access for this site in browser settings, then try again.'
                            : '🎙️ Could not access microphone. Check permissions and try again.',
                        'bot'
                    );
                });
        } else {
            tryStartRecognitionEngine();
        }
    }

    function startVoiceListening() {
        setAssistantOpen(true);
        unlockSpeechSynthesis();

        if (!voiceSecureContextOk()) {
            var host = window.location.hostname;
            var httpsPort = window.SM_VOICE_HTTPS_PORT || '5006';
            appendBubble(
                '🔒 **Voice on this device needs HTTPS.**\n\n' +
                    '• **Phone:** open **https://' + host + ':' + httpsPort + '** (accept certificate once)\n' +
                    '• **Mac/PC:** use **http://127.0.0.1:5005** — voice works there without HTTPS',
                'bot'
            );
            return;
        }

        if (!SpeechRecognitionCtor) {
            appendBubble(
                '🎙️ Voice recognition is not supported in this browser. Try Chrome or Safari, or type your command.',
                'bot'
            );
            return;
        }

        if (isListening) {
            recognition.stop();
            return;
        }

        if (window.speechSynthesis) window.speechSynthesis.cancel();

        if (!recognition) {
            recognition = new SpeechRecognitionCtor();
            recognition.lang = 'en-US';
            recognition.interimResults = true;
            recognition.maxAlternatives = 1;
            recognition.continuous = false;

            recognition.onstart = function () {
                isListening = true;
                voiceListenPending = false;
                micBtn.classList.add('ai-mic--listening');
                micBtn.innerHTML = '<i class="bi bi-stop-fill"></i>';
                micBtn.title = 'Listening… tap to stop';
                input.placeholder = '🎙️ Listening…';
            };

            recognition.onresult = function (event) {
                var interim = '';
                var finalText = '';
                for (var i = event.resultIndex; i < event.results.length; i++) {
                    var piece = (event.results[i][0].transcript || '').trim();
                    if (!piece) continue;
                    if (event.results[i].isFinal) {
                        finalText += (finalText ? ' ' : '') + piece;
                    } else {
                        interim += piece;
                    }
                }
                if (interim) input.value = interim;
                if (finalText) {
                    input.value = finalText;
                    sendMessage(finalText);
                    input.value = '';
                }
            };

            recognition.onerror = function (event) {
                var errMap = {
                    'no-speech': '🤫 No speech heard. Tap the mic and try again.',
                    'audio-capture': '🎙️ Microphone not found.',
                    'not-allowed': '🔒 Microphone access denied. Allow it in browser settings.',
                    'network': '📡 Voice service unavailable. Check connection or try typing.',
                    'aborted': '',
                    'service-not-allowed': '🔒 Voice blocked — use HTTPS (https://…) on mobile.',
                };
                var msg = errMap[event.error] || 'Voice error: ' + event.error;
                if (msg) appendBubble(msg, 'bot');
            };

            recognition.onend = function () {
                resetMicUi();
            };
        }

        requestMicThenListen();
    }

    if (micBtn) {
        if (!SpeechRecognitionCtor && !voiceSecureContextOk()) {
            micBtn.classList.add('ai-mic--unsupported');
            micBtn.title = 'Voice requires HTTPS on this device';
        }
        micBtn.addEventListener('click', function () {
            startVoiceListening();
        });
    }

    // Suggestion chips
    document.querySelectorAll('.ai-sugg-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            unlockSpeechSynthesis();
            var msg = btn.getAttribute('data-msg') || '';
            if (msg) {
                setAssistantOpen(true);
                sendMessage(msg);
                input.value = '';
            }
        });
    });

    // Form submit
    form.addEventListener('submit', function (e) {
        e.preventDefault();
        unlockSpeechSynthesis();
        var msg = (input.value || '').trim();
        if (!msg) return;
        sendMessage(msg);
        input.value = '';
    });

    // Clear chat
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            // Keep only welcome bubble
            var bubbles = messages.querySelectorAll('.ai-bubble');
            bubbles.forEach(function(b, i) { if (i > 0) b.remove(); });
        });
    }

    function appendBubble(text, role, typingId) {
        var wrap = document.createElement('div');
        wrap.className = 'ai-bubble ai-bubble--' + role;
        if (typingId) wrap.id = typingId;

        var avatar = document.createElement('span');
        avatar.className = 'ai-bubble__avatar';
        avatar.innerHTML = role === 'user'
            ? '<i class="bi bi-person-fill"></i>'
            : '<i class="bi bi-robot"></i>';

        var body = document.createElement('div');
        body.className = 'ai-bubble__body';

        var bubble = document.createElement('div');
        bubble.className = 'ai-bubble__text';

        if (typingId) {
            wrap.classList.add('ai-bubble--typing');
            bubble.innerHTML = '<span class="ai-typing-dots"><span></span><span></span><span></span></span>';
        } else {
            // Render **bold** and newlines
            bubble.innerHTML = renderMarkdown(text);
        }

        body.appendChild(bubble);
        wrap.appendChild(avatar);
        wrap.appendChild(body);
        messages.appendChild(wrap);
        messages.scrollTop = messages.scrollHeight;
        return wrap;
    }

    function renderMarkdown(text) {
        // Convert **bold** and newlines to HTML
        return (text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
    }

    function sendMessage(msg) {
        // Add user bubble
        appendBubble(msg, 'user');

        // Disable send while waiting
        if (sendBtn) sendBtn.disabled = true;

        // Add typing indicator
        var typingId = 'ai-typing-' + Date.now();
        var typingBubble = appendBubble('', 'bot', typingId);

        fetch('/api/ai_chat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ message: msg }),
        })
        .then(function(r) {
            if (!r.ok) throw new Error('Network error');
            return r.json();
        })
        .then(function(data) {
            // Remove typing indicator
            typingBubble.remove();

            // Show bot reply and SPEAK it
            var replyText = data.reply || '…';
            var botBubble = appendBubble(replyText, 'bot');
            speakText(replyText, botBubble);

            // If a device changed, sync the UI
            if (data.changed) {
                if (typeof data.device_id !== 'undefined' && typeof data.status !== 'undefined') {
                    applyDeviceStatusToDom(data.device_id, data.status);
                }
                if (typeof data.on_count === 'number') {
                    updateOnDeviceCountLabels(data.on_count);
                }
                // Refresh smart recommendations
                window.setTimeout(syncDeviceStatusFromServer, 400);
                // Show toast if present
                if (data.notification && data.notification.message && typeof showAppToast === 'function') {
                    showAppToast(data.notification.message, data.notification.category || 'success');
                }
            }
        })
        .catch(function() {
            typingBubble.remove();
            var errMsg = '⚠️ Could not reach the server. Please try again.';
            var errBubble = appendBubble(errMsg, 'bot');
            speakText('Could not reach the server. Please try again.', errBubble);
        })
        .finally(function() {
            if (sendBtn) sendBtn.disabled = false;
            input.focus();
        });
    }
}

/** Long-press floating assistant button → open + start voice (mobile-friendly). */
function bindAssistantFabLongPress(fabEl, onLongPress) {
    if (!fabEl || typeof onLongPress !== 'function') return;
    var delayMs = 520;
    var timer = null;
    var longPressFired = false;

    function clearTimer() {
        if (timer) {
            clearTimeout(timer);
            timer = null;
        }
    }

    function startHold() {
        clearTimer();
        longPressFired = false;
        timer = setTimeout(function () {
            longPressFired = true;
            onLongPress();
        }, delayMs);
    }

    fabEl.addEventListener('mousedown', startHold);
    fabEl.addEventListener('touchstart', startHold, { passive: true });
    fabEl.addEventListener('mouseup', clearTimer);
    fabEl.addEventListener('mouseleave', clearTimer);
    fabEl.addEventListener('touchend', clearTimer);
    fabEl.addEventListener('touchcancel', clearTimer);
    fabEl.addEventListener(
        'click',
        function (e) {
            if (longPressFired) {
                e.preventDefault();
                e.stopPropagation();
                longPressFired = false;
            }
        },
        true
    );
}
