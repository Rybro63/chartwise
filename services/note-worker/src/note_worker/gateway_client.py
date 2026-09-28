"""gRPC client for the LLM gateway.

The gateway does the fast retries against the provider. This client handles the slower, higher
level signals: RESOURCE_EXHAUSTED (rate limited: wait the hinted time) and UNAVAILABLE (circuit
open or provider down: back off and try again), within a retry budget long enough to ride out an
outage. While it waits, the encounter stays uncommitted in Kafka, so the backlog is visible as
consumer lag and drains when the provider recovers.
"""

import logging
import random
import time
from collections.abc import Callable

import grpc

from chartwise.llm.v1 import gateway_pb2, gateway_pb2_grpc

from . import metrics
from .errors import GatewayRejected, GatewayUnavailable

log = logging.getLogger(__name__)

_RETRYABLE = {grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
_PERMANENT = {grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.FAILED_PRECONDITION}


class GatewayClient:
    def __init__(self, target: str, client_id: str, *, deadline_s: float, retry_budget_s: float,
                 max_backoff_s: float = 15.0, channel: grpc.Channel | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._channel = channel or grpc.insecure_channel(target)
        self._stub = gateway_pb2_grpc.LlmGatewayStub(self._channel)
        self._client_id = client_id
        self._deadline_s = deadline_s
        self._budget_s = retry_budget_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep
        self._clock = clock

    def complete(self, *, request_id: str, system: str, user: str, json_schema: str, max_tokens: int) -> gateway_pb2.CompleteResponse:
        request = gateway_pb2.CompleteRequest(
            client_id=self._client_id, request_id=request_id, system_prompt=system,
            user_prompt=user, json_schema=json_schema, max_tokens=max_tokens)
        started = self._clock()
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._stub.Complete(request, timeout=self._deadline_s)
            except grpc.RpcError as e:
                code = e.code()
                if code in _PERMANENT:
                    raise GatewayRejected(f"gateway rejected request ({code.name})") from None
                if code not in _RETRYABLE:
                    raise GatewayUnavailable(f"gateway call failed ({code.name})") from None
                wait = max(_retry_after(e), self._backoff(attempt))
                if self._clock() - started + wait > self._budget_s:
                    raise GatewayUnavailable(f"gateway unavailable after {attempt} attempts ({code.name})") from None
                metrics.GATEWAY_RETRIES.labels(code.name).inc()
                log.info("gateway %s; retrying in %.1fs", code.name, wait,
                         extra={"encounter_id": request_id, "attempt": attempt})
                self._sleep(wait)

    def _backoff(self, attempt: int) -> float:
        return random.uniform(0, min(self._max_backoff_s, 0.5 * 2 ** (attempt - 1)))

    def close(self) -> None:
        self._channel.close()


def _retry_after(e: grpc.RpcError) -> float:
    try:
        for key, value in e.trailing_metadata() or ():
            if key == "retry-after-ms":
                return int(value) / 1000
    except (AttributeError, ValueError, TypeError):
        pass
    return 0.0
