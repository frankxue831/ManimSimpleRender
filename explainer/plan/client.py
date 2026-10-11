"""The one non-deterministic boundary in the planner, behind a tiny protocol.

Everything else under explainer/plan/ is pure, so the whole milestone is
testable without an API key: tests drive FakeClient. The anthropic SDK is
imported INSIDE draft(), never at module scope, so `explainer verify` and
`explainer render` keep working when the optional planner extra is not
installed.
"""

import json
import os
import subprocess
import tempfile

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_EFFORT = "high"

# Server-side refusal fallbacks: this model runs safety classifiers, and the
# planner feeds it arbitrary source code, so a benign security-adjacent repo
# can trip one. "default" lets the API pick the recommended fallback by
# refusal category rather than pinning a model we would have to maintain.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClientError(Exception):
    """The model could not be reached, or declined to answer."""


class MaxTokensExhausted(ClientError):
    """The model stopped mid-answer because it hit the max-tokens budget.

    A distinguishable SUBTYPE of ClientError, not a new sibling: every
    existing ``except ClientError`` still catches this, but explainer/cli.py
    checks for it FIRST so it alone can map to exit 6 ("no usable draft")
    instead of exit 5 ("LLM failure") -- see the design spec's exit ladder.
    Unlike an auth/transport/refusal failure, the request reached the model
    and made real progress; raising --max-tokens or lowering --effort may be
    enough to succeed on a retry, which is not true of the other ClientError
    cases, so the two are not meant to read the same way to a caller.
    """


# The grok CLI carries its own OAuth session, so this provider needs neither
# an API key nor an SDK -- subprocess is stdlib, and `explainer plan
# --provider grok` adds no dependency at all.
GROK_BIN_ENV = "EXPLAINER_GROK_BIN"
DEFAULT_GROK_BIN = "grok"
DEFAULT_GROK_TIMEOUT = 600

# Planning is a pure function of the fact sheet: grok is handed everything it
# needs in the prompt and must not go looking for more. Denying every tool is
# both the trust boundary (no reads, no writes, no network from a planning
# run) and the reliability fix -- observed cancellations all came from
# multi-turn tool-using runs, while the no-tool run completes in one turn.
_GROK_DENIED_TOOLS = (
    "Edit",
    "Write",
    "NotebookEdit",
    "MultiEdit",
    "Bash",
    "Read",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
)

# grok reports a finished turn as "end_turn". Everything else is a failure for
# our purposes, but token exhaustion is the one that must NOT read as an
# unreachable model: it maps to MaxTokensExhausted, which explainer/cli.py
# turns into exit 6 ("no usable draft") instead of exit 5. Both spellings are
# accepted because the CLI's wire format is not a stability contract and a
# missed spelling would silently downgrade a recoverable truncation.
_GROK_TRUNCATED = frozenset({"max_tokens", "length", "max_output_tokens"})


class FakeClient:
    """Returns canned plan IR. The whole test suite runs on this.

    Pass one payload dict, or a list/tuple of dicts to script successive
    draft() calls -- the last entry repeats once the script runs out, which
    is what a repair retry after the scripted calls sees.
    """

    def __init__(self, payload):
        if isinstance(payload, (list, tuple)):
            self._payloads = list(payload)
        else:
            self._payloads = [payload]
        self.calls = []

    def draft(self, system, prompt, schema):
        self.calls.append((system, prompt))
        index = min(len(self.calls) - 1, len(self._payloads) - 1)
        return self._payloads[index]


class AnthropicClient:
    """Draft a plan IR with Claude, constrained to *schema* by structured output."""

    def __init__(self, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT, max_tokens=DEFAULT_MAX_TOKENS):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def draft(self, system, prompt, schema):
        try:
            import anthropic
        except ImportError as exc:
            # Also the path taken when a test patches sys.modules["anthropic"]
            # to None -- Python raises ImportError rather than binding None.
            raise ClientError(
                "the planner needs the anthropic SDK: pip install -e '.[planner]'"
            ) from exc

        try:
            # Anthropic() itself can raise synchronously (e.g. no API key
            # resolvable from ANTHROPIC_API_KEY or an `ant` profile), so
            # construction lives inside this try -- client.py is the single
            # place an API failure is translated to ClientError, and nothing
            # here may leak a raw SDK exception past that boundary.
            client = anthropic.Anthropic()
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
                system=system,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # bad/missing API key, transport, auth, rate limit
            raise ClientError(f"the planner request failed: {exc}") from exc

        # stop_reason is checked BEFORE content: on a refusal the content list
        # is empty or partial, so indexing it first would mask the real cause.
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            detail = getattr(getattr(response, "stop_details", None), "category", None)
            raise ClientError(f"the model refused this request (category: {detail or 'unknown'})")
        if stop == "max_tokens":
            raise MaxTokensExhausted(
                f"the model hit the max-tokens budget ({self.max_tokens}) before finishing; "
                f"raise --max-tokens or lower --effort"
            )

        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        if not text.strip():
            raise ClientError(f"the model returned no content (stop_reason: {stop!r})")
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ClientError(f"the model returned invalid JSON: {exc}") from exc
        # json.loads() accepts any JSON value, but draft() is documented to
        # return a dict (a plan IR object) -- valid JSON that decodes to a
        # list or a bare number must fail closed here, not be handed to a
        # caller that assumes a mapping.
        if not isinstance(payload, dict):
            raise ClientError(f"the model's JSON was a {type(payload).__name__}, not an object")
        return payload


