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
            <div class="actions">
                <button type="button" class="save-btn" onclick="exportConfig()">Export Configuration</button>
            </div>
        </div>

        <div class="group">
            <h3 class="group-title">Import</h3>
            <input type="file" id="config-import-file" accept=".ofsettings,.openfollowsettings" style="display:none"
                   onchange="importConfig(this)">
            <div class="actions" id="import-actions">
                <button type="button" class="save-btn" id="import-btn"
                        onclick="document.getElementById('config-import-file').click()">Import Configuration&hellip;</button>
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

var _IMPORT_LABEL = 'Import Configuration\u2026';

/* Choosing the file starts the import; the modal is the one step before it applies. */
function importConfig(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    /* Cleared so picking the same file again after a failure fires change again. */
    input.value = '';
    window.OpenFollow.saveError.clear(document.getElementById('config-transfer-section'));

    var reader = new FileReader();
    reader.onerror = function() {
        _importFailed({error: 'The selected file could not be read.'});
    };
    reader.onload = async function(e) {
        var raw = e.target.result;
        try { JSON.parse(raw); }
        catch (err) {
            _importFailed({error: 'The selected file is not valid JSON.'});
            return;
        }
        var ok = await modalConfirm({
            title: 'Import configuration?',
            message: 'Every show setting is replaced by ' + file.name + ', including the station '
                + 'name. The web login, port and interface stay as they are, and so do local file '
                + 'paths. A backup of the current settings is saved on the station first.',
            confirmLabel: 'Import',
            danger: true,
        });
        if (!ok) return;
        _sendImport(raw, file.name);
    };
    reader.readAsText(file);
}

function _sendImport(body, name) {
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = 'Importing ' + name + '\u2026';
    fetch('/api/config/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: body
    })
    .then(async function(res) {
        var text = await res.text();
        var result = {};
        try { result = JSON.parse(text); } catch (e) { /* not JSON: reported below */ }
        if (!res.ok || result.error) {
            btn.disabled = false;
            btn.textContent = _IMPORT_LABEL;
            _importFailed(window.OpenFollow.saveError.fromText(res.status, text));
            return;
        }
        if (result.backup_error) {
            /* Ready again in case the page stays, which it does when the toast can't outlive a reload. */
            btn.disabled = false;
            btn.textContent = _IMPORT_LABEL;
            toastAfterReload('Imported ' + name + '. ' + NO_BACKUP_MADE, 'caution');
            return;
        }
        toastAfterReload('Imported ' + name);
    })
    .catch(function() {
        btn.disabled = false;
        btn.textContent = _IMPORT_LABEL;
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
            + 'page comes back on its own \u2013 and local file paths are kept too. A backup of '
            + 'the current settings is saved on the station first.',
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
        if (result.backup_error) {
            toastOnNextLoad('Restored defaults. ' + NO_BACKUP_MADE, 'caution');
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
