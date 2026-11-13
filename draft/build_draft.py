#!/usr/bin/env python3
"""DynoDraft data build: prospect list + ESPN college stats -> draft/data.json.

Run:  python3 draft/build_draft.py [prospects_2027.json]
Next year: add draft/prospects_2028.json (same shape) and point the workflow at it.

For each prospect it finds the ESPN player (cached as "espn_id" back into the prospects file),
pulls height/weight and per-season college stats and games played, then computes per season:
  - dominator: average of the player's share of team receiving yards and receiving TDs (WR/TE),
    or of team scrimmage yards and scrimmage TDs (RB). QBs get passing/rushing lines only.
  - breakout age: age during the first season with a dominator of 20% or more.
"""
import json, os, sys, datetime, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "prospects_2027.json")
OUT = os.path.join(HERE, "data.json")
WEB = "https://site.web.api.espn.com/apis/common/v3"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
BREAKOUT = 0.20


def get(url):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DynoDraft/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception:
            if i == 2:
                return None


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return 0.0


def find_espn(p):
    q = urllib.parse.quote(p["name"])
    d = get(f"{WEB}/search?query={q}&type=player&sport=football&league=college-football&limit=8") or {}
    items = [i for i in d.get("items", []) if i.get("league") == "college-football"]
    school = (p.get("school") or "").lower().replace("state", "st")
    def team(i):
        rel = i.get("teamRelationships") or [{}]
        return (rel[0].get("displayName") or "").lower().replace("state", "st")
    for i in items:
        if school and school.split()[0] in team(i):
            return i["id"]
    return items[0]["id"] if items else None


team_cache = {}
def team_totals(team_id, year):
    key = (team_id, year)
    if key not in team_cache:
        tot = None
        for typ in (3, 2):   # 3 = full season incl. postseason; 2 = regular season (season in progress)
            d = get(f"{CORE}/seasons/{year}/types/{typ}/teams/{team_id}/statistics")
            try:
                c = {x["name"]: {s["name"]: s["value"] for s in x["stats"]} for x in d["splits"]["categories"]}
                tot = {"rec_yds": c["receiving"]["receivingYards"], "rec_td": c["receiving"]["receivingTouchdowns"],
                       "rush_yds": c["rushing"]["rushingYards"], "rush_td": c["rushing"]["rushingTouchdowns"]}
                break
            except Exception:
                continue
        team_cache[key] = tot
    return team_cache[key]


def games_played(pid, year):
    for typ in (3, 2):   # 3 = whole season incl. postseason; 2 = regular season so far
        d = get(f"{CORE}/seasons/{year}/types/{typ}/athletes/{pid}/statistics")
        try:
            c = {x["name"]: {s["name"]: s.get("value") for s in x["stats"]} for x in d["splits"]["categories"]}
            gp = c.get("general", {}).get("gamesPlayed")
            if gp is not None:
                return int(gp)
        except Exception:
            continue
    return None


def age_on(dob, day):
    if not dob:
        return None
    b = datetime.date.fromisoformat(dob)
    return round((day - b).days / 365.2425, 1)


def build_player(p, draft_day):
    pid = p.get("espn_id") or find_espn(p)
    p["espn_id"] = pid
    out = {"name": p["name"], "pos": p["pos"], "school": p.get("school") or "", "dob": p.get("dob") or "",
           "age": age_on(p.get("dob"), draft_day), "espn_id": pid, "seasons": []}
    if not pid:
        return out
    bio = (get(f"{WEB}/sports/football/college-football/athletes/{pid}") or {}).get("athlete", {})
    out["ht"] = (bio.get("displayHeight") or "").replace(" ", "")
    out["wt"] = (bio.get("displayWeight") or "").replace(" lbs", "")
    if bio.get("team", {}).get("location"):
        out["school"] = bio["team"]["location"]
    st = get(f"{WEB}/sports/football/college-football/athletes/{pid}/stats") or {}
    slug_to_team = {k: v for k, v in (st.get("teams") or {}).items()}
    by_year = {}
    for cat in st.get("categories", []):
        name, labels = cat.get("name"), cat.get("labels") or []
        if name not in ("passing", "rushing", "receiving"):
            continue
        for row in cat.get("statistics", []):
            yr = row.get("season", {}).get("year")
            slug = row.get("teamSlug")
            s = by_year.setdefault(yr, {"year": yr, "team": slug_to_team.get(slug, {}).get("location", ""),
                                        "team_id": slug_to_team.get(slug, {}).get("id")})
            vals = dict(zip(labels, row.get("stats", [])))
            if name == "passing":
                s["pass"] = {k: vals.get(k) for k in ("CMP", "ATT", "YDS", "TD", "INT")}
            elif name == "rushing":
                s["rush"] = {k: vals.get(k) for k in ("CAR", "YDS", "TD")}
            else:
                s["rec"] = {k: vals.get(k) for k in ("REC", "YDS", "TD")}
    for yr in sorted(by_year):
        s = by_year[yr]
        tot = team_totals(s["team_id"], yr) if s.get("team_id") else None
        dom = None
        if tot and p["pos"] in ("WR", "TE") and s.get("rec") and tot["rec_yds"] and tot["rec_td"] is not None:
            y = num(s["rec"]["YDS"]) / tot["rec_yds"]
            t = num(s["rec"]["TD"]) / tot["rec_td"] if tot["rec_td"] else y
            dom = (y + t) / 2
        elif tot and p["pos"] == "RB" and (s.get("rush") or s.get("rec")):
            yds = num((s.get("rush") or {}).get("YDS")) + num((s.get("rec") or {}).get("YDS"))
            tds = num((s.get("rush") or {}).get("TD")) + num((s.get("rec") or {}).get("TD"))
            ty, tt = tot["rush_yds"] + tot["rec_yds"], tot["rush_td"] + tot["rec_td"]
            if ty:
                dom = (yds / ty + (tds / tt if tt else yds / ty)) / 2
        s["dom"] = round(dom * 100, 1) if dom is not None else None
        s["gp"] = games_played(pid, yr)
        s.pop("team_id", None)
        out["seasons"].append(s)
    doms = [s for s in out["seasons"] if s.get("dom") is not None]
    if doms:
        best = max(doms, key=lambda s: s["dom"])
        out["best_dom"], out["best_dom_year"] = best["dom"], best["year"]
        first = next((s for s in doms if s["dom"] >= BREAKOUT * 100), None)
        if first:
            out["breakout_year"] = first["year"]
            if p.get("dob"):   # age on Sept 1 of the breakout season
                out["breakout_age"] = age_on(p["dob"], datetime.date(first["year"], 9, 1))
    return out


def main():
    src = json.load(open(SRC))
    draft_day = datetime.date.fromisoformat(src["draft_day"])
    with ThreadPoolExecutor(6) as ex:
        players = list(ex.map(lambda p: build_player(p, draft_day), src["players"]))
    json.dump(src, open(SRC, "w"), indent=1, ensure_ascii=False)   # keeps the espn_id cache
    out = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
           "class": src["class"], "draft_day": src["draft_day"], "default_order": src["default_order"],
           "players": players}
    json.dump(out, open(OUT, "w"), separators=(",", ":"), ensure_ascii=False)
    miss = [p["name"] for p in players if not p.get("espn_id")]
    print("wrote", OUT, len(players), "players;", "no ESPN match:", miss or "none")


if __name__ == "__main__":
    main()
