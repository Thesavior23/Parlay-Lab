"""The scoring brain: player picks, game predictions, and parlay ideas.

All chances here are estimates, not guarantees.
"""
import math
import statistics
from datetime import date, datetime

STAT_LABEL = {
    "pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "3-pointers made",
    "stl": "steals", "blk": "blocks", "pra": "pts + reb + ast", "pr": "pts + reb",
    "pa": "pts + ast", "ra": "reb + ast",
}
# how much the opponent's defense matters for each stat (0-1)
DEF_WEIGHT = {"pts": 1.0, "fg3m": 0.8, "pra": 0.8, "pr": 0.8, "pa": 0.8, "ast": 0.5,
              "ra": 0.4, "reb": 0.3, "stl": 0.2, "blk": 0.2}
GAME_SD = 14.0      # how much NBA final margins swing around our guess (wider = more careful)
HOME_EDGE = 2.5     # home court is worth about 2.5 points


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def stat_value(g, stat):
    if stat == "pra":
        return g["pts"] + g["reb"] + g["ast"]
    if stat == "pr":
        return g["pts"] + g["reb"]
    if stat == "pa":
        return g["pts"] + g["ast"]
    if stat == "ra":
        return g["reb"] + g["ast"]
    return g.get(stat, 0.0)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def smooth(hits, n):
    """Hit rate pulled a little toward 50% so tiny samples don't look perfect."""
    return (hits + 1) / (n + 2)


def grade(p):
    if p >= 0.80:
        return "A"
    if p >= 0.70:
        return "B"
    if p >= 0.60:
        return "C"
    return "D"


def days_between(a, b):
    try:
        return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return None


def fmt(x):
    return f"{x:.0f}" if abs(x - round(x)) < 1e-9 else f"{x:.1f}"


# ======================================================== player picks

