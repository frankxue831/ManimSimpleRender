"""One bounded quality pass over an accepted draft, behind the trust boundary.

A revision re-enters the identical parse() path and is re-expanded with
code-extracted evidence, so critique can improve prose and structure but can
never touch facts -- there is still no token the model wrote that the gate
checks. A failed revision is discarded, never shipped: the caller keeps the
pre-critique draft (see cli.py), so the pass can make a run slower, never
worse.

This module also owns the draft-with-repairs loop itself, shared by the
first draft and the revision pass, so the two cannot drift on repair
semantics.
"""

import json

from explainer.plan.client import ClientError
from explainer.plan.plan_ir import PLAN_SCHEMA, PlanError
from explainer.plan.plan_ir import parse as parse_plan
from explainer.plan.prompt import SYSTEM_PROMPT, build_user_prompt, repair_suffix

# Each unit of --revisions costs one more LLM call, plus that call's own
# repair retries -- the same cost reasoning as cli.MAX_REPAIRS_LIMIT.
MAX_REVISIONS_LIMIT = 3

_RUBRIC = """\

Below is your own accepted draft plan for the fact sheet above. Revise it to
meet the editorial standard: sharpen the through-line question, cut or merge
any beat that does not change what the viewer believes, and make every
narration argue with concrete stakes instead of describing the screen. All
mechanics rules still apply; cite only lines shown in the focus targets
above. If the draft already meets the standard, return it unchanged. Return
the complete plan as JSON in the same schema either way.

DRAFT PLAN:
"""


def draft_plan_ir(client, factsheet, base_prompt, max_repairs):
    """Call *client* until the plan parses, retrying with repair_suffix.

    Returns ``(raw, plan)``. Raises :class:`PlanError` when every attempt
    failed; client/transport errors propagate untouched for the caller's
    exit ladder.
    """
    prompt = base_prompt
    last = None
    for _ in range(max_repairs + 1):
        raw = client.draft(SYSTEM_PROMPT, prompt, PLAN_SCHEMA)
        try:
            return raw, parse_plan(raw, factsheet)
        except PlanError as exc:
            last = exc
            prompt = base_prompt + repair_suffix(str(exc))
    raise PlanError(f"no usable plan after {max_repairs + 1} attempt(s): {last}")


def build_revision_prompt(factsheet, draft_raw):
    """The fact sheet, the rubric, and the draft to revise, in that order."""
    return build_user_prompt(factsheet) + _RUBRIC + json.dumps(draft_raw, indent=1) + "\n"


def revise_plan(client, factsheet, draft_raw, max_repairs):
    """Return ``(raw, plan)`` for a validated revision, or None to keep the draft.

    None on ANY failure -- transport, token exhaustion, or a revision that
    never parses -- because a quality pass must never lose a run that
    already holds a valid draft.
    """
    prompt = build_revision_prompt(factsheet, draft_raw)
    try:
        return draft_plan_ir(client, factsheet, prompt, max_repairs)
    except (ClientError, PlanError):
        return None
