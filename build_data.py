#!/usr/bin/env python3
"""DynoView data build: Sleeper + FantasyCalc -> data.json for one league.

Run:  python3 build_data.py            (writes data.json next to this file)
Config lives in config.json (league id, nicknames by Sleeper user id).
"""
import json, os, sys, csv, io, datetime, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
SLEEPER = "https://api.sleeper.app/v1"
FC = "https://api.fantasycalc.com/values/current"
DP = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/"
SKILL = ("QB", "RB", "WR", "TE")
AGE_CLIFF = {"QB": 32, "RB": 26, "WR": 28, "TE": 29}   # "approaching / past the cliff" from here
YOUNG = 25


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DynoView/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise


def get_csv(name):
    req = urllib.request.Request(DP + name, headers={"User-Agent": "DynoView/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def dynastyprocess(nqb):
    """DynastyProcess dynasty values (expert-consensus based), keyed by Sleeper id."""
    ids = {r["fantasypros_id"]: r["sleeper_id"] for r in get_csv("db_playerids.csv")
           if r.get("fantasypros_id") not in ("", "NA", None) and r.get("sleeper_id") not in ("", "NA", None)}
    col = "value_2qb" if nqb == 2 else "value_1qb"
    out, picks = {}, {}
    for r in get_csv("values.csv"):
        if r.get(col) in ("", "NA", None):
            continue
        if r.get("pos") == "PICK":
            nm = r["player"]                      # e.g. "2027 Early 1st" -> "2027 1st (Early)"
            parts = nm.split()
            if len(parts) == 3 and parts[1] in ("Early", "Mid", "Late"):
                nm = f"{parts[0]} {parts[2]} ({parts[1]})"
            if "Pick" not in nm:
                picks[nm] = float(r[col])
            continue
        sid = ids.get(r.get("fp_id"))
        if sid:
            out[sid] = float(r[col])
    return out, picks


def sl(path):
    return get(SLEEPER + path)


def age_of(bd, today):
    if not bd:
        return None
    try:
        d = datetime.date.fromisoformat(bd)
    except ValueError:
        return None
    return round((today - d).days / 365.2425, 1)


def main():
    today = datetime.date.today()
    lid = CFG["league_id"]
    nfl = sl("/state/nfl")
    league = sl(f"/league/{lid}")
    users = {u["user_id"]: u for u in sl(f"/league/{lid}/users")}
    rosters = sl(f"/league/{lid}/rosters")
    traded = sl(f"/league/{lid}/traded_picks")
    players = sl("/players/nfl")
    teams_n = league["total_rosters"]
    rp = league["roster_positions"]
    nqb = 2 if "SUPER_FLEX" in rp else 1
    ppr = league["scoring_settings"].get("rec", 1)
    fc_d = get(f"{FC}?isDynasty=true&numQbs={nqb}&numTeams={teams_n}&ppr={ppr}")
    fc_r = get(f"{FC}?isDynasty=false&numQbs={nqb}&numTeams={teams_n}&ppr={ppr}")
    dval = {str(x["player"].get("sleeperId")): x["value"] for x in fc_d if x["player"].get("sleeperId")}
    rval = {str(x["player"].get("sleeperId")): x["value"] for x in fc_r if x["player"].get("sleeperId")}
    pick_val = {x["player"]["name"]: x["value"] for x in fc_d if x["player"]["position"] == "PICK"}

    # ---- consensus rank: FantasyCalc + DynastyProcess, priced on FantasyCalc's market curve ----
    # Each source ranks every asset (players and picks). We average the two ranks, re-sort,
    # then give the Nth asset in the consensus order FantasyCalc's Nth-highest value.
    fc_assets = {pid: v for pid, v in dval.items()}
    fc_assets.update({"PICK:" + k: v for k, v in pick_val.items()})
    dp_assets = {}
    try:
        dp_raw, dp_picks = dynastyprocess(nqb)
        dp_assets.update(dp_raw)
        dp_assets.update({"PICK:" + k: v for k, v in dp_picks.items()})
    except Exception as e:
        print("DynastyProcess unavailable, using FantasyCalc only:", e)
    def ranks(d):
        return {k: i + 1 for i, k in enumerate(sorted(d, key=lambda k: -d[k]))}
    r_fc, r_dp = ranks(fc_assets), ranks(dp_assets)
    keys = list(set(r_fc) | {k for k in r_dp if not k.startswith("PICK:") or k in r_fc})
    def cscore(k):
        a, b = r_fc.get(k), r_dp.get(k)
        return (a + b) / 2 if a and b else (a or b)
    curve = sorted(fc_assets.values(), reverse=True)
    tail = curve[-1] if curve else 0
    cons = {}
    for i, k in enumerate(sorted(keys, key=cscore)):
        cons[k] = curve[i] if i < len(curve) else max(0, round(tail * (1 - (i - len(curve) + 1) / 100)))
    fc_rank = {k: v for k, v in r_fc.items() if not k.startswith("PICK:")}
    dp_rank = {k: v for k, v in r_dp.items() if not k.startswith("PICK:")}
    dval = {k: v for k, v in cons.items() if not k.startswith("PICK:")}
    pick_val = {k[5:]: v for k, v in cons.items() if k.startswith("PICK:")}
    fc_dv = {k: v for k, v in fc_assets.items() if not k.startswith("PICK:")}
    dp_dv = {}

    # ---------- teams ----------
    nick_cfg = CFG.get("nicknames", {})
    teams = {}
    for r in rosters:
        rid, oid = r["roster_id"], r.get("owner_id")
        u = users.get(oid, {})
        uname = u.get("display_name") or f"Team {rid}"
        tname = ((u.get("metadata") or {}).get("team_name") or "").strip() or uname
        st = r.get("settings") or {}
        teams[rid] = {
            "rid": rid, "uid": oid, "user": uname, "name": nick_cfg.get(oid or "", uname), "team": tname,
            "w": st.get("wins", 0), "l": st.get("losses", 0), "t": st.get("ties", 0),
            "pf": round(st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100, 2),
            "pa": round(st.get("fpts_against", 0) + st.get("fpts_against_decimal", 0) / 100, 2),
            "players": [], "picks": [],
        }

    # ---------- players ----------
    allp = []
    for r in rosters:
        taxi, ir = set(r.get("taxi") or []), set(r.get("reserve") or [])
        for pid in r.get("players") or []:
            p = players.get(pid, {})
            pos = p.get("position") or ""
            if pos not in SKILL + ("K", "DEF"):
                fp = [x for x in (p.get("fantasy_positions") or []) if x in SKILL + ("K", "DEF")]
                pos = fp[0] if fp else pos
            if pos == "DEF" or (not p and pid.isalpha()):
                rec = {"id": pid, "n": pid, "pos": "DST", "nfl": pid}
            else:
                rec = {"id": pid, "n": p.get("full_name") or pid, "pos": pos, "nfl": p.get("team") or "FA",
                       "age": age_of(p.get("birth_date"), today), "yrs": p.get("years_exp")}
            rec["dv"] = dval.get(pid, 0)
            if pid in fc_rank: rec["fcr"] = fc_rank[pid]
            if pid in dp_rank: rec["dpr"] = dp_rank[pid]
            rec["rv"] = rval.get(pid, 0)
            if pid in taxi: rec["slot"] = "Taxi"
            elif pid in ir: rec["slot"] = "IR"
            rec["rid"] = r["roster_id"]
            teams[r["roster_id"]]["players"].append(rec)
            allp.append(rec)

    # ---------- startable pool ----------
    # Liberal on purpose: a player is "startable" if he ranks inside roughly 1.3x the league's
    # starting spots at his position (dedicated spots + his position's usual share of flex),
    # by rest-of-season value. Players parked on IR are never startable until they come back.
    slots = {k: rp.count(k) for k in SKILL}
    flex = rp.count("FLEX") + rp.count("REC_FLEX") + rp.count("WRRB_FLEX")
    sflex = rp.count("SUPER_FLEX")
    FLEX_SHARE = {"QB": 0.0, "RB": 0.4, "WR": 0.5, "TE": 0.1}
    pool_n = {k: round(teams_n * (slots[k] + flex * FLEX_SHARE[k] + (sflex * 0.8 if k == "QB" else 0)) * 1.3) for k in SKILL}
    healthy = lambda p: p.get("slot") != "IR"
    by_pos = {k: sorted([p for p in allp if p["pos"] == k and p["rv"] > 0 and healthy(p)], key=lambda p: -p["rv"]) for k in SKILL}
    pos_rank = {}
    for k in SKILL:
        for i, p in enumerate(by_pos[k]):
            pos_rank[p["id"]] = i + 1
    for p in allp:
        if p["id"] in pos_rank:
            p["prk"] = pos_rank[p["id"]]
    # "Possible starter" (green dot), per team: best QB (+ a 2nd if close), best TE (+ a 2nd if close),
    # and 6-8 RB/WR with at least 2 of each. Healthy, non-taxi players only.
    for t in teams.values():
        ok = sorted([p for p in t["players"] if p["pos"] in SKILL and p.get("slot") not in ("IR", "Taxi")], key=lambda p: -p["rv"])
        pick = set()
        for k, n2 in (("QB", 1 + sflex), ("TE", 1)):
            g = [p for p in ok if p["pos"] == k]
            pick.update(p["id"] for p in g[:n2])
            if len(g) > n2 and g[n2]["rv"] >= 0.6 * g[n2 - 1]["rv"] and g[n2]["rv"] > 0:
                pick.add(g[n2]["id"])
        rw = [p for p in ok if p["pos"] in ("RB", "WR")]
        core = [p for p in ok if p["pos"] == "RB"][:2] + [p for p in ok if p["pos"] == "WR"][:2]
        chosen = {p["id"] for p in core}
        for p in rw:
            if len(chosen) >= 6: break
            chosen.add(p["id"])
        sixth = min([p["rv"] for p in rw if p["id"] in chosen] or [0])
        for p in rw:
            if len(chosen) >= 8: break
            if p["id"] not in chosen and sixth > 0 and p["rv"] >= 0.75 * sixth:
                chosen.add(p["id"])
        pick |= chosen
        for p in t["players"]:
            if p["pos"] in SKILL:
                p["start"] = p["id"] in pick

    # ---------- per-team position strength ----------
    # Strength = value of the players a team would actually start there (dedicated spots + its flex share),
    # ranked 1..N across the league. Top 3 = Strong, bottom 3 = Weak.
    depth_k = {k: max(1, round(slots[k] + flex * FLEX_SHARE[k] + (sflex if k == "QB" else 0))) for k in SKILL}
    for t in teams.values():
        t["_pos_val"] = {}
        for k in SKILL:
            # 70% this season, 30% dynasty; the best player counts most (weights 1, .75, .5)
            vals = sorted([0.7 * p["rv"] + 0.3 * p["dv"] for p in t["players"]
                           if p["pos"] == k and healthy(p) and p.get("slot") != "Taxi"], reverse=True)
            t["_pos_val"][k] = sum(v * w for v, w in zip(vals[: depth_k[k]], (1.0, 0.75, 0.5)))
    edge = max(2, round(teams_n * 0.3))
    for k in SKILL:
        order = sorted(teams.values(), key=lambda t: -t["_pos_val"][k])
        for i, t in enumerate(order):
            cnt = sum(1 for p in t["players"] if p["pos"] == k and p.get("start"))
            tag = "Strong" if i < edge else "Weak" if i >= teams_n - edge else "Average"
            t.setdefault("needs", {})[k] = {"rank": i + 1, "tag": tag, "have": cnt, "need": slots[k]}
    for t in teams.values():
        ps = t["players"]
        extra = sum(max(0, t["needs"][k]["have"] - slots[k]) for k in ("RB", "WR", "TE"))
        t["flex"] = {"have": min(extra, flex), "need": flex, "tag": "Weak" if extra < flex else "Average" if extra < flex + 2 else "Strong"}

        # best healthy lineup by rest-of-season value -> win-now strength
        avail = sorted([p for p in ps if p["pos"] in SKILL and p.get("slot") not in ("Taxi", "IR")], key=lambda p: -p["rv"])
        used, lineup = set(), 0
        for k in SKILL:
            for p in [q for q in avail if q["pos"] == k][: slots[k]]:
                used.add(p["id"]); lineup += p["rv"]
        for p in [p for p in avail if p["id"] not in used and p["pos"] in ("RB", "WR", "TE")][:flex]:
            used.add(p["id"]); lineup += p["rv"]
        if sflex:
            for p in [p for p in avail if p["id"] not in used][:sflex]:
                used.add(p["id"]); lineup += p["rv"]
        t["lineup_value"] = lineup
        t["lineup_ids"] = sorted(used)
        t["young_value"] = sum(p["dv"] for p in ps if p.get("age") is not None and p["age"] <= YOUNG)
        t["roster_value"] = sum(p["dv"] for p in ps)

    # ---------- draft race: bottom of the standings, ordered by max possible points ----------
    playoff_n = int(league["settings"].get("playoff_teams") or 6)
    standings = sorted(teams.values(), key=lambda t: (-t["w"], -t["pf"]))
    for i, t in enumerate(standings):
        t["standing"] = i + 1
        r = next(x for x in rosters if x["roster_id"] == t["rid"])
        st_ = r.get("settings") or {}
        t["max_pf"] = round(st_.get("ppts", 0) + st_.get("ppts_decimal", 0) / 100, 2)
    out_of = [t for t in standings[playoff_n:]]
    race = sorted(out_of, key=lambda t: t["max_pf"])
    draft_race = {"playoff_teams": playoff_n, "order": [t["rid"] for t in race],
                  "bubble": [t["rid"] for t in standings[max(0, playoff_n - 2): playoff_n]]}

    # ---------- picks ----------
    S = int(league["season"])
    drafts = sl(f"/league/{lid}/drafts") or []
    seasons = [S] if any(str(d.get("season")) == str(S) and d.get("status") != "complete" for d in drafts) else []
    seasons += [S + 1, S + 2, S + 3]
    rounds = int(league["settings"].get("draft_rounds") or 4)
    owner = {(str(x["season"]), int(x["round"]), x["roster_id"]): x["owner_id"] for x in traded}

    def z(vals):
        m = sum(vals) / len(vals); sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** .5 or 1
        return [(v - m) / sd for v in vals]
    rids = list(teams)
    zl = z([teams[r]["lineup_value"] for r in rids])
    games = max(1, max(teams[r]["w"] + teams[r]["l"] + teams[r]["t"] for r in rids))
    zp = z([teams[r]["pf"] for r in rids])
    wt_rec = min(0.5, games / 14 * 0.5)  # record matters more as the season goes on
    for i, r in enumerate(rids):
        teams[r]["now_score"] = round((1 - wt_rec) * zl[i] + wt_rec * zp[i], 3)
    # projected slot for next year's picks: the draft race decides 1-4, the rest follow win-now strength
    rest_order = sorted([r for r in rids if r not in draft_race["order"]], key=lambda r: teams[r]["now_score"])
    proj_order = draft_race["order"] + rest_order
    third = max(1, teams_n // 3)
    phase = {r: ("Early" if i < third else "Late" if i >= teams_n - third else "Mid") for i, r in enumerate(proj_order)}
    ordn = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}

    def pval(yr, rd, orig):
        base = f"{yr} {ordn.get(rd, str(rd) + 'th')}"
        if yr == S + 1 or yr == S:
            v = pick_val.get(f"{base} ({phase[orig]})")
            if v: return v
        return pick_val.get(base, 0)

    for yr in seasons:
        for rd in range(1, rounds + 1):
            for orig in rids:
                cur = owner.get((str(yr), rd, orig), orig)
                if cur not in teams: continue
                teams[cur]["picks"].append({"yr": yr, "rd": rd, "from": orig, "v": pval(yr, rd, orig),
                                            "proj": phase[orig] if yr in (S, S + 1) else ""})
    for t in teams.values():
        t["picks"].sort(key=lambda p: (p["yr"], p["rd"], p["from"] != t["rid"]))
        t["pick_value"] = sum(p["v"] for p in t["picks"])
        t["future_value"] = t["young_value"] + t["pick_value"]

    # ---------- classification ----------
    zf = z([teams[r]["future_value"] for r in rids])
    for i, r in enumerate(rids):
        teams[r]["future_score"] = round(zf[i], 3)
    now_rank = {r: i + 1 for i, r in enumerate(sorted(rids, key=lambda r: -teams[r]["now_score"]))}
    fut_rank = {r: i + 1 for i, r in enumerate(sorted(rids, key=lambda r: -teams[r]["future_score"]))}
    top = max(2, round(teams_n * 0.3)); bottom = teams_n - top

    # --- recent trade behavior: are they buying win-now pieces or selling for the future? ---
    import time, math
    now_ms = time.time() * 1000
    WINDOW_DAYS = 90
    flow = {r: 0.0 for r in rids}
    trade_log = {r: [] for r in rids}
    generic_pick = {rd: pick_val.get(f"{S + 1} {ordn[rd]}", 0) for rd in ordn}
    def asset(pid):
        pl = players.get(pid, {}); a = age_of(pl.get("birth_date"), today)
        d, r_ = dval.get(pid, 0), rval.get(pid, 0)
        if a is not None and a <= 24:
            return 0.0, float(d)            # future asset
        return float(max(d, r_)), 0.0       # win-now asset
    with ThreadPoolExecutor(8) as ex:
        tx_weeks = list(ex.map(lambda w: sl(f"/league/{lid}/transactions/{w}") or [], range(0, 19)))
    seen_tx = set()
    for txs in tx_weeks:
        for tx in txs:
            if tx.get("type") != "trade" or tx.get("status") != "complete" or tx["transaction_id"] in seen_tx:
                continue
            seen_tx.add(tx["transaction_id"])
            age_d = (now_ms - (tx.get("status_updated") or tx.get("created") or now_ms)) / 864e5
            if age_d > WINDOW_DAYS:
                continue
            w = 1 - 0.5 * age_d / WINDOW_DAYS     # recent trades count more
            moves = {r: [0.0, 0.0, 0.0, 0.0] for r in tx.get("roster_ids") or []}  # now_in, now_out, fut_in, fut_out
            for pid, to in (tx.get("adds") or {}).items():
                frm = (tx.get("drops") or {}).get(pid)
                n_, f_ = asset(pid)
                if to in moves: moves[to][0] += n_; moves[to][2] += f_
                if frm in moves: moves[frm][1] += n_; moves[frm][3] += f_
            for dp in tx.get("draft_picks") or []:
                rd = int(dp["round"]); yr = int(dp["season"])
                v = pick_val.get(f"{yr} {ordn.get(rd, '')}") or generic_pick.get(rd, 0)
                if dp.get("owner_id") in moves: moves[dp["owner_id"]][2] += v
                if dp.get("previous_owner_id") in moves: moves[dp["previous_owner_id"]][3] += v
            for r, (ni, no, fi, fo) in moves.items():
                if r in flow:
                    flow[r] += w * ((ni - no) - (fi - fo))
                    trade_log[r].append(round(age_d))
    for r in rids:
        b = math.tanh(flow[r] / 6000)
        teams[r]["behavior"] = round(b, 2)
        teams[r]["mode"] = "buying" if b >= 0.15 else "selling" if b <= -0.15 else ""
        teams[r]["recent_trades"] = len(trade_log[r])

    for r in rids:
        t = teams[r]; t["now_rank"], t["fut_rank"] = now_rank[r], fut_rank[r]
        b = t["behavior"]
        if now_rank[r] <= top:
            t["label"] = "All-in"
        elif now_rank[r] > bottom:
            t["label"] = "Rebuilding"
        else:
            t["label"] = "Frisky"
        # behavior can move a team one tier
        if t["label"] == "Frisky" and b >= 0.3: t["label"] = "All-in"
        elif t["label"] == "Frisky" and b <= -0.3: t["label"] = "Rebuilding"
        elif t["label"] == "All-in" and b <= -0.5: t["label"] = "Frisky"
        elif t["label"] == "Rebuilding" and b >= 0.5: t["label"] = "Frisky"
        # sliding contender premium: strength now (60%) + how hard they've been buying (40%)
        if t["label"] == "Rebuilding":
            t["premium"] = 0
        else:
            pct = 1 - (now_rank[r] - 1) / max(1, teams_n - 1)
            aggr = 0.6 * pct + 0.4 * (b + 1) / 2
            t["premium"] = round(10 + 15 * max(0.0, min(1.0, aggr)))

    # ---------- trade ideas: sell prime/aging starters off teams outside their window ----------
    import itertools
    win = CFG.get("windows", {})
    def window(p):
        w = win.get(p["pos"]); a = p.get("age")
        if not w or a is None: return ""
        a = int(a)
        return "Young" if a <= w[0] else "Prime" if a <= w[1] else "Aging"
    PREMIUM = {"All-in": 1.25, "Frisky": 1.15}
    sellers = [t for t in teams.values() if t["label"] in ("Rebuilding", "Frisky")]
    buyers = [t for t in teams.values() if t["label"] in PREMIUM]
    for_sale, ideas = [], []
    for s in sellers:
        cands = [p for p in s["players"] if p["pos"] in SKILL and (p.get("start") or p.get("slot") == "IR") and window(p) in ("Prime", "Aging")
                 and max(p["rv"], p["dv"]) >= (500 if window(p) == "Aging" else 1500)]
        for p in cands:
            wnd = window(p)
            urgency = (2 if s["label"] == "Rebuilding" else 1) * (1.5 if wnd == "Aging" else 1.0)
            fits = []
            for b in buyers:
                if b["rid"] == s["rid"]:
                    continue
                mine = sorted([q["rv"] for q in b["players"] if q["pos"] == p["pos"] and healthy(q) and q.get("slot") != "Taxi"], reverse=True)
                kth = mine[depth_k[p["pos"]] - 1] if len(mine) >= depth_k[p["pos"]] else 0
                if p["rv"] <= kth * 1.1:
                    continue  # wouldn't crack their lineup
                gain = p["rv"] - kth
                premium = 1 + b["premium"] / 100
                # aging players are worth what they score now; everyone else is worth the higher of now / long-term
                market = p["rv"] if wnd == "Aging" else max(p["dv"], p["rv"])
                rb_scarcity = 1.15 if p["pos"] == "RB" else 1.0   # starting RBs are harder to find than WRs
                target = market * premium * rb_scarcity
                assets = [{"label": f'{k["yr"]} {ordn[k["rd"]]}' + (f' ({k["proj"]})' if k["proj"] else "") +
                           ("" if k["from"] == b["rid"] else f' via {teams[k["from"]]["name"]}'), "v": k["v"], "first": k["rd"] == 1}
                          for k in b["picks"] if k["v"] > 0]
                # unproven young players (rookies, or not producing yet) count for less as payment
                assets += [{"label": f'{q["n"]} ({q["pos"]}, {int(q["age"])})',
                            "v": round(q["dv"] * (0.8 if (q.get("yrs") or 0) == 0 or q["rv"] < 0.5 * q["dv"] else 1.0)), "first": False}
                           for q in b["players"] if q["id"] not in b["lineup_ids"] and not q.get("start") and q.get("age") is not None
                           and q["age"] <= 24 and q["dv"] >= 300 and q["pos"] in SKILL]
                # 1st-round picks scale with how good the player is; aging players need to be genuinely great
                if market < 1500 or (wnd == "Aging" and market < 3000):
                    max_firsts = 0
                elif market >= 4000:
                    max_firsts = 2
                else:
                    max_firsts = 1
                best = None
                if market < 1000:
                    assets = []          # too cheap to build a package around; stays on the sell list only
                for size in ((1, 2, 3, 4) if target >= 7000 else (1, 2, 3)):   # stars can take a 4-piece package
                    for combo in itertools.combinations(assets, size):
                        if sum(c["first"] for c in combo) > max_firsts: continue
                        if any(c["first"] and c["v"] > 1.25 * target for c in combo): continue  # never overpay with a 1st
                        tot = sum(c["v"] for c in combo)
                        if 0.92 * target <= tot <= 1.25 * target:
                            score = abs(tot - target) + 400 * (size - 1)
                            if best is None or score < best[0]:
                                best = (score, list(combo), tot)
                fits.append({"buyer": b["rid"], "gain": gain, "weak": b["needs"][p["pos"]]["tag"] == "Weak", "deal": best,
                             "premium": premium, "market": market, "target": target, "rb": rb_scarcity > 1})
            fits.sort(key=lambda f: (-f["weak"], -f["gain"]))
            for_sale.append({"seller": s["rid"], "player": f'{p["n"]}', "pos": p["pos"], "age": int(p["age"]), "window": wnd,
                             "market": round(p["rv"] if wnd == "Aging" else max(p["dv"], p["rv"])), "urgency": urgency,
                             "shop_to": [f["buyer"] for f in fits[:4]]})
            for_sale[-1]["ir"] = p.get("slot") == "IR"
            if p.get("slot") == "IR":
                continue  # no packaged ideas for injured players
            for f in [f for f in fits if f["deal"]][:2]:
                b = teams[f["buyer"]]
                why = [f'{b["name"]} is {"weak" if f["weak"] else "average or better"} at {p["pos"]} (#{b["needs"][p["pos"]]["rank"]} of {teams_n}), and {p["n"]} would start for them']
                why.append(f'{p["n"]} is {int(p["age"])}, {"past his prime window" if wnd == "Aging" else "in his prime window"} on a {s["label"].lower()} team')
                if f.get("rb"): why.append("Starting RBs carry a 15% scarcity bump")
                ideas.append({"seller": s["rid"], "buyer": b["rid"], "give": {"label": f'{p["n"]} ({p["pos"]}, {int(p["age"])})', "v": p["dv"]},
                              "get": [{"label": c["label"], "v": c["v"]} for c in f["deal"][1]], "get_v": f["deal"][2], "why": why,
                              "market": round(f["market"]), "ask": round(f["target"]), "premium": round((f["premium"] - 1) * 100),
                              "fit": urgency * f["market"] * (1.3 if f["weak"] else 1)})
    ideas.sort(key=lambda x: -x["fit"])
    out_ideas = ideas[:40]
    for_sale.sort(key=lambda x: -(x["urgency"] * x["market"]))

    # ---------- history ----------
    hist = build_history(lid, teams, players, nfl)

    # ---------- output ----------
    out = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
        "league": {"id": lid, "name": league["name"], "season": S, "teams": teams_n, "week": nfl.get("display_week"),
                   "format": f'{nqb}QB · {"PPR" if ppr == 1 else str(ppr) + " PPR"} · {teams_n} teams',
                   "slots": slots, "flex": flex, "sflex": sflex},
        "teams": sorted(teams.values(), key=lambda t: t["name"].lower()),
        "ideas": out_ideas,
        "for_sale": for_sale,
        "draft_race": draft_race,
        "history": hist,
        "cliff": AGE_CLIFF,
        "windows": CFG.get("windows", {}),
    }
    for t in out["teams"]:
        t.pop("lineup_ids", None); t.pop("_pos_val", None)
        t["players"].sort(key=lambda p: (["QB", "RB", "WR", "TE", "K", "DST"].index(p["pos"]) if p["pos"] in ("QB", "RB", "WR", "TE", "K", "DST") else 9, -p["dv"]))
    path = os.path.join(HERE, "data.json")
    json.dump(out, open(path, "w"), separators=(",", ":"), ensure_ascii=False)
    print("wrote", path, os.path.getsize(path), "bytes;", len(out_ideas), "trade ideas;", len(hist["seasons"]), "seasons of history")


def build_history(lid, teams_now, players, nfl):
    """Walk the previous_league_id chain: records, head-to-head, champions, trades."""
    chain, cur = [], lid
    while cur and cur != "0" and len(chain) < 12:
        L = sl(f"/league/{cur}")
        chain.append(L)
        cur = L.get("previous_league_id")
    uid_name = {t["uid"]: t["name"] for t in teams_now.values() if t["uid"]}
    rec, h2h, champs, seasons = {}, {}, [], []
    trades_by_player, trades_by_user, adds_by_user = {}, {}, {}
    top_games, blowouts = [], []
    for L in chain:
        lid2, yr = L["league_id"], L["season"]
        ros = sl(f"/league/{lid2}/rosters")
        us = {u["user_id"]: u for u in sl(f"/league/{lid2}/users")}
        r2u = {r["roster_id"]: r.get("owner_id") for r in ros}
        def nm(rid):
            u = r2u.get(rid)
            return uid_name.get(u) or (us.get(u, {}).get("display_name") if u else None) or f"Team {rid}"
        s = L["settings"]
        last = s.get("playoff_week_start", 15) - 1
        if L["status"] != "complete":
            last = min(last, (s.get("last_scored_leg") or 0))
        weeks = list(range(1, last + 1))
        with ThreadPoolExecutor(8) as ex:
            mres = list(ex.map(lambda w: sl(f"/league/{lid2}/matchups/{w}") or [], weeks))
            tres = list(ex.map(lambda w: sl(f"/league/{lid2}/transactions/{w}") or [], range(1, 19)))
        for w, ms in zip(weeks, mres):
            games = {}
            for m in ms:
                if m.get("matchup_id"):
                    games.setdefault(m["matchup_id"], []).append(m)
            for g in games.values():
                if len(g) != 2: continue
                a, b = g
                pa, pb = a.get("points") or 0, b.get("points") or 0
                if pa == 0 and pb == 0: continue
                na, nb = nm(a["roster_id"]), nm(b["roster_id"])
                for n, mine, theirs, opp in ((na, pa, pb, nb), (nb, pb, pa, na)):
                    r = rec.setdefault(n, {"w": 0, "l": 0, "t": 0, "pf": 0.0})
                    r["pf"] += mine
                    k = "w" if mine > theirs else "l" if mine < theirs else "t"
                    r[k] += 1
                    hh = h2h.setdefault(n, {}).setdefault(opp, {"w": 0, "l": 0, "t": 0})
                    hh[k] += 1
                top_games.append({"yr": yr, "wk": w, "team": na if pa >= pb else nb, "pts": round(max(pa, pb), 2), "opp": nb if pa >= pb else na})
                blowouts.append({"yr": yr, "wk": w, "win": na if pa >= pb else nb, "lose": nb if pa >= pb else na,
                                 "margin": round(abs(pa - pb), 2), "score": f"{max(pa, pb):.1f}–{min(pa, pb):.1f}"})
        for txs in tres:
            for t in txs:
                if t.get("status") != "complete": continue
                if t["type"] == "trade":
                    for rid in t.get("roster_ids") or []:
                        trades_by_user[nm(rid)] = trades_by_user.get(nm(rid), 0) + 1
                    for pid in (t.get("adds") or {}):
                        trades_by_player[pid] = trades_by_player.get(pid, 0) + 1
                elif t["type"] in ("waiver", "free_agent"):
                    for pid, rid in (t.get("adds") or {}).items():
                        adds_by_user[nm(rid)] = adds_by_user.get(nm(rid), 0) + 1
        champ = None
        if L["status"] == "complete":
            try:
                wb = sl(f"/league/{lid2}/winners_bracket")
                fin = [m for m in wb if m.get("p") == 1]
                if fin and fin[0].get("w"): champ = nm(fin[0]["w"])
            except Exception:
                pass
        if champ: champs.append({"yr": yr, "team": champ})
        seasons.append(yr)
    most_traded = sorted(trades_by_player.items(), key=lambda kv: -kv[1])[:8]
    return {
        "seasons": seasons,
        "records": rec,
        "h2h": h2h,
        "champions": champs,
        "top_scores": sorted(top_games, key=lambda g: -g["pts"])[:5],
        "blowouts": sorted(blowouts, key=lambda g: -g["margin"])[:5],
        "most_traded": [{"n": players.get(pid, {}).get("full_name") or pid, "pos": players.get(pid, {}).get("position") or "", "times": c}
                        for pid, c in most_traded],
        "trades_by_manager": sorted(trades_by_user.items(), key=lambda kv: -kv[1]),
        "adds_by_manager": sorted(adds_by_user.items(), key=lambda kv: -kv[1]),
    }


if __name__ == "__main__":
    main()
