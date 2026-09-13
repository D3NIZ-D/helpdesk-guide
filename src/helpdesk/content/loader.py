"""Load runbook / guide / reference YAML files into :class:`Record` objects.

Two content roots are read in order (design doc section 19.1)::

    content/         core content, published under CC BY-SA
    content-local/   site-private content, git-ignored, never committed

A record in ``content-local/`` with the same ``code`` replaces the public
one entirely.  That is what lets an organisation keep "Acme VPN will not
connect" -- with its internal hostnames and processes -- out of the public
repository while still pulling upstream updates without merge conflicts.

``yaml.safe_load`` is mandatory here (design doc section 12.1): runbooks
are content, and content must never be able to construct Python objects.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from ..core.normalize import normalize_term, term_key
from .schema import Alias, Edge, Node, Record, Step, canonical_enum

__all__ = ["LoadError", "iter_content_files", "load_file", "load_roots"]

CONTENT_SUFFIXES = (".yaml", ".yml")
#: Files starting with ``_`` are templates and scaffolding, not content.
TEMPLATE_PREFIX = "_"


class LoadError(Exception):
    """A YAML file could not be turned into a record."""

    def __init__(self, path: Path | str, message: str) -> None:
        self.path = str(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _as_str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "evet", "on"}


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected an integer, got {value!r}") from exc


def _require(mapping: Mapping[str, Any], key: str, path: Path) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise LoadError(path, f"missing required field {key!r}")
    return mapping[key]


# --------------------------------------------------------------------------
# Sub-object parsing
# --------------------------------------------------------------------------

def _parse_aliases(raw: Any, path: Path) -> list[Alias]:
    aliases: list[Alias] = []
    for item in _as_list(raw):
        if isinstance(item, str):
            item = {"term": item}
        if not isinstance(item, Mapping):
            raise LoadError(path, f"alias entries must be strings or mappings, got {item!r}")
        term = str(item.get("term", "")).strip()
        if not term:
            raise LoadError(path, "alias entry has an empty 'term'")
        try:
            kind = canonical_enum("alias_kind", item.get("kind"), default="symptom")
        except ValueError as exc:
            raise LoadError(path, f"alias {term!r}: {exc}") from exc
        aliases.append(
            Alias(
                term=term,
                kind=kind,
                weight=float(item.get("weight", 1.0)),
                term_norm=normalize_term(term),
                term_key=term_key(term),
            )
        )
    return aliases


def _parse_edges(raw: Any, node_key: str, path: Path) -> list[Edge]:
    edges: list[Edge] = []
    for index, item in enumerate(_as_list(raw)):
        if not isinstance(item, Mapping):
            raise LoadError(path, f"node {node_key}: edge entries must be mappings")
        label = str(item.get("label", "")).strip()
        target = str(item.get("to") or item.get("target") or "").strip()
        if not label:
            raise LoadError(path, f"node {node_key}: an edge is missing 'label'")
        if not target:
            raise LoadError(path, f"node {node_key}: edge {label!r} is missing 'to'")
        edges.append(
            Edge(
                label=label,
                target=target,
                order_idx=int(item.get("order", index)),
                condition=(str(item["condition"]).strip() if item.get("condition") else None),
            )
        )
    edges.sort(key=lambda edge: edge.order_idx)
    return edges


def _parse_nodes(raw: Any, path: Path) -> list[Node]:
    nodes: list[Node] = []
    for item in _as_list(raw):
        if not isinstance(item, Mapping):
            raise LoadError(path, "node entries must be mappings")
        key = str(item.get("key", "")).strip()
        if not key:
            raise LoadError(path, "a node is missing its 'key'")
        try:
            node_type = canonical_enum("node_type", item.get("type"))
            risk = canonical_enum("risk", item.get("risk"), default="low")
            est_seconds = _as_int(item.get("est_seconds"))
        except ValueError as exc:
            raise LoadError(path, f"node {key}: {exc}") from exc

        nodes.append(
            Node(
                key=key,
                type=node_type,
                title=str(_require(item, "title", path)).strip(),
                body_md=(str(item["body_md"]) if item.get("body_md") else None),
                verify_text=(str(item["verify_text"]).strip() if item.get("verify_text") else None),
                risk=risk,
                requires_admin=_as_bool(item.get("requires_admin")),
                requires_user_downtime=_as_bool(item.get("requires_user_downtime")),
                est_seconds=est_seconds,
                rollback_md=(str(item["rollback_md"]) if item.get("rollback_md") else None),
                media=_as_str_list(item.get("media")),
                edges=_parse_edges(item.get("edges"), key, path),
                root_cause=(str(item["root_cause"]).strip() if item.get("root_cause") else None),
                closure_note=(str(item["closure_note"]).strip() if item.get("closure_note") else None),
                followup_md=(str(item["followup_md"]) if item.get("followup_md") else None),
                part_required=(str(item["part_required"]).strip() if item.get("part_required") else None),
                escalate_to=(str(item["escalate_to"]).strip() if item.get("escalate_to") else None),
                summary_template=(str(item["summary_template"]) if item.get("summary_template") else None),
            )
        )
    return nodes


def _parse_steps(raw: Any, path: Path) -> list[Step]:
    steps: list[Step] = []
    for index, item in enumerate(_as_list(raw)):
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, Mapping):
            raise LoadError(path, "step entries must be strings or mappings")
        title = str(item.get("title", "")).strip()
        if not title:
            raise LoadError(path, f"step {index + 1} has an empty 'title'")
        try:
            risk = canonical_enum("risk", item.get("risk"), default="low")
        except ValueError as exc:
            raise LoadError(path, f"step {index + 1}: {exc}") from exc
        steps.append(
            Step(
                title=title,
                note=(str(item["note"]).strip() if item.get("note") else None),
                body_md=(str(item["body_md"]) if item.get("body_md") else None),
                risk=risk,
                requires_admin=_as_bool(item.get("requires_admin")),
                est_seconds=_as_int(item.get("est_seconds")),
                order_idx=index,
            )
        )
    return steps


# --------------------------------------------------------------------------
# File loading
# --------------------------------------------------------------------------

def _review_due(reviewed_at: Any, period_days: int | None) -> str | None:
    """Derive the staleness deadline from the last review date.

    Computed at load time rather than at compile time so that ``lint`` and
    ``compile`` see exactly the same record; they used to disagree, and a
    validator that only fires in one of the two commands is worse than no
    validator.
    """
    if not reviewed_at or not period_days:
        return None
    try:
        reviewed = dt.date.fromisoformat(str(reviewed_at)[:10])
    except ValueError:
        return None
    return (reviewed + dt.timedelta(days=int(period_days))).isoformat()


def _infer_lang(path: Path, root: Path, declared: Any) -> str:
    """Language comes from the path (``runbooks/tr/...``) unless declared."""
    if declared:
        return str(declared).strip().lower()
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    for part in parts:
        if len(part) == 2 and part.isalpha():
            return part.lower()
    return "tr"


def load_file(path: Path, root: Path | None = None) -> Record:
    """Parse one content file into a :class:`Record`."""
    path = Path(path)
    root = Path(root) if root else path.parent

    raw_bytes = path.read_bytes()
    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise LoadError(path, f"invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise LoadError(path, "top level of a content file must be a mapping")

    try:
        tier = canonical_enum("tier", data.get("tier"), default="runbook")
        severity = canonical_enum("severity", data.get("severity"), default="medium")
        status = canonical_enum("status", data.get("status"), default="draft")
        verification = canonical_enum("verification", data.get("verification"), default="draft")
    except ValueError as exc:
        raise LoadError(path, str(exc)) from exc

    scope = data.get("scope") or {}
    if not isinstance(scope, Mapping):
        raise LoadError(path, "'scope' must be a mapping")

    record = Record(
        code=str(_require(data, "code", path)).strip().upper(),
        title=str(_require(data, "title", path)).strip(),
        tier=tier,
        lang=_infer_lang(path, root, data.get("lang")),
        summary=(str(data["summary"]).strip() if data.get("summary") else None),
        category=str(data.get("category") or "genel").strip().lower(),
        severity=severity,
        status=status,
        verification=verification,
        author=(str(data["author"]).strip() if data.get("author") else None),
        reviewed_at=(str(data["reviewed_at"]).strip() if data.get("reviewed_at") else None),
        review_period_days=_as_int(data.get("review_period_days")),
        review_due=_review_due(data.get("reviewed_at"), _as_int(data.get("review_period_days"))),
        os_scope=_as_str_list(scope.get("os")),
        asset_scope=_as_str_list(scope.get("asset")),
        tags=_as_str_list(data.get("tags")),
        aliases=_parse_aliases(data.get("aliases"), path),
        entry=(str(data["entry"]).strip() if data.get("entry") else None),
        nodes=_parse_nodes(data.get("nodes"), path),
        disambiguation=[d for d in _as_list(data.get("disambiguation")) if isinstance(d, Mapping)],
        steps=_parse_steps(data.get("steps"), path),
        likely_causes=_as_str_list(data.get("likely_causes")),
        fix_summary=(str(data["fix_summary"]).strip() if data.get("fix_summary") else None),
        source=(dict(data["source"]) if isinstance(data.get("source"), Mapping) else None),
        related_runbook=(str(data["related_runbook"]).strip().upper() if data.get("related_runbook") else None),
        related=[code.upper() for code in _as_str_list(data.get("related"))],
        merged_into=(str(data["merged_into"]).strip().upper() if data.get("merged_into") else None),
        source_path=str(path),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        min_engine_version=(str(data["min_engine_version"]).strip() if data.get("min_engine_version") else None),
    )
    return record


def iter_content_files(root: Path) -> Iterator[Path]:
    """Yield every content file under ``root``, skipping ``_templates``.

    The template check is applied to the path *relative to the root*, not
    to its absolute components.  An installed copy reads its content from
    ``site-packages/helpdesk/_bundled_content/``, and testing the absolute
    path would match that leading underscore and silently skip every
    record -- a compile that reports success and produces nothing.
    """
    runbooks = Path(root) / "runbooks"
    if not runbooks.is_dir():
        return
    for path in sorted(runbooks.rglob("*")):
        if path.suffix.lower() not in CONTENT_SUFFIXES:
            continue
        try:
            parts = path.relative_to(runbooks).parts
        except ValueError:  # pragma: no cover - rglob always yields children
            parts = (path.name,)
        if any(part.startswith(TEMPLATE_PREFIX) for part in parts):
            continue
        yield path


def load_roots(roots: Sequence[Path]) -> tuple[list[Record], list[LoadError]]:
    """Load every root in order; later roots override earlier ones by code.

    Returns the records plus any per-file errors, so a single broken file
    reports itself by name instead of aborting the whole build with a
    traceback.
    """
    by_code: dict[str, Record] = {}
    errors: list[LoadError] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in iter_content_files(root):
            try:
                record = load_file(path, root)
            except LoadError as exc:
                errors.append(exc)
                continue
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(LoadError(path, f"unexpected error: {exc}"))
                continue
            by_code[record.code] = record
    return list(by_code.values()), errors
