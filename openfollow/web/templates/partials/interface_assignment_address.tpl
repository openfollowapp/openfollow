%# One row's Address cell, rendered by the panel and swapped in by its poll.
%# A plane that cannot send shows its state, not a sentence: the interface it
%# names is already in the picker beside it.
<span id="{{row['slot']}}"{{!' hx-swap-oob="true"' if oob else ''}}>
% if row.get('outage'):
<span class="stat-chip off">{{row['address']}}</span>
% else:
{{row['address'] or '--'}}
% end
</span>
