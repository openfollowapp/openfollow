%# Options for the Save to USB storage device select; one that can't be written stays visible, with why.
% if not media:
<option value="" disabled selected>No USB storage device found. Plug one in.</option>
% end
% for m in media:
<option value="{{m.id}}"{{!' disabled' if not m.writable else ''}}{{!' selected' if m.writable and m.id == selected else ''}}>{{m.label if m.writable else f"{m.label} ({m.reason})"}}</option>
% end
