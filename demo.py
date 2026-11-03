"""Fake ESPN-shaped data for testing the app with no internet. Players are made up."""
import random
import zlib
from datetime import date, timedelta

TEAMS = [
    ("1", "MEM", "Memphis Grizzlies", "Grizzlies", "Memphis"),
    ("2", "SA", "San Antonio Spurs", "Spurs", "San Antonio"),
    ("3", "MIL", "Milwaukee Bucks", "Bucks", "Milwaukee"),
    ("4", "NY", "New York Knicks", "Knicks", "New York"),
    ("5", "DEN", "Denver Nuggets", "Nuggets", "Denver"),
    ("6", "BOS", "Boston Celtics", "Celtics", "Boston"),
]
OTHER = ["LAL", "GS", "PHX", "DAL", "MIA", "OKC", "HOU", "MIN", "SAC", "IND", "CLE", "ATL", "ORL", "CHI"]
FIRST = ["Marcus", "Tyrese", "Jalen", "Andre", "Dorian", "Kendall", "Malik", "Isaiah", "Caleb", "Darius"]
LAST = ["Hale", "Brooks", "Okafor", "Vance", "Reyes", "Whitaker", "Lindqvist", "Mensah", "Carter", "Doyle"]
POS = ["PG", "SG", "SF", "PF", "C", "G", "F", "SG", "PF", "C"]
# (pts, reb, ast, 3pm, minutes) per roster slot
ROLES = [(26, 5, 7, 3.0, 35), (21, 4, 4, 2.6, 33), (17, 6, 3, 1.8, 31), (14, 8, 2, 1.0, 29),
         (12, 10, 2, 0.3, 27), (10, 3, 3, 1.5, 22), (8, 4, 2, 1.0, 20), (6, 3, 1, 0.8, 14),
         (5, 4, 1, 0.2, 12), (3, 2, 1, 0.3, 8)]


def rng(*parts):
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def team(tid):
    return next(t for t in TEAMS if t[0] == tid)


def team_json(t):
    return {"id": t[0], "abbreviation": t[1], "displayName": t[2], "shortDisplayName": t[3],
            "name": t[3], "location": t[4]}


