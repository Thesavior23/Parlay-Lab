"""Builds today's picks and writes them to docs/data/ for the website.

Run:  python build_data.py            (real data, today's games)
      python build_data.py --demo     (fake data, for testing)
      python build_data.py --date 2026-11-05
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/Chicago")  # San Antonio time
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-5))

import analysis as A
from data_sources import ESPN, Fetcher, norm_name, season_for

HERE = os.path.dirname(os.path.abspath(__file__))
STATS = ["pts", "reb", "ast", "fg3m", "pra"]
MIN_MPG = 18          # only players who actually play real minutes
POOL = 6              # parallel downloads


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def pmap(fn, items):
    with ThreadPoolExecutor(POOL) as ex:
        return list(ex.map(fn, items))


def safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:
        log("  ! failed:", getattr(fn, "__name__", fn), a, "-", e)
        return default


def market_line(g):
    """ESPN odds like 'SA -12.5' -> home team's expected margin (+12.5). None if missing."""
    d = (g.get("odds_details") or "").strip()
    if d.upper() in ("EVEN", "PK", "PICK"):
        return 0.0
    parts = d.split()
    if len(parts) != 2:
        return None
    try:
        val = float(parts[1])
    except ValueError:
        return None
    if parts[0] == g["home"]["abbr"]:
        return -val
    if parts[0] == g["away"]["abbr"]:
        return val
    return None


def find_injury(inj_list, player):
    for i in inj_list:
        if (i.get("id") and i["id"] == player["id"]) or norm_name(i.get("name")) == norm_name(player["name"]):
            return i
    return None


