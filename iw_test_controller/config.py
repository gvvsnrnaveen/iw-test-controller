"""Test configuration: defaults, JSON load/save and CLI argument mapping."""

import copy
import json
import time

MODES = ["ap_sta", "mesh"]
MODE_LABELS = {"ap_sta": "AP(WDS) + STA(WDS)", "mesh": "802.11s Mesh"}
RADIOS = ["phy0", "phy1", "phy2"]
BANDWIDTHS = ["HT5", "HT10", "HT20", "HT40", "HT80"]
PHY_MODES = ["ht", "vht", "he", "eht"]
PHY_LABELS = {"ht": "HT", "vht": "VHT (5G)", "he": "HE", "eht": "EHT"}
ENCRYPTIONS = ["none", "psk2", "sae"]

DEFAULTS = {
    # controller server
    "listen": "0.0.0.0",
    "port": 5555,
    "token": "",
    # DUT selection ("" = first two connected DUTs, sorted by name)
    "dut_a": "",
    "dut_b": "",
    "wait_duts": 2,
    "connect_timeout": 0,          # seconds, 0 = wait forever (headless)
    "reconnect_timeout": 120,      # wait for a DUT that drops mid-run
    # test matrix
    "modes": list(MODES),
    "radios": list(RADIOS),
    "bandwidths": ["HT20", "HT40", "HT80", "HT10", "HT5"],
    "channels_2g": "auto",         # "auto" or "1,6,11"
    "channels_5g": "auto",         # "auto" or "36,40,149"
    "include_dfs": False,
    "dfs_wait": 70,                # extra seconds for CAC on DFS channels
    "swap_roles": False,           # also run AP/STA with roles reversed
    "phy_modes": ["ht"],           # uci htmode families to test: ht, vht, he, eht
    # link parameters
    "encryption": "none",
    "key": "",
    "country": "",
    "subnet": "192.168.250",       # DUT A = .1, DUT B = .2
    # pass/fail criteria
    "assoc_timeout": 60,
    "ping_count": 10,
    "ping_size": 56,
    "max_loss": 20.0,
    "bidirectional": True,
    "verify_width": True,
    "retries": 0,
    "settle_time": 3,
    "restore_at_end": True,
    # output
    "output": "results/iw-test-controller_{timestamp}.csv",
    "auto_start": False,
}


def default_config():
    return copy.deepcopy(DEFAULTS)


def load_config(path):
    cfg = default_config()
    with open(path) as f:
        data = json.load(f)
    unknown = set(data) - set(DEFAULTS) - {"phy_mode"}
    if unknown:
        raise ValueError("unknown config keys: %s" % ", ".join(sorted(unknown)))
    if "phy_mode" in data:          # legacy single-value key
        data.setdefault("phy_modes", [data["phy_mode"]])
        del data["phy_mode"]
    cfg.update(data)
    return cfg


def save_config(cfg, path):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)


def output_path(cfg):
    return cfg["output"].replace("{timestamp}", time.strftime("%Y%m%d_%H%M%S"))


def _csv_list(value, allowed, name):
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise ValueError("invalid %s: %s (allowed: %s)" % (name, ",".join(bad), ",".join(allowed)))
    return items


def parse_channel_list(value):
    """'auto' -> None, '1,6,11' -> [1, 6, 11]."""
    value = (value or "auto").strip().lower()
    if value in ("", "auto", "all"):
        return None
    return [int(v) for v in value.replace(" ", "").split(",") if v]


def validate(cfg):
    errs = []
    if not cfg["modes"]:
        errs.append("select at least one test mode")
    if not cfg["radios"]:
        errs.append("select at least one radio")
    if not cfg["bandwidths"]:
        errs.append("select at least one bandwidth")
    if not cfg["phy_modes"]:
        errs.append("select at least one PHY mode")
    bad = [m for m in cfg["phy_modes"] if m not in PHY_MODES]
    if bad:
        errs.append("invalid phy_modes: %s (allowed: %s)" % (",".join(bad), ",".join(PHY_MODES)))
    if cfg["encryption"] != "none" and len(cfg["key"]) < 8:
        errs.append("key must be at least 8 characters for %s" % cfg["encryption"])
    for k in ("channels_2g", "channels_5g"):
        try:
            parse_channel_list(cfg[k])
        except ValueError:
            errs.append("%s must be 'auto' or a comma separated channel list" % k)
    parts = cfg["subnet"].split(".")
    if len(parts) != 3 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        errs.append("subnet must be the first three octets, e.g. 192.168.250")
    if cfg["dut_a"] and cfg["dut_a"] == cfg["dut_b"]:
        errs.append("DUT A and DUT B must be different")
    return errs


