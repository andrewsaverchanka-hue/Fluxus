#!/usr/bin/env python3
"""Generate synthetic devices_server logs that exercise the Lima parser.

Real logs are customer data and never leave the terminals, so the migration
needs a generator rich enough to hit every branch of the parser: normal
banknote cycles, rejects, BNV events (including errors), collections, ANSI
colour codes, missing fields, and noise lines.

Usage:
    gen_logs.py out.log --lines 200000 [--seed 1]
"""

import argparse
import random
from datetime import datetime, timedelta

ANSI = ["\x1b[32m", "\x1b[33m", "\x1b[0m", "\x1b[31m"]

DENOMS = [100, 500, 1000, 2000, 5000, 10000]
MODELS = [
    ("BV20", "CashCode"),
    ("BV30", "CashCode"),
    ("NV9", "Money Controls"),
]
BNV_EVENTS = [
    "Note Stacking",
    "Note Stacked",
    "Note Rejecting",
    "Note Rejected",
    "Slave Reset",
    "Initialising",
    "Read Note",
    "Credit Note",
    "Note Cleared To Cash Box",
    "Note Cleared From Front",
    "Channel Disable",
    "Disabled",
]
BNV_ERRORS = ["Safe Note Jam", "Unsafe Note Jam", "Fraud Attempt", "Stacker Full"]


def ts_ms(dt):
    return int(dt.timestamp() * 1000)


def banknote_line(dt, event_suffix, uuid, denom, serial, model, vendor):
    stamp = dt.strftime("%d/%m %H:%M:%S")
    ansi = random.choice(ANSI) if random.random() < 0.25 else ""
    parts = [
        f"{ansi}{stamp} DBG",
        "DeviceEvent{",
        f"type: DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_{event_suffix}",
    ]
    if denom is not None:
        parts.append(f'banknote: {{ banknote: {denom} }}')
    if random.random() < 0.95:
        parts.append('currency: "USD"')
    parts.append(f'banknote_uuid: "{uuid}"')
    if random.random() < 0.5:
        parts.append(f"banknote_id: {random.randint(1000, 9999)}")
    parts.append(
        f'device: {{ id: "{serial}", model: "{model}", vendor: "{vendor}" }}'
    )
    if random.random() < 0.3:
        parts.append('description: "note accepted"')
    parts.append(f"timestamp_ms: {ts_ms(dt)}")
    parts.append("}ClrEol\x1b[0m")
    return " ".join(parts)


def bnv_line(dt, event, serial, model):
    stamp = dt.strftime("%d/%m %H:%M:%S")
    validator = "BanknoteValidatorBV" if model.startswith("BV") else "BanknoteValidator"
    extra = f'device: {{ id: "{serial}" }}' if random.random() < 0.5 else ""
    return (
        f"{stamp} DBG {validator} event= \"{event}\" {extra} "
        f"timestamp_ms: {ts_ms(dt)}"
    )


def collection_line(dt, notes):
    stamp = dt.strftime("%d/%m %H:%M:%S")
    body = " ".join(f"${n} x {c}" for n, c in notes)
    return f'{stamp} INF COLLECTION REPORT Request: Print notes="{body}"'


def noise_line(dt):
    stamp = dt.strftime("%d/%m %H:%M:%S")
    return random.choice(
        [
            f"{stamp} DBG heartbeat ok",
            f"{stamp} INF DevicesServer listening on 0.0.0.0:9000",
            f"{stamp} DBG serialPath=/dev/ttyS0 opened",
            f"{stamp} INF LCDM2000 status=0x30",
            f"{stamp} WRN retry connection",
        ]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lines", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    random.seed(args.seed)
    dt = datetime(2026, 10, 15, 8, 0, 0)
    serial_pool = [f"SN{i:04d}" for i in range(1, 6)]
    devices = {
        s: random.choice(MODELS) for s in serial_pool
    }
    uuid_counter = 0
    pending = []  # (serial, uuid, denom, dt)

    with open(args.out, "w", encoding="utf-8") as f:
        for _ in range(args.lines):
            dt += timedelta(milliseconds=random.randint(50, 4000))
            roll = random.random()

            if roll < 0.35 and pending:
                serial, uuid, denom, _ = pending.pop(0)
                model, vendor = devices[serial]
                suffix = random.choices(
                    ["STACKED", "REJECTED", "CREDIT"], weights=[88, 7, 5]
                )[0]
                f.write(
                    banknote_line(dt, suffix, uuid, denom, serial, model, vendor)
                    + "\n"
                )
                if suffix == "STACKED" and random.random() < 0.15:
                    f.write(collection_line(dt, [(random.choice([5, 10, 20, 50]), random.randint(1, 40))]) + "\n")
            elif roll < 0.6:
                serial = random.choice(serial_pool)
                model, vendor = devices[serial]
                uuid_counter += 1
                uuid = f"7f{uuid_counter:06d}-c1a2-4b3d-9e8f-{random.randint(0, 999999):06d}"
                denom = random.choice(DENOMS) if random.random() < 0.97 else None
                f.write(banknote_line(dt, "INSERTED", uuid, denom, serial, model, vendor) + "\n")
                pending.append((serial, uuid, denom, dt))
            elif roll < 0.68:
                serial = random.choice(serial_pool)
                model, _ = devices[serial]
                event = random.choice(BNV_ERRORS if random.random() < 0.15 else BNV_EVENTS)
                f.write(bnv_line(dt, event, serial, model) + "\n")
            elif roll < 0.70:
                notes = [(random.choice([5, 10, 20, 50, 100]), random.randint(1, 60)) for _ in range(random.randint(1, 4))]
                f.write(collection_line(dt, notes) + "\n")
            else:
                f.write(noise_line(dt) + "\n")

    print(f"wrote {args.lines} lines to {args.out}")


if __name__ == "__main__":
    main()
