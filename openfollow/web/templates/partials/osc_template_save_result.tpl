% # Minimal status fragment swapped into save-as-template modal
% # after POST /section/osc_templates round-trip.
% if defined('ok') and ok:
<span class="field-note-msg" role="status" aria-live="polite">{{_('Template saved.')}}</span>
% else:
<span class="field-error-msg" role="alert" aria-live="assertive">{{error or _('Could not save template.')}}</span>
% end