def build(day, espn, data_dir, prev_teams=None):
    """prev_teams: teams playing the day before that haven't played yet (for tomorrow's early look)."""
    prev_teams = prev_teams or set()
    season = season_for(day)
    ds = day.isoformat()
    warnings = []
    games = [g for g in espn.games_on(day)]
    log(f"{ds}: {len(games)} games")
    if not games:
        return {"date": ds, "generated": datetime.now(timezone.utc).isoformat(), "games": [],
                "players": {}, "picks": [], "parlays": [], "warnings": ["No NBA games on this day."]}

    teams = {}
    for g in games:
        for side in ("home", "away"):
            teams[g[side]["id"]] = g[side]

    # ---- team schedules, rosters, injuries
    tids = list(teams)
    sched_cur = dict(zip(tids, pmap(lambda t: safe(espn.team_schedule, t, season, True, default=[]), tids)))
    sched_prev = dict(zip(tids, pmap(lambda t: safe(espn.team_schedule, t, season - 1, False, default=[]), tids)))
    rosters = dict(zip(tids, pmap(lambda t: safe(espn.roster, t, default=[]), tids)))
    injuries = safe(espn.injuries, default=None)
    if injuries is None:
        injuries = {}
        warnings.append("Couldn't load the injury report — double-check injuries before you bet.")
    # past games only (in case the schedule includes today's finished game)
    for t in tids:
        sched_cur[t] = [x for x in sched_cur[t] if x["date"] < ds]

    profiles = {t: A.team_profile(sched_cur[t], sched_prev[t], ds) for t in tids}
    for t in tids:
        if teams[t]["abbr"] in prev_teams:
            profiles[t]["rest"] = 1  # plays tonight too, so tomorrow is a back-to-back
    league_pf = A.mean(p["pf"] for p in profiles.values()) or 112.0

    # ---- player game logs (this season + last season)
    everyone = [(t, pl) for t in tids for pl in rosters[t] if pl.get("id")]
    log(f"  loading game logs for {len(everyone)} players")
    cur_logs = dict(zip([p["id"] for _, p in everyone],
                        pmap(lambda tp: safe(espn.gamelog, tp[1]["id"], season, True, default=[]), everyone)))
    prev_logs = dict(zip([p["id"] for _, p in everyone],
                         pmap(lambda tp: safe(espn.gamelog, tp[1]["id"], season - 1, False, default=[]), everyone)))
    for k in cur_logs:
        cur_logs[k] = [g for g in cur_logs[k] if g["date"] < ds]

    def player_info(tid, pl):
        cur, prev = cur_logs.get(pl["id"], []), prev_logs.get(pl["id"], [])
        alls = cur + prev
        mpg10 = A.mean(g["min"] for g in alls[:10]) or 0
        ppg = A.mean(g["pts"] for g in (cur or prev[:20])) or 0
        mpg = A.mean(g["min"] for g in (cur or prev[:20])) or 0
        last = alls[0]["date"] if alls else None
        team_name = teams[tid]["name"]
        inj = find_injury(injuries.get(team_name, []), pl)
        return {"cur": cur, "all": alls, "mpg10": mpg10, "ppg": ppg, "mpg": mpg,
                "last_played_days": A.days_between(last, ds) if last else None,
                "status": inj["status"] if inj else None, "inj_detail": inj["detail"] if inj else None}

    info = {pl["id"]: player_info(t, pl) for t, pl in everyone}

    # ---- third season back only when we still need games vs this opponent
    opp_of = {}
    for g in games:
        opp_of[g["home"]["id"]] = g["away"]["abbr"]
        opp_of[g["away"]["id"]] = g["home"]["abbr"]
    need_more = [(t, pl) for t, pl in everyone if info[pl["id"]]["mpg10"] >= MIN_MPG
                 and sum(1 for x in info[pl["id"]]["all"] if x["opp"] == opp_of[t]) < 5]
    older = pmap(lambda tp: safe(espn.gamelog, tp[1]["id"], season - 2, False, default=[]), need_more)
    for (t, pl), logs in zip(need_more, older):
        info[pl["id"]]["all"] = info[pl["id"]]["all"] + logs

    # ---- games
    out_games, all_legs, players_out, picks = [], [], {}, []
    for g in games:
        h, a = g["home"], g["away"]
        inj_lists = {}
        for side in ("home", "away"):
            tid = g[side]["id"]
            lst = []
            for pl in rosters[tid]:
                i = info.get(pl["id"])
                if i and i["status"]:
                    lst.append({"name": pl["name"], "status": i["status"], "ppg": i["ppg"], "mpg": i["mpg"],
                                "last_played_days": i["last_played_days"], "detail": i["inj_detail"]})
            inj_lists[side] = lst
        h_imp = A.injury_impact(inj_lists["home"])
        a_imp = A.injury_impact(inj_lists["away"])
        pred = A.predict_game(g, profiles[h["id"]], profiles[a["id"]], h_imp, a_imp, ds, league_pf,
                              market_line(g))
        gp = A.game_picks(g, pred)
        fav_team = g[pred["fav"]]["abbr"]
        for pk in gp:
            all_legs.append({"id": f"{ds}:{g['id']}:{pk['leg']['type']}:{pk['leg']['side']}:{pk['leg'].get('line', '')}",
                             "kind": "game", "label": pk["label"], "p": pk["p"], "grade": pk["grade"],
                             "game_id": g["id"], "matchup": pk["matchup"], "fav_team": fav_team,
                             "leg": pk["leg"], "price": None, "edge": None})
            pk["id"] = all_legs[-1]["id"]
        out_games.append({
            "id": g["id"], "time": g["date"], "status": g["status_text"], "state": g["state"],
            "venue": g["venue"], "home": h, "away": a,
            "home_profile": {k: v for k, v in profiles[h["id"]].items() if k != "games"},
            "away_profile": {k: v for k, v in profiles[a["id"]].items() if k != "games"},
            "injuries": {"home": [{k: x[k] for k in ("name", "status", "detail")} for x in inj_lists["home"]],
                         "away": [{k: x[k] for k in ("name", "status", "detail")} for x in inj_lists["away"]]},
            "pred": pred, "picks": gp,
        })

        # ---- player picks for this game
        for side in ("home", "away"):
            me, opp = g[side], g["away" if side == "home" else "home"]
            opp_prof = profiles[opp["id"]]
            team_margin = A.margin_for(pred, side)
            # key teammates newly out (played within the last 10 days, so stats don't show it yet)
            key_out = [pl["name"] for pl in rosters[me["id"]]
                       if info.get(pl["id"]) and info[pl["id"]]["status"]
                       and "out" in info[pl["id"]]["status"].lower()
                       and info[pl["id"]]["ppg"] >= 14
                       and (info[pl["id"]]["last_played_days"] or 99) <= 10]
            for pl in rosters[me["id"]]:
                i = info.get(pl["id"])
                if not i or i["mpg10"] < MIN_MPG or not i["all"]:
                    continue
                if (i["last_played_days"] or 99) > 21:
                    continue  # hasn't played in 3 weeks
                if i["status"] and "out" in i["status"].lower():
                    players_out.setdefault(g["id"], []).append(pl["name"])
                    continue
                ctx = {"day": ds, "home": side == "home", "opp": opp["abbr"], "status": i["status"],
                       "force_b2b": me["abbr"] in prev_teams,
                       "key_teammates_out": [n for n in key_out if n != pl["name"]],
                       "opp_def_factor": opp_prof["pa"] / league_pf,
                       "pace_factor": pred["pace_factor"], "team_margin": team_margin}
                any_pick = False
                for stat in STATS:
                    r = A.find_lines(stat, i["cur"], i["all"], ctx)
                    if not r:
                        continue
                    any_pick = True
                    pid = f"{ds}:{pl['id']}:{stat}:{r['t']}"
                    pick = {"id": pid, "pid": pl["id"], "name": pl["name"], "team": me["abbr"], "opp": opp["abbr"],
                            "home": side == "home", "game_id": g["id"], "stat": stat,
                            "stat_label": A.STAT_LABEL[stat], "t": r["t"], "p": r["p"], "score": r["score"],
                            "grade": r["grade"], "bold": r["bold"], "ladder": r["ladder"],
                            "season_avg": r["season_avg"], "l10_avg": r["l10_avg"], "proj": r["proj"],
                            "hits10": r["hits10"], "n10": r["n10"], "split": r["split"],
                            "floor10": r["floor10"], "reasons": r["reasons"],
                            "last10": [x["v"] for x in r["last10"]],
                            "vs_opp": r["vs_opp"]}
                    picks.append(pick)
                    all_legs.append({"id": pid, "kind": "prop", "label": f"{pl['name']} {r['t']}+ {A.STAT_LABEL[stat]}",
                                     "name": pl["name"], "player_id": pl["id"], "team": me["abbr"], "pos": pl["pos"],
                                     "stat": stat, "t": r["t"], "p": r["p"], "grade": r["grade"],
                                     "game_id": g["id"], "matchup": f"{a['abbr']} @ {h['abbr']}",
                                     "price": None, "edge": None})
                if any_pick:
                    players_out.setdefault("_players", {})[pl["id"]] = {
                        "name": pl["name"], "team": me["abbr"], "opp": opp["abbr"], "home": side == "home",
                        "pos": pl["pos"], "headshot": pl.get("headshot"), "status": i["status"],
                        "mpg10": round(i["mpg10"], 1),
                        "last10": [{"d": x["date"], "o": x["opp"], "h": x["home"], "min": x["min"],
                                    "pts": x["pts"], "reb": x["reb"], "ast": x["ast"], "fg3m": x["fg3m"]}
                                   for x in i["all"][:10]],
                    }

    players = players_out.pop("_players", {})
    picks.sort(key=lambda x: (-x["p"], -x["t"]))
    parlays = A.build_parlays([l for l in all_legs if not (l["kind"] == "prop" and l["p"] < A.SAFE_P)])
    log(f"  {len(picks)} player picks, {len(parlays)} parlay ideas")
    news = build_news(espn, ds, games, teams, rosters, info, profiles, prev_teams)
    return {"date": ds, "generated": datetime.now(timezone.utc).isoformat(), "season": season,
            "games": out_games, "players": players, "picks": picks, "parlays": parlays,
            "news": news, "warnings": warnings}


