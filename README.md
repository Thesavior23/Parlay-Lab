# Parlay Lab

A phone website that finds the safest NBA player picks and game picks every day.
You place the picks on Kalshi yourself. The app only gives advice.

## What it does each day

For **every NBA game that day** it looks at every player who plays real minutes and finds
**the highest number he hits at least 80% of the time** (points, rebounds, assists,
3-pointers, and points + rebounds + assists). It also gives a bolder number (about 65%).

Each pick checks:
- Season average vs. the number
- How many of his last 10 games he hit it
- His last 5 games vs. today's opponent
- Home vs. away averages
- Injury report (his own status, and teammates who are out)
- Minutes going up or down lately
- Back-to-back games and rest days
- How many points the other team gives up
- Game pace (fast or slow game)
- Blowout risk (starters sitting in the 4th)
- His lowest game in the last 10

For each game it gives a winner pick and the safest "Team wins by X — NO" numbers,
using records, average winning margin, last 10 games, home court, rest, injuries and
head-to-head results.

It also builds parlay ideas, and the next morning it checks how every pick did
(the **Record** tab), so you can see if the app is actually right.

## Set it up (one time, about 10 minutes, free)

1. Make a free account at https://github.com.
2. Create a new repository: top-right **+** → **New repository**. Name it `parlay-lab`.
   Pick **Public** (free GitHub Pages needs public), then **Create repository**.
3. On the new repo page, click **uploading an existing file**. Drag in **everything
   inside this folder** (including the `.github` and `docs` folders), then
   **Commit changes**.
   - Hidden folders like `.github` often don't upload. Check that the repo shows a
     `.github` folder. If not: **Add file → Create new file**, type the name
     `.github/workflows/update.yml`, paste in the contents of that file from this
     folder, then **Commit changes**.
4. Turn on the website: **Settings** → **Pages** → under *Branch* pick `main` and
   the `/docs` folder → **Save**.
5. Let the robot save files: **Settings** → **Actions** → **General** → at the
   bottom pick **Read and write permissions** → **Save**.
6. Run it the first time: **Actions** tab → **Update picks** → **Run workflow**.
   Wait about 5–10 minutes until it shows a green check.
7. Open `https://YOUR-GITHUB-NAME.github.io/parlay-lab/` on your phone.
   In Safari tap **Share → Add to Home Screen** so it opens like an app.

Each update builds **today's picks and tomorrow's early picks**. Use the date menu at the
top right to switch between them. After that it updates by itself 3 times a day, on San Antonio (Central) time:
about 9 AM, 3 PM and 4:30 PM (one hour later in summer). Game times on the site show in CT.

## Test it on a computer (optional)

```
python build_data.py --demo
cd docs && python -m http.server 8000
```
Then open http://localhost:8000. Demo mode uses made-up players.
Delete `docs/data` afterwards so demo data doesn't go to your website.

## Good to know

- Chances are estimates, not promises. Watch the Record tab: if "A" picks hit
  less than about 80% over a few weeks, the numbers are too confident.
- Injury news can change right before tip-off. Check before you place a parlay.
- Every leg you add to a parlay lowers the chance it hits.
