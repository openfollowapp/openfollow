<div id="config-transfer-section" class="section" data-fold-key="config-transfer" data-help="config-transfer">
    <div class="section-head">
        <h2>Configuration Transfer</h2>
        <span class="section-note">Export and import device settings</span>
    </div>

    <div id="import-restart-notice" class="restart-notice" style="display:none">
        App is restarting&hellip; Please wait.
    </div>

    <div id="config-transfer-content">
        <div class="group">
            <h3 class="group-title">Export</h3>
            <p style="color:var(--muted);font-size:0.88rem;margin:0 0 0.6rem;">
                Download the current configuration as a JSON file.
            </p>
            <div class="actions">
                <button type="button" class="save-btn" onclick="exportConfig()">Export Configuration</button>
            </div>
        </div>

        <div class="group">
            <h3 class="group-title">Import</h3>
            <p style="color:var(--muted);font-size:0.88rem;margin:0 0 0.6rem;">
                Load a previously exported configuration file.
                The device's network IP address will be preserved.
            </p>
            <label for="config-import-file">Configuration File (.openfollowsettings)</label>
            <input type="file" id="config-import-file" accept=".openfollowsettings" style="display:none"
                   onchange="document.getElementById('config-import-filename').textContent = this.files[0] ? this.files[0].name : ''">
            <div class="actions">
                <button type="button" class="btn-secondary"
                        onclick="document.getElementById('config-import-file').click()">
                    Choose file
                </button>
                <span id="config-import-filename" class="field-note" style="margin:0;align-self:center"></span>
            </div>
            <div id="import-error" class="restart-notice" style="display:none;margin-top:0.72rem;border-color:rgba(255,140,140,0.35);background:rgba(255,140,140,0.13);color:#ffd7d7;"></div>
            <div class="actions" style="margin-top:0.72rem;">
                <button type="button" class="save-btn" id="import-btn" onclick="importConfig()">Import Configuration</button>
            </div>
        </div>
    </div>

    <!-- Restart confirmation overlay -->
    <div id="import-restart-overlay" style="display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.7);backdrop-filter:blur(4px);align-items:center;justify-content:center;">
        <div style="background:var(--bg-soft);border:1px solid var(--border);border-radius:1.1rem;padding:1.5rem;max-width:480px;width:calc(100% - 2rem);">
            <div class="section-head">
                <h2>Restart Required</h2>
            </div>
            <p style="color:var(--muted);font-size:0.9rem;margin:0 0 0.6rem;">
                The imported configuration has been validated.
                Some changes require an app restart to take effect:
            </p>
            <ul id="import-restart-reasons" style="color:var(--accent);font-size:0.9rem;margin:0 0 1rem;padding-left:1.2rem;"></ul>
            <div class="actions" style="flex-wrap:wrap;">
                <button type="button" class="restart-btn" onclick="confirmImportRestart()">Restart Now</button>
                <button type="button" class="secondary" onclick="skipImportRestart()">Apply Without Restart</button>
                <button type="button" class="secondary" onclick="cancelImportRestart()">Cancel</button>
            </div>
        </div>
    </div>
</div>

<script>
var _pendingImportData = null;

function _setImportError(msg) {
    var el = document.getElementById('import-error');
    if (msg) { el.textContent = msg; el.style.display = 'block'; }
    else { el.style.display = 'none'; }
}

function exportConfig() {
    window.location.href = '/api/config/export';
}

function importConfig() {
    _setImportError('');
    var fileInput = document.getElementById('config-import-file');
    if (!fileInput.files.length) {
        _setImportError('Please select a configuration file.');
        return;
    }
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = 'Importing\u2026';

    var reader = new FileReader();
    reader.onerror = function() {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';
        _setImportError('Could not read the selected file.');
    };
    reader.onload = function(e) {
        var raw = e.target.result;
        try { JSON.parse(raw); }
        catch (err) {
            btn.disabled = false;
            btn.textContent = 'Import Configuration';
            _setImportError('The selected file is not valid JSON.');
            return;
        }
        _sendImport(raw, '');
    };
    reader.readAsText(fileInput.files[0]);
}

function _sendImport(body, queryParams) {
    var btn = document.getElementById('import-btn');
    fetch('/api/config/import' + queryParams, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: body
    })
    .then(function(res) { return res.json(); })
    .then(function(result) {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';

        if (result.error) {
            _setImportError('Import failed: ' + result.error);
            return;
        }
        if (result.restarting) {
            _showRestartingState();
            return;
        }
        if (result.needs_restart) {
            _pendingImportData = body;
            var list = document.getElementById('import-restart-reasons');
            list.innerHTML = result.restart_reasons.map(function(r) {
                return '<li>' + r + '</li>';
            }).join('');
            document.getElementById('import-restart-overlay').style.display = 'flex';
            return;
        }
        /* success, no restart */
        showToast(result.skipped_restart_sections
            ? 'Configuration imported (some changes need a restart)'
            : 'Configuration imported successfully');
        setTimeout(function() { window.location.reload(); }, 600);
    })
    .catch(function() {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';
        _setImportError('Import request failed. Check your network connection.');
    });
}

function confirmImportRestart() {
    document.getElementById('import-restart-overlay').style.display = 'none';
    if (!_pendingImportData) return;
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = 'Importing\u2026';
    _sendImport(_pendingImportData, '?confirm_restart=1');
    _pendingImportData = null;
}

function skipImportRestart() {
    document.getElementById('import-restart-overlay').style.display = 'none';
    if (!_pendingImportData) return;
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = 'Importing\u2026';
    _sendImport(_pendingImportData, '?skip_restart=1');
    _pendingImportData = null;
}

function cancelImportRestart() {
    document.getElementById('import-restart-overlay').style.display = 'none';
    _pendingImportData = null;
}

function _showRestartingState() {
    document.getElementById('config-transfer-content').style.display = 'none';
    var notice = document.getElementById('import-restart-notice');
    notice.style.display = 'block';
    /* Poll until the server comes back up, then reload */
    var poll = setInterval(function() {
        fetch('/api/info').then(function(res) {
            if (res.ok) { clearInterval(poll); window.location.reload(); }
        }).catch(function() { /* still restarting */ });
    }, 2000);
}
</script>
