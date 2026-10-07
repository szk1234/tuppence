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
