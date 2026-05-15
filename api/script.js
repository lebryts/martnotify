const API_BASE = '/api';

// DOM Elements
const elements = {
    searchQuery: document.getElementById('searchQuery'),
    minQty: document.getElementById('minQty'),
    minValue: document.getElementById('minValue'),
    ntfyTopic: document.getElementById('ntfyTopic'),
    imCookie: document.getElementById('imCookie'),
    userAgent: document.getElementById('userAgent'),
    saveConfig: document.getElementById('saveConfig'),
    toggleMonitor: document.getElementById('toggleMonitor'),
    scanNow: document.getElementById('scanNow'),
    monitorStatus: document.getElementById('monitorStatus'),
    lastCheck: document.getElementById('lastCheck'),
    logContainer: document.getElementById('logContainer'),
    ntfyUrl: document.getElementById('ntfyUrl'),
    clearLogs: document.getElementById('clearLogs'),
    clearHistory: document.getElementById('clearHistory'),
    testNotify: document.getElementById('testNotify'),
    auditContent: document.getElementById('auditContent'),
    refreshAudit: document.getElementById('refreshAudit')
};

// ... existing code ...

elements.clearHistory.onclick = async () => {
    if (!confirm('This will reset matched leads memory. You will get alerts for old leads again. Continue?')) return;
    const res = await fetch(`${API_BASE}/clear-history`, { method: 'POST' });
    if (!res.ok) {
        const err = await res.json();
        alert('Failed to clear: ' + (err.message || 'Unknown error'));
    }
    updateUI();
};

elements.testNotify.onclick = async () => {
    elements.testNotify.disabled = true;
    await fetch(`${API_BASE}/test-notify`, { method: 'POST' });
    elements.testNotify.disabled = false;
    alert('Test notification sent! Check your phone.');
};

elements.clearLogs.onclick = async () => {
    if (!confirm('Clear all logs?')) return;
    const res = await fetch(`${API_BASE}/clear-logs`, { method: 'POST' });
    if (!res.ok) {
        const err = await res.json();
        alert('Failed to clear: ' + (err.message || 'Unknown error'));
    }
    updateUI();
};

let isRunning = false;

// Fetch initial status
async function updateUI() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();

        isRunning = data.isRunning;
        elements.monitorStatus.classList.toggle('active', isRunning);
        elements.monitorStatus.querySelector('.text').textContent = isRunning ? 'Monitoring' : 'Standby';
        elements.toggleMonitor.textContent = isRunning ? 'Stop Monitoring' : 'Start Monitoring';
        elements.toggleMonitor.className = isRunning ? 'btn secondary' : 'btn primary';

        elements.lastCheck.textContent = data.lastStatus;

        // Update config values if not focused
        if (document.activeElement !== elements.searchQuery) {
            const sq = data.config.searchQuery;
            elements.searchQuery.value = Array.isArray(sq) ? sq.join(', ') : (sq || '');
        }
        if (document.activeElement !== elements.minQty) elements.minQty.value = data.config.minQtyKg || '';
        if (document.activeElement !== elements.minValue) elements.minValue.value = data.config.minValue || '';
        if (document.activeElement !== elements.ntfyTopic) elements.ntfyTopic.value = data.config.ntfyTopic || '';
        if (data.config.userAgent) elements.userAgent.value = data.config.userAgent;
        // We don't populate the cookie field for security

        elements.ntfyUrl.href = `https://ntfy.sh/${data.config.ntfyTopic}`;
        elements.ntfyUrl.textContent = `ntfy.sh/${data.config.ntfyTopic}`;

        // Logs
        elements.logContainer.innerHTML = data.logs.map(log => {
            const isMatch = log.includes('Lead Match') || log.includes('Alert Sent');
            return `<div class="log-entry ${isMatch ? 'match' : ''}">${log}</div>`;
        }).join('');

        updateAuditLog();

    } catch (err) {
        console.error('Failed to fetch status:', err);
    }
}

// Actions
elements.saveConfig.onclick = async () => {
    const rawSearch = elements.searchQuery.value;
    const searchArray = rawSearch.split(',').map(s => s.trim()).filter(s => s);

    const config = {
        searchQuery: searchArray,
        minQtyKg: parseInt(elements.minQty.value),
        minValue: parseInt(elements.minValue.value),
        ntfyTopic: elements.ntfyTopic.value,
        imCookie: elements.imCookie.value,
        userAgent: elements.userAgent.value
    };

    await fetch(`${API_BASE}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });

    // Clear cookie field after saving
    elements.imCookie.value = '';
    updateUI();
};

let cronInterval = null;
const SCAN_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes
let nextScanTime = null;

elements.toggleMonitor.onclick = async () => {
    await fetch(`${API_BASE}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: !isRunning })
    });

    if (!isRunning) {
        // Starting monitoring — run first scan immediately, then every 10 min
        startAutoScan();
    } else {
        // Stopping monitoring
        stopAutoScan();
    }
    updateUI();
};

function startAutoScan() {
    stopAutoScan(); // clear any existing
    // Run first scan immediately
    triggerScan();
    // Then every 10 minutes
    nextScanTime = Date.now() + SCAN_INTERVAL_MS;
    cronInterval = setInterval(() => {
        triggerScan();
        nextScanTime = Date.now() + SCAN_INTERVAL_MS;
    }, SCAN_INTERVAL_MS);
}

function stopAutoScan() {
    if (cronInterval) {
        clearInterval(cronInterval);
        cronInterval = null;
    }
    nextScanTime = null;
}

async function triggerScan() {
    try {
        await fetch(`${API_BASE}/cron?manual=true`);
    } catch (err) { }
    updateUI();
}

elements.scanNow.onclick = async () => {
    elements.scanNow.disabled = true;
    elements.scanNow.innerHTML = '<span class="icon">⌛</span> Scanning...';
    await triggerScan();
    elements.scanNow.disabled = false;
    elements.scanNow.innerHTML = '<span class="icon">🔍</span> Scan Now';
    // Reset the auto-scan timer if monitoring is on
    if (isRunning) {
        startAutoScan();
    }
};

// Polling for UI updates + show next scan countdown
setInterval(() => {
    updateUI();
    // Update next-scan countdown in the status area
    if (nextScanTime && isRunning) {
        const secsLeft = Math.max(0, Math.round((nextScanTime - Date.now()) / 1000));
        const mins = Math.floor(secsLeft / 60);
        const secs = secsLeft % 60;
        elements.lastCheck.textContent += ` | Next scan in ${mins}m ${secs.toString().padStart(2, '0')}s`;
    }
}, 15000);

async function updateAuditLog() {
    try {
        const res = await fetch(`${API_BASE}/processed-leads`);
        const data = await res.json();
        if (data.content) {
            elements.auditContent.textContent = data.content;
        }
    } catch (err) {
        console.error('Failed to fetch audit log:', err);
    }
}

elements.refreshAudit.onclick = updateAuditLog;

updateUI();

// If already monitoring on page load, start the auto-scan loop
setTimeout(() => {
    if (isRunning) startAutoScan();
}, 1000);
