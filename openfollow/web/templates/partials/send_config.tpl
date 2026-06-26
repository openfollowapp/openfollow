<div id="send-config-section" class="section" data-fold-key="send-config">
    <div class="section-head">
        <h2>{{_('Send Config to Other Stations')}}</h2>
        <span class="section-note">{{_("Push this device's full configuration to all discovered peers")}}</span>
    </div>

    <p style="color:var(--muted);font-size:0.88rem;margin:0 0 0.8rem;">
        {{_("Sends all current settings to every online peer on the network. Each peer's device-specific IP address will be preserved. Restart-requiring changes (video source, OTP, RTTrPM, detection) are applied without automatic restart.")}}
    </p>

    <div id="send-config-result" style="display:none;margin-bottom:0.72rem;" class="update-notice"></div>

    <div class="actions">
        <button type="button" class="broadcast-btn" id="send-all-btn" onclick="broadcastAllConfig()">{{_('Send All Settings')}}</button>
    </div>
</div>

<script>
function broadcastAllConfig() {
    var btn = document.getElementById('send-all-btn');
    var result = document.getElementById('send-config-result');
    btn.disabled = true;
    btn.textContent = "{{_('Sending…')}}";
    result.style.display = 'none';
    fetch('/api/config/broadcast-all', { method: 'POST' })
        .then(function(res) { return res.json(); })
        .then(function(data) {
            btn.disabled = false;
            btn.textContent = "{{_('Send All Settings')}}";
            var total = data.peer_results.length;
            var failed = data.peer_results.filter(function(p) { return !p.success; });
            if (total === 0) {
                result.textContent = "{{_('No other stations discovered on the network.')}}";
                result.className = 'update-notice';
            } else if (failed.length === 0) {
                result.textContent = "{{_('Settings sent to ')}}" + total + "{{_(' station(s) successfully.')}}";
                result.className = 'update-notice';
            } else {
                result.textContent = "{{_('Sent to ')}}" + (total - failed.length) + '/' + total + "{{_(' stations. Failed: ')}}" + failed.map(function(p) { return p.name; }).join(', ');
                result.className = 'update-notice error';
            }
            result.style.display = 'block';
        })
        .catch(function() {
            btn.disabled = false;
            btn.textContent = "{{_('Send All Settings')}}";
            result.textContent = "{{_('Broadcast failed. Check your network connection.')}}";
            result.className = 'update-notice error';
            result.style.display = 'block';
        });
}
</script>
