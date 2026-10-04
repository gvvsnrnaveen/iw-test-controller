"""Executes a test plan sequentially on two DUTs and writes results to CSV."""

import csv
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .server import DutError

CSV_COLUMNS = [
    "test_id", "timestamp", "mode", "radio", "band", "channel", "freq_mhz", "bandwidth",
    "htmode", "dfs", "dut1", "dut1_role", "dut2", "dut2_role", "attempt", "result", "reason",
    "link_time_s", "dut1_channel", "dut1_width", "dut2_channel", "dut2_width",
    "signal_dbm", "tx_bitrate", "ping_tx", "ping_rx", "loss_pct", "rtt_avg_ms",
    "rev_loss_pct", "rev_rtt_avg_ms", "duration_s",
]


class TestFail(Exception):
    pass


class TestRunner(threading.Thread):
    def __init__(self, server, cfg, plan, dut_a, dut_b, csv_path, emit):
        super().__init__(daemon=True, name="runner")
        self.server = server
        self.cfg = cfg
        self.plan = plan
        self.dut_a = dut_a
        self.dut_b = dut_b
        self.csv_path = csv_path
        self.emit = emit
        self.ips = {dut_a: "%s.1" % cfg["subnet"], dut_b: "%s.2" % cfg["subnet"]}
        self.results = []
        self._stop_ev = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._prepared = {}           # name -> DutConnection that was prepared
        self._pool = ThreadPoolExecutor(max_workers=2)

    # ---------------------------------------------------------- control
    def stop(self):
        self._stop_ev.set()
        self._resume.set()

    def pause(self):
        self._resume.clear()
        self.emit("state", state="paused")

    def resume(self):
        self._resume.set()
        self.emit("state", state="running")

    @property
    def paused(self):
        return not self._resume.is_set()

    def log(self, msg):
        self.emit("log", msg=msg)

    # ------------------------------------------------------------- rpc
    def _conn(self, name):
        conn = self.server.get(name)
        if conn is not None and conn.alive:
            return conn
        self.log("waiting up to %ds for DUT '%s' to reconnect" % (self.cfg["reconnect_timeout"], name))
        deadline = time.time() + self.cfg["reconnect_timeout"]
        while time.time() < deadline and not self._stop_ev.is_set():
            conn = self.server.get(name)
            if conn is not None and conn.alive and conn.info:
                return conn
            time.sleep(1)
        raise DutError("DUT '%s' is not connected" % name)

    def _call(self, name, op, rpc_timeout, **params):
        conn = self._conn(name)
        if op != "prepare" and self._prepared.get(name) is not conn:
            self._prepare(name)
            conn = self._conn(name)
        return conn.call(op, rpc_timeout=rpc_timeout, **params)

    def _prepare(self, name):
        conn = self._conn(name)
        res = conn.call("prepare", rpc_timeout=120, ip=self.ips[name], netmask="255.255.255.0", firewall=1)
        if not res.get("ok"):
            raise DutError("prepare on %s failed: %s" % (name, res.get("error")))
        self._prepared[name] = conn
        self.log("prepared %s (test ip %s)" % (name, self.ips[name]))

    def _both(self, fn_a, fn_b):
        fa, fb = self._pool.submit(fn_a), self._pool.submit(fn_b)
        return fa.result(), fb.result()

    @staticmethod
    def _require(res, what):
        if not res.get("ok"):
            raise TestFail("%s: %s" % (what, res.get("error", "failed")))
        return res

    # -------------------------------------------------------------- run
    def run(self):
        self.emit("state", state="running")
        total = len(self.plan)
        passed = failed = 0
        os.makedirs(os.path.dirname(os.path.abspath(self.csv_path)), exist_ok=True)
        f = open(self.csv_path, "w", newline="")
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        f.flush()
        self.log("writing results to %s" % self.csv_path)
        aborted = None
        try:
            self._both(lambda: self._prepare(self.dut_a), lambda: self._prepare(self.dut_b))
            for n, tc in enumerate(self.plan, 1):
                self._resume.wait()
                if self._stop_ev.is_set():
                    break
                self.emit("test_start", test=tc, index=n, total=total)
                row = None
                for attempt in range(1, self.cfg["retries"] + 2):
                    row = self._run_one(tc, attempt)
                    if row["result"] == "PASS" or self._stop_ev.is_set():
                        break
                    if attempt <= self.cfg["retries"]:
                        self.log("retrying %s (%s)" % (tc.label(), row["reason"]))
                writer.writerow(row)
                f.flush()
                self.results.append(row)
                if row["result"] == "PASS":
                    passed += 1
                else:
                    failed += 1
                self.emit("test_result", row=row, index=n, total=total, passed=passed, failed=failed)
                for name in (self.dut_a, self.dut_b):
                    conn = self.server.get(name)
                    if conn is None or not conn.alive:
                        # give it a chance to come back; raises DutError (aborts run) if it doesn't
                        self._conn(name)
        except DutError as e:
            aborted = str(e)
            self.log("ABORTED: %s" % e)
        except Exception as e:  # keep the GUI alive on unexpected errors
            aborted = "internal error: %r" % e
            self.log("ABORTED: %r" % e)
        finally:
            f.close()
            self._cleanup()
            self._pool.shutdown(wait=False)
            stopped = self._stop_ev.is_set()
            self.emit("finished", passed=passed, failed=failed, total=total,
                      executed=passed + failed, stopped=stopped, aborted=aborted,
                      csv=self.csv_path)
            self.emit("state", state="idle")

    def _cleanup(self):
        for name in (self.dut_a, self.dut_b):
            conn = self.server.get(name)
            if conn is None or not conn.alive:
                continue
            try:
                conn.call("teardown", rpc_timeout=60)
                if self.cfg["restore_at_end"]:
                    conn.call("restore", rpc_timeout=180)
                    self.log("restored original configuration on %s" % name)
            except DutError as e:
                self.log("cleanup on %s failed: %s" % (name, e))

    def _run_one(self, tc, attempt):
        t0 = time.time()
        row = {k: "" for k in CSV_COLUMNS}
        row.update({
            "test_id": tc.idx, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": tc.mode, "radio": tc.radio, "band": tc.band, "channel": tc.channel,
            "freq_mhz": tc.freq, "bandwidth": tc.bandwidth, "htmode": tc.htmode,
            "dfs": "yes" if tc.dfs else "no", "dut1": tc.dut1, "dut1_role": tc.role1,
            "dut2": tc.dut2, "dut2_role": tc.role2, "attempt": attempt,
        })
        self.log("START %s (attempt %d)" % (tc.label(), attempt))
        reasons = []
        try:
            if tc.mode == "ap_sta":
                self._test_ap_sta(tc, row, reasons)
            else:
                self._test_mesh(tc, row, reasons)
        except TestFail as e:
            reasons.append(str(e))
        except DutError as e:
            reasons.append(str(e))
        finally:
            for name in (tc.dut1, tc.dut2):
                conn = self.server.get(name)
                if conn is None or not conn.alive or self._prepared.get(name) is not conn:
                    continue
                try:
                    conn.call("teardown", rpc_timeout=60)
                except DutError:
                    pass
        row["result"] = "FAIL" if reasons else "PASS"
        row["reason"] = "; ".join(reasons)
        row["duration_s"] = round(time.time() - t0, 1)
        self.log("%s %s %s" % (row["result"], tc.label(), row["reason"]))
        if not self._stop_ev.is_set():
            time.sleep(self.cfg["settle_time"])
        return row

    def _apply_params(self, tc):
        return dict(phy=tc.radio, channel=tc.channel, htmode=tc.htmode, chanbw=tc.chanbw,
                    encryption=self.cfg["encryption"], key=self.cfg["key"],
                    country=self.cfg["country"])

    def _link_timeout(self, tc):
        return self.cfg["assoc_timeout"] + (self.cfg["dfs_wait"] if tc.dfs else 0)

    def _test_ap_sta(self, tc, row, reasons):
        ap, sta = tc.dut1, tc.dut2
        ssid = "fpt%d%s" % (tc.idx, tc.radio)
        p = self._apply_params(tc)
        lt = self._link_timeout(tc)

        self._require(self._call(ap, "apply", 120, mode="ap", ssid=ssid, wds=1, **p), "AP apply")
        self._require(self._call(ap, "wait_link", lt + 20, mode="ap", timeout=lt, min_peers=0),
                      "AP start")
        self._require(self._call(sta, "apply", 120, mode="sta", ssid=ssid, wds=1, **p), "STA apply")
        link = self._require(self._call(sta, "wait_link", self.cfg["assoc_timeout"] + 20, mode="sta",
                                        timeout=self.cfg["assoc_timeout"]), "STA association")
        row["link_time_s"] = round(link.get("link_time", 0), 1)
        row["signal_dbm"] = link.get("signal_dbm", "")
        row["tx_bitrate"] = link.get("tx_bitrate", "")
        self._verify_and_ping(tc, row, reasons)

    def _test_mesh(self, tc, row, reasons):
        a, b = tc.dut1, tc.dut2
        mesh_id = "fptmesh%d%s" % (tc.idx, tc.radio)
        p = self._apply_params(tc)
        lt = self._link_timeout(tc)

        ra, rb = self._both(lambda: self._call(a, "apply", 120, mode="mesh", ssid=mesh_id, **p),
                            lambda: self._call(b, "apply", 120, mode="mesh", ssid=mesh_id, **p))
        self._require(ra, "mesh apply %s" % a)
        self._require(rb, "mesh apply %s" % b)
        la, lb = self._both(lambda: self._call(a, "wait_link", lt + 20, mode="mesh", timeout=lt),
                            lambda: self._call(b, "wait_link", lt + 20, mode="mesh", timeout=lt))
        self._require(la, "mesh peering %s" % a)
        self._require(lb, "mesh peering %s" % b)
        row["link_time_s"] = round(max(la.get("link_time", 0), lb.get("link_time", 0)), 1)
        row["signal_dbm"] = lb.get("signal_dbm", "")
        row["tx_bitrate"] = lb.get("tx_bitrate", "")
        self._verify_and_ping(tc, row, reasons)

    def _verify_and_ping(self, tc, row, reasons):
        d1, d2 = tc.dut1, tc.dut2
        s1, s2 = self._both(lambda: self._call(d1, "status", 30), lambda: self._call(d2, "status", 30))
        for tag, st, name in (("dut1", s1, d1), ("dut2", s2, d2)):
            ch, width = st.get("actual_channel", 0), st.get("actual_width", 0)
            row["%s_channel" % tag] = ch
            row["%s_width" % tag] = width
            if not st.get("ok"):
                reasons.append("%s status: %s" % (name, st.get("error")))
                continue
            if ch != tc.channel:
                reasons.append("%s on channel %s, expected %d" % (name, ch, tc.channel))
            if self.cfg["verify_width"] and width != tc.width_mhz:
                reasons.append("%s width %s MHz, expected %d" % (name, width, tc.width_mhz))

        count, size = self.cfg["ping_count"], self.cfg["ping_size"]
        pt = count * 2 + 20
        fwd = self._require(self._call(d2, "ping", pt, target=self.ips[d1], count=count, size=size),
                            "ping %s->%s" % (d2, d1))
        row["ping_tx"], row["ping_rx"] = fwd.get("ping_tx"), fwd.get("ping_rx")
        row["loss_pct"] = round(fwd.get("loss_pct", 100.0), 1)
        row["rtt_avg_ms"] = round(fwd.get("rtt_avg_ms", 0.0), 2)
        if row["loss_pct"] > self.cfg["max_loss"]:
            reasons.append("ping %s->%s loss %.1f%%" % (d2, d1, row["loss_pct"]))
        if self.cfg["bidirectional"]:
            rev = self._require(self._call(d1, "ping", pt, target=self.ips[d2], count=count, size=size),
                                "ping %s->%s" % (d1, d2))
            row["rev_loss_pct"] = round(rev.get("loss_pct", 100.0), 1)
            row["rev_rtt_avg_ms"] = round(rev.get("rtt_avg_ms", 0.0), 2)
            if row["rev_loss_pct"] > self.cfg["max_loss"]:
                reasons.append("ping %s->%s loss %.1f%%" % (d1, d2, row["rev_loss_pct"]))
