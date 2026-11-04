# Plan

The goal: every gauge and telltale the cluster supports works from ATS and
ETS2. Done means every item on the support checklist from step 2 works in game.

## 1. Bench bring-up and listen (done)

Wire the [minimum bench harness](pinout.md#minimum-bench-harness) as in the
[bench wiring diagram](wiring.md), power the
cluster, and log what it sends on CAN1 and J1708: its address claim, the PGNs
it requests, and what it asks the VECU for. Tools: `can_listen.py`,
`can_send.py` and `j1708_listen.py` in [tools/](../tools/).

## 2. Sweep and map every gauge and telltale (in progress)

Status is in [sweep_plan.md](sweep_plan.md) and [messages.md](messages.md).

Send the standard J1939 PGNs first, then sweep the Volvo proprietary range and
the J1587 PIDs until the communication error clears (see the
[sweep plan](sweep_plan.md)). Measure the analog input ranges (fuel and air
pressures) with the decade box. Record every result in
[messages.md](messages.md), with a checklist of what is supported and what is
still unknown.

Sweeps run with [`sweep_watch.py`](../tools/README.md#camera-sweep). Changes
are caught by a camera (DroidCam, exposure and white balance locked), by the
cluster's own bus output, or by pressing the GPIO41 button when a lamp
changes. Bit-by-bit sweeps then pin down which byte drives what.

## 3. ESP32-S3 firmware (stage 1 bridge only)

The bridge in `firmware/` sends the CAN1 and J1708 traffic the cluster
expects, drives the analog inputs through digital pots or a DAC, and takes
gauge values over USB serial.

## 4. Game link

A SimHub or scs-sdk bridge maps ATS/ETS2 telemetry to each gauge and telltale.
Test against the step 2 checklist until everything supported works in game.
