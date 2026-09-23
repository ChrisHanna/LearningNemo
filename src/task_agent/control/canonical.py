"""Canonical hashing helpers for approval-bound records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python


def canonical_json(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else to_jsonable_python(value)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def content_hash(value: BaseModel | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def subject_hash(subject: str) -> str:
    if not subject:
        raise ValueError("subject is required")
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()