def evaluate_prop(stat, t, logs_cur, logs_all, ctx):
    """
    stat: 'pts' etc. t: needs at least t. logs_*: newest first.
    ctx: day (YYYY-MM-DD), home (bool), opp (abbr), status, key_teammates_out,
         opp_def_factor, pace_factor, team_margin (projected, + = his team wins by), price.
    """
    label = STAT_LABEL.get(stat, stat)
    reasons = []

    def add(kind, text):
        reasons.append({"k": kind, "t": text})

    if not logs_all:
        return None
    small = len(logs_cur) < 5
    base = logs_cur if not small else logs_all[:20]
    base_vals = [stat_value(g, stat) for g in base]
    last10 = logs_all[:10]
    v10 = [stat_value(g, stat) for g in last10]
    season_avg = mean(base_vals)
    l10_avg = mean(v10)
    hits10 = sum(1 for v in v10 if v >= t)
    season_hits = sum(1 for v in base_vals if v >= t)

    pool = base if len(base) >= 8 else logs_all[:30]
    same_side = [stat_value(g, stat) for g in pool if g["home"] == ctx["home"]]
    other_side = [stat_value(g, stat) for g in pool if g["home"] != ctx["home"]]
    split_avg, other_avg = mean(same_side), mean(other_side)

    vs = [g for g in logs_all if g.get("opp") == ctx["opp"]][:5]
    vs_vals = [stat_value(g, stat) for g in vs]
    vs_hits = sum(1 for v in vs_vals if v >= t)

    min5 = mean(g["min"] for g in logs_all[:5])
    min_base = mean(g["min"] for g in base)
    min_trend = (min5 - min_base) / min_base if min_base else 0.0

    recent = [stat_value(g, stat) for g in logs_all[:20]]
    sd = statistics.pstdev(recent) if len(recent) >= 5 else math.sqrt(max(season_avg, 1)) + 1
    sd = max(sd, 0.8)
    floor10 = min(v10)
    rest = days_between(logs_all[0]["date"], ctx["day"])

    # ---- projection
    parts = [(l10_avg, 0.40), (season_avg, 0.35)]
    if split_avg is not None and len(same_side) >= 3:
        parts.append((split_avg, 0.15))
    if len(vs_vals) >= 2:
        parts.append((mean(vs_vals), 0.10))
    proj = sum(v * w for v, w in parts) / sum(w for _, w in parts)
    proj *= 1 + (ctx.get("opp_def_factor", 1.0) - 1) * DEF_WEIGHT.get(stat, 0.5)
    proj *= 1 + (ctx.get("pace_factor", 1.0) - 1) * 0.7
    if abs(min_trend) > 0.08:
        proj *= 1 + max(-0.1, min(0.1, min_trend * 0.3))
    kto = ctx.get("key_teammates_out") or []
    if kto:
        proj *= 1 + 0.04 * min(len(kto), 2)
    b2b = rest == 1
    if b2b:
        proj *= 0.97
    margin = ctx.get("team_margin")
    blowout = margin is not None and abs(margin) >= 11
    if blowout:
        proj *= 0.96

    model_p = 1 - phi((t - 0.5 - proj) / sd)
    p = 0.5 * model_p + 0.3 * smooth(hits10, len(v10)) + 0.2 * smooth(season_hits, len(base_vals))
    if len(vs_vals) >= 3:
        p = 0.85 * p + 0.15 * smooth(vs_hits, len(vs_vals))

    status = (ctx.get("status") or "").lower()
    out = "out" in status
    if out:
        p = 0.0
    elif "doubtful" in status:
        p *= 0.6
    elif any(w in status for w in ("questionable", "day-to-day", "game time", "gtd")):
        p *= 0.85
    elif "probable" in status:
        p *= 0.97
    if len(logs_all) < 5:
        p = 0.5 * p + 0.25
    p = max(0.01, min(0.99, p))

    # ---- plain-English reasons
    cushion = season_avg - t
    add("good" if cushion >= 1 else "warn" if cushion >= -0.5 else "bad",
        f"Averages {season_avg:.1f} {label} {'(recent games, few this season)' if small else 'this season'}"
        f" — needs {t}+")
    add("good" if hits10 >= 8 else "warn" if hits10 >= 6 else "bad",
        f"Hit {t}+ in {hits10} of last {len(v10)} games")
    if split_avg is not None and other_avg is not None:
        where = "at home" if ctx["home"] else "on the road"
        other = "on the road" if ctx["home"] else "at home"
        kind = "good" if split_avg >= other_avg else "warn"
        add(kind, f"{'Home' if ctx['home'] else 'Away'} game — averages {split_avg:.1f} {where}"
                  f" vs {other_avg:.1f} {other}")
    if vs_vals:
        add("good" if vs_hits >= math.ceil(len(vs_vals) * 0.6) else "warn",
            f"Last {len(vs_vals)} vs {ctx['opp']}: {', '.join(fmt(v) for v in vs_vals)}"
            f" — hit {vs_hits}/{len(vs_vals)}")
    else:
        add("info", f"No recent games vs {ctx['opp']} found")
    if abs(min_trend) >= 0.08:
        add("good" if min_trend > 0 else "bad",
            f"Minutes {'up' if min_trend > 0 else 'down'} {abs(min_trend):.0%} lately"
            f" ({min5:.0f} per game, last 5)")
    add("good" if floor10 >= t else "warn" if floor10 >= t - 2 else "info",
        f"Lowest game in last 10: {fmt(floor10)}")
    if ctx.get("status"):
        add("bad", f"Injury report: listed {ctx['status']}")
    if kto:
        add("good", f"Teammate{'s' if len(kto) > 1 else ''} out: {', '.join(kto)} — more chances for him")
    f = ctx.get("opp_def_factor", 1.0)
    if abs(f - 1) >= 0.025 and DEF_WEIGHT.get(stat, 0) >= 0.5:
        add("good" if f > 1 else "bad",
            f"{ctx['opp']} gives up {abs(f - 1):.0%} {'more' if f > 1 else 'fewer'} points than average")
    pf = ctx.get("pace_factor", 1.0)
    if abs(pf - 1) >= 0.02:
        add("good" if pf > 1 else "warn", "Fast, high-scoring matchup" if pf > 1 else "Slow, low-scoring matchup")
    if b2b:
        add("bad", "2nd night of a back-to-back — tired legs, stars sometimes rest")
    elif rest is not None and rest >= 3:
        add("good", f"Well rested ({rest - 1} days off)")
    if blowout:
        add("bad", f"Blowout risk (projected {abs(margin):.0f}-pt game) — starters may sit the 4th")
    price_yes = ctx.get("price")
    edge = None
    if price_yes:
        edge = p - price_yes
        add("good" if edge >= 0.05 else "bad" if edge <= -0.05 else "info",
            f"Kalshi YES {price_yes * 100:.0f}¢ vs our {p * 100:.0f}% — "
            + ("good value" if edge >= 0.05 else "overpriced" if edge <= -0.05 else "fair price"))
    if len(logs_all) < 8:
        add("warn", "Small sample — few games to judge from")

    return {
        "p": round(p, 3), "score": round(p * 100), "grade": grade(p), "out": out,
        "edge": round(edge, 3) if edge is not None else None,
        "proj": round(proj, 1), "sd": round(sd, 1),
        "season_avg": round(season_avg, 1), "l10_avg": round(l10_avg, 1),
        "hits10": hits10, "n10": len(v10),
        "last10": [{"d": g["date"], "o": g["opp"], "h": g["home"], "v": stat_value(g, stat)} for g in last10],
        "vs_opp": [{"d": g["date"], "v": stat_value(g, stat), "h": g["home"]} for g in vs],
        "split": {"home": round(split_avg if ctx["home"] else other_avg, 1) if split_avg is not None and other_avg is not None else None,
                  "away": round(other_avg if ctx["home"] else split_avg, 1) if split_avg is not None and other_avg is not None else None},
        "min5": round(min5, 1), "min_season": round(min_base, 1),
        "floor10": floor10, "rest": rest, "b2b": b2b, "blowout": blowout,
        "reasons": reasons,
    }


