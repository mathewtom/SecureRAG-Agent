"""AgenticChain - wraps the LangGraph agent with Sentinel-inherited
entry/exit security layers. This is the sibling-class equivalent of
Sentinel's deleted SecureRAGChain, rebuilt around a graph invocation
rather than a single retrieve-then-generate call.
"""

from __future__ import annotations

import inspect
import unicodedata
from typing import Any, Callable

from src.agent.state import SecurityDecision, SecurityVerdict, initial_state
from src.exceptions import BudgetExhausted, OutputFlagged, QueryBlocked


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
    ) -> None:
        self._graph = graph
        self._rate = rate_limiter
        self._in = input_scanners
        self._out = output_scanners
        self._audit = audit
        self._extract = extract_answer
        self._max_steps = max_steps

    def invoke(self, *, query: str, user_id: str) -> dict[str, Any]:
        request_id = self._audit.new_request_id()
        normalized = unicodedata.normalize("NFKC", query)

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

        final = self._graph.invoke(state, config={"recursion_limit": 50})

        if final.get("termination_reason") == "budget_exhausted":
            self._audit.log_budget_exhausted(
                request_id, user_id, final["step_count"],
            )
            raise BudgetExhausted(max_steps=final["max_steps"])

        answer = self._extract(final)

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
                raise OutputFlagged([result.reason])

        return {
            "request_id": request_id,
            "answer": answer,
            "source_doc_ids": final["retrieved_doc_ids"],
            "termination_reason": final["termination_reason"],
        }
