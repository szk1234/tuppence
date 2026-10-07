"""Module-level functions the sandbox tests run in a child process."""

import os
import time


def add(a, b):
    return a + b


def sleepy(seconds):
    time.sleep(seconds)
    return "woke"


def greedy():
    hoard = []
    for _ in range(80):
        hoard.append(bytearray(25 * 1024 * 1024))  # touched pages: real memory
        time.sleep(0.05)
    return len(hoard)


def explode():
    raise ValueError("bad table")


def vanish():
    os._exit(3)


def connect_out():
    import socket

    sock = socket.socket()
    try:
        sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()


def create_connection_blocked():
    import socket

    try:
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    except OSError as exc:
        return "switched off" in str(exc)
    return False


def hog():
    return len(bytearray(8 * 1024 * 1024 * 1024))  # one absurd allocation


def bulky():
    return "x" * (60 * 1024 * 1024)


def where():
    return os.getcwd()


def dns_blocked():
    import socket

    try:
        socket.getaddrinfo("localhost", 80)
    except OSError:
        return True
    return False


def udp_blocked():
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return True
    try:
        sock.sendto(b"hello", ("127.0.0.1", 9))
    except OSError:
        return True
    finally:
        sock.close()
    return False


MARKER_ENV = "TUPPENCE_TEST_MARKER"


class _Pwn:
    """Unpickling this runs os.mkdir(marker): proof that code ran in the unpickler."""

    def __init__(self, marker):
        self.marker = marker

    def __reduce__(self):
        return (os.mkdir, (self.marker,))


def _send(conn, payload):
    conn.send_bytes(payload)
    conn.close()


def pickle_child(conn, fn, args, memory_mb):
    import pickle

    _send(conn, pickle.dumps(_Pwn(args[0])))


def wrapped_pickle_child(conn, fn, args, memory_mb):
    import pickle

    _send(conn, pickle.dumps({"ok": _Pwn(args[0])}))


def garbage_child(conn, fn, args, memory_mb):
    _send(conn, b"\x00\xff not json at all")


def oversized_child(conn, fn, args, memory_mb):
    _send(conn, b"[" * (51 * 1024 * 1024))


def nan_child(conn, fn, args, memory_mb):
    _send(conn, b'{"ok": NaN}')


def shape_child(conn, fn, args, memory_mb):
    import json

    _send(conn, json.dumps({"ok": json.loads(args[0])}).encode())


def error_child(conn, fn, args, memory_mb):
    import json

    _send(conn, json.dumps({"error": args[0]}).encode())


def noop(*_args):
    return None
