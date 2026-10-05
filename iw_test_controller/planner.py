"""Builds the ordered list of test cases from the config and DUT capabilities."""

from dataclasses import dataclass, asdict

from .config import parse_channel_list

BAND_BWS = {"2g": ["HT5", "HT10", "HT20", "HT40"], "5g": ["HT20", "HT40", "HT80"]}
BW_MHZ = {"HT5": 5, "HT10": 10, "HT20": 20, "HT40": 40, "HT80": 80}
VHT80_BLOCKS = [36, 52, 100, 116, 132, 149]   # first channel of each 80 MHz block


@dataclass
class TestCase:
    idx: int
    mode: str          # ap_sta | mesh
    radio: str         # phy name (same on both DUTs)
    band: str          # 2g | 5g
    channel: int
    freq: int
    bandwidth: str     # HT5..HT80 (as requested)
    htmode: str        # uci htmode value sent to the DUT
    chanbw: int        # 5/10 for narrowband, else 20
    dfs: bool
    dut1: str
    role1: str         # ap | mesh
    dut2: str
    role2: str         # sta | mesh

    @property
    def width_mhz(self):
        return BW_MHZ[self.bandwidth]

    def label(self):
        return "#%d %s %s ch%d %s %s(%s)/%s(%s)" % (
            self.idx, self.mode, self.radio, self.channel, self.bandwidth,
            self.dut1, self.role1, self.dut2, self.role2)

    def as_dict(self):
        return asdict(self)


def _htmode(band, bw, phy_mode, channel, avail):
    """Return (htmode, chanbw) or None if the width cannot be used on this channel."""
    fam20 = {"ht": "HT", "vht": "VHT" if band == "5g" else "HT", "he": "HE", "eht": "EHT"}[phy_mode]
    if bw in ("HT5", "HT10"):
        # narrowband is plain 11g: htmode=HT5/HT10 + hwmode=11g, whatever the family
        return bw, BW_MHZ[bw]
    if bw == "HT20":
        return fam20 + "20", 20
    if bw == "HT40":
        if band == "2g":
            # OpenWrt picks HT40+ below channel 7 and HT40- otherwise for auto modes;
            # with plain HT we choose explicitly so more channels qualify.
            if fam20 == "HT":
                if channel + 4 in avail and (channel < 7 or channel - 4 not in avail):
                    return "HT40+", 20
                if channel - 4 in avail:
                    return "HT40-", 20
                return None
            second = channel + 4 if channel < 7 else channel - 4
            return (fam20 + "40", 20) if second in avail else None
        second = channel + 4 if (channel // 4) % 2 == 1 else channel - 4
        return (fam20 + "40", 20) if second in avail else None
    if bw == "HT80":
        if band != "5g":
            return None
        for start in VHT80_BLOCKS:
            block = [start + 4 * i for i in range(4)]
            if channel in block:
                if all(c in avail for c in block):
                    return ("VHT80" if fam20 == "HT" else fam20 + "80"), 20
                return None
        return None
    return None


def _span(band, bw, htmode, channel):
    """Channels occupied by a configuration (to inherit DFS flags)."""
    if bw == "HT40":
        if band == "2g":
            plus = htmode.endswith("+") or (not htmode.endswith("-") and channel < 7)
            return [channel, channel + 4 if plus else channel - 4]
        return [channel, channel + 4 if (channel // 4) % 2 == 1 else channel - 4]
    if bw == "HT80":
        for start in VHT80_BLOCKS:
            if start <= channel < start + 16:
                return [start + 4 * i for i in range(4)]
    return [channel]


def build_plan(cfg, dut_a, dut_b, radios_a, radios_b):
    """Return (tests, notes). notes explains everything that was skipped."""
    tests, notes = [], []
    seen = set()   # e.g. ht and vht both map to HT20 on 2.4 GHz - run it once
    user_chans = {"2g": parse_channel_list(cfg["channels_2g"]),
                  "5g": parse_channel_list(cfg["channels_5g"])}

    for mode in cfg["modes"]:
        for radio in cfg["radios"]:
            ra, rb = radios_a.get(radio), radios_b.get(radio)
            if not ra or not rb:
                notes.append("%s: %s missing on %s" % (mode, radio, dut_a if not ra else dut_b))
                continue
            for band in ("2g", "5g"):
                ca, cb = ra["chans"][band], rb["chans"][band]
                common = sorted(set(ca) & set(cb))
                if not common:
                    continue
                # usable = enabled on both, IR allowed, DFS only if requested
                usable = {}
                for ch in common:
                    a, b = ca[ch], cb[ch]
                    if a["no_ir"] or b["no_ir"]:
                        continue
                    dfs = a["dfs"] or b["dfs"]
                    if dfs and not cfg["include_dfs"]:
                        continue
                    usable[ch] = {"freq": a["freq"], "dfs": dfs}
                if not usable:
                    notes.append("%s %s %s: no usable channels (no-IR/DFS) - set --country?"
                                 % (mode, radio, band))
                    continue
                wanted = user_chans[band]
                if wanted is None:
                    chans = sorted(usable)
                else:
                    chans = [c for c in wanted if c in usable]
                    for c in wanted:
                        if c not in usable:
                            notes.append("%s %s: channel %d not usable on both DUTs" % (radio, band, c))
                for bw in cfg["bandwidths"]:
                    if bw not in BAND_BWS[band]:
                        continue
                    if bw == "HT40" and not (ra["ht40"] and rb["ht40"]):
                        notes.append("%s: HT40 not supported by both DUTs" % radio)
                        continue
                    if bw == "HT80" and not (ra["vht"] and rb["vht"]):
                        notes.append("%s: 80 MHz (VHT) not supported by both DUTs" % radio)
                        continue
                    for phy_mode in cfg["phy_modes"]:
                        if phy_mode in ("he", "eht") and not (ra.get(phy_mode) and rb.get(phy_mode)):
                            notes.append("%s %s: %s not supported by both DUTs"
                                         % (radio, band, phy_mode.upper()))
                            continue
                        for ch in chans:
                            hm = _htmode(band, bw, phy_mode, ch, usable)
                            if hm is None:
                                notes.append("%s %s ch%d: %s not possible" % (mode, radio, ch, bw))
                                continue
                            htmode, chanbw = hm
                            if (mode, radio, ch, bw, htmode) in seen:
                                continue
                            seen.add((mode, radio, ch, bw, htmode))
                            dfs = any(usable.get(c, {}).get("dfs") for c in _span(band, bw, htmode, ch))
                            if mode == "ap_sta":
                                pairs = [(dut_a, "ap", dut_b, "sta")]
                                if cfg["swap_roles"]:
                                    pairs.append((dut_b, "ap", dut_a, "sta"))
                            else:
                                pairs = [(dut_a, "mesh", dut_b, "mesh")]
                            for d1, r1, d2, r2 in pairs:
                                tests.append(TestCase(len(tests) + 1, mode, radio, band, ch,
                                                      usable[ch]["freq"], bw, htmode, chanbw, dfs,
                                                      d1, r1, d2, r2))
    return tests, notes
