"""
Атомарное выполнение связанных операций Mongo.

Транзакции в MongoDB работают только на replica set / mongos. Прод сейчас
standalone, поэтому:
  - replica set  → callback выполняется в настоящей транзакции
    (session.with_transaction — с retry транзиентных ошибок);
  - standalone   → callback выполняется как раньше, последовательно,
    session=None (pymongo принимает session=None во всех операциях).

Как включить транзакции на сервере: см. deploy/enable_replica_set.md —
после перевода mongod в single-node replica set этот модуль подхватит
поддержку автоматически, менять код не нужно.

Callback может быть выполнен повторно при транзиентной ошибке транзакции —
не выполняйте в нём побочных эффектов вне Mongo (email, Stripe и т.п.).
"""
from __future__ import annotations

import threading

_lock = threading.Lock()


def transactions_supported(client) -> bool:
    """True, если сервер поддерживает транзакции (replica set / mongos).
    Результат кэшируется на объекте клиента."""
    cached = getattr(client, "_roobico_tx_supported", None)
    if cached is not None:
        return cached
    with _lock:
        cached = getattr(client, "_roobico_tx_supported", None)
        if cached is not None:
            return cached
        try:
            hello = client.admin.command("hello")
            supported = bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"
        except Exception:
            supported = False
        client._roobico_tx_supported = supported
        return supported


def run_atomically(client, callback):
    """
    callback(session) -> result

    Все операции Mongo внутри callback обязаны передавать session=session,
    иначе на replica set они выполнятся вне транзакции.
    """
    if transactions_supported(client):
        with client.start_session() as session:
            return session.with_transaction(lambda s: callback(s))
    return callback(None)