# ======================================================== games

def team_profile(cur, prev, day):
    """cur/prev: completed games this season / last season (oldest first)."""
    n = len(cur)
    m_cur = mean(g["pf"] - g["pa"] for g in cur) or 0.0
    m_prev = mean(g["pf"] - g["pa"] for g in prev) or 0.0
    w = n / (n + 12)
    rating = (w * m_cur + (1 - w) * m_prev * 0.6) * 0.85
    src = cur if n >= 3 else (cur + prev[-20:])
    l10 = cur[-10:]
    home_g = [g for g in cur if g["home"]]
    away_g = [g for g in cur if not g["home"]]

    def rec(gs):
        w_ = sum(1 for g in gs if g["pf"] > g["pa"])
        return f"{w_}-{len(gs) - w_}"

    last_date = cur[-1]["date"] if cur else None
    return {
        "n": n, "rating": round(rating, 2), "margin": round(m_cur, 1), "prev_margin": round(m_prev, 1),
        "pf": mean(g["pf"] for g in src) or 112.0, "pa": mean(g["pa"] for g in src) or 112.0,
        "record": rec(cur), "l10_record": rec(l10), "l10_margin": round(mean(g["pf"] - g["pa"] for g in l10) or 0, 1),
        "home_record": rec(home_g), "away_record": rec(away_g),
        "home_margin": round(mean(g["pf"] - g["pa"] for g in home_g) or 0, 1),
        "away_margin": round(mean(g["pf"] - g["pa"] for g in away_g) or 0, 1),
        "rest": days_between(last_date, day) if last_date else None,
        "games": cur + prev,
    }


def injury_impact(players):
    """players: [{name, status, ppg, mpg, last_played_days}] -> points lost, list of notes."""
    total, notes = 0.0, []
    for pl in players:
        s = (pl.get("status") or "").lower()
        if "out" in s:
            w = 1.0
        elif "doubtful" in s:
            w = 0.75
        elif "questionable" in s or "day-to-day" in s:
            w = 0.4
        else:
            continue
        ppg, mpg = pl.get("ppg") or 0, pl.get("mpg") or 0
        imp = min(6.0, max(0, ppg - 6) * 0.35 + max(0, mpg - 15) * 0.05)
        lp = pl.get("last_played_days")
        if lp is None or lp > 10:
            imp *= 0.4  # been out a while: team results already show it
        imp *= w
        total += imp
        if imp >= 0.5:
            notes.append(f"{pl['name']} ({pl['status']}, {ppg:.0f} ppg)")
    return round(total, 1), notes


