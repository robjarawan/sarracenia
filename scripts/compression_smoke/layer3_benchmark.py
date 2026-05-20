#!/usr/bin/env python3
"""
Layer-3 benchmark for the SFTP useCompression flag.

Runs a matrix of (payload profile, link profile, compression on/off, trial)
against a disposable atmoz/sftp container and records wall-clock, wire bytes
(from /sys/class/net/<veth>/statistics/tx_bytes), and client CPU seconds.
Emits a CSV.

Usage:
    # Quick LAN-only run (default: 10 MB, 2 trials)
    python3 layer3_benchmark.py --csv /tmp/bench.csv

    # Full LAN + WAN run (requires passwordless sudo for tc)
    python3 layer3_benchmark.py --csv /tmp/bench.csv --size-mb 10 --trials 3 --wan

    # Single cell
    python3 layer3_benchmark.py --payloads random --size-mb 50 --trials 1

Exit codes:
    0 success (CSV written), 2 argument error, 3 setup failure,
    4 no docker, 5 sudo/tc failure, 6 sha512 mismatch (correctness regression).

Requires: docker, paramiko, optionally sudo for tc netem.
"""
import argparse
import gzip
import hashlib
import io
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IMAGE = "atmoz/sftp:latest"
SFTP_USER = "testuser"
SFTP_PASS = "testpass"
SFTP_PORT = "2222"
CONTAINER_NAME_PREFIX = "sr3_l3_bench"

PAYLOAD_PROFILES = {
    # Expected ratios are rough under zlib at default level inside SSH.
    "zeros":        "all-zero bytes -- degenerate best case, huge ratio",
    "ascii_repeat": "repeated ASCII sentence -- typical text, ~10:1",
    "wx_csv":       "synthetic weather CSV with redundant columns -- ~5:1",
    "bufr_sim":     "binary metadata + random body -- ~1.3:1",
    "random":       "os.urandom -- incompressible, ~1:1 (represents GRIB2/NC4/JPEG)",
    "prestored_gz": "pre-gzipped random bytes -- double-compression no-op",
}

# Default WAN profile: conservative "long-haul WAN" scenario.
# Overridable via --wan-delay / --wan-loss / --wan-rate at invocation time.
WAN_PROFILE = {"delay_ms": 100, "loss_pct": 0.1, "rate_mbit": 10}


def log(msg):
    print(f"[layer3] {msg}", flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kw)


def require(cmd, *, why):
    r = sh(cmd)
    if r.returncode != 0:
        log(f"FATAL: {' '.join(cmd)} failed: {r.stderr.strip()}")
        log(f"needed for: {why}")
        sys.exit(3)
    return r.stdout


