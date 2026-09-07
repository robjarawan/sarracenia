#!/usr/bin/env python3
"""Broker fixture for the #148 flow reproduction (PR #149).

Uses py-amqp (already an SR3 dependency) against the disposable local
RabbitMQ. Connection details come from REPRO_AMQP_URL in the environment
so no credential is written to any file. Actions:

  setup    declare the shared topic exchange.
  inject   publish two v03 notification messages for TAG (fresh unique
           relPaths and pubTime per run; BASEURL selects a missing or a
           real local tree).
  depth    print the message count of QUEUE (passive declare).
"""

import datetime
import json
import os
import sys

URL = os.environ["REPRO_AMQP_URL"]
EXCHANGE = os.environ.get("REPRO_EXCHANGE", "xs_retryfinal")


def connect():
    from amqp import Connection
    from urllib.parse import urlparse
    parts = urlparse(URL)
    conn = Connection(host="%s:%s" % (parts.hostname, parts.port or 5672),
                      userid=parts.username,
                      password=parts.password,
                      virtual_host="/")
    conn.connect()
    return conn


def setup():
    conn = connect()
    try:
        chan = conn.channel()
        chan.exchange_declare(EXCHANGE, "topic", durable=True)
        print("exchange ready: %s" % EXCHANGE, flush=True)
    finally:
        conn.close()


def inject():
    from amqp.basic_message import Message
    tag = os.environ["REPRO_TAG"]
    baseurl = os.environ["REPRO_BASEURL"]
    routing = os.environ["REPRO_ROUTING"]
    conn = connect()
    try:
        chan = conn.channel()
        for index in (1, 2):
            pubtime = datetime.datetime.utcnow().strftime(
                "%Y%m%dT%H%M%S.%f")
            body = {
                "pubTime": pubtime,
                "baseUrl": baseurl,
                "relPath": "run-%s-data-%d.bin" % (tag, index),
            }
            msg = Message(body=json.dumps(body),
                          content_type="application/json",
                          delivery_mode=2)
            chan.basic_publish(msg,
                               exchange=EXCHANGE,
                               routing_key=routing)
            print("published %s" % body["relPath"], flush=True)
    finally:
        conn.close()


def purge():
    """Remove leftover deliveries so a rerun starts from an empty queue."""
    queue = os.environ["REPRO_QUEUE"]
    conn = connect()
    try:
        chan = conn.channel()
        try:
            chan.queue_declare(queue, passive=True)
        except Exception:
            print("queue absent, nothing to purge", flush=True)
            return
        chan.queue_purge(queue)
        print("purged %s" % queue, flush=True)
    finally:
        conn.close()


def depth():
    queue = os.environ["REPRO_QUEUE"]
    conn = connect()
    try:
        chan = conn.channel()
        _name, count, _consumers = chan.queue_declare(queue,
                                                      passive=True)
        print(count, flush=True)
    finally:
        conn.close()


def main():
    getattr(sys.modules[__name__], sys.argv[1])()


if __name__ == "__main__":
    main()
