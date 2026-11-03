"""Event bus abstraction (Stage 2).

- If KAFKA_BOOTSTRAP is set (e.g. Redpanda at localhost:19092) events go to Kafka topic `user-events`,
  keyed by session_id so per-session order is preserved; malformed events go to `user-events-dlq`.
- Otherwise a local append-only JSONL log stands in for the topic, so the project runs with no Docker.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterator

from . import config

TOPIC, DLQ = "user-events", "user-events-dlq"
_lock = threading.Lock()


class EventBus:
    def __init__(self, path: Path = config.STREAM_LOG):
        self.kafka = None
        if config.KAFKA_BOOTSTRAP:
            from kafka import KafkaProducer  # pip install kafka-python
            self.kafka = KafkaProducer(bootstrap_servers=config.KAFKA_BOOTSTRAP, acks="all",
                                       key_serializer=str.encode, value_serializer=lambda v: json.dumps(v).encode())
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dlq_path = path.with_name(DLQ + ".jsonl")

    def publish(self, event: dict) -> None:
        if self.kafka:
            self.kafka.send(TOPIC, key=event["session_id"], value=event)
            return
        with _lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def dead_letter(self, raw: dict, error: str) -> None:
        if self.kafka:
            self.kafka.send(DLQ, key=str(raw.get("session_id", "")), value={"raw": raw, "error": error})
            return
        with _lock, self.dlq_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"raw": raw, "error": error}) + "\n")

    def flush(self):
        if self.kafka:
            self.kafka.flush()


def consume(path: Path = config.STREAM_LOG, follow: bool = True, from_start: bool = True) -> Iterator[dict]:
    """Tail the local log (or a Kafka topic when KAFKA_BOOTSTRAP is set)."""
    if config.KAFKA_BOOTSTRAP:
        from kafka import KafkaConsumer
        c = KafkaConsumer(TOPIC, bootstrap_servers=config.KAFKA_BOOTSTRAP, group_id="biq-stream",
                          auto_offset_reset="earliest" if from_start else "latest",
                          value_deserializer=lambda b: json.loads(b))
        for msg in c:
            yield msg.value
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8") as f:
        if not from_start:
            f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                yield json.loads(line)
            elif follow:
                time.sleep(0.2)
            else:
                return
