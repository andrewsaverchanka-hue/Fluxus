#!/usr/bin/env python3
"""Generate synthetic W2 logs (frontend + LCDM low-level).

Mirrors the line shapes the Python parsers' regexes expect, so both
implementations are exercised on the same input.

Usage:
    gen_w2.py frontend out.log --lines 20000
    gen_w2.py lowlevel out.log --lines 20000
"""

import argparse
import random
from datetime import datetime, timedelta

DEVICES = [("dev-1", "LCDM2000", "CashCode"), ("dev-2", "LCDM4000", "Innovative")]


def trx(n):
    return f"7f{n:06x}-c1a2-4b3d-9e8f-{n:012x}"


def stamp(dt):
    # [10/11/2026, 4:28:25 PM]
    return dt.strftime("[%m/%d/%Y, %-I:%M:%S %p]")


def gen_frontend(path, lines, seed=1):
    random.seed(seed)
    dt = datetime(2026, 10, 15, 8, 0, 0)
    out = []
    n = 0
    while len(out) < lines:
        n += 1
        t = trx(n)
        login = str(10000 + (n % 7))
        dev, model, vendor = random.choice(DEVICES)
        amount = random.choice([500, 1000, 2000, 5000])
        ok = random.random() < 0.75
        base = dt
        dt += timedelta(seconds=random.randint(5, 90))
        pre = stamp(dt)

        out.append(f'{pre} DEBUG payoutCouponAPI trx {t} login: {login}')
        out.append(
            f"{pre} DEBUG INSERT INTO terminal_balances SET `trx` = '{t}', "
            f"`sum` = {amount}, `user_login` = '{login}'"
        )
        out.append(f"{pre} DEBUG UPDATE terminal_balances SET `status` = 'in_progress' where id = {1000 + n}")
        out.append(
            f'{pre} DEBUG DispenserService.dispense login: \'{login}\' '
            f'commands [{{"deviceId":"{dev}","bankIndex":1,"notesAmount":{amount}}}] '
            f'nominal and current count [[1,20],[2,50]]'
        )
        dispensed = amount if ok else amount // 2
        detail = '' if ok else ',"statusDetail":"JAM at DIV Sensor"'
        out.append(
            f'{pre} DEBUG processNewEvent {{"type":16,"dispensed":{{"bankIndex":1,'
            f'"notesAmountRequested":{amount},"notesAmountDispensed":{dispensed}{detail}}},'
            f'"model":"{model}","device":{{"id":"{dev}"}},"vendor":"{vendor}"}} '
            f"login: '{login}'"
        )
        out.append(
            f"{pre} DEBUG INSERT INTO terminal_transactions_box VALUES "
            f"({2000 + n}, 1, 20, {max(1, dispensed // 2000)}, 2) login: '{login}'"
        )
        if ok:
            out.append(
                f"{pre} DEBUG UPDATE terminal_transactions SET `sum` = {dispensed}, "
                f"`status` = 'ok' where id = {2000 + n} login: '{login}'"
            )
            out.append(f"{pre} DEBUG printSlip BARCODE balance login: '{login}'")
            out.append(f"{pre} DEBUG Complete transaction transactionId: {t}")
            out.append(f"{pre} DEBUG Redirect to HOME withdraw ok")
        else:
            out.append(f"{pre} DEBUG cannotContinue {{ code: 12 }} login: '{login}'")
            out.append(f"{pre} ERROR Error occurs while withdrawing trx: {t}")
            out.append(f"{pre} DEBUG Redirect to HOME failed withdraw")

        if random.random() < 0.25:
            ctype = random.choice(["collection", "withdraw", "cashin"])
            if ctype == "collection":
                out.append(
                    f'{pre} INFO COLLECTION REPORT {{"outByNotes":"$20*10 = $200\\n\\n$10*5 = $50",'
                    f'"totalOut":250}}'
                )
            elif ctype == "withdraw":
                out.append(
                    f'{pre} INFO Withdraw report {{"takenFromDispenserBoxes":"Box 1\\n$20*3 = $60",'
                    f'"takenFromRejectedBox":"$10*1 = $10","totalSum":70}}'
                )
            else:
                out.append(
                    f'{pre} INFO Cash in {{"addedToDispenserBoxes":"Box 1\\n$20*3 = $60",'
                    f'"totalSum":60}}'
                )
        if random.random() < 0.1:
            out.append(f"{pre} DEBUG heartbeat ok")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out[:lines]) + "\n")
    print(f"wrote {min(lines, len(out))} lines to {path}")


