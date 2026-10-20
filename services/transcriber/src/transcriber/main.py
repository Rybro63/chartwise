import logging
import signal
import threading

from confluent_kafka import Consumer, Producer
from prometheus_client import start_http_server

from . import logging_setup
from .config import Settings
from .encounter_client import EncounterClient
from .transcribe import Transcriber
from .worker import TOPIC, TranscribeWorker, consumer_config, producer_config

log = logging.getLogger("transcriber")


def main() -> None:
    logging_setup.configure()
    settings = Settings()
    start_http_server(settings.metrics_port)
    log.info("loading Whisper model %s", settings.model)
    transcriber = Transcriber(settings.model, download_root=settings.model_dir)

    api = EncounterClient(settings.encounter_service_url, settings.username, settings.password)
    consumer = Consumer(consumer_config(settings.kafka_bootstrap, settings.group_id, settings.max_poll_interval_ms))
    producer = Producer(producer_config(settings.kafka_bootstrap))
    consumer.subscribe([TOPIC])
    worker = TranscribeWorker(consumer, producer, api, transcriber)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("transcriber started")
    try:
        while not stop.is_set():
            worker.run_once()
    finally:
        consumer.close()
        producer.flush(10)
        api.close()


if __name__ == "__main__":
    main()
