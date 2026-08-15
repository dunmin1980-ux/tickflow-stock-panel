"""Single-use internal TLS endpoint for deterministic transport cases."""

from __future__ import annotations

import argparse
import socket
import ssl
import struct
import time
from contextlib import suppress


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("tls", "reset", "timeout", "protocol", "refused"),
        required=True,
    )
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--hold", type=float, default=2.0)
    args = parser.parse_args()

    if args.mode == "refused":
        print("READY", flush=True)
        time.sleep(args.hold)
        return 0

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 443))
    listener.listen(1)
    print("READY", flush=True)
    connection, _address = listener.accept()
    try:
        if args.mode == "reset":
            connection.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
            return 0
        if args.mode == "timeout":
            time.sleep(args.hold)
            return 0
        if args.mode == "protocol":
            connection.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")
            return 0
        if not args.cert or not args.key:
            raise RuntimeError("certificate_required")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)

        def record_sni(
            _socket: ssl.SSLSocket,
            server_name: str | None,
            _context: ssl.SSLContext,
        ) -> None:
            print(f"SNI_OBSERVED:{server_name or 'NONE'}", flush=True)

        context.set_servername_callback(record_sni)
        tls_connection = context.wrap_socket(connection, server_side=True)
        connection = None
        try:
            tls_connection.settimeout(args.hold)
            with suppress(TimeoutError, ssl.SSLError):
                tls_connection.recv(1)
        finally:
            tls_connection.close()
    finally:
        if connection is not None:
            connection.close()
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