def gen_lowlevel(path, lines, seed=2):
    random.seed(seed)
    dt = datetime(2026, 10, 15, 8, 0, 0)
    out = []
    req = 100
    while len(out) < lines:
        req += 1
        job = 7 + req % 5
        dt += timedelta(seconds=random.randint(1, 40))
        pre = dt.strftime("%d/%m %H:%M:%S DBG")
        model = random.choice(["LCDM2000", "LCDM4000"])
        serial = f"serialPath=/dev/ttyS{req % 4}"

        if model == "LCDM2000":
            cmds = random.randint(1, 2)
            ok = random.random() < 0.8
            out.append(f"{pre} {serial} Handle Dispense Request: reqID={req} commands={cmds}")
            for ci in range(cmds):
                amount = random.choice([1000, 2000, 5000])
                out.append(
                    f"{pre} {serial} Handle Dispense Request: reqID={req} cmdIndex={ci} "
                    f"bankIndex=1 amount={amount}"
                )
                out.append(f"{pre} {serial} Dispense worker: job start reqID={req} jobID={job}")
                out.append(
                    f"{pre} {serial} Dispense: start reqID={req} jobID={job} cmdIndex={ci} "
                    f"bankIndex=1 amount={amount}"
                )
                out.append(f"{pre} {serial} Dispense: raw response hex=0A0B0C")
                out.append(f"{pre} {serial} Dispense: upper CHK=1 EXIT=0; lower CHK=1 EXIT=0")
                out.append(f"{pre} {serial} Dispense: status=0x30 (Good)")
                out.append(f"{pre} {serial} CommandDispense: chk=1 disp={amount // 100} rej=0")
                out.append(
                    f"{pre} {serial} Dispense: settled reqID={req} jobID={job} cmdIndex={ci} "
                    f"bankIndex=1 amount={amount} attempts=1"
                )
                if ok:
                    out.append(
                        f"{pre} {serial} Dispense: event OK reqID={req} jobID={job} cmdIndex={ci} "
                        f"bankIndex=1 amount={amount} requested={amount} chk=1 "
                        f"dispensed={amount // 100} rejected=0"
                    )
                else:
                    out.append(
                        f'{pre} {serial} Dispense: error reqID={req} jobID={job} cmdIndex={ci} '
                        f'bankIndex=1 amount={amount} chkSensor=0 dispensed=0 rejected=0 '
                        f'error="JAM at DIV Sensor"'
                    )
            out.append(f"{pre} {serial} Dispense worker: job done reqID={req} jobID={job}")
        else:
            bank = random.randint(1, 2)
            notes = random.randint(1, 20)
            out.append(
                f"{pre} {serial} LCDM4000 CCTalk: Dispense sequence: start command bankIndex={bank} notesAmount={notes}"
            )
            out.append(f"{pre} {serial} LCDM4000 CCTalk: Processor: Write: Dispense payload=ABCD1234")
            out.append(
                f'{pre} {serial} LCDM4000 CCTalk: Dispense: note result bankIndex={bank} '
                f'chkSensorTotal={notes} deviceCode=0 deviceDescription="OK" '
                f"dispensedTotal={notes} noteDispensed={notes} noteN={notes} noteRejected=0"
            )
            out.append(
                f"{pre} {serial} LCDM4000 CCTalk: Dispense: finished bankIndex={bank} "
                f"chkSensorTotal={notes} dispensedTotal={notes} notesAmount={notes} rejectedTotal=0"
            )
        if random.random() < 0.2:
            out.append(
                f"{pre} {serial} LCDM4000 CCTalk: Command: Status SensorDVTL=OK SensorDVTR=OK "
                f"SensorEJT=OK SensorEXIT=OK SensorRJT=OK"
            )
            out.append(
                f"{pre} {serial} LCDM4000 CCTalk: Command: Status Cassette1Exists=OK "
                f"Cassette1Length=500 Cassette1Opacity=80 Cassette1Status=0"
            )
        if random.random() < 0.1:
            out.append(f"{pre} {serial} heartbeat")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out[:lines]) + "\n")
    print(f"wrote {min(lines, len(out))} lines to {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["frontend", "lowlevel"])
    ap.add_argument("out")
    ap.add_argument("--lines", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    if a.kind == "frontend":
        gen_frontend(a.out, a.lines, a.seed)
    else:
        gen_lowlevel(a.out, a.lines, a.seed)


if __name__ == "__main__":
    main()