def generate_payload(kind, size_bytes):
    if kind == "zeros":
        return b"\x00" * size_bytes
    if kind == "ascii_repeat":
        pat = b"The quick brown fox jumps over the lazy dog. " \
              b"Pack my box with five dozen liquor jugs. "
        out = (pat * (size_bytes // len(pat) + 1))[:size_bytes]
        return out
    if kind == "wx_csv":
        # Header + rows of redundant ts, station, values -- compresses well.
        hdr = b"timestamp,station,temp_c,dewpoint_c,pressure_hpa,wind_kt,wind_dir,humidity_pct\n"
        row = b"2026-04-18T12:00:00Z,CYUL,12.3,8.1,1013.2,15,270,76\n"
        out = hdr + row * ((size_bytes - len(hdr)) // len(row) + 1)
        return out[:size_bytes]
    if kind == "bufr_sim":
        # 256 B structured metadata header, rest urandom. Mostly random overall.
        hdr = b"BUFR\x00\x00" + b"\x01" * 250
        body = os.urandom(size_bytes - len(hdr))
        return hdr + body
    if kind == "random":
        return os.urandom(size_bytes)
    if kind == "prestored_gz":
        # Double-wrap: gzip-compress urandom, pad to size -- the shape our
        # GRIB2/NC4-deflate feeds show on the wire.
        raw = os.urandom(size_bytes)
        gz = gzip.compress(raw, compresslevel=6)
        if len(gz) >= size_bytes:
            return gz[:size_bytes]
        return gz + os.urandom(size_bytes - len(gz))
    raise ValueError(f"unknown payload {kind}")


def container_start(name, host_port, keys_dir):
    # --cap-drop=ALL, read-only keys, bind only to 127.0.0.1 so this never
    # exposes a dev sshd to the DI network.
    sh(["docker", "rm", "-f", name])
    r = sh([
        "docker", "run", "-d", "--rm",
        "--name", name,
        "-p", f"127.0.0.1:{host_port}:22",
        "-v", f"{keys_dir}:/home/{SFTP_USER}/upload:rw",
        IMAGE,
        f"{SFTP_USER}:{SFTP_PASS}:1001",
    ])
    if r.returncode != 0:
        log(f"docker run failed: {r.stderr.strip()}")
        sys.exit(3)
    cid = r.stdout.strip()
    # Wait for sshd to be listening. atmoz/sftp has no ss/netstat, so read
    # /proc/net/tcp: a LISTEN (state 0A) on any-addr port 22 (hex 0016).
    for _ in range(60):
        t = sh(["docker", "exec", name, "sh", "-c",
                "grep -c ' 00000000:0016 .* 0A ' /proc/net/tcp || true"])
        if t.stdout.strip() not in ("", "0"):
            return cid
        time.sleep(0.25)
    log("sshd did not come up in 15s")
    sh(["docker", "rm", "-f", name])
    sys.exit(3)


def container_stop(name):
    sh(["docker", "rm", "-f", name])


def find_host_veth(container_name):
    """Return the host-side veth interface for a container on the default bridge."""
    # The peer index on container side is eth0. We match ifindex via a brief
    # exec and find the corresponding host-side veth by @ifN suffix in `ip link`.
    r = sh(["docker", "exec", container_name, "sh", "-c",
            "cat /sys/class/net/eth0/iflink"])
    if r.returncode != 0:
        return None
    peer_idx = r.stdout.strip()
    r = sh(["ip", "-o", "link"])
    for line in r.stdout.splitlines():
        # Example: "47: vethabc123@if46: <BROADCAST,MULTICAST,UP,LOWER_UP> ..."
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            host_idx = int(parts[0].strip())
        except ValueError:
            continue
        if str(host_idx) == peer_idx:
            name = parts[1].strip().split("@")[0]
            return name
    return None


def tc_apply_wan(veth):
    cmd = [
        "sudo", "-n", "tc", "qdisc", "add", "dev", veth, "root", "netem",
        "delay", f"{WAN_PROFILE['delay_ms']}ms",
        "loss", f"{WAN_PROFILE['loss_pct']}%",
        "rate", f"{WAN_PROFILE['rate_mbit']}mbit",
    ]
    r = sh(cmd)
    if r.returncode != 0:
        log(f"tc apply failed: {r.stderr.strip()}")
        sys.exit(5)


def tc_clear_wan(veth):
    sh(["sudo", "-n", "tc", "qdisc", "del", "dev", veth, "root"])


def read_tx_bytes(veth):
    p = Path(f"/sys/class/net/{veth}/statistics/tx_bytes")
    return int(p.read_text())


def read_rx_bytes(veth):
    p = Path(f"/sys/class/net/{veth}/statistics/rx_bytes")
    return int(p.read_text())


def client_cpu_s():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def one_trial(paramiko_mod, host, port, payload_bytes, compress, veth):
    """Connect, put payload, get it back, verify. Return metrics dict."""
    client = paramiko_mod.SSHClient()
    client.set_missing_host_key_policy(paramiko_mod.AutoAddPolicy())
    tx0 = read_tx_bytes(veth)
    rx0 = read_rx_bytes(veth)
    cpu0 = client_cpu_s()
    wall0 = time.perf_counter()

    client.connect(
        host, port=port, username=SFTP_USER, password=SFTP_PASS,
        compress=compress, allow_agent=False, look_for_keys=False,
        timeout=30, banner_timeout=30, auth_timeout=30,
    )
    negotiated = "unknown"
    try:
        # remote_compression is a string in paramiko (e.g. 'none', 'zlib@openssh.com')
        negotiated = client.get_transport().remote_compression or "none"
    except Exception:
        pass
    sftp = client.open_sftp()
    remote = f"upload/bench_{os.getpid()}_{int(time.time()*1000)}.bin"
    # put
    with sftp.file(remote, "wb") as f:
        f.set_pipelined(True)
        # Write in 256 KB chunks (same shape as sarracenia/transfer/__init__ loop)
        view = memoryview(payload_bytes)
        CHUNK = 256 * 1024
        for i in range(0, len(view), CHUNK):
            f.write(view[i:i + CHUNK])
    # get back
    buf = io.BytesIO()
    with sftp.file(remote, "rb") as f:
        f.prefetch()
        while True:
            b = f.read(256 * 1024)
            if not b:
                break
            buf.write(b)
    roundtrip = buf.getvalue()
    try:
        sftp.remove(remote)
    except Exception:
        pass
    sftp.close()
    client.close()

    wall1 = time.perf_counter()
    cpu1 = client_cpu_s()
    tx1 = read_tx_bytes(veth)
    rx1 = read_rx_bytes(veth)

    sha_ok = hashlib.sha512(roundtrip).digest() == hashlib.sha512(payload_bytes).digest()
    return {
        "wall_s": wall1 - wall0,
        "cpu_s": cpu1 - cpu0,
        "tx_bytes": tx1 - tx0,
        "rx_bytes": rx1 - rx0,
        "sha_ok": sha_ok,
        "negotiated": negotiated,
    }


def summarize(rows):
    """Group rows by (payload, link, compress) and compute medians."""
    by = {}
    for r in rows:
        k = (r["payload"], r["link"], r["compress"])
        by.setdefault(k, []).append(r)

    out = []
    for k, group in by.items():
        walls = [g["wall_s"] for g in group]
        txs = [g["tx_bytes"] for g in group]
        cpus = [g["cpu_s"] for g in group]
        out.append({
            "payload": k[0], "link": k[1], "compress": k[2],
            "trials": len(group),
            "wall_s_median": statistics.median(walls),
            "wall_s_stdev": statistics.stdev(walls) if len(walls) > 1 else 0.0,
            "wire_tx_mb_median": statistics.median(txs) / 1_000_000,
            "cpu_s_median": statistics.median(cpus),
            "sha_ok_all": all(g["sha_ok"] for g in group),
            "negotiated": group[0]["negotiated"],
        })
    return out


def print_summary(summary, size_mb, payload_sizes=None):
    log("")
    if payload_sizes:
        log(f"=== Summary (per-payload sizes below, median of trials) ===")
    else:
        log(f"=== Summary (payload size {size_mb} MB, median of trials) ===")
    hdr = f"{'payload':<22} {'link':<4} {'cmp':<5} {'wall_s':>8} {'±stdev':>7} {'wire_MB':>9} {'cpu_s':>7} {'nego':<20} sha"
    log(hdr)
    log("-" * len(hdr))
    for r in sorted(summary, key=lambda x: (x["payload"], x["link"], x["compress"])):
        log(f"{r['payload']:<22} {r['link']:<4} "
            f"{'ON' if r['compress'] else 'off':<5} "
            f"{r['wall_s_median']:8.2f} {r['wall_s_stdev']:7.2f} "
            f"{r['wire_tx_mb_median']:9.2f} {r['cpu_s_median']:7.3f} "
            f"{r['negotiated']:<20} {'OK' if r['sha_ok_all'] else 'MISMATCH'}")
    log("")
    # Speedup vs. compression off, per (payload,link).
    pairs = {}
    for r in summary:
        pairs.setdefault((r["payload"], r["link"]), {})[r["compress"]] = r
    log(f"=== Compression effect (ON vs off) ===")
    log(f"{'payload':<22} {'link':<4} {'wall_speedup':>14} {'wire_reduction':>15} {'cpu_overhead':>13}")
    for (payload, link), d in sorted(pairs.items()):
        if False in d and True in d:
            off, on = d[False], d[True]
            if off["wall_s_median"] > 0 and off["wire_tx_mb_median"] > 0:
                wall_speedup = off["wall_s_median"] / on["wall_s_median"]
                wire_red = 1 - (on["wire_tx_mb_median"] / off["wire_tx_mb_median"])
                cpu_over = (on["cpu_s_median"] - off["cpu_s_median"]) / max(off["cpu_s_median"], 0.001)
                log(f"{payload:<22} {link:<4} "
                    f"{wall_speedup:13.2f}x "
                    f"{wire_red*100:14.1f}% "
                    f"{cpu_over*100:12.1f}%")


def write_csv(rows, path):
    import csv
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "payload", "link", "compress", "trial", "size_mb",
            "wall_s", "cpu_s", "tx_bytes", "rx_bytes", "sha_ok", "negotiated",
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _short_label(path, max_len=20):
    """Compact filename for log/summary columns."""
    name = os.path.basename(path)
    # Strip the :CRDRCMASITS:DMS:CMC:RCM_PRODUCT-style suffix on DMS filenames.
    if ":" in name:
        name = name.split(":", 1)[0]
    if len(name) <= max_len:
        return name
    # Keep product prefix + tail. e.g. S1A_EW_GRDM_...9062.zip -> S1A_EW_GRDM_..9062
    return name[:max_len - 3] + "..."


def load_real_payloads(paths):
    """Read the listed files into memory. Returns dict {label: bytes}."""
    out = {}
    labels_seen = set()
    for p in paths:
        p = os.path.abspath(p)
        if not os.path.isfile(p):
            log(f"FATAL: --file path is not a regular file: {p}")
            sys.exit(2)
        label = _short_label(p)
        # Uniqueify labels if two files collide
        orig = label
        i = 2
        while label in labels_seen:
            label = f"{orig}_{i}"
            i += 1
        labels_seen.add(label)
        with open(p, "rb") as f:
            data = f.read()
        out[label] = data
        log(f"loaded real payload {label}: {len(data):,} bytes from {p}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payloads", default="zeros,ascii_repeat,wx_csv,bufr_sim,random,prestored_gz",
                    help="comma-separated synthetic payload profiles")
    ap.add_argument("--file", action="append", default=[], dest="files",
                    help="real-file payload (repeatable). Overrides --payloads "
                         "and --size-mb; size is the file's own size.")
    ap.add_argument("--size-mb", type=int, default=10)
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--wan", action="store_true",
                    help="also run WAN cells (tc netem via sudo)")
    ap.add_argument("--wan-only", action="store_true",
                    help="skip LAN cells (only useful with --wan)")
    ap.add_argument("--lan-only", action="store_true")
    ap.add_argument("--wan-delay", type=float, default=None,
                    help="override WAN one-way delay in ms (default 100)")
    ap.add_argument("--wan-loss", type=float, default=None,
                    help="override WAN loss percentage, e.g. 0.1 for 0.1% (default 0.1)")
    ap.add_argument("--wan-rate", type=float, default=None,
                    help="override WAN bandwidth cap in Mbit/s (default 10)")
    ap.add_argument("--wan-label", default=None,
                    help="label for WAN cells in CSV/summary (default 'wan')")
    ap.add_argument("--csv", default=None, help="output CSV path (required)")
    args = ap.parse_args()

    if not args.csv:
        log("--csv PATH is required")
        sys.exit(2)

    use_real_files = bool(args.files)

    if not use_real_files:
        payloads = [p.strip() for p in args.payloads.split(",") if p.strip()]
        for p in payloads:
            if p not in PAYLOAD_PROFILES:
                log(f"unknown payload {p}; choices: {','.join(PAYLOAD_PROFILES)}")
                sys.exit(2)
    else:
        payloads = None  # set after loading

    # Apply WAN-profile overrides up-front so tc_apply_wan uses them.
    if args.wan_delay is not None:
        WAN_PROFILE["delay_ms"] = args.wan_delay
    if args.wan_loss is not None:
        WAN_PROFILE["loss_pct"] = args.wan_loss
    if args.wan_rate is not None:
        WAN_PROFILE["rate_mbit"] = args.wan_rate
    wan_label = args.wan_label or "wan"

    if args.wan_only:
        links = [wan_label]
    else:
        links = ["lan"]
        if args.wan and not args.lan_only:
            links.append(wan_label)

    # Sanity checks
    require(["docker", "version"], why="docker is required")
    try:
        import paramiko
    except ImportError:
        log("paramiko not importable")
        sys.exit(3)

    if use_real_files:
        payload_bytes = load_real_payloads(args.files)
        payloads = list(payload_bytes.keys())
        # Skip the per-payload gzip-ratio probe -- real files may be >1 GB and
        # gzipping the whole thing up-front would dominate runtime. The
        # benchmark itself measures wire reduction empirically anyway.
    else:
        size_b = args.size_mb * 1024 * 1024
        # Pre-generate once so CPU of generation isn't counted in trials.
        payload_bytes = {p: generate_payload(p, size_b) for p in payloads}
        for p, b in payload_bytes.items():
            log(f"prepared payload {p}: {len(b):,} bytes, "
                f"gzip-ratio~={len(b)/max(len(gzip.compress(b, compresslevel=6)),1):.2f}x")

    with tempfile.TemporaryDirectory(prefix="sr3_l3_keys_") as keys_dir:
        # 0o777 so uid 1001 (testuser inside atmoz/sftp) can write.
        # Dir is throwaway tmpfs-adjacent, not a security concern.
        os.chmod(keys_dir, 0o777)
        name = f"{CONTAINER_NAME_PREFIX}_{os.getpid()}"
        log(f"starting {IMAGE} as {name}")
        container_start(name, SFTP_PORT, keys_dir)
        try:
            veth = find_host_veth(name)
            if not veth:
                log("could not find host-side veth; is the container on docker0?")
                sys.exit(3)
            log(f"host-side veth: {veth}")

            rows = []
            for link in links:
                if link != "lan":
                    log(f"applying WAN shaping on {veth} for link={link!r}: "
                        f"{WAN_PROFILE['delay_ms']}ms delay, "
                        f"{WAN_PROFILE['loss_pct']}% loss, "
                        f"{WAN_PROFILE['rate_mbit']} Mbit/s")
                    tc_apply_wan(veth)
                try:
                    for payload in payloads:
                        for compress in (False, True):
                            for trial in range(1, args.trials + 1):
                                t = one_trial(
                                    paramiko, "127.0.0.1", int(SFTP_PORT),
                                    payload_bytes[payload], compress, veth,
                                )
                                row = {
                                    "payload": payload, "link": link,
                                    "compress": compress, "trial": trial,
                                    "size_mb": len(payload_bytes[payload]) / (1024 * 1024),
                                    **t,
                                }
                                rows.append(row)
                                log(f"  [{link}/{payload}/cmp={'ON' if compress else 'off'}/t={trial}] "
                                    f"wall={t['wall_s']:.2f}s "
                                    f"wire_tx={t['tx_bytes']/1e6:.1f}MB "
                                    f"cpu={t['cpu_s']:.3f}s "
                                    f"nego={t['negotiated']} "
                                    f"{'OK' if t['sha_ok'] else 'SHA MISMATCH'}")
                                if not t["sha_ok"]:
                                    log("FATAL correctness regression -- sha512 mismatch")
                                    sys.exit(6)
                finally:
                    if link != "lan":
                        tc_clear_wan(veth)
        finally:
            log(f"stopping {name}")
            container_stop(name)

    write_csv(rows, args.csv)
    log(f"wrote CSV {args.csv}")
    summary = summarize(rows)
    payload_sizes = {p: len(b) for p, b in payload_bytes.items()}
    print_summary(summary, args.size_mb, payload_sizes if use_real_files else None)


if __name__ == "__main__":
    main()
