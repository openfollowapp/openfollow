<div id="config-transfer-section" class="section" data-fold-key="config-transfer" data-help="config-transfer">
    <div class="section-head">
        <h2>Configuration</h2>
        <span class="section-note">Export, import and restore device settings</span>
    </div>

    <div class="group">
        <h3 class="group-title">Export</h3>
        <div class="actions" id="export-actions">
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
            <button type="button" class="btn-secondary" id="restore-defaults-btn"
                    onclick="restoreDefaults()">Restore Defaults</button>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title" id="backups-heading">Backups on this station</h3>
        <div id="backups-list"></div>
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

/* Fetched rather than navigated to, so a refusal shows its reason here instead of replacing the page. */
async function exportConfig() {
    var saveError = window.OpenFollow.saveError;
    saveError.clear(document.getElementById('config-transfer-section'));
    var res;
    try {
        res = await fetch('/api/config/export');
    } catch (e) {
        _transferFailed(saveError.UNREACHABLE, 'Not exported.', 'export-actions');
        return;
    }
    if (!res.ok) {
        _transferFailed(await saveError.fromResponse(res), 'Not exported.', 'export-actions');
        return;
    }
    var named = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
    var link = document.createElement('a');
    link.href = URL.createObjectURL(await res.blob());
    link.download = named ? named[1] : 'settings.ofsettings';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function() { URL.revokeObjectURL(link.href); }, 10000);
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
    _sendRestarting('/api/config/reset', {method: 'POST'}, _restoreFailed, 'Restored defaults.');
}

/* A change the station restarts for: the restart's progress shows from the moment it is sent. */
async function _sendRestarting(url, init, failed, done) {
    openfollowRestartProgress();
    var res, text;
    try {
        res = await fetch(url, init);
        text = await res.text();
    } catch (e) {
        closeModal();
        failed(window.OpenFollow.saveError.UNREACHABLE);
        return;
    }
    var result = {};
    try { result = JSON.parse(text); } catch (e) { /* not JSON: reported below */ }
    if (!res.ok || !result.success) {
        closeModal();
        failed(window.OpenFollow.saveError.fromText(res.status, text));
        return;
    }
    if (result.backup_error) {
        toastOnNextLoad(done + ' ' + NO_BACKUP_MADE, 'caution');
    }
    openfollowAwaitRestart(result.instance || '');
}

var _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* "28 Oct 2026, 21:40" in this browser's time zone. */
function _backupTime(iso) {
    var d = new Date(iso);
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return d.getDate() + ' ' + _MONTHS[d.getMonth()] + ' ' + d.getFullYear()
        + ', ' + two(d.getHours()) + ':' + two(d.getMinutes());
}

function _backupCell(row, text, className) {
    var cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = text;
    row.appendChild(cell);
    return cell;
}

function _backupRow(backup) {
    var row = document.createElement('tr');
    _backupCell(row, _backupTime(backup.created), 'backup-made');
    _backupCell(row, backup.before);
    var version = _backupCell(row, backup.version);
    var button = document.createElement('button');
    button.setAttribute('type', 'button');
    button.className = 'btn-secondary small';
    button.textContent = 'Restore\u2026';
    if (backup.newer) {
        row.className = 'backup-newer';
        var note = document.createElement('span');
        note.className = 'backup-note';
        note.textContent = 'Newer than this station';
        version.appendChild(note);
        button.disabled = true;
    } else {
        button.addEventListener('click', function() { restoreBackup(backup); });
    }
    _backupCell(row, '', 'row-actions').appendChild(button);
    return row;
}

function _backupsUnread(list, info) {
    var notice = document.createElement('div');
    notice.className = 'notice error';
    notice.setAttribute('role', 'alert');
    notice.textContent = info.error;
    var sub = document.createElement('div');
    sub.className = 'notice-sub';
    sub.textContent = info.action || 'Details in Logs.';
    notice.appendChild(sub);
    list.replaceChildren(notice);
}

async function loadBackups() {
    var list = document.getElementById('backups-list');
    var backups;
    try {
        var res = await fetch('/api/config/backups');
        if (!res.ok) {
            _backupsUnread(list, await window.OpenFollow.saveError.fromResponse(res));
            return;
        }
        backups = (await res.json()).backups;
    } catch (e) {
        _backupsUnread(list, {error: 'The backups on this station could not be read.', action: 'Reload the page.'});
        return;
    }
    if (!backups.length) {
        var empty = document.createElement('p');
        empty.className = 'backups-empty';
        empty.textContent = 'No backups are saved on this station yet.';
        list.replaceChildren(empty);
        return;
    }
    var table = document.createElement('table');
    table.className = 'data-table backups-table';
    table.setAttribute('aria-labelledby', 'backups-heading');
    table.innerHTML = '<thead><tr><th scope="col">Made</th><th scope="col">Before</th><th scope="col">Version</th>'
        + '<th scope="col"><span class="visually-hidden">Restore</span></th></tr></thead>';
    var body = document.createElement('tbody');
    backups.forEach(function(backup) { body.appendChild(_backupRow(backup)); });
    table.appendChild(body);
    var wrap = document.createElement('div');
    wrap.className = 'backups-table-wrap';
    wrap.appendChild(table);
    list.replaceChildren(wrap);
}

function _backupFailed(info) {
    _transferFailed(info, 'Not restored.', 'backups-list');
}

async function restoreBackup(backup) {
    window.OpenFollow.saveError.clear(document.getElementById('config-transfer-section'));
    var ok = await modalConfirm({
        title: 'Restore this backup?',
        message: 'The settings from ' + _backupTime(backup.created) + ' (made before ' + backup.before
            + ', version ' + backup.version + ') replace the current ones, together with the marker '
            + 'catalog, saved templates and lens calibrations of that time, and the station restarts. '
            + 'This station\'s name, network access and local file paths stay as they are. A backup '
            + 'of the current settings is saved on the station first.',
        confirmLabel: 'Restore and Restart',
        danger: true,
    });
    if (!ok) return;
    _sendRestarting('/api/config/backups/restore', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: backup.name}),
    }, _backupFailed, 'Restored the backup.');
}

loadBackups();
</script>
