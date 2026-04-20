"""AgenticChain - wraps the LangGraph agent with Sentinel-inherited
entry/exit security layers. This is the sibling-class equivalent of
Sentinel's deleted SecureRAGChain, rebuilt around a graph invocation
rather than a single retrieve-then-generate call.
"""

from __future__ import annotations

import datetime as _dt
import hashlib as _hashlib
import inspect
import unicodedata
from typing import Any, Callable

from securerag_agent.agent.state import SecurityDecision, SecurityVerdict, initial_state
from securerag_agent.exceptions import BudgetExhausted, OutputFlagged, QueryBlocked
from securerag_agent.rate_limiter import RateLimitExceeded


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _query_hash(q: str) -> str:
    return _hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]


def _call_scanner(scanner: Any, primary: str, **context: Any) -> Any:
    """Call `scanner.scan(primary, ...)` passing only the context
    kwargs the scanner actually accepts.

    Sentinel-inherited scanners have heterogeneous signatures
    (InjectionScanner takes just text; OutputScanner takes output +
    question; ClassificationGuard takes just output). Rather than
    force every scanner to accept a uniform `**kwargs`, this helper
    introspects each scanner's signature and passes only the kwargs
    it declares. Unknown kwargs are silently dropped.
    """
    try:
        params = inspect.signature(scanner.scan).parameters
    except (ValueError, TypeError):
        # e.g. Mock without spec - pass everything, let duck typing work
        return scanner.scan(primary, **context)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return scanner.scan(primary, **context)
    kwargs = {k: v for k, v in context.items() if k in params}
    return scanner.scan(primary, **kwargs)


def _to_verdict(layer: str, stage: str, result: Any) -> SecurityVerdict:
    if getattr(result, "blocked", False):
        decision = SecurityDecision.BLOCK
    elif getattr(result, "flagged", False):
        decision = SecurityDecision.FLAG
    else:
        decision = SecurityDecision.PASS
    # stage must be Literal["entry","in_graph","exit"]; rely on caller
    return SecurityVerdict(
        layer=layer,
        stage=stage,  # type: ignore[typeddict-item]
        verdict=decision,
        details=getattr(result, "reason", None),
    )


class AgenticChain:
    def __init__(
        self,
        *,
        graph: Any,
        rate_limiter: Any,
        input_scanners: list[Any],
        output_scanners: list[Any],
        audit: Any,
        extract_answer: Callable[[dict[str, Any]], str],
        max_steps: int = 20,
        audit_sink: Any | None = None,
    ) -> None:
        self._graph = graph
        self._rate = rate_limiter
        self._in = input_scanners
        self._out = output_scanners
        self._audit = audit
        self._extract = extract_answer
        self._max_steps = max_steps
        self._audit_sink = audit_sink

    def _emit_start(
        self, request_id: str, user_id: str, normalized: str
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink.emit({
            "ts": _now_iso(),
            "event": "request_start",
            "request_id": request_id,
            "user_id": user_id,
            "query_sha256": _query_hash(normalized),
        })

    def _emit_end(
        self, request_id: str, outcome: str, step_count: int
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink.emit({
            "ts": _now_iso(),
            "event": "request_end",
            "request_id": request_id,
            "outcome": outcome,
            "step_count": step_count,
        })

    def invoke(self, *, query: str, user_id: str) -> dict[str, Any]:
        request_id = self._audit.new_request_id()
        normalized = unicodedata.normalize("NFKC", query)

        # Emit request_start before any checks so that every outcome —
        # including rate-limit denial — produces a matched start/end pair.
        self._emit_start(request_id, user_id, normalized)

        # `final` may not be assigned if an early exception fires; declare
        # it here so the OutputFlagged handler can reference it safely.
        final: dict[str, Any] = {}

        try:
            self._rate.check(user_id)

            entry_verdicts: list[SecurityVerdict] = []
            for scanner in self._in:
                result = _call_scanner(scanner, normalized)
                verdict = _to_verdict(scanner.name, "entry", result)
                entry_verdicts.append(verdict)
                self._audit.log_verdict(request_id, user_id,
                                        scanner.name, "entry", result)
                if getattr(result, "blocked", False):
                    raise QueryBlocked(result.reason, {"layer": scanner.name})

            state = initial_state(
                request_id=request_id,
                user_id=user_id,
                query=normalized,
                max_steps=self._max_steps,
                seed_verdicts=entry_verdicts,
            )

            # LangGraph counts each node execution as one super-step. The
            # worst case is `agent_llm` -> `tools` alternation up to
            # `max_steps` tool hops, which is ~2*max_steps node executions.
            # Add a small safety margin so LangGraph's internal cap never
            # fires before our explicit max_steps check does.
            recursion_limit = self._max_steps * 2 + 10
            final = self._graph.invoke(
                state, config={"recursion_limit": recursion_limit}
            )

            if final.get("termination_reason") == "budget_exhausted":
                self._audit.log_budget_exhausted(
                    request_id, user_id, final["step_count"],
                )
                raise BudgetExhausted(max_steps=final["max_steps"])

            answer = self._extract(final)
            final["final_answer"] = answer

            for scanner in self._out:
                result = _call_scanner(scanner, answer,
                                       question=normalized,
                                       user_id=user_id)
                final["security_verdicts"].append(
                    _to_verdict(scanner.name, "exit", result),
                )
                self._audit.log_verdict(request_id, user_id,
                                        scanner.name, "exit", result)
                if getattr(result, "flagged", False):
                    # Different scanners use different field names:
                    # OutputScanner returns `reasons: list[str]`, others use
                    # singular `reason: str`. Normalize to a list.
                    reasons = getattr(result, "reasons", None)
                    if reasons is None:
                        reasons = [getattr(result, "reason", "flagged")]
                    raise OutputFlagged(list(reasons))

        except RateLimitExceeded:
            self._emit_end(request_id, "rate_limited", step_count=0)
            raise
        except QueryBlocked:
            self._emit_end(request_id, "blocked", step_count=0)
            raise
        except BudgetExhausted as exc:
            self._emit_end(request_id, "budget_exhausted",
                           step_count=exc.max_steps)
            raise
        except OutputFlagged:
            self._emit_end(request_id, "flagged",
                           step_count=final.get("step_count", 0))
            raise
        except Exception:
            self._emit_end(request_id, "error", step_count=0)
            raise

        self._emit_end(request_id, "answered",
                       step_count=final["step_count"])
        return {
            "request_id": request_id,
            "answer": answer,
            "source_doc_ids": final["retrieved_doc_ids"],
            "termination_reason": final["termination_reason"],
        }
