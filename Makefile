# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Local CI gate and developer task runner: mirrors the CI workflow's
# lint / typecheck / security / test / build steps plus coverage and mutation.

.PHONY: ci ci-remote lint format typecheck security audit test test-unit test-integration test-smoke-e2e build dmg coverage coverage-html coverage-xml install-hooks \
        mutation mutation-results mutation-show mutation-clean

# Combined line + branch coverage floor – ratchet up with every PR.
# Path to 100% is the gate; CI invokes test-integration so the
# value never drifts between local `make test` and CI.
COVERAGE_MIN ?= 100

# Parallelize the suite with pytest-xdist, and cap each worker's native thread
# pools so the two don't multiply.
#
# Worker count is headroom-aware (not ``-n logical``/``-n auto``): small
# CI/Pi runners (≤4 logical CPUs) use every core, but a fat interactive dev
# workstation reserves ~1/3 of its cores so the OS/UI stays responsive – a
# 10-core Mac runs 6 workers and keeps 4 cores free. ``getconf`` is portable
# across macOS/Linux; the ``|| echo 2`` keeps the 2-vCPU CI default if it's
# absent.
#
# Worker count alone is not enough: OpenCV and numpy-BLAS each grow a pool sized
# to the full core count inside every worker, so N workers fan out to N x cores
# threads and freeze an interactive workstation regardless of ``-n``.
# ``PYTEST_THREAD_CAPS`` pins the BLAS/OpenMP pools per worker (Linux/Pi target);
# the OpenCV pool ignores those env vars on macOS and is capped in conftest.py.
#
# ``--dist load`` distributes individual tests across workers; ``loadscope``
# would pin each large module to one worker and bottleneck on the slowest. Safe
# because the suite has no cross-test shared state (function-scoped fixtures,
# ephemeral ports, ``tmp_path`` I/O). pytest-cov combines per-worker coverage, so
# the gate is unaffected. Override with ``PYTEST_PARALLEL=`` for a sequential run,
# or ``PYTEST_WORKERS=N`` to pin the worker count.
PYTEST_WORKERS ?= $(shell n=$$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2); \
                          if [ "$$n" -gt 4 ]; then echo $$((2 * n / 3)); else echo "$$n"; fi)
PYTEST_PARALLEL ?= -n $(PYTEST_WORKERS) --dist load

# One thread per worker for the BLAS/OpenMP pools (see the note above). Prefixed
# onto every pytest invocation so the xdist workers inherit it from the shell.
PYTEST_THREAD_CAPS ?= OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
                      NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1

ci: lint typecheck security test build

# Pre-push gate. Runs `make ci` on a reachable testing Pi (the real
# aarch64 / py3.13 / trixie target – catches wheel/typecheck gaps the Mac
# masks), falling back to a local `make ci` when no Pi is reachable. Host
# list + behaviour are env-overridable; see scripts/ci-remote.sh header.
# Use this – not bare `make ci` – before every `git push`.
ci-remote:
	@bash scripts/ci-remote.sh

# Lint. `ruff check` replaces `flake8`; `ruff format --check` replaces
# autopep8/black. Config in `[tool.ruff]` block of pyproject.toml.
# Run `make format` to fix formatting failures.
lint:
	poetry run ruff check openfollow tests
	poetry run ruff format --check openfollow tests

# Auto-format the tree with `ruff format`. Run this to fix a `make lint`
# failure. NOT part of `make ci`, which only checks (gates never rewrite).
format:
	poetry run ruff format openfollow tests

# Static security scanning. bandit scans the whole `openfollow` package
# (privilege broker + network adapters are highest-value targets); skip-list
# in `[tool.bandit]` block of pyproject.toml. Fast, offline, deterministic.
security:
	poetry run bandit -c pyproject.toml -r openfollow