def build_news(espn, ds, games, teams, rosters, info, profiles, prev_teams):
    """Injury report, back-to-backs, rested teams, hot/cold players and headlines."""
    matchup = {}
    for g in games:
        for side in ("home", "away"):
            other = g["away" if side == "home" else "home"]
            matchup[g[side]["id"]] = ("vs " if side == "home" else "@ ") + other["abbr"]

    injuries, big = [], []
    for tid, t in teams.items():
        rows = []
        for pl in rosters[tid]:
            i = info.get(pl["id"])
            if not i or not i["status"]:
                continue
            key = i["ppg"] >= 12 or i["mpg"] >= 24
            fresh = (i["last_played_days"] or 99) <= 10
            row = {"name": pl["name"], "status": i["status"], "detail": i["inj_detail"] or "",
                   "ppg": round(i["ppg"], 1), "key": key, "fresh": fresh}
            rows.append(row)
            if key and fresh and "out" in i["status"].lower():
                big.append({"team": t["abbr"], "name": pl["name"], "ppg": round(i["ppg"], 1),
                            "detail": i["inj_detail"] or ""})
        rows.sort(key=lambda r: (not r["key"], -r["ppg"]))
        if rows:
            injuries.append({"team": t["abbr"], "name": t["name"], "matchup": matchup[tid], "players": rows})
    injuries.sort(key=lambda x: x["team"])
    big.sort(key=lambda x: -x["ppg"])

    b2b, rested = [], []
    for tid, t in teams.items():
        rest = profiles[tid]["rest"]
        if rest == 1:
            b2b.append({"team": t["abbr"], "name": t["name"], "matchup": matchup[tid],
                        "note": "Plays tonight too, so this is the 2nd night in a row" if t["abbr"] in prev_teams
                        else "Played last night — 2nd night in a row"})
        elif rest is not None and rest >= 3:
            rested.append({"team": t["abbr"], "name": t["name"], "matchup": matchup[tid],
                           "note": f"{rest - 1} days off"})

    hot, cold = [], []
    for tid, t in teams.items():
        for pl in rosters[tid]:
            i = info.get(pl["id"])
            if not i or i["mpg10"] < 20 or (i["status"] and "out" in i["status"].lower()):
                continue
            base = i["cur"] if len(i["cur"]) >= 6 else i["all"][:20]
            last5 = i["all"][:5]
            if len(base) < 5 or len(last5) < 5:
                continue
            for stat, label in (("pts", "points"), ("reb", "rebounds"), ("ast", "assists")):
                avg = sum(g[stat] for g in base) / len(base)
                l5 = sum(g[stat] for g in last5) / 5
                need = 3 if stat == "pts" else 2
                row = {"name": pl["name"], "team": t["abbr"], "matchup": matchup[tid], "stat": label,
                       "l5": round(l5, 1), "avg": round(avg, 1)}
                if l5 >= avg * 1.2 and l5 - avg >= need:
                    hot.append(dict(row, diff=round(l5 - avg, 1)))
                elif l5 <= avg * 0.8 and avg - l5 >= need:
                    cold.append(dict(row, diff=round(l5 - avg, 1)))
    hot.sort(key=lambda r: -r["diff"])
    cold.sort(key=lambda r: r["diff"])

    headlines = []
    try:
        data = espn.f.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news",
                          {"limit": 25}, ttl=900)
        for a in data.get("articles", []) or []:
            link = ((a.get("links") or {}).get("web") or {}).get("href")
            if not a.get("headline") or not link:
                continue
            headlines.append({"title": a["headline"], "desc": a.get("description") or "",
                              "link": link, "published": a.get("published")})
    except Exception as e:
        log("  ! headlines failed:", e)

    return {"big_injuries": big[:12], "injuries": injuries, "b2b": b2b, "rested": rested,
            "hot": hot[:10], "cold": cold[:8], "headlines": headlines[:15]}