def add_cli_args(p):
    """Register test-configuration options (defaults None -> keep config value)."""
    g = p.add_argument_group("server")
    g.add_argument("--listen", help="listen address (default %(default)s)" % {"default": DEFAULTS["listen"]})
    g.add_argument("--port", type=int, help="listen port (default %d)" % DEFAULTS["port"])
    g.add_argument("--token", help="shared token DUT agents must present")

    g = p.add_argument_group("DUTs")
    g.add_argument("--dut-a", help="name or IP of DUT A (AP / first mesh node)")
    g.add_argument("--dut-b", help="name or IP of DUT B (STA / second mesh node)")
    g.add_argument("--wait-duts", type=int, help="number of DUTs to wait for (default 2)")
    g.add_argument("--connect-timeout", type=int, help="seconds to wait for DUTs, 0 = forever")
    g.add_argument("--reconnect-timeout", type=int, help="seconds to wait for a dropped DUT")

    g = p.add_argument_group("test matrix")
    g.add_argument("--modes", help="comma list of: %s" % ",".join(MODES))
    g.add_argument("--radios", help="comma list of: %s" % ",".join(RADIOS))
    g.add_argument("--bandwidths", help="comma list of: %s (HT5/HT10 2.4 GHz only, HT80 5 GHz only)"
                   % ",".join(BANDWIDTHS))
    g.add_argument("--channels-2g", help="'auto' or comma list, e.g. 1,6,11")
    g.add_argument("--channels-5g", help="'auto' or comma list, e.g. 36,44,149")
    g.add_argument("--include-dfs", action="store_true", default=None, help="include DFS channels")
    g.add_argument("--dfs-wait", type=int, help="extra seconds for DFS CAC (default 70)")
    g.add_argument("--swap-roles", action="store_true", default=None,
                   help="repeat AP/STA tests with DUT roles swapped")
    g.add_argument("--phy-modes", "--phy-mode", dest="phy_modes",
                   help="comma list of htmode families: %s (ht uses VHT80 for 80 MHz, vht is 5 GHz only)"
                   % ",".join(PHY_MODES))

    g = p.add_argument_group("link")
    g.add_argument("--encryption", choices=ENCRYPTIONS)
    g.add_argument("--key", help="passphrase for psk2/sae (letters, digits, ._-+@:=,)")
    g.add_argument("--country", help="regulatory country code, e.g. US, IN, DE")
    g.add_argument("--subnet", help="test subnet first three octets (default 192.168.250)")

    g = p.add_argument_group("criteria")
    g.add_argument("--assoc-timeout", type=int, help="seconds to wait for link (default 60)")
    g.add_argument("--ping-count", type=int, help="pings per direction (default 10)")
    g.add_argument("--ping-size", type=int, help="ping payload bytes (default 56)")
    g.add_argument("--max-loss", type=float, help="max ping loss %% for PASS (default 20)")
    g.add_argument("--no-bidirectional", dest="bidirectional", action="store_false", default=None,
                   help="ping only DUT B -> DUT A")
    g.add_argument("--no-verify-width", dest="verify_width", action="store_false", default=None,
                   help="do not fail when the operating width differs from the requested one")
    g.add_argument("--retries", type=int, help="retries for a failed test (default 0)")
    g.add_argument("--settle-time", type=int, help="seconds to pause between tests")
    g.add_argument("--no-restore", dest="restore_at_end", action="store_false", default=None,
                   help="leave DUTs in test config at the end")

    g = p.add_argument_group("output")
    g.add_argument("-o", "--output", help="CSV path, {timestamp} is expanded")
    g.add_argument("--auto-start", action="store_true", default=None,
                   help="GUI: start automatically once the DUTs are connected")


def apply_cli(cfg, args):
    list_opts = {"modes": MODES, "radios": RADIOS, "bandwidths": BANDWIDTHS, "phy_modes": PHY_MODES}
    for key in DEFAULTS:
        val = getattr(args, key, None)
        if val is None:
            continue
        if key in list_opts:
            val = _csv_list(val, list_opts[key], key)
        cfg[key] = val
    return cfg
