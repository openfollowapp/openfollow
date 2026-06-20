/*
 * Client-side unit display/parse helpers (length conversion at input/display boundary).
 * Zone-editor canvas and camera wizard convert lengths; data model/API/scene math
 * stay in METRES. Faithful port of openfollow.units (format_length / parse_length /
 * metric_echo). Keep in sync with Python version (source of truth, has test suite).
 *
 * The active system is seeded from the server: base.tpl emits
 * ``window.OPENFOLLOW_UNIT_SYSTEM`` (from ``config.ui.unit_system``) before this
 * script loads. Callers use ``window.OpenFollow.units.{isImperial,formatLength,
 * parseLength,metricEcho,unitSuffixLength}``.
 */
(function () {
    window.OpenFollow = window.OpenFollow || {};

    // Exact SI definitions – 1 ft = 0.3048 m, 1 in = 0.0254 m.
    var M_PER_FT = 0.3048, M_PER_IN = 0.0254, IN_PER_FT = 12.0;
    var UNIT_TO_M = {m: 1.0, cm: 0.01, mm: 0.001, ft: M_PER_FT, "'": M_PER_FT, "in": M_PER_IN, '"': M_PER_IN};
    // Whole-magnitude validator: one-or-more unsigned tokens, nothing else.
    var LEN_FULL_RE = /^(?:\d*\.?\d+\s*(?:mm|cm|ft|in|m|'|")?\s*)+$/i;

    var imperial = false;

    function setSystem(sys) { imperial = (sys === 'imperial'); }
    function isImperial() { return imperial; }

    // Fixed-decimal string with round-half-to-even, matching Python's
    // format(x, '.Nf'). JS toFixed rounds half AWAY from zero, which diverges
    // from Python (banker's) for values that land exactly on a half (n/2^k like
    // 0.0625 m / 0.125 in) – and the "Stored: X.XXX m" echo is the operator's
    // contract for the persisted value, so it must agree with the server.
    function toFixedHalfEven(x, dp) {
        if (!isFinite(x)) return x.toFixed(dp);
        var neg = x < 0;
        var scaled = Math.abs(x) * Math.pow(10, dp);
        var floorVal = Math.floor(scaled);
        // ``scaled - floorVal === 0.5`` is exact only for dyadic x (where the
        // half is representable); every other value falls to nearest rounding,
        // matching Python's correctly-rounded format() on the same double.
        var n = (scaled - floorVal === 0.5)
            ? ((floorVal % 2 === 0) ? floorVal : floorVal + 1)
            : Math.round(scaled);
        var s = String(n);
        if (dp > 0) {
            while (s.length <= dp) s = '0' + s;
            s = s.slice(0, s.length - dp) + '.' + s.slice(s.length - dp);
        }
        return (neg ? '-' : '') + s;
    }

    // Metric: "1.524 m". Imperial adaptive: <1 ft -> "1.97 in";
    // >=1 ft -> "5 ft 4.85 in".
    function formatLength(meters) {
        if (!imperial) return toFixedHalfEven(meters, 3) + ' m';
        var sign = meters < 0 ? '-' : '';
        var a = Math.abs(meters);
        if (a < M_PER_FT) return sign + toFixedHalfEven(a / M_PER_IN, 2) + ' in';
        var totalIn = a / M_PER_IN;
        var ft = Math.floor(totalIn / IN_PER_FT);
        var remIn = totalIn - ft * IN_PER_FT;
        // 2-dp rounding can push the remainder to 12.00 in – carry it into the
        // feet column so we never render "4 ft 12.00 in" (matches Python).
        if (Math.round(remIn * 100) / 100 >= IN_PER_FT) { ft += 1; remIn = 0.0; }
        return sign + ft + ' ft ' + toFixedHalfEven(remIn, 2) + ' in';
    }

    // Always "X.XXX m" – the canonical stored value shown beside imperial inputs.
    function metricEcho(meters) { return toFixedHalfEven(meters, 3) + ' m'; }

    // Parse operator input to METRES; returns NaN on empty/garbage so callers
    // skip the update (a canvas only moves on a valid parse). Bare number =
    // feet in imperial / metres in metric; explicit m/cm/mm/ft/in/'/" work in
    // either mode; compounds like 5'6" / "5 ft 6 in"; one leading sign.
    function parseLength(raw) {
        var s = String(raw == null ? '' : raw).trim();
        if (!s) return NaN;
        var sign = 1.0;
        if (s[0] === '+' || s[0] === '-') { if (s[0] === '-') sign = -1.0; s = s.slice(1).trim(); }
        if (!LEN_FULL_RE.test(s)) return NaN;
        var defaultMult = imperial ? M_PER_FT : 1.0;
        // Local regex so there's no shared mutable ``lastIndex`` cursor across
        // calls. One token: a number with an optional unit suffix.
        var tokenRe = /(\d*\.?\d+)\s*(mm|cm|ft|in|m|'|")?/gi;
        var tokens = [], bare = 0, mt;
        while ((mt = tokenRe.exec(s)) !== null) {
            tokens.push(mt);
            if (!mt[2]) bare++;
        }
        if (!tokens.length) return NaN;
        // A bare number is a complete value; mixing it with units ("5 6 in") is
        // ambiguous – reject rather than guess (matches Python).
        if (bare && tokens.length > 1) return NaN;
        var total = 0.0;
        for (var i = 0; i < tokens.length; i++) {
            var unit = (tokens[i][2] || '').toLowerCase();
            total += parseFloat(tokens[i][1]) * (unit ? UNIT_TO_M[unit] : defaultMult);
        }
        var result = sign * total;
        return isFinite(result) ? result : NaN;
    }

    // Mirror openfollow.units.unit_suffix_length exactly ("m", not "meters").
    function unitSuffixLength() { return imperial ? 'ft / in' : 'm'; }

    window.OpenFollow.units = {
        setSystem: setSystem,
        isImperial: isImperial,
        formatLength: formatLength,
        parseLength: parseLength,
        metricEcho: metricEcho,
        unitSuffixLength: unitSuffixLength,
    };

    // base.tpl injected the active system before this script tag.
    if (window.OPENFOLLOW_UNIT_SYSTEM) setSystem(window.OPENFOLLOW_UNIT_SYSTEM);
})();