def settle(day, espn, data_dir):
    """Look up what actually happened for a past day's picks."""
    ds = day.isoformat()
    slate_path = os.path.join(data_dir, "slates", f"{ds}.json")
    if not os.path.exists(slate_path):
        return None
    with open(slate_path) as f:
        slate = json.load(f)
    games = {g["id"]: g for g in safe(espn.games_on, day, default=[])}
    season = season_for(day)
    pids = sorted({p["pid"] for p in slate["picks"]})
    logs = dict(zip(pids, pmap(lambda pid: safe(espn.gamelog, pid, season, True, default=[]), pids)))
    res_players = {}
    for pid in pids:
        gl = [g for g in logs[pid] if g["date"] == ds]
        if gl:
            x = gl[-1]
            res_players[pid] = {k: x[k] for k in ("pts", "reb", "ast", "fg3m", "min")}
            res_players[pid]["pra"] = x["pts"] + x["reb"] + x["ast"]
    res_games = {}
    for gid, g in games.items():
        if g["completed"]:
            res_games[gid] = {"home": g["home"]["score"], "away": g["away"]["score"]}

    def outcome(pk):
        if pk.get("stat"):
            r = res_players.get(pk["pid"])
            if r is None:
                return "void" if res_games.get(pk["game_id"]) else "pending"
            return "hit" if r[pk["stat"]] >= pk["t"] else "miss"
        return game_outcome(pk["leg"], res_games.get(pk["game_id"]))

    summary = {"picks": 0, "hits": 0, "by_grade": {}}
    pick_results = {}
    for pk in slate["picks"]:
        o = outcome(pk)
        pick_results[pk["id"]] = o
        if o in ("hit", "miss"):
            summary["picks"] += 1
            summary["hits"] += o == "hit"
            bg = summary["by_grade"].setdefault(pk["grade"], [0, 0])
            bg[0] += o == "hit"
            bg[1] += 1
    for g in slate["games"]:
        for pk in g["picks"]:
            pick_results[pk["id"]] = game_outcome(pk["leg"], res_games.get(g["id"]))
    for c in slate["parlays"]:
        outs = [pick_results.get(l["id"], "pending") for l in c["legs"]]
        c["result"] = "miss" if "miss" in outs else "pending" if "pending" in outs else "hit"
    return {"date": ds, "players": res_players, "games": res_games, "pick_results": pick_results,
            "parlay_results": [{"name": c["name"], "result": c["result"]} for c in slate["parlays"]],
            "summary": summary, "checked": datetime.now(timezone.utc).isoformat()}


