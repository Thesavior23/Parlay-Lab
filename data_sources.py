"""Fetch NBA data from ESPN (games, player stats, schedules, injuries).

Every request is cached on disk for a short time so re-runs are fast and polite.
"""
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = timezone(timedelta(hours=-5))

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"


# ---------------------------------------------------------------- helpers

def num(v):
    """Turn ESPN/Kalshi values ("110", {"value": 110}, 110.0) into a float or None."""
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def norm_name(s):
    """'Luka Dončić Jr.' -> 'luka doncic'"""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower())
    words = [w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(words)


def et_date(iso):
    """ESPN times are UTC ('2026-11-03T00:30Z'); return the US Eastern game date 'YYYY-MM-DD'."""
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(_ET).date().isoformat()
    except ValueError:
        return iso[:10]


def season_for(d):
    """ESPN names a season by the year it ends (Nov 2026 -> 2027)."""
    return d.year + 1 if d.month >= 8 else d.year


class Fetcher:
    def __init__(self, mock=None):
        self.mock = mock  # demo mode: function(url, params) -> data
        self.calls = 0
        os.makedirs(CACHE_DIR, exist_ok=True)

    def get(self, url, params=None, ttl=600):
        params = params or {}
        if self.mock:
            return self.mock(url, params)
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        path = os.path.join(CACHE_DIR, hashlib.sha1(full.encode()).hexdigest() + ".json")
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            with open(path) as f:
                return json.load(f)
        last_err = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(full, headers={
                    "User-Agent": "Mozilla/5.0 (nba-parlay-helper)",
                    "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.calls += 1
                break
            except Exception as e:  # network hiccup: wait and retry
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        else:
            if os.path.exists(path):  # fall back to old copy
                with open(path) as f:
                    return json.load(f)
            raise last_err
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        return data


# ---------------------------------------------------------------- ESPN

class ESPN:
    def __init__(self, fetcher):
        self.f = fetcher

    def games_on(self, d):
        data = self.f.get(f"{ESPN_SITE}/scoreboard", {"dates": d.strftime("%Y%m%d")}, ttl=300)
        games = []
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            sides = {}
            for c in comp.get("competitors", []):
                t = c.get("team", {})
                rec = None
                for r in c.get("records", []) or []:
                    if r.get("type") in ("total", "overall") or r.get("name") in ("overall", "All Splits"):
                        rec = r.get("summary")
                        break
                if rec is None and c.get("records"):
                    rec = c["records"][0].get("summary")
                sides[c.get("homeAway")] = {
                    "id": str(t.get("id")),
                    "abbr": t.get("abbreviation"),
                    "name": t.get("displayName"),
                    "short": t.get("shortDisplayName") or t.get("name"),
                    "location": t.get("location"),
                    "logo": t.get("logo"),
                    "record": rec,
                    "score": num(c.get("score")),
                }
            if "home" not in sides or "away" not in sides:
                continue
            odds = (comp.get("odds") or [{}])[0] if comp.get("odds") else {}
            st = (ev.get("status") or {}).get("type", {})
            games.append({
                "id": str(ev.get("id")),
                "date": ev.get("date"),
                "home": sides["home"],
                "away": sides["away"],
                "state": st.get("state"),
                "completed": bool(st.get("completed")),
                "status_text": st.get("shortDetail") or st.get("description"),
                "venue": (comp.get("venue") or {}).get("fullName"),
                "odds_details": odds.get("details"),
                "over_under": num(odds.get("overUnder")),
            })
        return games

    def team_schedule(self, team_id, season, current=True):
        data = self.f.get(f"{ESPN_SITE}/teams/{team_id}/schedule", {"season": season},
                          ttl=3600 if current else 7 * 86400)
        out = []
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            st = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
            if not st.get("completed"):
                continue
            me = opp = None
            for c in comp.get("competitors", []):
                cid = str(c.get("id") or (c.get("team") or {}).get("id"))
                if cid == str(team_id):
                    me = c
                else:
                    opp = c
            if not me or not opp:
                continue
            pf, pa = num(me.get("score")), num(opp.get("score"))
            if pf is None or pa is None:
                continue
            out.append({
                "date": et_date(ev.get("date")),
                "home": me.get("homeAway") == "home",
                "pf": pf, "pa": pa,
                "opp": (opp.get("team") or {}).get("abbreviation"),
            })
        out.sort(key=lambda g: g["date"])
        return out

    def roster(self, team_id):
        data = self.f.get(f"{ESPN_SITE}/teams/{team_id}/roster", ttl=12 * 3600)
        items = data.get("athletes", [])
        flat = []
        for a in items:
            if isinstance(a, dict) and "items" in a:  # grouped by position
                flat.extend(a["items"])
            else:
                flat.append(a)
        out = []
        for a in flat:
            pos = (a.get("position") or {}).get("abbreviation", "")
            out.append({"id": str(a.get("id")), "name": a.get("displayName") or a.get("fullName"),
                        "pos": pos, "headshot": (a.get("headshot") or {}).get("href")})
        return out

    def injuries(self):
        """Returns {team displayName: [{name, id, status, detail, pos}]}"""
        data = self.f.get(f"{ESPN_SITE}/injuries", ttl=900)
        out = {}
        for team in data.get("injuries", []):
            tname = team.get("displayName") or (team.get("team") or {}).get("displayName")
            lst = []
            for inj in team.get("injuries", []):
                ath = inj.get("athlete") or {}
                aid = ath.get("id")
                if not aid:
                    for link in ath.get("links", []) or []:
                        m = re.search(r"/id/(\d+)", link.get("href", ""))
                        if m:
                            aid = m.group(1)
                            break
                lst.append({
                    "name": ath.get("displayName"),
                    "id": str(aid) if aid else None,
                    "status": inj.get("status") or (inj.get("type") or {}).get("description", ""),
                    "detail": inj.get("shortComment") or inj.get("longComment") or "",
                    "pos": (ath.get("position") or {}).get("abbreviation", ""),
                })
            if tname:
                out[tname] = lst
        return out

    def gamelog(self, athlete_id, season, current=True):
        """List of games (newest first): date, home, opp, min, pts, reb, ast, fg3m, stl, blk, to."""
        data = self.f.get(f"{ESPN_WEB}/athletes/{athlete_id}/gamelog", {"season": season},
                          ttl=1800 if current else 7 * 86400)
        labels = data.get("labels") or data.get("names") or []
        idx = {l.upper(): i for i, l in enumerate(labels)}
        meta = data.get("events", {}) or {}
        games = {}
        for st in data.get("seasonTypes", []) or []:
            stname = (st.get("displayName") or "").lower()
            if "preseason" in stname:
                continue
            for cat in st.get("categories", []) or []:
                for e in cat.get("events", []) or []:
                    eid = str(e.get("eventId"))
                    stats = e.get("stats") or []
                    if eid in games or not stats:
                        continue
                    m = meta.get(eid, {})

                    def g(key, _stats=stats):
                        i = idx.get(key)
                        if i is None or i >= len(_stats):
                            return 0.0
                        v = _stats[i]
                        if isinstance(v, str) and "-" in v and key in ("3PT", "FG", "FT"):
                            v = v.split("-")[0]
                        return num(v) or 0.0

                    mins = g("MIN")
                    if mins <= 0:
                        continue  # did not play
                    games[eid] = {
                        "event_id": eid,
                        "date": et_date(m.get("gameDate")),
                        "home": m.get("atVs") == "vs",
                        "opp": (m.get("opponent") or {}).get("abbreviation"),
                        "min": mins, "pts": g("PTS"), "reb": g("REB"), "ast": g("AST"),
                        "fg3m": g("3PT"), "stl": g("STL"), "blk": g("BLK"), "to": g("TO"),
                    }
        return sorted(games.values(), key=lambda x: x["date"], reverse=True)
