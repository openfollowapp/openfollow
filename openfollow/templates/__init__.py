# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""File-based template system for OSC, camera, grid, zones and paths configurations."""

from openfollow.templates.schema import (
    TEMPLATE_FILE_SUFFIX,
    TEMPLATE_LEGACY_SUFFIX,
    TEMPLATE_READ_SUFFIXES,
    TEMPLATE_VERSION,
    VALID_TYPES,
    OpenFollowTemplate,
    TemplateValidationError,
    validate_payload,
)

__all__ = (
    "OpenFollowTemplate",
    "TEMPLATE_FILE_SUFFIX",
    "TEMPLATE_LEGACY_SUFFIX",
    "TEMPLATE_READ_SUFFIXES",
    "TEMPLATE_VERSION",
    "TemplateValidationError",
    "VALID_TYPES",
    "validate_payload",
)
