from concurrent import futures

import grpc
import pytest

from chartwise.llm.v1 import gateway_pb2, gateway_pb2_grpc
from note_worker.errors import GatewayRejected, GatewayUnavailable
from note_worker.gateway_client import GatewayClient


class ScriptedGateway(gateway_pb2_grpc.LlmGatewayServicer):
    """Plays back a list of (status code, retry-after-ms) failures, then succeeds."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def Complete(self, request, context):
        self.calls += 1
        if self.script:
            code, retry_after = self.script.pop(0)
            if retry_after is not None:
                context.set_trailing_metadata((("retry-after-ms", str(retry_after)),))
            context.abort(code, "scripted failure")
        return gateway_pb2.CompleteResponse(text="{}", model="m", attempts=1)


@pytest.fixture
def serve():
    servers = []

    def _serve(script):
        impl = ScriptedGateway(script)
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        gateway_pb2_grpc.add_LlmGatewayServicer_to_server(impl, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        servers.append(server)
        return impl, f"127.0.0.1:{port}"

    yield _serve
    for s in servers:
        s.stop(0)


def client(target, sleeps, budget=60.0):
    now = [0.0]

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    return GatewayClient(target, "note-worker", deadline_s=5, retry_budget_s=budget, sleep=sleep, clock=lambda: now[0])


def call(c):
    return c.complete(request_id="enc-1", system="s", user="u", json_schema="{}", max_tokens=100)


def test_honours_rate_limit_retry_after(serve):
    impl, target = serve([(grpc.StatusCode.RESOURCE_EXHAUSTED, 1500)])
    sleeps = []
    assert call(client(target, sleeps)).model == "m"
    assert impl.calls == 2
    assert sleeps[0] >= 1.5


def test_rides_out_an_open_circuit(serve):
    impl, target = serve([(grpc.StatusCode.UNAVAILABLE, 2000)] * 5)
    sleeps = []
    assert call(client(target, sleeps)).model == "m"
    assert impl.calls == 6


def test_gives_up_after_retry_budget(serve):
    impl, target = serve([(grpc.StatusCode.UNAVAILABLE, 10_000)] * 100)
    with pytest.raises(GatewayUnavailable) as e:
        call(client(target, [], budget=30))
    assert "UNAVAILABLE" in e.value.safe_reason


def test_permanent_rejection_is_not_retried(serve):
    impl, target = serve([(grpc.StatusCode.FAILED_PRECONDITION, None)])
    with pytest.raises(GatewayRejected):
        call(client(target, []))
    assert impl.calls == 1
