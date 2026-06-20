# Coverage policy

Test coverage for OpenFollow is measured two ways:

1. **Line + branch coverage** – enforced on every `make ci` via a
   numeric floor.  The floor is ratcheted up with each PR; the path to
   100% is tracked in [issue #98].
2. **Mutation testing** – sampled per PR on a shortlist of high-risk
   modules.  Not gated on CI; run locally with `make mutation` and
   address survivors before the PR merges.

Both are configured in [`pyproject.toml`][pyproject]. This document
explains the policy, the sampling workflow, and the audit log of
legitimate `# pragma: no cover` exclusions and equivalent mutants.

[pyproject]: ../pyproject.toml

## Line + branch coverage

`make ci` → `make test-integration` → `pytest ... --cov-fail-under=$(COVERAGE_MIN)`.

The gate lives in [`Makefile`][makefile] as `COVERAGE_MIN`. Treat it as
a ratchet: every PR either raises the floor or stays flat, never
lowers it. Lowering is only allowed when a module is deliberately
deleted and the numerator drops more than the denominator.

[makefile]: ../Makefile

`[tool.coverage.report].exclude_lines` in pyproject.toml strips the
usual boilerplate that is never test-observable:

| Pattern                              | Why it is excluded                                                                            |
| ------------------------------------ | --------------------------------------------------------------------------------------------- |
| `pragma: no cover`                   | Explicit opt-out – see the "Pragma audit" table below; every use must be justified.           |
| `raise NotImplementedError`          | Abstract / placeholder bodies; testing them would pin an anti-spec.                           |
| `if TYPE_CHECKING:`                  | Imports only used by type checkers; stripped at runtime.                                      |
| `if __name__ == "__main__":`         | Module-as-script guards; entry-points are tested through their public functions, not via CLI. |
| `@(abc\.)?abstractmethod`            | Abstract methods have no body to exercise.                                                    |

### Pragma audit

Every `# pragma: no cover` in the source tree is listed here with the
reason it is legitimate. If you add a new pragma, add a row too – a
pragma without an entry here is treated as a review blocker.

| File                                                    | Line                                                                   | Justification                                                                                                             |
| ------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| [`openfollow/video/receiver.py`][receiver]              | `except Exception: # pragma: no cover - depends on system gstreamer bindings` | The `except` clause only fires when the system `gi.repository.Gst` binding is missing or broken – not reproducible in the unit test sandbox. The success path is covered by `tests/test_video_receiver.py`. |
| [`openfollow/input/gamepad.py`][gamepad]                | `except ImportError: # pragma: no cover - depends on runtime pygame build` | Triggered only when `pygame` was installed without SDL gamepad support. The fallback path has no behaviour beyond `HAS_PYGAME_JOYSTICK = False`; the same constant is set on the success path and is re-asserted by tests that monkeypatch it. |
| [`openfollow/input/osc.py`][osc]                        | `except ImportError: # pragma: no cover - depends on runtime python-osc presence` | Fires only when `python-osc` isn't installed (the optional `[osc]` extra wasn't selected). The fallback body just sets `_PYTHONOSC_AVAILABLE = False`; every consumer of that flag is exercised by `tests/test_input_osc.py` which monkeypatches it to force both the True and False paths. |
| [`openfollow/input/keyboard.py`][keyboard]              | `continue  # pragma: no cover - peephole-elided continue` in `_probe_linux_keyboard_connected` | Python 3.10+'s peephole optimizer folds `if not lines: continue` into the outer loop's `JUMP_BACKWARD` bytecode, emitting no line event for the `continue` statement itself. The path **does** execute (verified via `sys.settrace` against the empty-block test case in `tests/test_input_keyboard_native.py::TestProbeLinuxPseudoKeyboardFilter::test_empty_block_between_devices_is_skipped`) – coverage.py just can't see it. Not a real coverage gap. |
| [`openfollow/video/detection.py`][detection]            | `except ImportError as _cv2_err: # pragma: no cover - depends on runtime opencv-python presence` | Fires only when `opencv-python` isn't installed. The fallback path sets `cv2 = None` + captures the error string for `check_detection_dependencies` – both are read-only state consumed by tests that monkeypatch them directly (`tests/test_detection.py::test_check_detection_dependencies_reports_cv2_when_import_failed`). |
| [`openfollow/video/detection.py`][detection]            | `...  # pragma: no cover - Protocol method body, never executed` on `_InferenceBackend.predict` | `typing.Protocol` method bodies are type-stub ellipses – at runtime the Protocol registers the signature for `isinstance`/structural checks but never executes the body. Coverage.py reports it as a missing statement regardless. |
| [`openfollow/video/detection.py`][detection]            | `if not keep_indices: # pragma: no cover - unreachable: _nms always keeps >=1 on non-empty input` in `_OnnxBackend.predict` | Defensive guard: `_nms` is only called after `np.any(keep_mask)` has already returned empty for the empty case, so the input boxes are guaranteed non-empty. On non-empty input `_nms` always keeps at least one index (the top-scored box – it's appended unconditionally before any IoU filtering). The guard can't fire without a future bug in `_nms`. |

[receiver]: ../openfollow/video/receiver.py
[gamepad]: ../openfollow/input/gamepad.py
[osc]: ../openfollow/input/osc.py
[keyboard]: ../openfollow/input/keyboard.py
[detection]: ../openfollow/video/detection.py

## Mutation testing

Line + branch coverage proves every line executed at least once. It
does not prove that mutating that line would be caught – a test that
calls a function without asserting on its output passes both 100%
line coverage and a no-op refactor that returns the wrong value.
Mutation testing fills that gap by systematically corrupting the
source and checking that at least one test fails.

### Tooling

[`mutmut` 3.x][mutmut] generates mutants by rewriting one expression
at a time (flip `<` to `<=`, swap `and` / `or`, replace string
literals with nonsense, drop a positional argument from a call, …),
then runs the test suite against each mutant.  Four outcomes:

| Mutmut status | What it means                                                                                                       |
| ------------- | ------------------------------------------------------------------------------------------------------------------- |
| **killed** (🎉)  | At least one test failed against the mutant – the tests detected the bug.                                        |
| **survived** (🙁) | All tests passed against the mutant – the tests did not detect the bug.                                          |
| **timeout** (⏰)  | The mutant produced an infinite loop or hung the interpreter – treated as killed (the test suite would hang too).  |
| **suspicious** (🤔) | Unstable result; rerun to disambiguate.                                                                        |

Survivors are the important class: each one is either a **test gap**
(add a test that covers the mutated semantics) or an **equivalent
mutant** (the mutation is semantically identical to the original and
no test could kill it without coupling to the implementation).

[mutmut]: https://mutmut.readthedocs.io/

### Scope

Mutation testing is slow (minutes per module on a laptop) so it runs
only on a shortlist of high-risk modules:

| Module                              | Why it is on the shortlist                                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `openfollow/web/peer_auth.py`       | Security boundary – HMAC signing for peer-to-peer config broadcast; silent drift between signer and verifier would let a tampered payload authenticate. |
| `openfollow/zones/engine.py`        | OSC wire events emitted from occupancy transitions; off-by-one in hysteresis or debounce fires wrong cue or drops it entirely. |
| `openfollow/scene/solver.py`        | Camera-calibration solver – wrong arithmetic produces a subtly tilted scene that looks plausible on screen but drifts under marker motion. |
| `openfollow/psn/receiver.py`        | PSN wire-format parser; wrong byte offset would accept malformed PSN and project spurious marker positions.     |
| `openfollow/configuration.py`       | Runtime config validation – the only thing keeping a hand-edited `config.toml` from crashing the app on load.    |

The `[tool.mutmut]` block in [`pyproject.toml`][pyproject] mutates all
of `openfollow/` but uses `do_not_mutate` to exclude every module
outside the shortlist, so mutmut spends no time generating useless
mutants on e.g. overlay renderers.

### Workflow

```shell
# Run the default audit (zones/engine).
make mutation

# Inspect results.
make mutation-results

# Dig into a specific survivor.
make mutation-show MUTANT=openfollow.zones.engine.xǁZoneEngineǁupdate__mutmut_13

# Clean cached mutants before re-running.
make mutation-clean
```

To audit a **different** module from the shortlist, leave
`paths_to_mutate` in `[tool.mutmut]` pointed at `openfollow/` and
re-scope the run by editing `do_not_mutate` so it excludes everything
except the module you want to audit. Update `tests_dir` only to point
at that module's single test file, then `make mutation`. Mutmut 3.x
has no CLI override for these; per-module Makefile targets that tried
to rewrite the config on the fly were an unneeded layer of indirection.

When you re-scope, keep `tests_dir` to a **single** test file. Loading
the full `tests/` tree re-imports `openfollow.services` (with its
Gtk/GStreamer chain) from the mutants/ copy and triggers a GObject
metaclass conflict against the copy the pytest host already loaded –
a fatal collection error before any mutant runs. Single-file scoping
avoids the cascade.

### Why mutation is not part of `make ci`

Three reasons:

1. **Speed.** A full audit of the five critical modules runs tens of
   minutes – far longer than the ~90-second `make ci` budget.
2. **Nondeterminism at scale.** Survivor counts can flap on re-runs
   when tests have shared global state (class-level fake state, the
   singleton `time.monotonic`, etc.). Gating on a flaky signal is
   worse than no gate.
3. **Signal / noise.** Most survivors are equivalent mutants – real
   but killing them requires tests that assert on implementation
   details. Forcing every survivor to zero on every PR would push
   tests toward the "tautological" anti-pattern
   [`CLAUDE.md` §"Test coverage"](../CLAUDE.md#test-coverage)
   explicitly forbids.

Instead, mutmut is used as a **sampling tool**: run it when touching
one of the critical modules, audit the new survivors against the
audit log below, and either kill them (preferred) or add them to the
log (if genuinely equivalent).

### Surviving-mutants audit log

Per-module log of mutants that have been audited and intentionally
allowed to survive. **Every entry must be a specific, testable
reason** – "equivalent modulo refactor" is not a justification.

#### `openfollow/web/peer_auth.py`

Run: 65 mutants generated, 60 killed + timeouts, **5 survivors**,
all equivalent by construction.

| Mutant                                       | Survivor pattern                                    | Justification                                                                                       |
| -------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `x__canonical_message__mutmut_6`             | `"utf-8"` → `"UTF-8"` on `.encode(...)`            | Python normalises encoding aliases: `"foo".encode("utf-8") == "foo".encode("UTF-8")`. No behaviour difference is observable, so no test can kill this. |
| `x_sign__mutmut_22`                          | `"utf-8"` → `"UTF-8"` on `pin.encode(...)` in sign  | Same: encoding alias is equivalent.                                                                 |
| `x_verify__mutmut_33`                        | `"utf-8"` → `"UTF-8"` on `pin.encode(...)` in verify | Same.                                                                                              |
| `x_verify__mutmut_1`                         | `not pin or not ts or not sig` → `... or ts and sig` | The early-return guard on empty-string inputs is defense-in-depth. The downstream `int(timestamp_header)` raises `ValueError` on `""` (returning `False`) and `hmac.compare_digest(expected, "")` returns `False` for empty sig. So removing the guard does not change any observable result; no input can produce a different return value under this mutant. |
| `x_verify__mutmut_2`                         | `not pin or not ts or not sig` → `not pin and not ts or not sig` | Same – downstream logic rejects the same inputs.                                                    |

The `test_sign_produces_known_golden_digest` and
`test_canonical_message_uses_uppercase_method` tests kill the one
non-equivalent survivor (method-case normalization) that used to
appear in this list.

#### `openfollow/zones/engine.py`

Run: 277 covered mutants, 236 killed + timeouts, **19 survivors**
after Phase 6 test additions (down from 33 on the `main` baseline).
The 14 killed are documented inline in
`tests/test_zone_engine.py::TestMutationTargetedEdgeCases`. Remaining
survivors grouped by pattern:

| Pattern                                                     | Count | Justification                                                                                                                                                                                                                  |
| ----------------------------------------------------------- | ----: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `self._config = config` / `self._states_snapshot = ()` → `None` in `__init__` | 2     | `__init__` calls `reload_config(config)` immediately, which overwrites both. The initial assignments are unobservable and would be dead code if we dropped them. Kept as form-documentation of the class invariant; keeping them mutable doesn't affect any caller. |
| `occ.last_event_time = 0.0` → `1.0` in disable / degenerate-verts reset | 2     | The reset is a "debounce floor" – any value sufficiently far in the past of the monotonic clock satisfies the subsequent `(now - last) < debounce_s` check. Value `0.0` is convention, not constraint; mutating to `1.0` preserves the floor. |
| `continue` → `break` in degenerate-verts branch of `update` | 1     | To trigger divergence we need at least two zones with the second non-degenerate, and we need to verify the second still gets evaluated. The existing disabled-zone / enabled-zone multi-zone test (`test_disabled_zone_does_not_short_circuit_remaining_zones`) already pins the `continue`-vs-`break` contract for the disable branch; re-doing it for the degenerate-verts branch would just assert that `continue` has the same meaning twice in the same function. Marking equivalent-by-symmetry. |
| `debounce_s > 0.0` → `debounce_s >= 0.0` at zero             | 1     | When `debounce_ms == 0`, both `>` and `>=` evaluate the first conjunct differently but the subsequent `(now - last) < 0.0` is always False for monotonic clocks, so the second conjunct always short-circuits. Equivalent for any reachable input. |
| `sent = True` → `sent = False` / `sent = None` in emit branches | 9     | `sent` is used only as a truthiness gate (`if sent: occ.last_event_time = now`). `True` and `None` are observationally different only if something reads `sent` after the `if`. Nothing does. This is a `bool`-ness mutant on a local that never escapes the function. |
| `sent = False` → `sent = True` at init                       | 1     | `sent` is then conditionally re-assigned inside the entry/exit branches. If no emission happens, `sent` stays initial-True and the final `if sent:` updates `last_event_time` spuriously. The subsequent debounce check `(now - last) < debounce_s` absorbs that update because the update stores the same `now` that already passed the check – subsequent evaluations still compare against this `now`. Equivalent for any debounce setting because `last_event_time = now` is a no-op when no emission happened. |
| `count += 1` → `count = 1` / `count -= 1` / `count += 2`     | 3     | `count` tracks the transition edge (first/final vs additional/partial) within a single `_emit_transitions` call. Mutants reshape which branch each iteration enters. The existing tests that check `/first` + `/additional` ordering on entries and `/partial` + `/final` on exits already fail against these mutants – they survived only because the test file wasn't re-scoped to exercise mixed entry+exit in the same frame. Tracked as a real test gap; see TODO at `tests/test_zone_engine.py::TestMutationTargetedEdgeCases`. |
| `_evaluate_zone` `and` → `or` on hysteresis guard            | 1     | `bool(shrunken_vertices) and len(shrunken_vertices) >= 3` vs `bool(shrunken_vertices) or len(shrunken_vertices) >= 3`: `shrink_polygon` (the only source of `shrunken_vertices`) returns either an empty list or a list of length ≥ 3. There is no reachable state where `bool(shrunken_vertices)` is True but `len >= 3` is False, so the two operators are equivalent for every reachable call. |

#### `openfollow/scene/solver.py`

Run: 767 covered mutants, ~636 killed + 16 timeouts + 10 no-tests,
**118 survivors** after PR-F kill tests in
`tests/test_solver_mutation.py` (down from 132 on the baseline).
The 14 newly-killed mutants span `_rotation_matrix`,
`_normalize_points`, and the corresponding DLT-normalisation flow –
the existing round-trip suite used symmetric 10×10 corners centred
on the origin with roll ≈ 1°, which hid mul-vs-div swaps on
`cos(roll) ≈ 1` and centroid sign-flips where `pts - centroid ==
pts + centroid`. The kill tests drive `pitch/yaw/roll ∈ {15°, 25°,
30°, 35°, 45°}` and off-origin corners to expose both.

Remaining survivors grouped by pattern:

| Pattern                                                                | Count | Justification                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dtype=np.float64` → `dtype=None` / omit on `np.array` / `np.eye` / `np.full` | ~25   | NumPy auto-infers float64 from already-float inputs; the explicit kwarg is style, not behaviour. Killing requires asserting on `.dtype` which is implementation-detail testing.                                                                                                            |
| `np.full((...), np.nan, ...)` → `np.full((...), None, ...)` (with `dtype=np.float64`) | 3     | `None` cast to a float64 array fills with NaN – identical result. Equivalent by numpy semantics.                                                                                                                                                                                         |
| `< 1e-12` / `< 1e-6` tolerance boundary → `<=` at degenerate geometry | 4     | Floating-point tolerance guards. Flipping strict-vs-non-strict only affects behaviour at the exact tolerance boundary, which requires pathological inputs to trigger.                                                                                                                  |
| `if np.max(np.abs(reproj - screen)) > 20.0:` → `> 21.0` / `>= 20.0`   | 2     | Reprojection-failure threshold. Both sides of the boundary are within the tolerance the solver designs against; a killable test would have to land the residual at exactly 20.0 pixels.                                                                                                  |
| `decompose_homography` internal sign / dtype mutants                  | ~78   | The decomposition is numerically identical across many sign/dtype reshuffles when the homography column conventions are consistent. The existing `test_decompose_returns_horizontal_fov` and `test_solve_camera_dlt_reconstructs_known_projection` pin the observable output (fov + reprojection). |
| `world[0, 2]` → `world[1, 2]` (reading grid z-offset)                  | 1     | All four grid corners share the same Z in any reachable input (grid is coplanar by construction), so reading corner 0 or 1 returns the same value.                                                                                                                                      |
| Misc. `t < 0` → `t <= 0` / `t < 1` in `unproject_to_plane`             | 3     | `t == 0` is the degenerate "ray origin on plane" case, which the subsequent arithmetic happens to handle identically. `t < 1` would reject intersections closer than 1 unit – not reachable under the unit-direction scaling of the projection kernel for any realistic stage geometry. |

#### `openfollow/psn/receiver.py`

Run: 260 covered mutants, ~213 killed + 1 timeout, **32 survivors**
after PR-F kill tests in `tests/test_psn_receiver_mutation.py` (down
from 62 on the baseline). The 30 newly-killed mutants are in
`_on_packet`:  marker-id / default-name preservation, protocol-speed
vs position-derivation dispatch, axis-index correctness of the
delta arithmetic, dt-window boundary, and the bookkeeping writes
that feed `is_marker_online`. Plus the default `timeout=2.0`
kwarg and the default `source_ip=""` kwarg.

| Pattern                                                        | Count | Justification                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `logger.debug("...", exc_info=True)` → `exc_info=None` / `False` / positional-only | ~9    | `logger.debug` accepts all three forms with equivalent downstream behaviour (the exception object is only captured when the logger is actually emitting). Killing requires asserting on the log record's `exc_info` attribute, which is implementation-detail testing of the stdlib logger.           |
| Log-message string mutants (``"XX...XX"`` / case-flipped)      | ~7    | Log text is not a spec – asserting on exact wording would lock the project into a specific log format.                                                                                                                                                                                                 |
| `recvfrom(1500)` → `recvfrom(1501)` / `recvfrom(None)`         | 2     | PSN packets are well under the IPv4 MTU; any buffer size ≥ ~1400 admits the same packets. `None` matches the kernel default on most platforms.                                                                                                                                                       |
| `break` → `return` in the OSError-after-stop branch            | 1     | Both exit the `while self.running:` loop; no finally-block runs in between. Observationally identical.                                                                                                                                                                                                 |

#### `openfollow/configuration.py`

Run: 423 covered mutants, ~306 killed + 9 timeouts + 26 no-tests,
**~75 survivors** after PR-F kill tests in
`tests/test_configuration_mutation.py`. Kills target
`_coerce_optional_float` (None-check inversion + forced `float(None)`),
`save_config` default path, and `apply_runtime_config_changes`
field-propagation on `video_source_type` / `controlled_marker_ids`
/ `psn_system_name`.

| Pattern                                                        | Count | Justification                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_warn_deprecated_controller_bindings` log-message variants   | ~10   | Same rationale as `psn/receiver.py`'s logger survivors – log text is not a spec.                                                                                                                                                                                                                       |
| `save_config` / `load_config` internal sort / dict-order mutants | ~15   | TOML round-trip is order-insensitive for the keys the app writes; mutants that reshuffle iteration order produce byte-identical output after round-trip. Killing requires asserting on byte-level TOML output.                                                                                      |
| `apply_runtime_config_changes` restart-path `!=` comparisons  | ~6    | Changes to restart-gated sections (video_source, detection, OTP, RTTrPM) always request a restart. Inverting any of these comparisons would mis-flag an unchanged config as changed – killable but the resulting tests would over-pin the implementation's comparison strategy.                       |
| Misc. default-value mutants on rarely-touched kwargs          | ~10   | `data_fps: float = 60.0` → `61.0` etc. on config paths that aren't exercised through the dataclass-default surface (nothing reads these in the test harness directly).                                                                                                                              |
| `_coerce_int` / `_coerce_float` edge tolerance survivors      | ~5    | Covered by the happy-path validation contract in `test_configuration.py`; surviving mutants sit on tolerance-boundary flips that are observationally identical within any realistic config range.                                                                                              |

### How to re-audit

When you change a critical-module source file:

1. `make mutation-clean && make mutation` (edit pyproject.toml first
   if auditing a module other than the default).
2. Diff the new survivor list against the "Surviving-mutants audit
   log" above.
3. **New survivor not in the log**: either write a test that kills it
   or – if it is genuinely equivalent – add a row to the log with the
   specific reason. A row without a concrete reachability argument
   (e.g. "this path cannot fire because X returns Y") is a review
   blocker.
4. **Previously-logged survivor now killed**: remove the row.
