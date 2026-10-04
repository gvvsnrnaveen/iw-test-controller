"""TCP server that DUT agents connect to, plus a synchronous RPC wrapper."""

import json
import socket
import threading
import time


class DutError(Exception):
    """Transport-level failure talking to a DUT (timeout, disconnect)."""


def parse_radio_info(info):
    """Turn the flat 'info' reply into {phy: {...}}."""
    radios = {}
    for phy in [r for r in info.get("radios", "").split(",") if r]:
        chans = {"2g": {}, "5g": {}}
        for band in ("2g", "5g"):
            for item in info.get("%s_chans_%s" % (phy, band), "").split(","):
                parts = item.split("/")
                if len(parts) == 3:
                    ch, freq, flags = (int(x) for x in parts)
                    chans[band][ch] = {"freq": freq, "dfs": bool(flags & 1), "no_ir": bool(flags & 2)}
        radios[phy] = {
            "band": info.get("%s_band" % phy, ""),
            "chans": chans,
            "ht40": bool(info.get("%s_ht40" % phy)),
            "vht": bool(info.get("%s_vht" % phy)),
            "he": bool(info.get("%s_he" % phy)),
            "eht": bool(info.get("%s_eht" % phy)),
            "uci": info.get("%s_uci" % phy, ""),
        }
    return radios


class DutConnection:
    def __init__(self, server, sock, addr):
        self.server = server
        self.sock = sock
        self.ip = addr[0]
        self.name = None
        self.hello = {}
        self.info = {}
        self.radios = {}
        self.alive = True
        self.busy = ""
        self.connected_at = time.time()
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending = {}
        self._next_id = 1

    def start(self):
        threading.Thread(target=self._reader, daemon=True, name="dut-%s" % self.ip).start()

    # -------------------------------------------------------------- rpc
    def call(self, op, rpc_timeout=60, **params):
        """Send a command and wait for its result dict (raises DutError)."""
        if not self.alive:
            raise DutError("%s is disconnected" % self.name)
        ev = threading.Event()
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            self._pending[cid] = [ev, None]
        msg = {"type": "cmd", "id": cid, "op": op}
        msg.update(params)
        self.busy = op
        try:
            self._send(msg)
            if not ev.wait(rpc_timeout):
                raise DutError("%s: '%s' timed out after %ss" % (self.name, op, rpc_timeout))
            with self._lock:
                res = self._pending[cid][1]
            if res is None:
                raise DutError("%s disconnected during '%s'" % (self.name, op))
            return res
        finally:
            self.busy = ""
            with self._lock:
                self._pending.pop(cid, None)

    def _send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        try:
            with self._send_lock:
                self.sock.sendall(data)
        except OSError as e:
            self.close()
            raise DutError("%s: send failed: %s" % (self.name, e))

    # ----------------------------------------------------------- reader
    def _reader(self):
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._handle(line)
        except OSError:
            pass
        finally:
            self.close()

    def _handle(self, line):
        try:
            msg = json.loads(line.decode(errors="replace"))
        except ValueError:
            self.server.emit("log", msg="bad message from %s: %r" % (self.ip, line[:200]))
            return
        mtype = msg.get("type")
        if mtype == "hello":
            self.hello = msg
            if self.server.token and msg.get("token") != self.server.token:
                self.server.emit("log", msg="rejected %s: bad token" % self.ip)
                try:
                    self._send({"type": "bye", "reason": "bad token"})
                except DutError:
                    pass
                self.close()
                return
            self.server._register(self, msg.get("name") or self.ip)
            threading.Thread(target=self._fetch_info, daemon=True).start()
        elif mtype == "result":
            with self._lock:
                slot = self._pending.get(msg.get("id"))
                if slot:
                    slot[1] = msg
                    slot[0].set()
        elif mtype == "log":
            self.server.emit("log", msg="[%s] %s" % (self.name, msg.get("msg", "")))

    def _fetch_info(self):
        try:
            info = self.call("info", rpc_timeout=60)
        except DutError as e:
            self.server.emit("log", msg="info from %s failed: %s" % (self.name, e))
            return
        self.info = info
        self.radios = parse_radio_info(info)
        self.server.emit("dut_info", name=self.name)

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        with self._lock:
            for slot in self._pending.values():
                slot[0].set()
        self.server._unregister(self)

    def summary(self):
        radios = ", ".join("%s:%s" % (p, r["band"] or "?") for p, r in sorted(self.radios.items()))
        return {
            "name": self.name,
            "ip": self.ip,
            "version": self.hello.get("version", ""),
            "model": self.info.get("model", ""),
            "radios": radios or ("(querying)" if not self.info else "none"),
            "sim": bool(self.hello.get("sim")),
            "busy": self.busy,
        }


class ControllerServer:
    def __init__(self, host="0.0.0.0", port=5555, token="", emit=None):
        self.host = host
        self.port = port
        self.token = token
        self._emit = emit or (lambda kind, **kw: None)
        self._duts = {}
        self._lock = threading.Lock()
        self._sock = None
        self.running = False

    def emit(self, kind, **kw):
        self._emit(kind, **kw)

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(16)
        self._sock = s
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True, name="accept").start()
        self.emit("log", msg="listening for DUT agents on %s:%d" % (self.host, self.port))
        self.emit("server", running=True)

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        for conn in list(self._duts.values()):
            conn.close()
        self.emit("server", running=False)

    def _accept_loop(self):
        while self.running:
            try:
                sock, addr = self._sock.accept()
            except OSError:
                break
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            DutConnection(self, sock, addr).start()

    def _register(self, conn, name):
        with self._lock:
            old = self._duts.get(name)
            if old is not None and old.ip != conn.ip and old.alive:
                name = "%s@%s" % (name, conn.ip)
                old = self._duts.get(name)
            conn.name = name
            self._duts[name] = conn
        if old is not None and old is not conn:
            self.emit("log", msg="WARNING: '%s' reconnected while its previous session was open "
                                 "(duplicate agent running on the DUT?)" % name)
            old.server = _NullServer()   # stale session from the same DUT
            old.close()
        self.emit("log", msg="DUT '%s' connected from %s (agent %s%s)" % (
            name, conn.ip, conn.hello.get("version", "?"), ", SIMULATION" if conn.hello.get("sim") else ""))
        self.emit("dut_connected", name=name)

    def _unregister(self, conn):
        removed = False
        with self._lock:
            if conn.name and self._duts.get(conn.name) is conn:
                del self._duts[conn.name]
                removed = True
        if removed:
            self.emit("log", msg="DUT '%s' (%s) disconnected" % (conn.name, conn.ip))
            self.emit("dut_disconnected", name=conn.name)

    def get(self, name_or_ip):
        with self._lock:
            if name_or_ip in self._duts:
                return self._duts[name_or_ip]
            for c in self._duts.values():
                if c.ip == name_or_ip:
                    return c
        return None

    def names(self):
        with self._lock:
            return sorted(self._duts)

    def ready_names(self):
        """DUTs that have finished the initial info query."""
        with self._lock:
            return sorted(n for n, c in self._duts.items() if c.info)


class _NullServer:
    def emit(self, *a, **kw):
        pass

    def _unregister(self, conn):
        pass
