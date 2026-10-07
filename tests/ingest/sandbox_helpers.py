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
