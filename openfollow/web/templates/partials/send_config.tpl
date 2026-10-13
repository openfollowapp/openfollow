<div id="send-config-section" class="section" data-fold-key="send-config">
    <div class="section-head">
        <h2>Send Config to Other Stations</h2>
        <span class="section-note">Push this device's full configuration to all discovered peers</span>
    </div>

    <p style="color:var(--muted);font-size:0.88rem;margin:0 0 0.8rem;">
        Sends all current settings to every online peer on the network.
        Each peer's device-specific IP address will be preserved.
    </p>

    <div id="send-config-result" style="display:none;margin-bottom:0.72rem;" class="update-notice"></div>

    <div class="actions">
        <button type="button" class="broadcast-btn" id="send-all-btn" onclick="broadcastAllConfig()">Send All Settings</button>
    </div>
</div>

<script>
function broadcastAllConfig() {
    var btn = document.getElementById('send-all-btn');
    var result = document.getElementById('send-config-result');
    var section = document.getElementById('send-config-section');
    var saveError = window.OpenFollow.saveError;
    btn.disabled = true;
    btn.textContent = 'Sending\u2026';
    result.style.display = 'none';
    saveError.clear(section);
    fetch('/api/config/broadcast-all', { method: 'POST' })
        .then(async function(res) {
            btn.disabled = false;
            btn.textContent = 'Send All Settings';
            if (!res.ok) {
                saveError.show(section, await saveError.fromResponse(res), 'Not sent.');
                return;
            }
            var data = await res.json();
            var total = data.peer_results.length;
            var failed = data.peer_results.filter(function(p) { return !p.success; });
            if (failed.length > 0) {
                saveError.show(section, {
                    error: 'Not sent to ' + failed.length + ' of ' + total + ' stations: '
                        + failed.map(function(p) { return p.name || p.ip; }).join(', ') + '.',
                    action: 'Check that they are switched on, then send again.',
                }, '');
                return;
            }
            result.textContent = total === 0
                ? 'No other stations discovered on the network.'
                : 'Settings sent to ' + total + ' station(s) successfully.';
            result.className = 'update-notice';
            result.style.display = 'block';
        })
        .catch(function() {
            btn.disabled = false;
            btn.textContent = 'Send All Settings';
            saveError.show(section, saveError.UNREACHABLE, 'Not sent.');
        });
}
</script>
