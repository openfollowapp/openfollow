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
  crosshair / Z display / drop line / ground circle / color palette –
  Marker dataclass fields, submitted by the form's Save button.

Each section folds independently. Earlier iterations bundled both
inside a single wrapping form (one box, one Save), which was
confusing – splitting them mirrors how movement.tpl and the trigger
zones live as separate boxes on the same tab.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path

pytestmark = pytest.mark.unit


def _render_marker() -> str:
    return template("partials/marker", config=AppConfig(), saved=False)


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
        """The skeleton (built once per root) anchors the diff via
        ``data-role`` attributes on the two tbodies + the station-name
        span. Without these, ``applyData`` has no stable join points."""
        body = _render_marker()
        assert 'data-role="catalog-body"' in body
        assert 'data-role="selection-body"' in body
        assert 'data-role="station-name"' in body

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

    def test_add_row_has_status_feedback(self) -> None:
        """The Add handler reports success / error via a status element and
        chains a ``.then`` on the add fetch."""
        body = _render_marker()
        assert 'id="add-marker-feedback"' in body
        assert "Added marker" in body
        assert "Could not add marker" in body

    def test_add_row_blocks_live_duplicate(self) -> None:
        """Adding a live id is blocked by the duplicate guard."""
        body = _render_marker()
        assert "already exists" in body
        assert "querySelector('[data-marker-id=\"' + id" in body
