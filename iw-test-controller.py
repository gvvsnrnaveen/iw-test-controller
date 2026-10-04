#!/usr/bin/env python3
"""iw-test-controller - runs on the Linux Client Device connected to the LAN side of the DUTs.

GUI (default):   ./iw-test-controller.py
Headless CLI:    ./iw-test-controller.py --headless --dut-a dut1 --dut-b dut2 -o results.csv

DUT agents connect to this program (iw-test-agent -s <controller-ip>).
Run the controller preferably on ethernet
"""

import argparse
import signal
import sys
import threading
import time

from iw_test_controller import __version__
from iw_test_controller.config import (add_cli_args, apply_cli, default_config, load_config, output_path,
                             save_config, validate)
from iw_test_controller.planner import build_plan
from iw_test_controller.runner import TestRunner
from iw_test_controller.server import ControllerServer


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="iw-test-controller: Wi-Fi test controller (AP/STA WDS + 802.11s mesh on two DUTs)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  %(prog)s                                   # GUI\n"
               "  %(prog)s --headless                        # wait for 2 DUTs, run everything\n"
               "  %(prog)s --headless --modes mesh --radios phy0 --bandwidths HT20,HT80 \\\n"
               "           --channels-5g 36,149 --country US -o mesh5g.csv\n"
               "  %(prog)s --headless --dry-run              # print the plan only\n")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--headless", action="store_true", help="run without GUI (CLI mode)")
    p.add_argument("-c", "--config", help="load settings from JSON file (CLI options override)")
    p.add_argument("--save-config", metavar="PATH", help="write the effective config to PATH and exit")
    p.add_argument("--dry-run", action="store_true", help="headless: print the test plan and exit")
    p.add_argument("-q", "--quiet", action="store_true", help="headless: only print results")
    add_cli_args(p)
    return p.parse_args(argv)


class Headless:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.runner = None
        self.lock = threading.Lock()

    def emit(self, kind, **kw):
        with self.lock:
            ts = time.strftime("%H:%M:%S")
            if kind == "log" and not self.args.quiet:
                print("%s  %s" % (ts, kw["msg"]), flush=True)
            elif kind == "test_result":
                r = kw["row"]
                print("%s  [%d/%d] %-4s #%s %s %s ch%s %s %s/%s loss=%s%% rtt=%sms %s" % (
                    ts, kw["index"], kw["total"], r["result"], r["test_id"], r["mode"], r["radio"],
                    r["channel"], r["bandwidth"], r["dut1"], r["dut2"], r["loss_pct"],
                    r["rtt_avg_ms"], r["reason"]), flush=True)

    def wait_duts(self, server):
        cfg = self.cfg
        deadline = time.time() + cfg["connect_timeout"] if cfg["connect_timeout"] else None
        want = [n for n in (cfg["dut_a"], cfg["dut_b"]) if n]
        print("waiting for %s ..." % (", ".join(want) if want else "%d DUT agents" % cfg["wait_duts"]),
              flush=True)
        while deadline is None or time.time() < deadline:
            ready = server.ready_names()
            if want:
                conns = [server.get(n) for n in want]
                if all(c is not None and c.info for c in conns):
                    if len(want) == 2:
                        return conns[0], conns[1]
                    others = [server.get(n) for n in ready if server.get(n) is not conns[0]]
                    if others:
                        return conns[0], others[0]
            elif len(ready) >= max(2, cfg["wait_duts"]):
                return server.get(ready[0]), server.get(ready[1])
            time.sleep(0.5)
        return None, None

    def run(self):
        cfg = self.cfg
        server = ControllerServer(cfg["listen"], cfg["port"], cfg["token"], emit=self.emit)
        try:
            server.start()
        except OSError as e:
            print("error: cannot listen on %s:%d: %s" % (cfg["listen"], cfg["port"], e), file=sys.stderr)
            return 2
        try:
            ca, cb = self.wait_duts(server)
            if not ca:
                print("error: DUTs did not connect within %ds" % cfg["connect_timeout"], file=sys.stderr)
                return 2
            plan, notes = build_plan(cfg, ca.name, cb.name, ca.radios, cb.radios)
            for n in notes:
                self.emit("log", msg="plan: skipped %s" % n)
            print("plan: %d tests (DUT A=%s, DUT B=%s)" % (len(plan), ca.name, cb.name), flush=True)
            if self.args.dry_run:
                for tc in plan:
                    print("  " + tc.label() + (" [DFS]" if tc.dfs else "") + "  htmode=" + tc.htmode)
                return 0
            if not plan:
                print("error: empty plan", file=sys.stderr)
                return 2
            self.runner = TestRunner(server, cfg, plan, ca.name, cb.name, output_path(cfg), self.emit)
            result = {}
            orig_emit = self.runner.emit

            def capture(kind, **kw):
                if kind == "finished":
                    result.update(kw)
                orig_emit(kind, **kw)
            self.runner.emit = capture

            def on_sigint(_sig, _frm):
                if self.runner.is_alive() and not self.runner._stop_ev.is_set():
                    print("\nstopping after current test (Ctrl-C again to force quit)...", flush=True)
                    self.runner.stop()
                else:
                    sys.exit(130)
            signal.signal(signal.SIGINT, on_sigint)

            self.runner.start()
            while self.runner.is_alive():
                self.runner.join(0.5)
            how = "aborted: %s" % result.get("aborted") if result.get("aborted") else \
                ("stopped" if result.get("stopped") else "completed")
            print("\nrun %s: %d/%d executed, %d PASS, %d FAIL\nresults: %s" % (
                how, result.get("executed", 0), result.get("total", 0), result.get("passed", 0),
                result.get("failed", 0), result.get("csv", "")), flush=True)
            if result.get("aborted"):
                return 2
            return 0 if result.get("failed", 0) == 0 and not result.get("stopped") else 1
        finally:
            server.stop()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        cfg = load_config(args.config) if args.config else default_config()
        apply_cli(cfg, args)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    errs = validate(cfg)
    if errs:
        for e in errs:
            print("error: %s" % e, file=sys.stderr)
        return 2
    if args.save_config:
        save_config(cfg, args.save_config)
        print("saved %s" % args.save_config)
        return 0
    if args.headless or args.dry_run:
        return Headless(cfg, args).run()
    try:
        from iw_test_controller.gui import run_gui
    except ImportError as e:
        print("error: GUI unavailable (%s). Install python3-tk or use --headless." % e, file=sys.stderr)
        return 2
    return run_gui(cfg)


if __name__ == "__main__":
    sys.exit(main())
