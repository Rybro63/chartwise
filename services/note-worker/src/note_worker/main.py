import logging
import signal
import sys
import threading

from confluent_kafka import Consumer, Producer
from prometheus_client import start_http_server

from . import logging_setup, telemetry
from .config import Settings
from .consumer import DraftConsumer, consumer_config, producer_config
from .encounter_client import EncounterClient
from .errors import DeliveryFailed
from .gateway_client import GatewayClient
from .pipeline import NotePipeline

log = logging.getLogger("note_worker")


def main() -> None:
    logging_setup.configure()
    telemetry.setup()
    settings = Settings()
    start_http_server(settings.metrics_port)

    encounters = EncounterClient(settings.encounter_service_url, settings.worker_username, settings.worker_password)
    gateway = GatewayClient(settings.gateway_target, settings.gateway_client_id,
                            deadline_s=settings.gateway_deadline_s, retry_budget_s=settings.gateway_retry_budget_s)
    pipeline = NotePipeline(encounters, gateway, max_tokens=settings.max_tokens)
    consumer = Consumer(consumer_config(settings.kafka_bootstrap, settings.group_id, settings.max_poll_interval_ms))
    producer = Producer(producer_config(settings.kafka_bootstrap))
    worker = DraftConsumer(consumer, producer, pipeline, batch_size=settings.batch_size, concurrency=settings.concurrency)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("note worker started (batch=%d, concurrency=%d)", settings.batch_size, settings.concurrency)
    exit_code = 0
    try:
        worker.run(stop)
    except DeliveryFailed:
        # Crash without committing; the batch is redelivered to whoever owns the partition next.
        log.exception("kafka delivery failed")
        exit_code = 1
    finally:
        consumer.close()
        producer.flush(10)
        encounters.close()
        gateway.close()
        log.info("note worker stopped")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