def predict_game(game, hp, ap, h_inj, a_inj, day, league_pf, market_home=None):
    reasons = []

    def add(kind, text):
        reasons.append({"k": kind, "t": text})

    h, a = game["home"], game["away"]
    rest_adj = 0.0
    if hp["rest"] == 1:
        rest_adj -= 1.5
    if ap["rest"] == 1:
        rest_adj += 1.5
    form = 0.0
    if hp["n"] >= 5 and ap["n"] >= 5:
        form = 0.15 * ((hp["l10_margin"] - hp["margin"]) - (ap["l10_margin"] - ap["margin"]))
    model = hp["rating"] - ap["rating"] + HOME_EDGE + rest_adj + form - h_inj[0] + a_inj[0]
    model = 16 * math.tanh(model / 16)  # keep early-season blowout guesses in check
    margin = model if market_home is None else 0.5 * model + 0.5 * market_home
    p_home = phi(margin / GAME_SD)
    total = (hp["pf"] + ap["pa"]) / 2 + (ap["pf"] + hp["pa"]) / 2

    add("info", f"{h['abbr']} at home — home court is worth about {HOME_EDGE} points")
    if market_home is not None:
        fav_abbr = h["abbr"] if market_home >= 0 else a["abbr"]
        add("info", f"Betting line: {fav_abbr} by {abs(market_home):g} (our numbers alone: "
                    f"{h['abbr'] if model >= 0 else a['abbr']} by {abs(model):.1f}) — we blend both")
    add("info", f"Season: {h['abbr']} {hp['record']} (avg margin {hp['margin']:+.1f}) · "
                f"{a['abbr']} {ap['record']} (avg margin {ap['margin']:+.1f})")
    add("info", f"Last 10: {h['abbr']} {hp['l10_record']} · {a['abbr']} {ap['l10_record']}")
    add("info", f"{h['abbr']} at home: {hp['home_record']} · {a['abbr']} on the road: {ap['away_record']}")
    if hp["n"] < 6 or ap["n"] < 6:
        add("warn", "Early season — leaning partly on last season's results")
    for side, prof in ((h, hp), (a, ap)):
        if prof["rest"] == 1:
            add("bad", f"{side['abbr']} on a back-to-back (tired)")
        elif prof["rest"] and prof["rest"] >= 3:
            add("good", f"{side['abbr']} well rested")
    for side, inj in ((h, h_inj), (a, a_inj)):
        if inj[1]:
            add("bad", f"{side['abbr']} missing: {', '.join(inj[1])} (≈{inj[0]:.1f} pts weaker)")
    h2h = [g for g in hp["games"] if g["opp"] == a["abbr"]][-5:]
    if h2h:
        w = sum(1 for g in h2h if g["pf"] > g["pa"])
        add("info", f"Head-to-head (last {len(h2h)}): {h['abbr']} won {w}, "
                    + ", ".join(f"{int(g['pf'])}-{int(g['pa'])}" for g in reversed(h2h)))

    fav = "home" if margin >= 0 else "away"
    return {
        "margin_home": round(margin, 1), "p_home": round(p_home, 3), "total": round(total, 1),
        "fav": fav, "fav_p": round(max(p_home, 1 - p_home), 3),
        "toss_up": abs(margin) < 3.5, "pace_factor": round(total / (2 * league_pf), 3),
        "reasons": reasons,
    }


def margin_for(pred, side):
    return pred["margin_home"] if side == "home" else -pred["margin_home"]


def spread_probs(pred, side, line):
    """Chance that `side` wins by MORE than `line` (YES) and the opposite (NO)."""
    m = margin_for(pred, side)
    p_yes = 1 - phi((line - m) / GAME_SD)
    return p_yes, 1 - p_yes