class GrokCliClient:
    """Draft a plan IR by driving the grok CLI headless.

    A second provider behind the same one-method protocol as
    :class:`AnthropicClient`. Nothing downstream changes: the LLM still only
    chooses WHAT to cite, evidence tokens are still extracted from real source
    by explainer/plan/evidence.py, and a grok-drafted storyboard faces the
    identical citation gate. A weaker model can produce a worse ARC; it cannot
    produce an unverified FACT.

    This talks to the CLI rather than to api.x.ai because the CLI carries its
    own OAuth session -- no API key, no SDK, no new dependency, since
    subprocess is stdlib. The cost is that the transport is a binary on PATH
    (see ``GROK_BIN_ENV``) whose JSON output is not a stability contract, so
    every field read here is defended.
    """

    def __init__(self, model=None, effort=None, timeout=DEFAULT_GROK_TIMEOUT, binary=None):
        self.model = model
        # Left None by default on purpose: the default grok model REJECTS
        # --reasoning-effort and exits non-zero, so sending it unconditionally
        # would break every run that did not also pick a model that takes it.
        self.effort = effort
        self.timeout = timeout
        self.binary = binary or os.environ.get(GROK_BIN_ENV) or DEFAULT_GROK_BIN

    def _argv(self, system, schema, prompt_path):
        argv = [
            self.binary,
            "--permission-mode",
            "dontAsk",
            "--disallowed-tools",
            ",".join(_GROK_DENIED_TOOLS),
            "--disable-web-search",
            "--no-memory",
            "--no-subagents",
            # The fact sheet is line-numbered and whitespace-significant; the
            # planner's guarantee starts with the model seeing it byte for byte.
            "--verbatim",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--system-prompt-override",
            system,
            "--prompt-file",
            prompt_path,
        ]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--reasoning-effort", self.effort]
        return argv

    def draft(self, system, prompt, schema):
        # The prompt goes via a file rather than argv: a fact sheet carrying
        # several focus ranges runs to tens of kilobytes and would risk the
        # platform argument-length limit.
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = os.path.join(directory, "prompt.md")
            with open(prompt_path, "w", encoding="utf-8") as handle:
                handle.write(prompt)
            try:
                completed = subprocess.run(
                    self._argv(system, schema, prompt_path),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ClientError(
                    f"the grok CLI was not found at {self.binary!r}; install it or point "
                    f"{GROK_BIN_ENV} at the binary"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ClientError(
                    f"the grok CLI timed out after {self.timeout}s without returning a plan"
                ) from exc
            except OSError as exc:
                raise ClientError(f"the grok CLI could not be run: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            raise ClientError(
                f"the grok CLI exited {completed.returncode}: "
                f"{detail[-1] if detail else 'no stderr'}"
            )

        try:
            body = json.loads(completed.stdout or "")
        except ValueError as exc:
            raise ClientError(f"the grok CLI returned unparsable output: {exc}") from exc
        if not isinstance(body, dict):
            raise ClientError(f"the grok CLI returned a {type(body).__name__}, not a result object")

        # Checked before structuredOutput, same reasoning as the refusal check
        # in AnthropicClient: on a truncated turn the output is absent, so
        # reading it first would report the symptom instead of the cause.
        stop = body.get("stopReason")
        if stop in _GROK_TRUNCATED:
            raise MaxTokensExhausted(
                f"grok stopped at its token limit (stopReason: {stop!r}) before finishing; "
                f"narrow the --focus ranges or lower --beats"
            )

        payload = body.get("structuredOutput")
        if payload is None:
            # grok's headless final turn is frequently cancelled before it
            # emits structured output. An absent answer must never be read as
            # a valid empty plan -- that would emit a storyboard with no beats
            # and call the run a success.
            raise ClientError(
                f"grok returned no structured output (stopReason: {stop!r}); "
                f"the turn produced no usable plan"
            )
        if not isinstance(payload, dict):
            raise ClientError(
                f"grok's structured output was a {type(payload).__name__}, not an object"
            )
        return payload
