# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Local CI gate and developer task runner: mirrors the CI workflow's
# lint / typecheck / security / test / build steps plus coverage and mutation.

.PHONY: ci ci-remote lint format typecheck security audit test test-unit test-integration test-smoke-e2e build coverage coverage-html coverage-xml install-hooks \
        mutation mutation-results mutation-show mutation-clean

# Combined line + branch coverage floor – ratchet up with every PR.
# Path to 100% is the gate; CI invokes test-integration so the
# value never drifts between local `make test` and CI.
COVERAGE_MIN ?= 100

# Parallelize the suite with pytest-xdist.
#
# ``-n logical`` (not ``auto``): ``auto`` counts physical cores, so on a 2-vCPU
# single-core-with-hyperthreads CI runner it resolves to one worker and runs
# sequentially. ``logical`` uses both hyperthreads, which the I/O-bound
# integration/smoke step keeps busy.
#
# ``--dist load`` distributes individual tests across workers; ``loadscope``
# would pin each large module to one worker and bottleneck on the slowest. Safe
# because the suite has no cross-test shared state (function-scoped fixtures,
# ephemeral ports, ``tmp_path`` I/O). pytest-cov combines per-worker coverage, so
# the gate is unaffected. Override with ``PYTEST_PARALLEL=`` for a sequential run.
PYTEST_PARALLEL ?= -n logical --dist load

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
	poetry run pytest -m unit -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=term-missing

test-integration:
	poetry run pytest -m "integration or smoke" -q $(PYTEST_PARALLEL) --cov=openfollow --cov-append --cov-report=term-missing --cov-fail-under=$(COVERAGE_MIN)

# End-to-end smoke test. Spins a real GStreamer pipeline and PSN packet
# through OTP/RTTrPM output sockets. Opt-in, OUT of `make ci` (needs
# GStreamer runtime). Per-main-push signal, not a PR gate. Skips if
# GStreamer isn't installed.
test-smoke-e2e:
	poetry run pytest -m smoke_e2e -q

coverage:
	poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=term-missing

coverage-html:
	poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=html:htmlcov --cov-report=term

coverage-xml:
	poetry run pytest -q $(PYTEST_PARALLEL) --cov=openfollow --cov-report=xml:coverage.xml --cov-report=term

build:
	poetry build --no-interaction

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
