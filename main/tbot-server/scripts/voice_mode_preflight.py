#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys


def parse_ping_output(output):
    loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
    duplicate_match = re.search(r"\+(\d+) duplicates", output)
    rtt_match = re.search(
        r"(?:round-trip|rtt) min/avg/max/(?:stddev|mdev) = "
        r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
        output,
    )
    if not loss_match:
        raise ValueError("could not parse ping output")
    if not rtt_match:
        return {
            "loss_pct": float(loss_match.group(1)),
            "min_ms": 0.0,
            "avg_ms": 0.0,
            "max_ms": 0.0,
            "jitter_ms": 0.0,
            "duplicates": int(duplicate_match.group(1)) if duplicate_match else 0,
        }
    return {
        "loss_pct": float(loss_match.group(1)),
        "min_ms": float(rtt_match.group(1)),
        "avg_ms": float(rtt_match.group(2)),
        "max_ms": float(rtt_match.group(3)),
        "jitter_ms": float(rtt_match.group(4)),
        "duplicates": int(duplicate_match.group(1)) if duplicate_match else 0,
    }


def run_ping(device_ip, count, timeout_ms):
    command = ["ping", "-c", str(count), "-W", str(timeout_ms), device_ip]
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.stdout


def preflight_failure(stats, args):
    if stats["loss_pct"] > args.max_loss_pct:
        return f"packet_loss {stats['loss_pct']:.1f}% > {args.max_loss_pct:.1f}%"
    if stats["avg_ms"] > args.max_avg_ms:
        return f"avg_latency {stats['avg_ms']:.1f}ms > {args.max_avg_ms:.1f}ms"
    if getattr(args, "max_max_ms", None) is not None and stats["max_ms"] > args.max_max_ms:
        return f"max_latency {stats['max_ms']:.1f}ms > {args.max_max_ms:.1f}ms"
    if args.max_jitter_ms is not None and stats["jitter_ms"] > args.max_jitter_ms:
        return f"jitter {stats['jitter_ms']:.1f}ms > {args.max_jitter_ms:.1f}ms"
    if args.max_duplicates is not None and stats["duplicates"] > args.max_duplicates:
        return f"duplicate_replies {stats['duplicates']} > {args.max_duplicates}"
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Preflight network checks for robot voice-mode live smoke."
    )
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--max-loss-pct", type=float, default=0.0)
    parser.add_argument("--max-avg-ms", type=float, default=1000.0)
    parser.add_argument("--max-max-ms", type=float)
    parser.add_argument("--max-jitter-ms", type=float)
    parser.add_argument("--max-duplicates", type=int)
    args = parser.parse_args()

    output = run_ping(args.device_ip, args.count, args.timeout_ms)
    try:
        stats = parse_ping_output(output)
    except ValueError as exc:
        print(f"PREFLIGHT_PING_PARSE_FAILED: {exc}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 2

    print(
        "PREFLIGHT_PING "
        f"loss_pct={stats['loss_pct']:.1f} "
        f"avg_ms={stats['avg_ms']:.1f} "
        f"max_ms={stats['max_ms']:.1f} "
        f"jitter_ms={stats['jitter_ms']:.1f} "
        f"duplicates={stats['duplicates']}"
    )
    failure = preflight_failure(stats, args)
    if failure:
        print(f"PREFLIGHT_FAIL {failure}", file=sys.stderr)
        return 1
    print("PREFLIGHT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
