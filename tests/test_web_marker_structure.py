# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pin the Marker tab's two-section structure.

The Marker tab contains two **independent top-level sections** – each
its own box, the same sibling-forms pattern as general.tpl and the
neighbouring movement.tpl / trigger_zones.tpl on this tab:

- ``Marker Control & Visibility``: standalone ``<section>`` (NOT a
  form). The catalog (shared, sync'd across stations) and this
  station's selection are rendered from JSON polled at
  ``/api/markers/catalog`` every 1.5 s via a JS render block embedded
  in the partial. Writes go out-of-band through ``/api/markers/...``
  API calls, so there's no Save button. The standalone ``/markers``
  page has been retired in favour of this inline UI.
- ``Marker Visuals``: ``<form id="marker-section">`` with body /
  crosshair / Z display / Z line / ground circle / color palette –
  Marker dataclass fields, submitted by the form's Save button.

Each section folds independently. Earlier iterations bundled both
inside a single wrapping form (one box, one Save), which was
confusing – splitting them mirrors how movement.tpl and the trigger
zones live as separate boxes on the same tab.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path

pytestmark = pytest.mark.unit


def _render_marker(config: AppConfig | None = None) -> str:
    return template("partials/marker", config=config if config is not None else AppConfig(), saved=False)


def _tag_after(html: str, needle: str) -> str:
    return html.split(needle, 1)[1].split(">", 1)[0]


class TestMarkerStyleForm:
    """The style toggle shows one style's groups; the other style's stay in
    the form, hidden, so a save keeps both."""

    def test_style_toggle_is_a_two_option_segmented_radio_without_sublabels(self) -> None:
        body = _render_marker()
        toggle = body.split('aria-label="Marker style"', 1)[1].split("</div>", 1)[0]
        assert toggle.count('name="marker_style"') == 2
        assert 'value="crosshair"' in toggle and 'value="cone"' in toggle
        assert "<small>" not in toggle

    def test_crosshair_style_shows_the_crosshair_groups_only(self) -> None:
        body = _render_marker()
        assert "hidden" in _tag_after(body, 'data-style-only="cone"')
        for _ in range(4):
            assert "hidden" not in _tag_after(body, 'data-style-only="crosshair"')
            body = body.split('data-style-only="crosshair"', 1)[1]

    def test_cone_style_shows_the_cone_group_only(self) -> None:
        cfg = AppConfig()
        cfg.marker.marker_style = "cone"
        body = _render_marker(cfg)
        assert "hidden" not in _tag_after(body, 'data-style-only="cone"')
        rest = body
        for _ in range(4):
            assert "hidden" in _tag_after(rest, 'data-style-only="crosshair"')
            rest = rest.split('data-style-only="crosshair"', 1)[1]

    def test_hidden_groups_still_carry_their_inputs(self) -> None:
        cfg = AppConfig()
        cfg.marker.marker_style = "cone"
        body = _render_marker(cfg)
        for name in ("ball_visible", "crosshair_size", "z_line_thickness", "ground_circle_size"):
            assert f'name="{name}"' in body

    def test_cone_group_carries_the_fill_controls(self) -> None:
        body = _render_marker()
        group = body.split('data-style-only="cone"', 1)[1].split('data-style-only="crosshair"', 1)[0]
        for name in (
            "cone_base_diameter",
            "cone_top_diameter",
            "cone_thickness",
            "cone_filled",
            "cone_opacity",
            "cone_shaded",
        ):
            assert f'name="{name}"' in group

    def test_switching_style_returns_a_hidden_invalid_field_to_its_stored_value(self) -> None:
        """A hidden field cannot show its error; left ``aria-invalid`` it kept Save disabled
        with no reason on screen. The suite cannot run page JS, so this pins the handler."""
        from pathlib import Path

        import openfollow.web as web

        base = (Path(web.__file__).parent / "templates" / "base.tpl").read_text(encoding="utf-8")
        restore = base.split("function restoreHiddenInvalid(group) {", 1)[1].split("\n }\n", 1)[0]
        assert "group.querySelectorAll('[aria-invalid=\"true\"]')" in restore
        assert "input.value = input.defaultValue;" in restore
        assert "input.setAttribute('aria-invalid', 'false');" in restore
        start = base.index("closest('input[name=\"marker_style\"]')")
        body = base[start : base.index("document.addEventListener", start)]
        hide = body.split("el.setAttribute('hidden', '');", 1)[1]
        assert hide.lstrip().startswith("restoreHiddenInvalid(el);")
        assert "refreshFormGate(form);" in body

    def test_a_validation_answer_landing_after_the_switch_is_undone_too(self) -> None:
        """The answer waits out the blur delay, so switching style straight after an invalid
        edit hid the field first and the late answer disabled Save. Registered on ``document``,
        the handler runs after the body-level one that marks the field."""
        from pathlib import Path

        import openfollow.web as web

        base = (Path(web.__file__).parent / "templates" / "base.tpl").read_text(encoding="utf-8")
        start = base.index("closest('[data-style-only][hidden]')")
        handler = base[base.rindex("document.addEventListener(", 0, start) : base.index("});", start)]
        assert handler.startswith("document.addEventListener('htmx:afterSwap',")
        assert "restoreHiddenInvalid(group);" in handler
        assert "refreshFormGate(group.closest('form'));" in handler

    def test_z_display_is_not_tied_to_a_style(self) -> None:
        """The Z readout applies to both styles, so its group never hides."""
        body = _render_marker()
        z_group_tag = body.rsplit('name="z_display_from_stage"', 1)[0].rsplit('<div class="group"', 1)[1]
        assert "data-style-only" not in z_group_tag.split(">", 1)[0]

    def test_z_display_is_the_last_group_in_the_form(self) -> None:
        """The last group drops its bottom divider; it must be one that shows
        in both styles, or cone style ends on a hidden group's divider."""
        body = _render_marker()
        form = body.split('id="marker-section"', 1)[1].split("</form>", 1)[0]
        last_group = form.rsplit('<div class="group"', 1)[1]
        assert "Z Display" in last_group
        assert "data-style-only" not in last_group.split(">", 1)[0]


class TestMarkerTabStructure:
    def test_renders_marker_visuals_section(self) -> None:
        body = _render_marker()
        assert 'data-fold-key="marker-visuals"' in body

    def test_renders_marker_control_visibility_section(self) -> None:
        body = _render_marker()
        assert 'data-fold-key="marker-control-visibility"' in body
        # The heading carries the "Marker " prefix so the title matches
        # the neighbouring "Marker Visuals" / "Marker Movement"
        # sections on the tab. The bare "Control & Visibility" wording
        # was too ambiguous next to those siblings.
        assert "<h2>Marker Control &amp; Visibility</h2>" in body or "<h2>Marker Control & Visibility</h2>" in body

    def test_two_independent_top_level_sections(self) -> None:
        """Earlier iterations nested both heads inside one wrapping
        form, producing a single box with two h2s and a shared Save.
        Pin that the catalog section lives OUTSIDE the visuals form so
        each renders as its own box (matching the movement /
        trigger-zones siblings on this tab)."""
        body = _render_marker()
        cv_idx = body.index('id="marker-control-visibility-section"')
        form_idx = body.index('id="marker-section"')
        # Control & Visibility section starts before the form, and the
        # form opens AFTER the control-visibility section closes.
        cv_close = body.index("</section>", cv_idx)
        assert cv_idx < cv_close < form_idx, (
            "marker-control-visibility-section must close before the marker-section form opens"
        )

    def test_marker_visuals_form_is_foldable(self) -> None:
        """The Marker Visuals form IS the section (no nested
        wrapper), so its top-level form tag carries the fold key –
        same pattern as movement.tpl. Pin that the fold key sits on
        the form tag itself, not on an inner subsection."""
        body = _render_marker()
        assert 'id="marker-section"' in body
        form_tag_end = body.index(">", body.index('id="marker-section"'))
        form_tag = body[: form_tag_end + 1]
        assert 'data-fold-key="marker-visuals"' in form_tag

    def test_catalog_root_present_with_polling(self) -> None:
        """The Control & Visibility subsection embeds the catalog
        renderer's polling root. Pinned so a refactor can't silently
        drop the integration back into a separate page."""
        body = _render_marker()
        assert 'id="marker-catalog-root"' in body
        assert 'hx-get="/api/markers/catalog"' in body
        assert "every 1500ms" in body
        # ``hx-swap="none"`` is required (we render manually in JS) –
        # any other value would race with the manual render.
        assert 'hx-swap="none"' in body

    def test_catalog_root_carries_explicit_hx_target(self) -> None:
        """Pin ``hx-target="this"`` on the polling div as defence in
        depth. Today the catalog lives outside any form, so there's no
        ancestor ``hx-target`` to inherit – but an earlier structure
        nested it inside ``<form id="marker-section" hx-target="#marker-section">``
        which silently routed every catalog poll's ``htmx:afterRequest``
        to the form. The listener's ``elt === root`` guard then skipped
        render, leaving the section stuck at "Loading…". Keeping the
        explicit ``hx-target="this"`` here means the same regression
        can't re-enter the next time someone re-parents the catalog
        div under a form."""
        body = _render_marker()
        import re

        m = re.search(
            r'<div\s+id="marker-catalog-root"[^>]*>',
            body,
        )
        assert m is not None, "marker-catalog-root div not found"
        assert 'hx-target="this"' in m.group(0), (
            'marker-catalog-root must set hx-target="this" so a '
            "future re-parenting under a form can't silently steal "
            "the poll's afterRequest target via attribute inheritance"
        )

    def test_no_link_to_dedicated_markers_page(self) -> None:
        body = _render_marker()
        assert 'href="/markers"' not in body

    def test_marker_id_inputs_no_longer_present(self) -> None:
        """Per-marker control/view selection is owned by the inline
        catalog JS (which fetches ``/api/markers/selection`` directly),
        not by the outer form's submit. The legacy
        ``controlled_marker_ids`` / ``viewer_marker_ids`` form fields
        are gone."""
        body = _render_marker()
        assert 'name="controlled_marker_ids"' not in body
        assert 'name="viewer_marker_ids"' not in body

    def test_marker_visuals_heading_present(self) -> None:
        body = _render_marker()
        assert "<h2>Marker Visuals</h2>" in body

    def test_visuals_save_button_inside_visuals_form(self) -> None:
        """The Visuals form has its own Save button. The catalog
        section has no Save (writes go through /api/markers/...
        directly). Pin that the form-submit Save lives inside the
        visuals form and not inside the catalog section."""
        body = _render_marker()
        # Form-submit save vs the JS-emitted ``type="button"`` saves in
        # the catalog rows.
        submit_idx = body.rfind('type="submit"')
        form_open_idx = body.index('id="marker-section"')
        form_close_idx = body.rindex("</form>")
        catalog_close_idx = body.index("</section>", body.index('id="marker-control-visibility-section"'))
        assert form_open_idx < submit_idx < form_close_idx, "Save submit button must live inside the visuals form"
        assert submit_idx > catalog_close_idx, "Save submit button must live AFTER the catalog section closes"

    def test_catalog_init_guarded_against_double_install(self) -> None:
        """The marker partial can be re-swapped via the form's
        hx-post response. The catalog IIFE installs a ``document.body``
        listener – without a guard, each form save would attach a
        second listener and double-render the catalog every 1.5 s."""
        body = _render_marker()
        assert "__markerCatalogInit" in body


class TestMarkerCatalogDiffRenderer:
    """Catalog renderer must diff against existing DOM rather than
    reassign innerHTML on every poll. Pins the diff structural markers
    to prevent silent regressions to full-reload."""

    def test_skeleton_marker_attributes_present(self) -> None:
        """The skeleton (built once per root) anchors the diff via the
        catalog body's ``data-role``; without it ``applyData`` has no
        stable join point."""
        body = _render_marker()
        assert 'data-role="catalog-body"' in body

    def test_skeleton_idempotency_flag(self) -> None:
        body = _render_marker()
        assert "skeletonReady" in body
        assert "ensureSkeleton" in body

    def test_diff_reconciliation_present(self) -> None:
        """The keyed-list reconciler is what makes the diff work –
        without it the only path is full innerHTML reassignment. Pin
        the function name AND that it walks ``entries`` against a
        ``data-marker-id`` lookup."""
        body = _render_marker()
        assert "reconcileBody" in body
        assert "getAttribute('data-marker-id')" in body

    def test_focus_guarded_writes(self) -> None:
        body = _render_marker()
        assert "document.activeElement" in body
        assert "setInputIfChanged" in body
        assert "setCheckedIfChanged" in body
        # The coarse "freeze whole render while any input is focused"
        # guard is gone – its removal is the whole point of the diff.
        # (The comment block above the IIFE may still name-check the
        # removed function as explanation, so we pin the FUNCTION
        # definition, not the literal name.)
        assert "function isEditingTextInput" not in body

    def test_no_inner_html_reassignment_in_poll_path(self) -> None:
        """Render path must not call ``root.innerHTML`` on each poll.
        Only ensureSkeleton sets root innerHTML; no other path should."""
        body = _render_marker()
        # ``setHTMLIfChanged`` writes innerHTML on inner cells (cells
        # contain mixed text + ``<strong>`` / conflict-flag spans), so
        # the rule is specifically about NOT reassigning the root's
        # innerHTML on each poll. Sanity-check the literal old form
        # ``root.innerHTML = html`` (the previous render path) is gone.
        assert "root.innerHTML = html" not in body


class TestMarkerAddRowReCreate:
    """Add-row supports (re)creating a marker with a typed id, including a
    previously-deleted id."""

    def test_add_row_does_not_clobber_typed_id(self) -> None:
        """The unconditional next_free_id reassignment is gone, replaced by
        a blank-only seed, so a typed id survives to the Add handler."""
        body = _render_marker()
        assert "if (cur !== computedNextId) idIn.value" not in body
        # Seed only when the field is blank and unfocused.
        assert "idIn.value.trim() === ''" in body

    def test_add_row_reports_success_and_failure(self) -> None:
        """The Add handler reports success in its status element and a failed
        add on the shared failed-save line, never silently."""
        body = _render_marker()
        assert 'id="add-marker-feedback"' in body
        assert "Added marker" in body
        assert ".then(writeResult(tr)).then(function(ok)" in body
        assert "writeFailed(tr, window.OpenFollow.saveError.UNREACHABLE)" in body

    def test_row_states_read_as_labels(self) -> None:
        body = _render_marker()
        assert '<span class="stat-chip off conflict-flag">Conflict</span>' in body
        # One controlling station per line, so a conflict reads as a list.
        assert "o.name).join('<br>+ ');" in body
        assert """return '<span class="stat-chip warn not-controlled">Not controlled</span>';""" in body
        assert "flash('Added marker ' + id, 'ok');" in body
        assert "flash.textContent = 'Saved';" in body

    def test_add_row_blocks_live_duplicate(self) -> None:
        """Adding a live id is blocked by the duplicate guard."""
        body = _render_marker()
        assert "already exists" in body
        assert "querySelector('[data-marker-id=\"' + id" in body


class TestThisStationToggle:
    """One table: each catalog row says what this station does with the marker, and
    the per-station selection table is gone."""

    def test_the_column_replaces_viewed_by(self) -> None:
        body = _render_marker()
        assert (
            '\'<th>This Station <span id="selection-saved-flash" class="saved-flash" aria-live="polite"></span></th>\''
            in body
        )
        assert "Viewed by" not in body
        assert "marker-selection-table" not in body
        assert "This station\\'s selection" not in body

    def test_each_row_and_the_add_row_carry_the_three_states(self) -> None:
        body = _render_marker()
        assert "[['control', 'View &amp; Control'], ['view', 'View'], ['hide', 'Hide']]" in body
        assert "thisStationToggle('this-station-' + id, id)" in body
        assert "thisStationToggle('this-station-new', null)" in body
        assert "seg-toggle seg-toggle--3 seg-toggle--compact" in body

    def test_the_save_reads_every_row_and_control_implies_view(self) -> None:
        body = _render_marker()
        assert "root.querySelectorAll('tr[data-marker-id] input[data-this-station]:checked')" in body
        assert "if (state === 'control') controlled.push(id);" in body
        assert "if (state !== 'hide') viewer.push(id);" in body

    def test_the_add_row_saves_its_choice_with_the_new_marker(self) -> None:
        """Hide is saved too: re-adding a deleted id must clear a selection the
        delete's best-effort prune could not write."""
        body = _render_marker()
        assert "if (root && chosen) postSelection(root, null, {id: id, state: chosen.value});" in body
        assert "chosen.value !== 'hide'" not in body

    def test_saves_run_one_at_a_time_and_read_the_toggles_when_sent(self) -> None:
        """Each save posts the whole selection, and the server runs requests in
        parallel: two in flight could commit out of order and put back the older one."""
        body = _render_marker()
        start = body.index("function postSelection(root, row, extra) {")
        post = body[start : body.index("\n    }\n", start)]
        assert "selectionQueue = selectionQueue" in post
        assert ".then(function() { return saveSelection(root, row, extra); })" in post
        # The snapshot is taken inside the queued save, not when the click happened.
        assert "querySelectorAll" not in post
        save = body[body.index("function saveSelection(root, row, extra) {") :]
        assert save.index("root.querySelectorAll('tr[data-marker-id] input[data-this-station]:checked')") < save.index(
            "fetch('/api/markers/selection'"
        )

    def test_the_poll_never_moves_a_toggle_mid_save(self) -> None:
        body = _render_marker()
        assert "const settled = selectionPending === 0 && Date.now() >= selectionSettleUntil;" in body
        assert "if (!tr.dataset.synced || (settled && !toggle.contains(document.activeElement))) {" in body

    def test_a_save_flashes_its_row_background(self) -> None:
        body = _render_marker()
        assert "tr.classList.add(ok ? 'row-saved' : 'row-failed');" in body
        assert "@keyframes row-flash-green { from { background-color: var(--success-chip); } }" in body
        assert "@keyframes row-flash-red { from { background-color: var(--error-chip); } }" in body


class TestMarkerTableTextSize:
    """Everything in the catalog table reads at the size of its row buttons; its chips keep their own."""

    @staticmethod
    def _font_sizes() -> dict[str, list[str]]:
        css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", _render_marker(), re.S))
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        return {
            selector.strip(): re.findall(r"font-size\s*:\s*([^;]+)", body)
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
            if "marker-catalog-table" in selector or "saved-flash" in selector
        }

    def test_the_table_takes_the_shared_size_and_sets_none_of_its_own(self) -> None:
        assert '<table class="marker-catalog-table data-table">' in _render_marker()
        sizes = [f"{sel}: {v}" for sel, vs in self._font_sizes().items() for v in vs]
        assert [s for s in sizes if not s.endswith(": inherit")] == []

    def test_every_text_button_in_the_table_is_small(self) -> None:
        classes = re.findall(
            r'<button type="button" class="([^"]+)"[^>]*>(?:Save|Delete|Add)</button>', _render_marker()
        )
        assert len(classes) == 3
        assert all("small" in c.split() for c in classes), classes

    def test_the_this_station_toggle_reads_in_normal_case_at_the_button_size(self) -> None:
        # Its options are labels, so without this they take the form label's caps and spacing.
        base = (Path(__file__).resolve().parents[1] / "openfollow/web/templates/base.tpl").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style[^>]*>(.*?)</style>", base, re.S)), flags=re.S)
        rules = {sel.strip(): body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)}
        assert "text-transform: none" in rules[".seg-toggle .seg-option"]
        assert "letter-spacing: normal" in rules[".seg-toggle .seg-option"]
        assert "font-size: var(--btn-font-sm)" in rules[".seg-toggle--compact .seg-option > span"]
