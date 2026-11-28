#!/usr/bin/env python3
"""Minimal read-only TFTP server for U-Boot image transfer.

Serves the tftp directory next to this script over UDP/69 with RFC 2347
option negotiation (blksize / tsize / timeout). Single file per client.
"""
import os
import socket
import struct
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tftp')
PORT = 69
MAX_BLKSIZE = 1468

OP_RRQ = 1
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5
OP_OACK = 6


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def parse_rrq(pkt):
    parts = pkt[2:].split(b"\x00")
    if len(parts) < 2:
        return None, None, {}
    name = parts[0].decode(errors="replace")
    opts = {}
    rest = parts[2:]
    for i in range(0, len(rest) - 1, 2):
        k = rest[i].decode(errors="replace").lower()
        v = rest[i + 1].decode(errors="replace")
        if k:
            opts[k] = v
    return name, parts[1].decode(errors="replace"), opts


def oack(opts):
    pkt = struct.pack("!H", OP_OACK)
    for k, v in opts.items():
        pkt += k.encode() + b"\x00" + str(v).encode() + b"\x00"
    return pkt


def error_pkt(code, msg):
    return struct.pack("!HH", OP_ERROR, code) + msg.encode() + b"\x00"


def send_data(sock, addr, block, data, blksize, retries=6):
    pkt = struct.pack("!HH", OP_DATA, block) + data
    for _ in range(retries):
        sock.sendto(pkt, addr)
        try:
            resp, peer = sock.recvfrom(1024)
        except socket.timeout:
            continue
        if peer != addr or len(resp) < 4:
            continue
        op = struct.unpack("!H", resp[:2])[0]
        if op == OP_ACK and struct.unpack("!H", resp[2:4])[0] == block:
            return True
        if op == OP_ERROR:
            return False
    return False


def serve_file(path, addr, opts):
    blksize = 512
    neg = {}
    try:
        if "blksize" in opts:
            blksize = max(8, min(int(opts["blksize"]), MAX_BLKSIZE))
            neg["blksize"] = blksize
        if "tsize" in opts:
            neg["tsize"] = os.path.getsize(path)
    except ValueError:
        pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(3)
    try:
        if neg:
            for _ in range(6):
                sock.sendto(oack(neg), addr)
                try:
                    resp, peer = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                if peer == addr and struct.unpack("!H", resp[:2])[0] == OP_ACK \
                        and struct.unpack("!H", resp[2:4])[0] == 0:
                    break
            else:
                return
        total = 0
        block = 1
        with open(path, "rb") as f:
            while True:
                chunk = f.read(blksize)
                if not send_data(sock, addr, block & 0xFFFF, chunk, blksize):
                    log("FAILED block %d -> %s" % (block, addr[0]))
                    return
                total += len(chunk)
                if len(chunk) < blksize:
                    break
                block += 1
        log("done %s -> %s  %d bytes" % (os.path.basename(path), addr[0], total))
    finally:
        sock.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    log("tftp root = %s, listening on udp/%d" % (ROOT, PORT))
    while True:
        pkt, addr = srv.recvfrom(2048)
        if len(pkt) < 4 or struct.unpack("!H", pkt[:2])[0] != OP_RRQ:
            continue
        name, mode, opts = parse_rrq(pkt)
        if not name:
            continue
        path = os.path.abspath(os.path.join(ROOT, name.lstrip("/\\")))
        if not path.startswith(os.path.abspath(ROOT)) or not os.path.isfile(path):
            log("not found: %s (from %s)" % (name, addr[0]))
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("0.0.0.0", 0))
            s.sendto(error_pkt(1, "file not found"), addr)
            s.close()
            continue
        log("request %s from %s opts=%s" % (name, addr[0], opts))
        threading.Thread(target=serve_file, args=(path, addr, opts), daemon=True).start()


if __name__ == "__main__":
    main()
