<div id="config-transfer-section" class="section" data-fold-key="config-transfer" data-help="config-transfer">
    <div class="section-head">
        <h2>Configuration</h2>
        <span class="section-note">Export, import and restore device settings</span>
    </div>

    <div id="config-restart-notice" class="restart-notice" style="display:none">
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
            <label for="config-import-file">Configuration File (.ofsettings)</label>
            <input type="file" id="config-import-file" accept=".ofsettings,.openfollowsettings" style="display:none"
                   onchange="document.getElementById('config-import-filename').textContent = this.files[0] ? this.files[0].name : ''">
            <div class="actions">
                <button type="button" class="btn-secondary"
                        onclick="document.getElementById('config-import-file').click()">
                    Choose file
                </button>
                <span id="config-import-filename" class="field-note" style="margin:0;align-self:center"></span>
            </div>
            <div class="actions" id="import-actions" style="margin-top:0.72rem;">
                <button type="button" class="save-btn" id="import-btn" onclick="importConfig()">Import Configuration</button>
            </div>
        </div>

        <div class="group">
            <h3 class="group-title">Restore Defaults</h3>
            <div class="actions" id="restore-actions">
                <button type="button" class="btn-danger" id="restore-defaults-btn"
                        onclick="restoreDefaults()">Restore Defaults</button>
            </div>
        </div>
    </div>
</div>

<script>
function _transferFailed(info, lead, actionsId) {
    window.OpenFollow.saveError.show(
        document.getElementById('config-transfer-section'), info, lead, document.getElementById(actionsId));
}

function _importFailed(info) {
    _transferFailed(info, 'Not imported.', 'import-actions');
}

function exportConfig() {
    window.location.href = '/api/config/export';
}

function importConfig() {
    window.OpenFollow.saveError.clear(document.getElementById('config-transfer-section'));
    var fileInput = document.getElementById('config-import-file');
    if (!fileInput.files.length) {
        _importFailed({error: 'No file is selected.', action: 'Choose a configuration file first.'});
        return;
    }
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = 'Importing\u2026';

    var reader = new FileReader();
    reader.onerror = function() {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';
        _importFailed({error: 'The selected file could not be read.'});
    };
    reader.onload = function(e) {
        var raw = e.target.result;
        try { JSON.parse(raw); }
        catch (err) {
            btn.disabled = false;
            btn.textContent = 'Import Configuration';
            _importFailed({error: 'The selected file is not valid JSON.'});
            return;
        }
        _sendImport(raw);
    };
    reader.readAsText(fileInput.files[0]);
}

function _sendImport(body) {
    var btn = document.getElementById('import-btn');
    fetch('/api/config/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: body
    })
    .then(async function(res) {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';
        var text = await res.text();
        var result = {};
        try { result = JSON.parse(text); } catch (e) { /* not JSON: reported below */ }
        if (!res.ok || result.error) {
            _importFailed(window.OpenFollow.saveError.fromText(res.status, text));
            return;
        }
        showToast('Configuration imported successfully');
        setTimeout(function() { window.location.reload(); }, 600);
    })
    .catch(function() {
        btn.disabled = false;
        btn.textContent = 'Import Configuration';
        _importFailed(window.OpenFollow.saveError.UNREACHABLE);
    });
}

function _restoreFailed(info) {
    _transferFailed(info, 'Not restored.', 'restore-actions');
}

async function restoreDefaults() {
    window.OpenFollow.saveError.clear(document.getElementById('config-transfer-section'));
    var ok = await modalConfirm({
        title: 'Restore defaults?',
        message: 'Every setting goes back to its default and the station restarts. Network '
            + 'access is not reset: the web login, port and interface stay as they are, so this '
            + 'page comes back on its own \u2013 and local file paths are kept too. Export first '
            + 'if you want a copy; this cannot be undone.',
        confirmLabel: 'Restore Defaults',
        danger: true,
    });
    if (!ok) return;

    var btn = document.getElementById('restore-defaults-btn');
    btn.disabled = true;
    btn.textContent = 'Restoring\u2026';
    fetch('/api/config/reset', {method: 'POST'})
    .then(async function(res) {
        btn.disabled = false;
        btn.textContent = 'Restore Defaults';
        var text = await res.text();
        var result = {};
        try { result = JSON.parse(text); } catch (e) { /* not JSON: reported below */ }
        if (!res.ok || !result.success) {
            _restoreFailed(window.OpenFollow.saveError.fromText(res.status, text));
            return;
        }
        /* The reset restarts the station; wait for it to answer again. */
        _showRestartingState();
    })
    .catch(function() {
        btn.disabled = false;
        btn.textContent = 'Restore Defaults';
        _restoreFailed(window.OpenFollow.saveError.UNREACHABLE);
    });
}

function _showRestartingState() {
    document.getElementById('config-transfer-content').style.display = 'none';
    var notice = document.getElementById('config-restart-notice');
    notice.style.display = 'block';
    /* Poll until the server comes back up, then reload */
    var poll = setInterval(function() {
        fetch('/api/info').then(function(res) {
            if (res.ok) { clearInterval(poll); window.location.reload(); }
        }).catch(function() { /* still restarting */ });
    }, 2000);
}
</script>
