# iw-test-controller – AP/STA (WDS) + 802.11s mesh test controller

Automates Wi-Fi link tests between **two DUTs**. It runs every applicable channel and bandwidth on
each radio and writes a PASS/FAIL row per test to a CSV file.

This repository holds the **controller** (Python, runs on a laptop). Each DUT runs the
**agent**, an OpenWrt-compatible C program kept in its own repository:
[iw-test-agent](https://github.com/gvvsnrnaveen/iw-test-agent)
(`git@github.com:gvvsnrnaveen/iw-test-agent.git`).

```
 Laptop (this repo, Python GUI or CLI)           DUT 1 / DUT 2 (iw-test-agent, C, OpenWrt)
 ┌───────────────────────────┐   TCP 5555        ┌──────────────────────────────┐
 │ iw-test-controller        │◄──────────────────│ iw-test-agent -s <laptop>    │
 │  server  ─ planner        │  JSON lines over  │  uci / wifi / iw / ping      │
 │  runner  ─ CSV writer     │  the ethernet     │  backup + restore of config  │
 └───────────────────────────┘  management link  └──────────────────────────────┘
```

The DUTs connect **to the laptop**. Start the controller first, then the agents. Agents retry
every 5 s, so the order doesn't matter much. Once both DUTs are connected, press **Start**,
or use auto-start.

## Layout

| Path | What |
|---|---|
| `iw_test_controller.py` | entry point (GUI by default, `--headless` for CLI) |
| `iw_test_controller/server.py` | TCP server and RPC to the agents |
| `iw_test_controller/planner.py` | builds the channel × bandwidth × radio × mode matrix |
| `iw_test_controller/runner.py` | runs the tests one by one, judges PASS/FAIL, writes the CSV |
| `iw_test_controller/config.py` | configuration defaults and JSON load/save |
| `iw_test_controller/gui.py` | Tkinter GUI |
| `iw_test_controller/manual.py` | user manual shown under *Help → User manual* (F1) |
| `example_config.json` | sample config for `-c` |

## Requirements

* Laptop: Python ≥ 3.7 and Tk (`sudo apt install python3-tk`). No pip packages are needed.
* DUT: OpenWrt ≥ 21.02 with [iw-test-agent](https://github.com/gvvsnrnaveen/iw-test-agent)
  installed, plus `iw`, `iwinfo` and busybox `ping`. For mesh with `sae`, install
  `wpad-mesh-*`. Open mesh works with stock `wpad`.
* Set a **country** (`--country US`, etc.). In the world regdomain most 5 GHz channels are
  *no-IR*, and the planner skips them.

## Set up the agents on the DUTs

The agent's build, install and option reference live in the
[iw-test-agent README](https://github.com/gvvsnrnaveen/iw-test-agent). In short:

```sh
git clone git@github.com:gvvsnrnaveen/iw-test-agent.git

# native build (for simulation on the laptop)
make -C iw-test-agent/src

# cross compile with an OpenWrt / QSDK toolchain
export STAGING_DIR=<sdk>/staging_dir
make -C iw-test-agent/src CC=<sdk>/staging_dir/toolchain-*/bin/aarch64-openwrt-linux-musl-gcc

# or build an .ipk (installs /usr/sbin/iw-test-agent + /etc/init.d/iw-test-agent)
cp -r iw-test-agent <sdk>/package/iw-test-agent
make -C <sdk> package/iw-test-agent/compile V=s
```

In the examples below, `192.168.1.100` is the laptop and `192.168.1.1` / `192.168.1.2` are the
DUTs.

Option A, ad hoc:
```sh
scp iw-test-agent/src/iw-test-agent root@192.168.1.1:/tmp/
ssh root@192.168.1.1 '/tmp/iw-test-agent -s 192.168.1.100 -n dut1 -l /tmp/iw-test-agent.log &'
# repeat for 192.168.1.2 with -n dut2
```

Option B, persistent (the .ipk is installed):
```sh
uci set iw-test-agent.main.server=192.168.1.100
uci set iw-test-agent.main.name=dut1
uci set iw-test-agent.main.enabled=1
uci commit iw-test-agent && /etc/init.d/iw-test-agent enable && /etc/init.d/iw-test-agent start
```

If you start the controller with `--token`, give each agent the same value (`-t` or
`uci set iw-test-agent.main.token=...`).

## Run the controller

GUI:
```sh
./iw_test_controller.py            # optionally -c my.json, --country US, ...
```
The server starts listening at launch, and connected DUTs appear in the table. Choose DUT A and
DUT B, then tick modes, radios and bandwidths. Use **Preview plan** to see the matrix and an
estimated duration, then **Start tests**. Pause/Resume and Stop work between tests. Results
appear live, coloured green or red. Double-click a row to see every field. *File → Save config*
writes a JSON file that the CLI can reuse. *Help → User manual* (or F1) opens the full
manual for the GUI, CLI, test flow, CSV columns and troubleshooting.

Headless CLI:
```sh
./iw_test_controller.py --headless --country US                       # full matrix, first 2 DUTs
./iw_test_controller.py --headless --dut-a dut1 --dut-b dut2 \
    --modes ap_sta --radios phy0 --bandwidths HT20,HT80 --channels-5g 36,149 -o ap5g.csv
./iw_test_controller.py --headless --dry-run --swap-roles              # print plan only
./iw_test_controller.py -c example_config.json --headless
```
Exit code: `0` all passed, `1` some failed or the run was stopped, `2` setup error or abort.
Run `./iw_test_controller.py --help` for every option. Each option in the GUI has a CLI flag.

## What one test does

1. **Prepare** (once per DUT): the agent backs up `/etc/config/{wireless,network,firewall}` to
   `/tmp/iw_test_agent_backup`. It creates bridge `br-fpt` with `<subnet>.1` (DUT A) or
   `<subnet>.2` (DUT B), plus an ACCEPT firewall zone, and disables the existing wifi-ifaces.
2. **Apply**: enable only the radio under test. Set `channel`, `htmode`, and `chanbw` for
   HT5/HT10. Create `wireless.fpt_iface` (ifname `fpt0`, `wds=1` for AP/STA, `mesh_id` for mesh),
   then run `wifi up`.
3. **Link**
   * AP/STA: the AP must come up (DFS channels get extra CAC time), then the STA must show
     `Connected to` within the timeout.
   * Mesh: both nodes must show a peer in `mesh plink ESTAB`.
4. **Verify**: `iw dev fpt0 info` on both DUTs must show the requested channel and width.
   Turn this off with `--no-verify-width`.
5. **Traffic**: ping DUT B→A, and A→B unless `--no-bidirectional`. Loss must be ≤ `--max-loss`.
6. **Teardown**, then the next test. At the end, or if the controller disconnects or the agent
   receives SIGTERM, the agent restores the original config. A stale backup found at agent
   start-up is restored too.

Matrix rules:
* Bandwidths per band: 2.4 GHz gets HT5/HT10/HT20/HT40; 5 GHz gets HT20/HT40/HT80.
* A channel is used only if it is enabled on **both** DUTs and isn't no-IR.
* DFS channels need `--include-dfs`.
* HT40 needs a valid secondary channel. On 2.4 GHz that's explicit HT40+ or HT40−; on 5 GHz it
  follows the OpenWrt pairing.
* HT80 needs the full 80 MHz block. It maps to `VHT80` (ht/vht), `HE80` (he) or `EHT80` (eht).
* `--phy-modes ht,vht,he,eht` runs the matrix once per htmode family, e.g. `HT20`, `VHT20`,
  `HE20`, `EHT20`. HE and EHT tests need both DUTs to report that capability; duplicate
  htmodes (vht on 2.4 GHz is plain HT) run only once.
* `--swap-roles` repeats each AP/STA test with the roles reversed.

## CSV columns

`test_id, timestamp, mode, radio, band, channel, freq_mhz, bandwidth, htmode, dfs, dut1, dut1_role,
dut2, dut2_role, attempt, result, reason, link_time_s, dut1_channel, dut1_width, dut2_channel,
dut2_width, signal_dbm, tx_bitrate, ping_tx, ping_rx, loss_pct, rtt_avg_ms, rev_loss_pct,
rev_rtt_avg_ms, duration_s`

## Notes and limitations

* **HT5 / HT10 on ath11k**: these are configured through OpenWrt's `chanbw` option.
  Upstream ath11k/mac80211 does not implement 5/10 MHz for AP/STA/mesh. Unless your
  firmware/driver build supports it, these tests will FAIL with
  `width 20 MHz, expected 5`, which is a real finding, not a tool error. If your vendor tree has
  its own knob, pass it via `-N/--narrowband-cmd` on the agent.
* SSIDs, keys and country codes may contain only `A-Z a-z 0-9 . _ - + @ : = ,`, because they are
  passed to `uci` through the shell.
* The agent disables all existing wifi-ifaces for the duration of the run. Manage the DUTs over
  ethernet.
* Simulation: `iw-test-agent -s 127.0.0.1 -n dut1 -S` (and `-n dut2`) lets you try the full GUI
  and CLI flow on the laptop without hardware.
* Run only one agent per DUT. A second agent with the same name replaces the first session, and
  the controller logs a warning.
