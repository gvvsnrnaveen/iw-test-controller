"""User manual shown under Help -> User manual.

Each section is (title, body). Body markup, one rule per line:
  "## text"      sub-heading
  "* text"       bullet
  4-space indent code / table (monospace, no wrapping)
  anything else  paragraph text
"""

MANUAL = [
    ("1. Overview", """\
iw-test-controller automates Wi-Fi link tests between two DUTs (devices under test). For every \
applicable radio, channel, bandwidth and PHY mode it sets up a link between the two DUTs, checks \
that it came up on the requested channel and width, sends ping traffic across it, and writes one \
PASS/FAIL row per test to a CSV file.

Two link types are tested:
* AP(WDS) + STA(WDS): DUT A runs an access point, DUT B joins it as a 4-address (WDS) station.
* 802.11s Mesh: both DUTs join the same mesh and must form an ESTAB peer link.

The software has two parts:
* The controller (this program) runs on a Linux laptop. It has a GUI (default) and a headless CLI.
* The agent (iw-test-agent, a C program) runs on each OpenWrt DUT. It connects to the \
controller over TCP port 5555 and carries out uci / wifi / iw / ping commands on request.

    Laptop (controller, GUI or CLI)          DUT A / DUT B (iw-test-agent)
    +---------------------------+  TCP 5555  +------------------------------+
    | server  - planner         |<-----------| iw-test-agent -s <laptop-ip> |
    | runner  - CSV writer      |  JSON over | uci / wifi / iw / ping       |
    +---------------------------+  ethernet  | backup + restore of config   |
                                             +------------------------------+

The DUTs connect to the laptop, not the other way round. Start the controller first, then the \
agents. Agents retry every 5 seconds, so the order is not critical.
"""),

    ("2. Requirements", """\
## Laptop
* Python 3.7 or newer with Tk (Debian/Ubuntu: sudo apt install python3-tk). No pip packages.
* An ethernet connection to the DUTs' LAN side (the management link).

## DUTs
* OpenWrt 21.02 or newer with iw-test-agent installed, plus iw, iwinfo and busybox ping.
* For mesh tests with sae encryption install a wpad-mesh-* package. Open mesh works with stock wpad.
* Manage the DUTs over ethernet: the agent disables every existing wifi-iface while tests run.

## Regulatory domain
Always set a Country (for example US, IN, DE). In the default world regdomain most 5 GHz \
channels are no-IR (no initiating radiation) and the planner skips them.
"""),

    ("3. Setting up the DUT agents", """\
The agent source, build and full option list are in the iw-test-agent repository \
(https://github.com/gvvsnrnaveen/iw-test-agent). The examples below use 192.168.1.100 for the \
laptop and 192.168.1.1 / 192.168.1.2 for the DUTs.

## Build
    git clone git@github.com:gvvsnrnaveen/iw-test-agent.git
    make -C iw-test-agent/src                     # native build, for simulation
    export STAGING_DIR=<sdk>/staging_dir           # cross compile for the DUT
    make -C iw-test-agent/src CC=<sdk>/staging_dir/toolchain-*/bin/aarch64-openwrt-linux-musl-gcc

Or build an .ipk (installs /usr/sbin/iw-test-agent and /etc/init.d/iw-test-agent):
    cp -r iw-test-agent <sdk>/package/iw-test-agent
    make -C <sdk> package/iw-test-agent/compile V=s

## Option A: run ad hoc
    scp iw-test-agent/src/iw-test-agent root@192.168.1.1:/tmp/
    ssh root@192.168.1.1 '/tmp/iw-test-agent -s 192.168.1.100 -n dut1 -l /tmp/iw-test-agent.log &'
Repeat for 192.168.1.2 with -n dut2.

## Option B: persistent service (.ipk installed)
    uci set iw-test-agent.main.server=192.168.1.100
    uci set iw-test-agent.main.name=dut1
    uci set iw-test-agent.main.enabled=1
    uci commit iw-test-agent
    /etc/init.d/iw-test-agent enable && /etc/init.d/iw-test-agent start

## Token
If the controller has a Token set, every agent must present the same value \
(-t <token>, or uci set iw-test-agent.main.token=<token>). Agents with a wrong token are \
rejected and logged as "rejected <ip>: bad token".

Run only one agent per DUT. A second agent with the same name replaces the first session and \
the controller logs a warning.
"""),

    ("4. Quick start (GUI)", """\
1. Start the controller:
    ./iw-test-controller.py
   Optionally load settings at start-up: ./iw-test-controller.py -c my.json --country US
2. The server starts listening at launch ("listening on 0.0.0.0:5555" under Controller server).
3. Start the agents on both DUTs (section 3). They appear in the Connected DUTs table once \
their radio information has been read.
4. Pick DUT A and DUT B. The first two connected DUTs are pre-selected.
5. Under Test selection tick the modes, radios, bandwidths and PHY modes, and set the Country.
6. Press Preview plan. The Plan tab lists every test and the status bar shows the test count \
and an estimated duration. Skipped combinations are explained in the Log tab.
7. Press Start tests. Results appear live in the Results tab (green = PASS, red = FAIL) and \
are written to the CSV file as each test finishes.
8. When the run ends a dialog shows the totals and the CSV path. Use File -> Open results \
folder to get to the file.
"""),

    ("5. Main window", """\
## Menu bar
* File -> Load config...: load a JSON configuration into all fields.
* File -> Save config...: save the current fields as JSON (reusable with -c, GUI or CLI).
* File -> Open results folder: open the folder the CSV is written to.
* File -> Quit: asks for confirmation if tests are running, stops them and restores the DUTs.
* Help -> User manual (F1): this manual.
* Help -> About: version, author and project links.

## Controller server
* Listen: address to bind (0.0.0.0 = all interfaces).
* Port: TCP port the agents connect to (default 5555).
* Token: optional shared secret the agents must present (shown as *).
* Start server / Stop: start or stop listening. Changes to Listen, Port or Token take effect \
the next time the server is started. The server cannot be stopped while tests are running.
* The line below the buttons shows "listening on <addr>:<port>" or "stopped".

## Connected DUTs
The table lists every connected agent:
* Name: agent name (-n). If two different IPs use the same name, the second becomes name@ip.
* IP: address the agent connected from.
* Model: board model reported by the DUT.
* Radios: phy:band for each radio, or "(querying)" while the info is being read.
* Agent: agent version.
* Busy: the command the DUT is executing right now (apply, wait_link, ping, ...), updated every second.

DUT A (AP / mesh 1) and DUT B (STA / mesh 2) choose which DUTs are tested. You can type a name \
or an IP. Refresh info re-reads model and radio capabilities from all DUTs (for example after \
changing the country or firmware on a DUT).

## Test selection, column 1
* Modes: AP(WDS) + STA(WDS) and/or 802.11s Mesh.
* Radios: phy0, phy1, phy2. A radio is tested only if it exists on both DUTs; the same phy name \
is used on both.

## Test selection, column 2
* Bandwidths: HT5, HT10, HT20, HT40, HT80. HT5/HT10 apply to 2.4 GHz only, HT80 to 5 GHz only. \
These are channel widths; the actual uci htmode depends on the PHY mode (section 8).
* PHY modes: HT, VHT (5G), HE, EHT. The matrix is run once per ticked family.

## Test selection, column 3
* 2.4G channels / 5G channels: "auto" (every usable channel) or a comma list, e.g. 1,6,11 or 36,149.
* Country: regulatory country code applied to the radio under test, e.g. US.
* Encryption: none, psk2 or sae.
* Key: passphrase for psk2/sae, at least 8 characters.
* Test subnet: first three octets of the test network. DUT A gets <subnet>.1, DUT B gets \
<subnet>.2 (default 192.168.250). Use a subnet that does not clash with your LAN.

SSID, key and country may contain only A-Z a-z 0-9 . _ - + @ : = , because they are passed to uci \
through the shell.

## Test selection, column 4 (timers and criteria)
* Assoc timeout (sec): time allowed for the link to come up (default 60).
* DFS CAC wait (sec): extra time added on DFS channels for the channel availability check (default 70).
* Ping count: pings per direction (default 10).
* Ping size: ping payload in bytes (default 56).
* Max loss %: highest ping loss that still counts as PASS (default 20).
* Retries: how many times a failed test is repeated before it is recorded as FAIL (default 0).
* Settle (sec): pause after every test (default 3).
* Reconnect wait (sec): how long to wait for a DUT that drops off mid-run before aborting (default 120).

## Test selection, column 5 (options)
* Include DFS channels: also test DFS channels (radar channels, slower because of CAC).
* Swap AP/STA roles too: repeat every AP/STA test with DUT B as AP and DUT A as STA.
* Bidirectional ping: ping B->A and A->B. When off, only B->A is pinged.
* Fail on width mismatch: FAIL if the operating width reported by iw differs from the requested one.
* Restore DUT config at end: restore the DUTs' original wireless/network/firewall config at the \
end of the run. Turn off to leave the DUTs in the last test configuration for debugging.
* Auto-start when DUTs connect: start the run automatically as soon as two DUTs are ready \
(once per program start).
* CSV: output file. {timestamp} is replaced with YYYYmmdd_HHMMSS at start. Use "..." to browse.

## Control bar
* Preview plan: build the test list without touching the DUTs.
* Start tests: build the plan and run it.
* Pause / Resume: pause before the next test starts. The current test always finishes.
* Stop: after confirmation, stop after the current test, tear down and restore the DUTs.
* Progress bar, done/total counter, and running PASS / FAIL counts.

## Tabs
* Results: one row per finished test. Double-click a row to see every field of that row.
* Plan: the test list from the last Preview/Start, with band, frequency, htmode, DFS flag and roles.
* Log: timestamped controller log: connections, plan notes, each test's START / PASS / FAIL, \
retries, restores and errors.

## Status bar
Shows the current state: "ready", "running 5/120: <test>", "paused", plan summary, or the final result.
"""),

    ("6. Running and controlling tests", """\
## Before a run
* Check both DUTs show their radios in the Connected DUTs table (not "(querying)").
* Set the Country. Without it most 5 GHz channels are skipped.
* Use Preview plan and read the Log tab: it lists every skipped combination and why.
* Estimated duration per test is about 25 s + ping time + settle time, plus 60 s on DFS channels.

## During a run
* All selection fields stay editable but changes only apply to the next run.
* Pause takes effect before the next test; Stop takes effect after the current test.
* If a DUT disconnects, the controller waits up to Reconnect wait seconds for it to come back, \
prepares it again and continues. If it does not come back the run is aborted (warning dialog).
* Closing the window while tests run asks for confirmation, then stops the run and restores the DUTs.

## End of a run
A dialog shows "Run completed / stopped / aborted: <reason>", the executed / total count, the \
PASS and FAIL counts and the CSV path. The CSV contains every test that finished, even if the run \
was stopped or aborted.

## Retries
With Retries > 0 a failed test is repeated up to that many times. Only the last attempt is written \
to the CSV; its attempt column shows which attempt it was.
"""),

    ("7. What one test does", """\
1. Prepare (once per DUT per run): the agent backs up /etc/config/wireless, network and firewall \
to /tmp/iw_test_agent_backup, creates bridge br-fpt with the test IP (<subnet>.1 or .2) and an \
ACCEPT firewall zone, and disables the existing wifi-ifaces.
2. Apply: only the radio under test is enabled. channel, htmode, country and (for HT5/HT10) \
chanbw are set. Interface wireless.fpt_iface (ifname fpt0) is created: wds=1 for AP/STA, a \
mesh_id for mesh. Then wifi up.
3. Link:
* AP/STA: the AP must come up first (DFS channels get the extra CAC wait), then the STA must \
report "Connected to" within the assoc timeout. SSID is fpt<test#><phy>.
* Mesh: both nodes are configured in parallel and both must show a peer in mesh plink ESTAB. \
Mesh ID is fptmesh<test#><phy>.
4. Verify: iw dev fpt0 info on both DUTs must show the requested channel and, if Fail on width \
mismatch is ticked, the requested width.
5. Traffic: DUT B pings DUT A, and DUT A pings DUT B if Bidirectional ping is ticked. The loss in \
each direction must be at most Max loss %.
6. Teardown, settle, then the next test.

At the end of the run (or when the controller disconnects, or the agent receives SIGTERM) the \
agent restores the original configuration. A stale backup found when the agent starts is restored too.
"""),

    ("8. Test matrix rules", """\
The plan is the product of mode x radio x band x bandwidth x PHY mode x channel, filtered as follows:
* Bandwidths per band: 2.4 GHz gets HT5, HT10, HT20, HT40; 5 GHz gets HT20, HT40, HT80.
* A channel is used only if it is enabled on both DUTs and is not no-IR on either.
* DFS channels are used only with Include DFS channels. A wide channel is marked DFS if any \
channel it spans is DFS.
* A channel you list that is not usable on both DUTs is skipped and noted in the log.
* HT40 needs HT40 support on both DUTs and a usable secondary channel. On 2.4 GHz with HT the \
planner picks HT40+ or HT40- explicitly; with other families OpenWrt's choice is used. On 5 GHz \
the standard pairing is used (36+40, 44+48, ...).
* HT80 needs VHT support on both DUTs and all four channels of the 80 MHz block \
(36-48, 52-64, 100-112, 116-128, 132-144, 149-161).
* HE and EHT tests need both DUTs to report that capability.

## htmode per PHY mode
    Bandwidth   HT            VHT (5G)   HE      EHT
    HT5/HT10    HT20          HT20       HE20    EHT20     (plus chanbw = 5 or 10)
    HT20        HT20          VHT20      HE20    EHT20
    HT40        HT40+/-       VHT40      HE40    EHT40
    HT80        VHT80         VHT80      HE80    EHT80
On 2.4 GHz VHT falls back to plain HT, and a combination that maps to an htmode already in the \
plan is run only once.

## Roles
* AP/STA: DUT A = ap, DUT B = sta. With Swap AP/STA roles each test is also run reversed.
* Mesh: both DUTs are mesh nodes.
"""),

    ("9. Results and the CSV file", """\
The CSV is written row by row, so it is usable even if the run is interrupted. Default path: \
results/iw-test-controller_{timestamp}.csv (relative to the directory the program was started in).

    Column           Meaning
    test_id          test number in the plan
    timestamp        start time of the test
    mode             ap_sta or mesh
    radio            phy name
    band             2g or 5g
    channel          requested channel
    freq_mhz         centre frequency of the primary channel
    bandwidth        requested width (HT5..HT80)
    htmode           uci htmode sent to the DUT
    dfs              yes / no
    dut1, dut1_role  first DUT and its role (ap / mesh)
    dut2, dut2_role  second DUT and its role (sta / mesh)
    attempt          attempt number (1 + retries used)
    result           PASS or FAIL
    reason           why it failed (empty for PASS), several reasons joined with ;
    link_time_s      time for the link to come up
    dut1_channel     channel reported by iw on dut1
    dut1_width       width (MHz) reported by iw on dut1
    dut2_channel     channel reported by iw on dut2
    dut2_width       width (MHz) reported by iw on dut2
    signal_dbm       signal seen by the STA (or mesh node 2)
    tx_bitrate       tx bitrate seen by the STA (or mesh node 2)
    ping_tx, ping_rx packets sent / received, dut2 -> dut1
    loss_pct         ping loss %, dut2 -> dut1
    rtt_avg_ms       average RTT, dut2 -> dut1
    rev_loss_pct     ping loss %, dut1 -> dut2 (bidirectional only)
    rev_rtt_avg_ms   average RTT, dut1 -> dut2 (bidirectional only)
    duration_s       total time of the test
"""),

    ("10. Configuration files", """\
File -> Save config writes every setting to JSON. Load it with File -> Load config or with -c on \
the command line (CLI options override values from the file). Unknown keys are rejected. \
example_config.json in the repository is a complete example.

    Key                Default                                      GUI field / CLI option
    listen             0.0.0.0                                      Listen / --listen
    port               5555                                         Port / --port
    token              ""                                           Token / --token
    dut_a, dut_b       "" (first two DUTs by name)                  DUT A, DUT B / --dut-a, --dut-b
    wait_duts          2                                            - / --wait-duts
    connect_timeout    0 (wait forever)                             - / --connect-timeout
    reconnect_timeout  120                                          Reconnect wait / --reconnect-timeout
    modes              ["ap_sta", "mesh"]                           Modes / --modes
    radios             ["phy0", "phy1", "phy2"]                     Radios / --radios
    bandwidths         ["HT20", "HT40", "HT80", "HT10", "HT5"]      Bandwidths / --bandwidths
    phy_modes          ["ht"]                                       PHY modes / --phy-modes
    channels_2g        "auto"                                       2.4G channels / --channels-2g
    channels_5g        "auto"                                       5G channels / --channels-5g
    include_dfs        false                                        Include DFS / --include-dfs
    dfs_wait           70                                           DFS CAC wait / --dfs-wait
    swap_roles         false                                        Swap roles / --swap-roles
    encryption         "none"                                       Encryption / --encryption
    key                ""                                           Key / --key
    country            ""                                           Country / --country
    subnet             "192.168.250"                                Test subnet / --subnet
    assoc_timeout      60                                           Assoc timeout / --assoc-timeout
    ping_count         10                                           Ping count / --ping-count
    ping_size          56                                           Ping size / --ping-size
    max_loss           20.0                                         Max loss % / --max-loss
    bidirectional      true                                         Bidirectional / --no-bidirectional
    verify_width       true                                         Width mismatch / --no-verify-width
    retries            0                                            Retries / --retries
    settle_time        3                                            Settle / --settle-time
    restore_at_end     true                                         Restore config / --no-restore
    output             "results/iw-test-controller_{timestamp}.csv" CSV / -o, --output
    auto_start         false                                        Auto-start / --auto-start

The token and key are stored in plain text in the JSON file.
"""),

    ("11. Command line and headless mode", """\
Every GUI setting has a command-line option (section 10). Without --headless the options \
pre-fill the GUI.

## Program options
    --headless           run without the GUI
    -c, --config PATH    load settings from a JSON file
    --save-config PATH   write the effective configuration to PATH and exit
    --dry-run            wait for the DUTs, print the plan and exit (implies headless)
    -q, --quiet          headless: print only the result lines
    --version            print the version
    -h, --help           list every option

## Headless behaviour
* Waits for DUT A and DUT B (or, if not given, for --wait-duts DUTs and takes the first two by \
name). --connect-timeout limits the wait; 0 waits forever.
* Prints one line per test: [n/total] PASS/FAIL #id mode radio channel bandwidth duts loss rtt reason.
* Ctrl-C once stops after the current test and restores the DUTs; Ctrl-C again quits immediately.

## Exit codes
    0   all tests passed
    1   some tests failed, or the run was stopped
    2   setup error (bad option, cannot listen, DUTs not connected, empty plan) or run aborted

## Examples
    ./iw-test-controller.py --headless --country US
    ./iw-test-controller.py --headless --dut-a dut1 --dut-b dut2 \\
        --modes ap_sta --radios phy0 --bandwidths HT20,HT80 --channels-5g 36,149 -o ap5g.csv
    ./iw-test-controller.py --headless --modes mesh --encryption sae --key testkey123 --country DE
    ./iw-test-controller.py --headless --phy-modes ht,he --include-dfs --country US
    ./iw-test-controller.py --headless --dry-run --swap-roles
    ./iw-test-controller.py -c example_config.json --headless
    ./iw-test-controller.py -c base.json --country IN --save-config india.json
"""),

    ("12. Simulation mode", """\
You can try the full GUI and CLI flow on the laptop without hardware. Build the agent natively \
and start two simulated agents:
    iw-test-agent -s 127.0.0.1 -n dut1 -S &
    iw-test-agent -s 127.0.0.1 -n dut2 -S &
The log shows "SIMULATION" when a simulated agent connects.
"""),

    ("13. Troubleshooting", """\
## DUT does not appear in Connected DUTs
* Check the agent points to the laptop's IP (-s) and the port matches.
* Check the laptop firewall allows TCP 5555 in.
* Check the Log tab for "rejected <ip>: bad token" (token mismatch).
* Check the agent's own log (-l /tmp/iw-test-agent.log).

## "the plan is empty" or many skipped combinations
* Set the Country. "no usable channels (no-IR/DFS) - set --country?" means every channel was \
no-IR or DFS.
* "<phy> missing on <dut>": the radio does not exist on that DUT. Untick it.
* "channel N not usable on both DUTs": the channel is disabled, no-IR or DFS on one DUT.
* "HT40 / 80 MHz (VHT) / HE / EHT not supported by both DUTs": capability missing on a DUT.
* "chN: HT40/HT80 not possible": the secondary channel or the 80 MHz block is not usable.

## Common FAIL reasons
* "AP start: ..." - the AP interface did not come up (check radio, htmode support, DFS radar).
* "STA association: ..." - the STA did not connect within Assoc timeout. Try a longer timeout, \
check encryption support (sae needs wpad with SAE).
* "mesh peering <dut>: ..." - no ESTAB peer. For sae mesh install wpad-mesh-*.
* "<dut> on channel X, expected Y" - the driver moved to another channel.
* "<dut> width W MHz, expected V" - the driver used a different width. HT5/HT10 on ath11k \
typically fail with "width 20 MHz, expected 5": upstream ath11k/mac80211 does not implement \
5/10 MHz, so this is a real finding. Untick Fail on width mismatch to ignore width.
* "ping A->B loss N%" - loss above Max loss %. Check the test subnet does not clash with the LAN.
* "<dut>: '<op>' timed out after Ns" - the agent did not answer in time.

## Run aborted
* "DUT '<name>' is not connected": the DUT did not reconnect within Reconnect wait.
* "prepare on <dut> failed": the agent could not back up or set up the test network.

## DUT left in test configuration
Start the agent again: a stale backup in /tmp/iw_test_agent_backup is restored at start-up. \
If Restore DUT config at end was off, the DUT stays in the last test configuration on purpose.

## GUI does not start
"GUI unavailable ... Install python3-tk or use --headless": install Tk or run headless.
"""),
]