def game_picks(game, pred):
    """Winner pick + the safest 'wins by X — NO' numbers for both teams."""
    picks = []
    h, a = game["home"], game["away"]
    teams = {"home": h, "away": a}
    other = {"home": "away", "away": "home"}

    def pick(kind, desc, p, explain, leg):
        picks.append({"kind": kind, "label": desc, "p": round(p, 3), "grade": grade(p),
                      "explain": explain, "leg": leg, "game_id": game["id"],
                      "matchup": f"{a['abbr']} @ {h['abbr']}"})

    fav, dog = pred["fav"], other[pred["fav"]]
    p = pred["fav_p"]
    pick("winner", f"{teams[fav]['short']} to win", p,
         f"We give {teams[fav]['abbr']} a {p:.0%} chance to win."
         + (" This one is close to a coin flip — a 'wins by X — NO' pick is safer." if pred["toss_up"] else ""),
         {"type": "ml", "side": fav, "team": teams[fav]["abbr"]})

    # safest "wins by X.5 — NO": smallest line where NO still has >= 80% chance
    for side in (fav, dog):
        t = teams[side]
        chosen = None
        for k in range(1, 40):
            line = k + 0.5
            if spread_probs(pred, side, line)[1] >= 0.80:
                chosen = line
                break
        if chosen is None:
            continue
        p_no = spread_probs(pred, side, chosen)[1]
        pick("spread", f"{t['short']} win by {chosen:g}+ — NO", p_no,
             f"Hits if {t['abbr']} wins by {int(chosen)} or less, OR {t['abbr']} loses. "
             f"Kalshi may not have exactly {chosen:g} — pick the closest line that is HIGHER (safer).",
             {"type": "spread", "side": side, "team": t["abbr"], "line": chosen, "pick": "NO"})

    # bolder: favorite actually covers a small number
    mf = margin_for(pred, fav)
    if mf >= 6:
        line = max(1.5, math.floor(mf - 5) + 0.5)
        p_yes = spread_probs(pred, fav, line)[0]
        if p_yes >= 0.62:
            pick("spread", f"{teams[fav]['short']} win by {line:g}+ — YES", p_yes,
                 f"Bolder: hits only if {teams[fav]['abbr']} wins by more than {line:g}.",
                 {"type": "spread", "side": fav, "team": teams[fav]["abbr"], "line": line, "pick": "YES"})
    picks.sort(key=lambda x: -x["p"])
    return picks


# smallest line worth suggesting for each stat (anything lower is too obvious to bother)
MIN_LINE = {"pts": 10, "reb": 4, "ast": 3, "fg3m": 2, "pra": 15}
SAFE_P, BOLD_P = 0.80, 0.65


def find_lines(stat, logs_cur, logs_all, ctx):
    """Find the highest number the player hits with >=80% (safe) and >=65% (bold)."""
    if not logs_all:
        return None
    avg = mean(stat_value(g, stat) for g in logs_all[:15])
    top = int(math.ceil(avg * 1.6)) + 2
    safe = bold = None
    probs = {}
    for t in range(1, max(top, MIN_LINE.get(stat, 1)) + 1):
        r = evaluate_prop(stat, t, logs_cur, logs_all, ctx)
        if r is None or r["out"]:
            return None
        probs[t] = r
        if r["p"] >= SAFE_P:
            safe = t
        if r["p"] >= BOLD_P:
            bold = t
    if safe is None or safe < MIN_LINE.get(stat, 1):
        return None
    res = dict(probs[safe])
    res["t"] = safe
    res["bold"] = {"t": bold, "p": probs[bold]["p"]} if bold and bold > safe else None
    res["ladder"] = [{"t": t, "p": probs[t]["p"]} for t in sorted(probs)
                     if t >= MIN_LINE.get(stat, 1) and probs[t]["p"] >= 0.5]
    return res


# ======================================================== parlays

def pos_group(pos):
    pos = (pos or "").upper()
    if "C" in pos:
        return "C"
    if "F" in pos:
        return "F"
    return "G"


def conflicts(leg, chosen):
    """Returns a reason string if `leg` clashes with already chosen legs."""
    for c in chosen:
        if leg["game_id"] == c["game_id"] and leg["kind"] == "game" and c["kind"] == "game":
            return "two game-result picks on one game"
        if leg["kind"] == "prop" and c["kind"] == "prop":
            if leg["player_id"] == c["player_id"]:
                return "same player twice"
            if (leg["team"] == c["team"] and leg["stat"] == c["stat"] and leg["stat"] in ("reb", "ast", "blk")
                    and pos_group(leg["pos"]) == pos_group(c["pos"])):
                return "teammates fighting for the same stat"
    same_game = sum(1 for c in chosen if c["game_id"] == leg["game_id"])
    if same_game >= 2:
        return "too many picks from one game"
    return None