def game_outcome(leg, score):
    if not score or score.get("home") is None:
        return "pending"
    margin_home = score["home"] - score["away"]
    m = margin_home if leg["side"] == "home" else -margin_home
    if leg["type"] == "ml":
        return "hit" if m > 0 else "miss"
    if leg["type"] == "spread":
        covered = m > leg["line"]
        return "hit" if covered == (leg.get("pick") == "YES") else "miss"
    return "pending"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, San Antonio time)")
    ap.add_argument("--demo", action="store_true", help="use fake data for testing")
    ap.add_argument("--out", default=os.path.join(HERE, "docs", "data"))
    args = ap.parse_args()

    day = date.fromisoformat(args.date) if args.date else datetime.now(ET).date()
    if args.demo:
        import demo
        fetcher = Fetcher(mock=demo.Mock(day))
    else:
        fetcher = Fetcher()
    espn = ESPN(fetcher)
    data_dir = args.out

    # today's picks
    slate = build(day, espn, data_dir)
    slate["demo"] = bool(args.demo)
    slate["early"] = False
    write_json(os.path.join(data_dir, "slates", f"{day.isoformat()}.json"), slate)

    # tomorrow's early look (teams playing tonight will be on a back-to-back)
    tomorrow = day + timedelta(days=1)
    playing_tonight = {g[s]["abbr"] for g in slate["games"] if g.get("state") != "post" for s in ("home", "away")}
    slate2 = build(tomorrow, espn, data_dir, prev_teams=playing_tonight)
    slate2["demo"] = bool(args.demo)
    slate2["early"] = True
    write_json(os.path.join(data_dir, "slates", f"{tomorrow.isoformat()}.json"), slate2)

    # the page opens on today, or on tomorrow when there are no games today
    write_json(os.path.join(data_dir, "latest.json"), slate if slate["games"] else slate2)

    # check results for the last 3 days (games finish late, stats get fixed)
    hist_path = os.path.join(data_dir, "history.json")
    history = {}
    if os.path.exists(hist_path):
        with open(hist_path) as f:
            history = json.load(f)
    for back in (1, 2, 3):
        d = day - timedelta(days=back)
        r = settle(d, espn, data_dir)
        if r:
            write_json(os.path.join(data_dir, "results", f"{d.isoformat()}.json"), r)
            history[d.isoformat()] = {"summary": r["summary"], "parlays": r["parlay_results"]}
    write_json(hist_path, dict(sorted(history.items())))
    index = sorted(f[:-5] for f in os.listdir(os.path.join(data_dir, "slates")) if f.endswith(".json"))
    write_json(os.path.join(data_dir, "index.json"), {"slates": index})
    log(f"done. web requests made: {fetcher.calls}")


if __name__ == "__main__":
    main()