class Mock:
    def __init__(self, day):
        self.day = day

    def pairings(self, d):
        return [(TEAMS[0], TEAMS[1]), (TEAMS[2], TEAMS[3]), (TEAMS[4], TEAMS[5])]  # (away, home)

    def __call__(self, url, params):
        if url.endswith("/scoreboard"):
            return self.scoreboard(params["dates"])
        if "/schedule" in url:
            return self.schedule(url.split("/teams/")[1].split("/")[0], int(params["season"]))
        if url.endswith("/roster"):
            return self.roster(url.split("/teams/")[1].split("/")[0])
        if url.endswith("/injuries"):
            return self.injuries()
        if "/gamelog" in url:
            return self.gamelog(url.split("/athletes/")[1].split("/")[0], int(params["season"]))
        raise RuntimeError("demo: no data for " + url)

    def scoreboard(self, ds):
        d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
        past = d < self.day
        events = []
        for i, (aw, hm) in enumerate(self.pairings(d)):
            r = rng("score", ds, i)
            hs, as_ = r.randint(98, 128), r.randint(96, 124)
            comps = []
            for side, t, sc in (("home", hm, hs), ("away", aw, as_)):
                comps.append({"homeAway": side, "team": team_json(t), "score": str(sc) if past else "0",
                              "records": [{"type": "total", "summary": f"{r.randint(3, 7)}-{r.randint(2, 6)}"}]})
            events.append({"id": f"9{ds}{i}", "date": f"{d.isoformat()}T23:30Z",
                           "status": {"type": {"state": "post" if past else "pre", "completed": past,
                                               "shortDetail": "Final" if past else "7:30 PM ET"}},
                           "competitions": [{"competitors": comps, "venue": {"fullName": f"{hm[4]} Arena"}}]})
        return {"events": events}

    def schedule(self, tid, season):
        t = team(tid)
        strength = {"1": 4, "2": 6, "3": -5, "4": 3, "5": 2, "6": 5}[tid]
        n = 9 if season == self.season() else 82
        end = self.day - timedelta(days=1) if season == self.season() else date(season, 4, 12)
        events = []
        for k in range(n):
            r = rng("sched", tid, season, k)
            d = end - timedelta(days=2 * k)
            home = r.random() < 0.5
            pf = round(r.gauss(113 + strength / 2 + (1.5 if home else -1.5), 10))
            pa = round(r.gauss(113 - strength / 2, 10))
            if pf == pa:
                pf += 1
            opp = r.choice(OTHER + [x[1] for x in TEAMS if x[0] != tid])
            events.append({"date": d.isoformat() + "T23:00Z", "competitions": [{
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"id": tid, "homeAway": "home" if home else "away", "team": {"abbreviation": t[1]},
                     "score": {"value": pf}},
                    {"id": "99", "homeAway": "away" if home else "home", "team": {"abbreviation": opp},
                     "score": {"value": pa}}]}]})
        return {"events": events}

    def season(self):
        return self.day.year + 1 if self.day.month >= 8 else self.day.year

    def roster(self, tid):
        out = []
        for i in range(10):
            r = rng("name", tid, i)
            out.append({"id": f"{tid}0{i}", "displayName": f"{r.choice(FIRST)} {r.choice(LAST)}",
                        "position": {"abbreviation": POS[i]}})
        return {"athletes": out}

    def injuries(self):
        lst = []
        for tid, slot, status in (("3", 0, "Out"), ("4", 4, "Questionable"), ("5", 2, "Out")):
            t = team(tid)
            name = self.roster(tid)["athletes"][slot]["displayName"]
            lst.append({"displayName": t[2], "injuries": [{
                "status": status, "shortComment": "Demo injury (ankle).",
                "athlete": {"displayName": name, "links": [{"href": f"https://x/id/{tid}0{slot}/x"}]}}]})
        return {"injuries": lst}

    def gamelog(self, aid, season):
        tid, slot = aid[0], int(aid[2:])
        pts, reb, ast, tpm, mins = ROLES[slot]
        cur = season == self.season()
        n = 9 if cur else 70
        end = self.day - timedelta(days=1) if cur else date(season, 4, 12)
        events, rows = {}, []
        today_opp = {"1": "SA", "2": "MEM", "3": "NY", "4": "MIL", "5": "BOS", "6": "DEN"}[tid]
        # demo: the Bucks' star has been out for the last 2 days (just got hurt)
        for k in range(n):
            r = rng("log", aid, season, k)
            d = end - timedelta(days=2 * k)
            eid = f"{aid}{season}{k}"
            home = r.random() < 0.5
            opp = today_opp if k % 9 == 4 else r.choice(OTHER)
            m = max(0, round(r.gauss(mins, 3)))
            f = m / mins if mins else 0
            p = max(0, round(r.gauss(pts * f * (1.06 if home else 0.95), pts * 0.25 + 1)))
            rb = max(0, round(r.gauss(reb * f, reb * 0.3 + 0.8)))
            a = max(0, round(r.gauss(ast * f, ast * 0.3 + 0.8)))
            t3 = max(0, round(r.gauss(tpm * f, 1.1)))
            events[eid] = {"gameDate": d.isoformat() + "T23:30:00.000+00:00", "atVs": "vs" if home else "@",
                           "opponent": {"abbreviation": opp}}
            rows.append({"eventId": eid, "stats": [str(m), f"{p // 2}-{p}", "45", f"{t3}-{t3 + 4}", "35",
                                                   "2-2", "80", str(rb), str(a), "1", "1", "2", "2", str(p)]})
        return {"labels": ["MIN", "FG", "FG%", "3PT", "3P%", "FT", "FT%", "REB", "AST", "BLK", "STL", "PF", "TO", "PTS"],
                "events": events,
                "seasonTypes": [{"displayName": f"{season - 1}-{str(season)[2:]} Regular Season",
                                 "categories": [{"type": "event", "events": rows}]}]}