def combo(name, legs, note):
    p = 1.0
    cost = 1.0
    priced = True
    for l in legs:
        p *= l["p"]
        if l.get("price"):
            cost *= l["price"]
        else:
            priced = False
    helps = []
    for i, x in enumerate(legs):
        for y in legs[i + 1:]:
            if x["kind"] == y["kind"] == "prop" and x["team"] == y["team"] and {x["stat"], y["stat"]} == {"ast", "pts"}:
                helps.append(f"{x['name']} & {y['name']}: passes turn into points — these help each other")
            if {x["kind"], y["kind"]} == {"prop", "game"} and x["game_id"] == y["game_id"]:
                g, pr = (x, y) if x["kind"] == "game" else (y, x)
                if g.get("fav_team") == pr["team"] and pr["stat"] in ("pts", "pra", "pa", "pr"):
                    helps.append(f"{pr['name']} scoring big helps {pr['team']} win")
    return {"name": name, "note": note, "legs": legs, "p": round(p, 3),
            "cost": round(cost, 3) if priced else None,
            "payout": round(1 / cost, 2) if priced and cost > 0 else None, "helps": helps}


def build_parlays(candidates):
    """candidates: unified legs (props + game picks) with p, price, edge, game_id ..."""
    pool = [c for c in candidates if c["p"] >= 0.62 and not c.get("out")
            and (c.get("price") is None or c["price"] <= 0.92)]
    # keep only the best line per player+stat so ideas aren't repetitive
    best = {}
    for c in pool:
        k = (c.get("player_id") or c["label"], c.get("stat") or c["kind"])
        score = c["p"] + 0.5 * (c.get("edge") or 0)
        if k not in best or score > best[k][0]:
            best[k] = (score, c)
    pool = [v[1] for v in best.values()]

    def greedy(sorted_pool, n, require_games_spread=True):
        chosen = []
        for c in sorted_pool:
            if conflicts(c, chosen):
                continue
            if c["kind"] == "game" and any(x["kind"] == "game" for x in chosen):
                continue
            if require_games_spread and any(x["game_id"] == c["game_id"] for x in chosen) and \
                    len({x["game_id"] for x in pool}) > n:
                continue
            chosen.append(c)
            if len(chosen) == n:
                break
        return chosen if len(chosen) == n else None

    by_safe = sorted(pool, key=lambda c: -c["p"])
    by_value = sorted([c for c in pool if c.get("edge") is not None and c["p"] >= 0.65],
                      key=lambda c: -(c["edge"]))
    out = []
    for n, name, note in ((2, "Safest 2-pick", "The two picks we trust most, from different games."),
                          (3, "Safest 3-pick", "Three strong picks from different games."),
                          (4, "Safe 4-pick", "Bigger payout, still only high-confidence picks.")):
        legs = greedy(by_safe, n)
        if legs:
            out.append(combo(name, legs, note))
    legs = greedy(by_value, 3)
    if legs:
        out.append(combo("Best value 3-pick", legs,
                         "Picks where Kalshi's price is cheaper than our chance — more payout for the risk."))
    games = [c for c in by_safe if c["kind"] == "game"]
    props = [c for c in by_safe if c["kind"] == "prop"]
    if games and props:
        g = games[0]
        mate = next((p for p in props if p["game_id"] == g["game_id"] and p["team"] == g.get("fav_team")), None)
        if mate:
            out.append(combo("Same-game stack", [g, mate],
                             "A team pick plus its own player — they tend to win together."))
        other = [p for p in props if p["game_id"] != g["game_id"]][:2]
        if len(other) == 2 and not conflicts(other[1], [g, other[0]]):
            out.append(combo("Game + players mix", [g] + other,
                             "One safe game pick with two strong player picks from other games."))
    ast_pts = None
    for x in props:
        if x["stat"] != "ast":
            continue
        y = next((p for p in props if p["team"] == x["team"] and p["stat"] == "pts"
                  and p["player_id"] != x["player_id"]), None)
        if y:
            ast_pts = [x, y]
            break
    if ast_pts:
        out.append(combo("Passer + scorer pair", ast_pts,
                         "One player's assists feed the other's points — they help each other."))
    return out