# Supply-chain audit. pip-audit fails on known CVE (local `openfollow` +
# git-sourced `pypsn` are un-auditable skips, not failures).
#
# NOT part of `make ci` (queries live PyPI DB); newly-disclosed CVE in
# transitive dep would otherwise block all PRs. CI runs separately on
# weekly schedule + main pushes. When no fix exists yet, ignore with:
#   make audit PIP_AUDIT_IGNORE="--ignore-vuln GHSA-xxxx-yyyy-zzzz"
PIP_AUDIT_IGNORE ?=
audit:
	poetry run pip-audit $(PIP_AUDIT_IGNORE)

# Static type checking. `mypy --strict` enforced on curated batch in
# pyproject.toml (`[[tool.mypy.overrides]]`). Adding a module to the
# strict batch is opt-in and requires zero new errors.
typecheck:
	poetry run mypy

test: test-unit test-integration

test-unit:
	$(PYTEST_THREAD_CAPS) poetry run pytest -m unit -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=term-missing

test-integration:
	$(PYTEST_THREAD_CAPS) poetry run pytest -m "integration or smoke" -q $(PYTEST_PARALLEL) --cov=openfollow --cov-append --cov-report=term-missing --cov-fail-under=$(COVERAGE_MIN)

# End-to-end smoke test. Spins a real GStreamer pipeline and PSN packet
# through OTP/RTTrPM output sockets. Opt-in, OUT of `make ci` (needs
# GStreamer runtime). Per-main-push signal, not a PR gate. Skips if
# GStreamer isn't installed.
test-smoke-e2e:
	$(PYTEST_THREAD_CAPS) poetry run pytest -m smoke_e2e -q

coverage:
	$(PYTEST_THREAD_CAPS) poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=term-missing

coverage-html:
	$(PYTEST_THREAD_CAPS) poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=html:htmlcov --cov-report=term

coverage-xml:
	$(PYTEST_THREAD_CAPS) poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=xml:coverage.xml --cov-report=term

build:
	poetry build --no-interaction

# Build the self-contained macOS .app and wrap it in a .dmg
# (dist/OpenFollow-<version>-<arch>.dmg). macOS-only, NOT part of `make ci`
# (heavy: bundles the detection + export toolchains incl. torch, ~2 GB output).
# One-time host setup: `brew install librsvg create-dmg`. The pipeline and the
# Gatekeeper caveat live in packaging/macos/build-dmg.sh + docs/PACKAGING.md.
dmg:
	@[ "$$(uname -s)" = "Darwin" ] || { echo "make dmg must run on macOS"; exit 1; }
	poetry install --no-interaction --with package-macos -E detection -E export
	bash packaging/macos/build-dmg.sh

install-hooks:
	poetry run pre-commit install

# ---------------------------------------------------------------------------
# Mutation testing.
#
# `make mutation` runs `mutmut run` with `[tool.mutmut]` block from
# pyproject.toml. By default targets `openfollow/zones/engine.py` against
# `tests/test_zone_engine.py`. To audit a different module, edit
# `paths_to_mutate` / `tests_dir` keys and re-run (mutmut 3.x has no CLI
# override). Critical-module shortlist in comment above same block.
#
# Runs are NOT wired into `make ci` – they are too slow (minutes per
# module) and the survivor set is a per-PR sampling tool, not a gate.
# See docs/COVERAGE.md for the policy and how to interpret survivors.
# ---------------------------------------------------------------------------

mutation-clean:
	rm -rf mutants .mutmut-cache

mutation: mutation-clean
	poetry run mutmut run

mutation-results:
	poetry run mutmut results

mutation-show:
	@# Pass MUTANT=<full-name> e.g. `make mutation-show MUTANT=openfollow.zones.engine.xǁZoneEngineǁupdate__mutmut_13`
	@test -n "$(MUTANT)" || { echo "usage: make mutation-show MUTANT=<mutant-name>"; exit 2; }
	poetry run mutmut show "$(MUTANT)"
