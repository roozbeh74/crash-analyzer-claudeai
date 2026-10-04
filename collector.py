#!/usr/bin/env python3
"""FaucetPay crash round collector.
Connects to the crash websocket, parses the crash_update protocol exactly
(verified against the real message log), appends each finished round to
rounds-YYYY-MM.jsonl (UTC month), deduplicates by round id across runs.
Designed to run for RUN_SECONDS inside a scheduled job, then exit.
"""
import asyncio, datetime, glob, json, os, time
import websockets

WS_URL = os.environ.get("FP_WS", "wss://socket.faucetpay.io/crash/")
RUN_SECONDS = float(os.environ.get("RUN_SECONDS", "200"))

def now_ms():
    return int(time.time() * 1000)

class Collector:
    def __init__(self, base_dir="."):
        self.base = base_dir
        self.phase = "idle"
        self.last_cd = None
        self.pending = []
        self.cur = None
        self.seq = 0
        self.ids = set()
        self.total = 0
        for path in sorted(glob.glob(os.path.join(base_dir, "rounds-*.jsonl"))):
            try:
                f = open(path, "r", encoding="utf-8")
            except OSError:
                continue
            with f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("id") is not None:
                        self.ids.add(str(rec["id"]))
                    self.seq = max(self.seq, rec.get("seq") or 0)
                    self.total += 1

    def month_file(self):
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
        return os.path.join(self.base, "rounds-" + stamp + ".jsonl")

    def record(self, crash, est, id_=None, seed=None):
        cur = self.cur
        last_time = cur.get("lastTime") or 0 if cur else 0
        dur = round(last_time / 1000, 2) if last_time > 0 else None
        players = len(cur["bets"]) if cur else None
        cash_n = len(cur["cashouts"]) if cur else None
        cash_at = [round(c["at"], 2) for c in cur["cashouts"][:50]] if cur else []
        self.seq += 1
        rec = {
            "seq": self.seq,
            "id": id_ if id_ is not None else self.seq,
            "crash": round(crash, 2),
            "t": now_ms(),
            "dur": dur,
            "players": players,
            "cashouts": cash_n,
            "cashAt": cash_at,
            "seed": seed,
            "est": bool(est),
        }
        if cur:
            cur["state"] = "crashed"
            cur["final"] = rec["crash"]
            if id_ is not None:
                cur["id"] = id_
        with open(self.month_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if id_ is not None:
            self.ids.add(str(id_))
        self.total += 1
        print("round #%s id=%s crash=%s est=%s" % (rec["seq"], rec["id"], rec["crash"], rec["est"]), flush=True)
        return rec

    def finalize_if_running(self):
        if self.cur and self.cur.get("state") == "running" and (self.cur.get("lastM") or 0) >= 1.01:
            self.record(self.cur["lastM"], True)
        if self.cur:
            self.cur["state"] = "crashed"

    def on_new_round(self, cd):
        new_phase = (self.phase != "betting") or (
            self.last_cd is not None and cd is not None and (cd - self.last_cd) > 0.5)
        if new_phase:
            self.finalize_if_running()
            self.pending = []
            self.phase = "betting"
        if cd is not None:
            self.last_cd = cd

    def on_new_bet(self, b):
        self.pending.append({
            "user": b.get("user_name"),
            "id": b.get("user_id"),
            "amt": b.get("bet_amt"),
            "coin": b.get("coin"),
        })
        if len(self.pending) > 500:
            self.pending.pop(0)

    def on_start_game(self):
        self.finalize_if_running()
        self.cur = {"bets": self.pending, "cashouts": [], "state": "running", "lastM": 1, "lastTime": 0}
        self.phase = "running"

    def on_update_value(self, t, m):
        if m is None:
            return
        if not self.cur or self.cur["state"] != "running":
            self.cur = {"bets": self.pending, "cashouts": [], "state": "running", "lastM": 1, "lastTime": t or 0}
            self.phase = "running"
        self.cur["lastM"] = m
        if t is not None:
            self.cur["lastTime"] = t

    def on_cashout(self, c):
        if self.cur and self.cur["state"] == "running":
            try:
                at = float(c.get("at"))
            except (TypeError, ValueError):
                return
            self.cur["cashouts"].append({"user": c.get("user_name"), "at": at, "coin": c.get("coin")})

    def on_crashed(self, m, crash):
        id_ = m.get("id") if m and m.get("id") is not None else None
        seed = str(m["server_seed"]) if m and m.get("server_seed") else None
        if crash is None or crash < 1:
            if self.cur:
                self.cur["state"] = "crashed"
            self.phase = "crashed"
            return
        if id_ is not None and str(id_) in self.ids:
            return
        self.record(crash, False, id_, seed)
        self.phase = "crashed"

    def handle(self, d):
        method = d.get("method") or ""
        try:
            v = float(d["value"]) if d.get("value") is not None else None
        except (TypeError, ValueError):
            v = None
        if method == "new_round":
            self.on_new_round(v)
        elif method == "new_bet":
            self.on_new_bet(d)
        elif method == "start_game":
            self.on_start_game()
        elif method == "update_value":
            try:
                t = float(d.get("time"))
            except (TypeError, ValueError):
                t = None
            self.on_update_value(t, v)
        elif method == "bet_cashedout_update":
            self.on_cashout(d)
        elif method == "crashed":
            self.on_crashed(d, v)

async def collect(c):
    deadline = time.time() + RUN_SECONDS
    while time.time() < deadline:
        try:
            async with websockets.connect(
                WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_queue=500
            ) as ws:
                print("socket connected", flush=True)
                while time.time() < deadline:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and (d.get("method") or d.get("action")):
                        c.handle(d)
        except Exception as e:
            print("socket error: %r" % e, flush=True)
            await asyncio.sleep(3)
    c.finalize_if_running()

def main():
    print("collector starting, RUN_SECONDS=%s" % RUN_SECONDS, flush=True)
    c = Collector(".")
    print("existing rounds known: %d" % c.total, flush=True)
    asyncio.run(collect(c))
    print("collector finished, total rounds: %d" % c.total, flush=True)

if __name__ == "__main__":
    main()
