# Tools

PC-side scripts for the bench. Install with `pip install -r requirements.txt`
(Python 3.9 or newer).

| Script | What it does |
|---|---|
| `can_listen.py` | Logs CAN1 and summarizes each source and PGN, with Requests (0xEA00) and Address Claimed (0xEE00) decoded |
| `can_send.py` | Sends one PGN on repeat, for hand tests and waking the cluster |
| `j1708_listen.py` | Logs J1708/J1587 frames from a USB-serial adapter on the XY-485 |
| `j1939.py`, `j1587.py` | Shared helpers |
| `test_tools.py` | Offline checks: `python -m unittest test_tools` |

## First power-up session

With the CANable 2.0 on CAN1 (A:15/A:16) and a USB-serial adapter on the
XY-485 (A:17/A:18), run both listeners in separate windows, then power the
cluster as in [docs/pinout.md](../docs/pinout.md#first-power-up):

```
python can_listen.py --interface slcan --channel COM5 --log first_power_can.csv --quiet
python j1708_listen.py --port COM6 --log first_power_j1708.csv
```

Use `--interface gs_usb` instead if the CANable runs candleLight firmware.
After a minute, Ctrl+C prints a summary. Run `can_listen.py` again with
`--poke` to ask every node to claim its address.

If the cluster stays dark (it has no ignition pin, so it may wait for bus
traffic), give it something to hear:

```
python can_send.py --channel COM5 --pgn F004 --sa 00 --data "FF FF FF 40 1F FF FF FF" --period 0.01
```

Save the logs, and copy what the cluster requested into
[docs/messages.md](../docs/messages.md).
