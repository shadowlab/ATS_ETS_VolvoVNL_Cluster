# Plan

The goal: every gauge and telltale the cluster supports works from ATS and
ETS2. Done means every item on the support checklist from step 2 works in game.

## 1. Bench bring-up and listen

Wire the [minimum bench harness](pinout.md#minimum-bench-harness) as in the
[bench wiring diagram](wiring.md), power the
cluster, and log what it sends on CAN1 and J1708: its address claim, the PGNs
it requests, and what it asks the VECU for. Tools: `can_listen.py`,
`can_send.py` and `j1708_listen.py` in [tools/](../tools/).

## 2. Sweep and map every gauge and telltale

Send the standard J1939 PGNs first, then sweep the Volvo proprietary range and
the J1587 PIDs until the communication error clears (see the
[sweep plan](sweep_plan.md)). Measure the analog input ranges (fuel and air
pressures) with the decade box. Record every result in
[messages.md](messages.md), with a checklist of what is supported and what is
still unknown.

Sweeps run unattended with a camera watching the cluster
([`sweep_watch.py`](../tools/README.md#camera-sweep)): DroidCam on an Android phone over USB, with exposure, white balance
and focus locked. For each candidate the script sends the frame for about a
second and compares the picture against an idle baseline, ignoring masked
areas like the clock. When something changes, it saves before and after
pictures and logs the source address, PGN and payload to a CSV, then returns
to baseline before the next candidate. A review page lists the hits to
confirm or ignore, and a bit-by-bit sweep then pins down which byte drives
what.

## 3. ESP32-S3 firmware

The bridge in `firmware/` sends the CAN1 and J1708 traffic the cluster
expects, drives the analog inputs through digital pots or a DAC, and takes
gauge values over USB serial.

## 4. Game link

A SimHub or scs-sdk bridge maps ATS/ETS2 telemetry to each gauge and telltale.
Test against the step 2 checklist until everything supported works in game.
