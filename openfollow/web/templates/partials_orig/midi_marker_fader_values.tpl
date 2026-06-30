% # Live-value response for read-only marker faders (GET every 100ms).
% # Three OOB swaps per marker: fill bar height, handle position, numeric value.
% # No pickup annotation (gamepad-driven). OOB targets by marker id leave
% # rest of strip untouched.
% for entry in marker_fader_values:
%   mid = entry['marker_id']
%   pct = '%g' % (max(0.0, min(1.0, entry['value'])) * 100)
<div id="marker-fader-strip-fill-{{mid}}" class="midi-fader-strip-fill" style="height:{{pct}}%" hx-swap-oob="true"></div>
<div id="marker-fader-strip-handle-{{mid}}" class="midi-fader-strip-handle" style="bottom:{{pct}}%" hx-swap-oob="true"></div>
<span id="marker-fader-strip-value-{{mid}}" class="midi-fader-strip-value" hx-swap-oob="true">{{'%.2f' % entry['value']}}</span>
% end
