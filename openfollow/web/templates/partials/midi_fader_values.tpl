% # Live-value response for mixer-style faders (GET every 100ms).
% # Three OOB swaps per fader: fill bar height, handle position, numeric value
% # + optional "(not picked up)" annotation. OOB targets by id leave click
% # handlers and selection state on strip wrapper untouched.
% for entry in fader_values:
%   pct = '%g' % (max(0.0, min(1.0, entry['value'])) * 100)
<div id="midi-fader-strip-fill-{{entry['index']}}" class="midi-fader-strip-fill" style="height:{{pct}}%" hx-swap-oob="true"></div>
<div id="midi-fader-strip-handle-{{entry['index']}}" class="midi-fader-strip-handle" style="bottom:{{pct}}%" hx-swap-oob="true"></div>
<span id="midi-fader-strip-value-{{entry['index']}}" class="midi-fader-strip-value" hx-swap-oob="true">{{'%.2f' % entry['value']}}{{!'<span class="midi-fader-strip-not-picked-up">not picked up</span>' if not entry['picked_up'] else ''}}</span>
% end
