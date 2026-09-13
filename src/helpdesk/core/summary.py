"""Escalation summary rendering (design doc sections 7.1 and 10).

The summary is the feature that earns the tool its keep.  A technician who
already knows how to fix a monitor gains little from being walked through
it -- but writing the handover by hand takes five minutes, and this
produces it from the session record for free, every time, in a consistent
shape that L2 can actually read.

Templates use a deliberately tiny subset of mustache syntax::

    Symptom: {{query_raw}}
    Runbook: {{record_code}} v{{version}}
    {{#each steps}}- {{title}} -> {{answer}}
    {{/each}}

A full template engine is not used here on purpose: templates live in
content YAML, content is untrusted input, and Jinja2 on untrusted input is
a sandbox-escape problem nobody needs.  This renderer can only substitute
strings and loop over a list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["DEFAULT_TEMPLATE_EN", "DEFAULT_TEMPLATE_TR", "build_summary", "render_template"]

_EACH_RE = re.compile(r"\{\{#each\s+(\w+)\s*\}\}(.*?)\{\{/each\s*\}\}", re.DOTALL)
_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


DEFAULT_TEMPLATE_TR = """\
Belirti: {{query_raw}}
Kayıt: {{record_code}} — {{record_title}} (v{{version}})
Bağlam: {{context}}

İzlenen adımlar:
{{#each steps}}  {{order}}. {{title}} → {{answer}}
{{/each}}
Atlanan adımlar: {{skipped_titles}}
Toplam süre: {{duration_min}} dk ({{step_count}} adım)
Sonuç: {{outcome_label}}
{{closing}}"""


DEFAULT_TEMPLATE_EN = """\
Symptom: {{query_raw}}
Record: {{record_code}} — {{record_title}} (v{{version}})
Context: {{context}}

Steps taken:
{{#each steps}}  {{order}}. {{title}} -> {{answer}}
{{/each}}
Skipped: {{skipped_titles}}
Total time: {{duration_min}} min ({{step_count}} steps)
Outcome: {{outcome_label}}
{{closing}}"""


def _lookup(context: Mapping[str, Any], dotted: str) -> str:
    value: Any = context
    for part in dotted.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return ""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "evet" if value else "hayır"
    return str(value)


def render_template(template: str, context: Mapping[str, Any]) -> str:
    """Render ``template`` with ``context``; unknown variables become empty."""

    def render_each(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        items = context.get(name) or []
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            return ""
        chunks: list[str] = []
        for item in items:
            scope = dict(context)
            if isinstance(item, Mapping):
                scope.update(item)
            else:
                scope["item"] = item
            chunks.append(
                _VAR_RE.sub(lambda m, sc=scope: _lookup(sc, m.group(1)), body)
            )
        return "".join(chunks)

    expanded = _EACH_RE.sub(render_each, template or "")
    return _VAR_RE.sub(lambda m: _lookup(context, m.group(1)), expanded)


_OUTCOME_LABELS = {
    "tr": {
        "resolved": "Çözüldü",
        "escalated": "Çözülemedi, eskale edildi",
        "unresolved": "Çözülemedi",
        "abandoned": "Oturum yarıda bırakıldı",
    },
    "en": {
        "resolved": "Resolved",
        "escalated": "Not resolved, escalated",
        "unresolved": "Not resolved",
        "abandoned": "Session abandoned",
    },
}


def build_summary(
    *,
    record: Mapping[str, Any],
    state_dict: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    outcome: str,
    query_raw: str,
    node: Mapping[str, Any] | None = None,
    lang: str = "tr",
    template: str | None = None,
) -> str:
    """Render the paste-into-the-ticket summary for a finished session."""
    lang = lang if lang in _OUTCOME_LABELS else "tr"

    skipped_label = "(atlandı)" if lang == "tr" else "(skipped)"
    no_answer_label = "(cevapsız)" if lang == "tr" else "(no answer)"

    def answer_text(step: Mapping[str, Any]) -> str:
        # A skipped step is always labelled as skipped, even though the
        # technician still had to pick an edge to move on: "we did not
        # check this" must never read as "we checked it and it was fine".
        if step.get("skipped"):
            answer = step.get("answer")
            return f"{skipped_label} {answer}".strip() if answer else skipped_label
        return str(step.get("answer") or no_answer_label)

    rendered_steps = [
        {
            "order": step.get("order", index + 1),
            "title": step.get("title", ""),
            "answer": answer_text(step),
            "key": step.get("key", ""),
        }
        for index, step in enumerate(steps)
    ]

    skipped_titles = ", ".join(
        step.get("title", "") for step in steps if step.get("skipped")
    ) or ("yok" if lang == "tr" else "none")

    context_pairs = ", ".join(
        f"{key}={value}" for key, value in (state_dict.get("context") or {}).items()
    ) or ("belirtilmedi" if lang == "tr" else "not specified")

    closing = ""
    if node:
        if node.get("type") == "resolution":
            closing = "\n".join(
                filter(None, [
                    f"Kök neden: {node['root_cause']}" if lang == "tr" and node.get("root_cause")
                    else (f"Root cause: {node['root_cause']}" if node.get("root_cause") else ""),
                    node.get("closure_note") or "",
                    (f"Parça: {node['part_required']}" if lang == "tr" else
                     f"Part required: {node['part_required']}") if node.get("part_required") else "",
                    node.get("followup_md") or "",
                ])
            )
        elif node.get("type") == "escalation" and node.get("escalate_to"):
            closing = (
                f"Devredilen ekip: {node['escalate_to']}" if lang == "tr"
                else f"Escalated to: {node['escalate_to']}"
            )

    elapsed = int(state_dict.get("elapsed_s") or 0)
    context: dict[str, Any] = {
        "query_raw": query_raw,
        "record_code": record.get("code", ""),
        "record_title": record.get("title", ""),
        "version": state_dict.get("version", 1),
        "steps": rendered_steps,
        "step_count": len(rendered_steps),
        "skipped_titles": skipped_titles,
        "context": context_pairs,
        "duration_min": max(1, round(elapsed / 60)) if elapsed else 0,
        "duration_s": elapsed,
        "outcome": outcome,
        "outcome_label": _OUTCOME_LABELS[lang].get(outcome, outcome),
        "closing": closing,
    }

    chosen = template or (node or {}).get("summary_template")
    if not chosen:
        chosen = DEFAULT_TEMPLATE_TR if lang == "tr" else DEFAULT_TEMPLATE_EN
    return render_template(chosen, context).strip()
