# Ping-pong

A small office scorekeeper with a Flask/HTML interface and shared CSV storage.
The browser UI uses simple tabs, tables, and controls. There
are no accounts or administrator roles: everyone who can reach it can edit scores.
This branch is web-only. The original desktop version remains on `main`.

## Run locally

With Docker and the Compose plugin installed, run:

```sh
./build.sh
```

This builds the images and runs both servers in the current terminal:
**http://localhost:8080/** uses `data/`; **http://localhost:8081/** uses
`test_data/`. Logs stay visible. Press Ctrl+C to stop both containers; the CSV
files remain in place. Run this from a terminal (it does not launch a new terminal
window). The script works from any directory and uses the local Compose settings,
not the NAS URL prefixes. Stop any standalone servers occupying these ports first.
The attached behavior follows [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

Alternatively, without Docker, install the web dependencies and start Gunicorn:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/gunicorn --bind 127.0.0.1:8080 --workers 1 --threads 1 'web:create_app()'
```

Open **http://localhost:8080/**. Stop with Ctrl+C. This uses the **existing
`data/players.csv`, `data/matches.csv`, and `data/tournaments.csv` beside `web.py`**,
regardless of the working directory. Browser edits save to those files immediately;
this is not demo data. Missing tables are created on the first relevant save.
No CSV format conversion or database migration is needed.

Use one Gunicorn worker and one thread: requests are short and this serializes
CSV operations within the service. Existing directory locks still protect against
other app instances. A service restart expires open forms; refresh before saving.
Pages refresh after saves and when switching tabs. Use Refresh to see edits from
other computers. Live scoring retains revision checks to reject stale clicks.
Destructive actions ask for confirmation. JavaScript supplies those confirmations,
local timestamps, and duplicate-click prevention; keep it enabled in your browser.

## Container / QNAP deployment

The image contains Python, Flask, Gunicorn, and the existing rules/storage modules;
it runs both web services. With Docker Compose installed:

```sh
docker compose up --build -d
docker compose logs -f
# Stop before backing up the tables:
docker compose down
```

Compose starts two independent services:

| Service | Local URL | Host tables | NAS URL (planned) |
|---|---|---|---|
| Main | http://localhost:8080/ | `data/` | http://192.168.88.131/immer-pong/ |
| Test | http://localhost:8081/ | `test_data/` | http://192.168.88.131/ping-pong-test/ |

`test_data/` contains fictional players, sample results, an in-progress live match,
and a demo tournament. It uses no office records; test edits never sync back to `data/`. Test CSV files are ignored by Git;
copy them separately when deploying. If empty, a service starts with no players
or matches. The test interface is marked **Test server**. Cookies are separate,
so switching between ports does not invalidate the other service's forms.

To run the test service without Docker, in a second terminal:

```sh
.venv/bin/gunicorn --bind 127.0.0.1:8081 --workers 1 --threads 1 "web:create_app('test_data')"
```

Both relative data paths resolve beside `web.py`. Compose mounts `./data` at
`/app/data` and `./test_data` at `/app/test_data` in separate containers. The image
contains neither set of CSV files. Rebuilding preserves both host directories.

### QNAP Container Station deployment

`compose.nas.yaml` is a **standalone** application definition. Paste that one file
into Container Station; do not combine it with `compose.yaml`. Local development
continues to use `./build.sh` and ports 8080/8081.

1. Use the existing project folder on the NAS, containing `Dockerfile`,
   `requirements.txt`, Python sources, `templates/`, `static/`, and the current
   `data/` and `test_data/` directories. No image export/import is needed.
2. Replace **every** `/share/Container/immer-pong` in `compose.nas.yaml` with
   that folder's actual absolute path **on the NAS**, not its Linux client mount
   path. Both services build from its Dockerfile on the NAS. The main service
   mounts the existing `data/` CSVs; the test service mounts `test_data/`.
   These are live bind mounts, not copies. Rebuilds do not reset the tables.
   Missing data directories cause deployment to fail rather than silently
   creating an empty directory. The NAS needs internet access for the base image
   and Python dependencies during the build.
3. The image uses UID/GID **1000:1000**. Set `user: "<uid>:<gid>"` on each service
   if needed, using a NAS account that can write the corresponding data directory.
4. In **Container Station → Applications → Create Application**, use the name
   **`immer-ping`**, paste the entire `compose.nas.yaml`, validate it, and create
   the app. This is the Compose project name; its two services are `ping-pong`
   and `ping-pong-test`.
   Container Station must support Compose source builds (`build`); validation of
   this file locally does not verify the installed QNAP version's build support.
   See the [QNAP Container Station guide](https://www.qnap.com/en/how-to/tutorial/article/how-to-use-container-station-3).
   Rebuild the application image when deploying source changes.
5. Configure the NAS HTTP reverse proxy on port 80:

   | URL | Backend on NAS host | Tables |
   |---|---|---|
   | `http://192.168.88.131/immer-pong/` | `http://127.0.0.1:18080` | `data/` |
   | `http://192.168.88.131/ping-pong-test/` | `http://127.0.0.1:18081` | `test_data/` |

   Preserve the entire request path. Redirect each bare prefix to its trailing-slash
   URL. The app handles its prefix for links, assets, redirects, forms, and cookies.
   These backend ports avoid using QNAP's usual management port 8080.

### Updating the existing NAS application

After saving source changes in the shared project folder, run on the NAS via SSH:

```sh
cd /share/storage_das1/heitor/ping-pong
sudo docker compose -p immer-ping -f compose.nas.yaml up -d --build
```

Always use `-p immer-ping` to target the existing Container Station application.
A different project name creates a separate application and can fail because
ports 18080/18081 are already allocated. The command rebuilds the images and
replaces containers as needed; `data/` and `test_data/` remain in place. Restarting
alone does not load source changes copied into the images.

### Optional NAS reverse proxy

For an nginx proxy running on the NAS host, these locations belong inside the
server block for `192.168.88.131` (the `proxy_pass` URLs have no trailing slash):

```nginx
location = /immer-pong { return 301 /immer-pong/; }
location /immer-pong/ {
    proxy_pass http://127.0.0.1:18080;
    proxy_set_header Host $http_host;
}
location = /ping-pong-test { return 301 /ping-pong-test/; }
location /ping-pong-test/ {
    proxy_pass http://127.0.0.1:18081;
    proxy_set_header Host $http_host;
}
```

This follows nginx's [path-preserving proxy behavior](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass).
Keep the backend ports bound to loopback when the proxy runs on the host. A proxy
in another container requires Docker networking instead of these loopback targets.
Before rollout, confirm which QNAP service owns port 80 and whether its proxy
supports path-based routing; the exact NAS configuration is still pending.
No NAS settings have been changed. Keep access within the office network.

## Tests

Tests create temporary ledgers and do not modify the office CSV files:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## Use

1. Add members in **Players**.
2. In **Matches**, live scoring is the default. Check **Enter final result** only
   when recording a finished score directly.
3. Open **Leaderboards** for rankings and totals. Use **Refresh** to pick up other
   people's changes; changing tabs also refreshes.
4. Use **Tournament** to create a named event, select its players and system, and
   start the highlighted next match from the bracket. Tournament matches always
   use live scoring; finishing one unlocks the next fixture.

Delete an incorrect match from history and register it again. The replacement
counts at its new registration position. Final-result entry requires a winner
but does not enforce a target or overtime rule. Removing a player retires them;
restore them from Players when needed. Renaming updates their name in all views.

Elo starts at 1000. For a match between players 1 and 2, with pre-match ratings
$R_1$ and $R_2$, player 1's expected score is:

$$
E_1 = \frac{1}{1 + 10^{(R_2 - R_1)/400}}
$$

The actual score $S_1$ is 1 for a win and 0 for a loss. With $K = 32$, the
rating updates are:

$$
\Delta R_1 = K(S_1 - E_1), \qquad R_1' = R_1 + \Delta R_1, \qquad R_2' = R_2 - \Delta R_1
$$

Each player's first rating is 1000. Point margin does not affect Elo. All
historical matches, including retired players' matches, are replayed in CSV row
order. Only active players appear in the leaderboard; players without matches
are unranked. Exact Elo determines ranking, with alphabetical ties; the
displayed rating is rounded.
Match history places **P1 ELO** beside Player 1 and **P2 ELO** beside Player 2.
Each cell shows the pre-match rating in the normal text color, followed by its
change in green for a gain or red for a loss, for example `1000.00 (+16.00)`.
Values are recalculated when history changes, with two decimal places displayed.

## Live scoring

Select two players and a target (at least 2, default 7), then click **Create match**.
Use the named **+1 point** buttons as points are played. The app automatically
declares the winner and adds the completed result to history, Elo, and the Last 5
results. In-progress matches do not affect leaderboards. Live scoring supports
both ordinary matches and tournament fixtures; a tournament has one active
fixture at a time.

At a tie one point below the target (for example, **6–6 when playing to 7**),
overtime begins. Each overtime round contains two points:

- **2–0 or 0–2:** the player who won both points wins the match.
- **1–1:** reset the overtime score to **0–0** and start a new two-point round.

Consecutive points across a reset boundary do not win: A, B, B, A is two split
rounds, so neither player has won. The screen shows cumulative totals separately
from the current overtime score. History and leaderboard point totals include
**all actual points**, including split overtime rounds; resets never erase them.

Each click is saved immediately. Use **Resume / review** to continue a match after
closing the app, or from another computer. Use **Refresh** to load other people's
changes. If another client has scored since your last view, your click is rejected
and you must refresh before trying again. Players who retire after a match starts
can still finish it.

**Undo last point** corrects a misclick and restores the prior overtime state.
Undoing a winning point requires confirmation and reopens the match, removing its
result from the leaderboard until it finishes again. Finished live matches enter
Elo history in completion order, not creation order. Re-finishing after an undo
places the result at its new completion position. **Delete live match** removes
an abandoned or mistaken match and its point log.

**Point history** shows the scorer, time, cumulative score, and overtime round
for every recorded point. It is available in Live scoring and for completed
matches in the history table. Final-result-only matches have no point sequence.
Player Stats includes a Clutch score computed from complete point histories.

## Tournaments

- **Single-Elimination:** an even number of players, minimum two. A loss eliminates
  a player. Non-power-of-two fields (such as six) get first-round byes.
- **Double-Elimination with Group stage:** any number of players, minimum four.
  Choose the number of groups; every group must contain at least two players.
  Each group plays a single round-robin. Only its winner enters the upper
  bracket; everyone else enters the lower bracket with one
  playoff loss. Group losses do not otherwise carry into the playoffs. Upper
  losers drop into lower; a lower-bracket loss eliminates the player. There is
  **one grand final, with no reset**, even if the lower-bracket finalist wins.
- **Round-robin:** any player count of at least two, including odd counts. Each
  pair plays once; the final table determines the winner.

The draw is randomized once at creation and saved. Groups are balanced by
distributing the draw in turn across groups. Group and round-robin tables sort
by wins, point difference, points scored, then saved draw order. The last tie
breaker is stable across renames. Playoff seeding uses group placing, wins, point
difference, points scored, then saved draw order. Both brackets award first-round
byes to their best seeds as needed. With nine players in three groups, the three
winners fill a four-slot upper bracket (one bye); the other six enter the lower
bracket, where the two best runners-up receive byes. Each upper round runs alongside a lower elimination round, followed
by a lower round mixing its survivors with the upper losers. Further lower rounds
are played if needed to determine a finalist. Byes are not matches and do not
affect Elo.

Existing tournaments retain their original qualification and seeding rules. New
tournaments save `format_version=2`; older CSV rows default to version 1 and are
upgraded without changing their format when another tournament is created.

The bracket displays all played and currently available rounds, with the next
playable fixture highlighted. Later rounds appear once their prerequisites finish.
If the highlighted match cannot be played yet, use **Skip for now** to move to
another available fixture; skipped matches remain in the bracket and can be
started with **Play now** later.
Every actual tournament result appears in **Matches**, with its tournament name,
and counts toward the global leaderboard and Elo in registration order.
Participants are fixed at creation; a player retired afterward can still finish
an existing tournament. Completed events remain in the selector for review, along
with all scores, group tables, brackets, and their winner.

Use **Undo latest result** to correct an event. Tournament results must be undone
in reverse registration order, including when deleting through Matches. This
prevents changing an early winner while keeping later matches for the wrong
players. Undoing the final reopens a completed tournament. Re-entering results
adds them at the end of global history, so Elo is recalculated in that new order.

## Shared data and recovery

`data/players.csv`, `data/matches.csv`, and `data/tournaments.csv` are UTF-8 tables created as needed. Players have stable IDs, names, and `active` (1/0). Matches
have IDs, UTC timestamps, player IDs, integer scores, and optional tournament and
fixture IDs. The `first_server` column stores the starting server’s player ID;
existing matches default to Player 1. New live matches show a “{player name} to serve”
button for each player at 0–0. Choose one to enable point and ACE buttons.
The winner of each point serves next, and serve statistics use the saved first server.
Live matches also use these columns in the same match table:
`target_points`, `point_log`, `status`, and `revision`. `point_log` is an ordered
JSON list of objects containing the scoring player's stable ID (`player`) and UTC
`timestamp`. List position is the point order; overtime and cumulative totals can
be reconstructed from the target and sequence. Undo removes the mistaken last
event, and `revision` increases on every server selection, point, or undo to reject stale submissions.
`status` is `in_progress` or `completed`. The match `timestamp` records creation
until the first finish, then its most recent completion. Existing final-result
matches have no target, an empty point log, status `completed`, and revision 0.
Point logs, totals, and completion are written together in one atomic table
replacement. Tournaments store their name, system, UTC creation time, saved player
draw (a JSON list in one CSV cell), and group count. Brackets and completion are
reconstructed from the ledger; saving a result only writes `matches.csv`.
Times display in the viewer's local timezone. Back up **all three files together
while the service using those tables is stopped**.
Do not edit CSV files manually while anyone is using the app.

Matches also store `elo1_before` and `elo2_before`: each player's Elo immediately
before that completed result in ledger order. Unfinished matches leave these blank.
Every match-table write recomputes these columns, including after deletions and
undoing a winning point. History and Stats read the saved ratings. Elo starts at
1000 with K=32; stored values retain full precision.

**Existing CSV data:** app startup upgrades older match tables, preserving all
results and point logs and backfilling historical Elo. Before the atomic upgrade,
it saves a `matches.pre-elo-<id>.csv.bak` copy alongside the table. Subsequent starts
leave upgraded tables unchanged. Stop older app instances before upgrading; their
CSV reader does not understand the new columns.

Stats shows median Elo for players with at least two completed matches (including
retired players). Match totals and points include all completed results, while
serving and clutch rates require point histories.
Duel comparisons use only the selected pair's completed matches.

Player advanced stats are calculated as follows:

- **Net Points:**
  $$
  \text{Net Points} = \frac{\text{points scored} - \text{points conceded}}{\text{completed matches}}
  $$
  This is the average scoring margin per match, including all overtime points.
  For example, wins of 7–4 and 7–5 plus a 3–7 loss give
  $\frac{3 + 2 - 4}{3} = +0.33$ points per match.
- **SRS (Simple Rating System):**
  $$
  \text{SRS} = \text{Net Points} + \operatorname{mean}(\text{opponents' SRS})
  $$
  The equations are solved together for all players, counting each opponent once
  per completed match against them. Each connected group of opponents is centered
  so its player ratings sum to zero. Positive ratings indicate above-average
  performance within that group, in points per match, adjusted for opponent
  strength. Separate groups have independent baselines and are not directly
  comparable.
- **Performance expectation:**
  $$
  \text{Performance} = 100 \times \operatorname{mean}(S_i - E_i), \qquad
  E_i = \frac{1}{1 + 10^{(R_{\text{opp},i} - R_{\text{player},i})/400}}
  $$
  The actual result $S_i$ is 1 for a win and 0 for a loss. Each expected win
  probability $E_i$ uses both players' pre-match ratings. The result is in
  percentage points (pp): winning 60% of matches with an average expected win
  probability of 50% gives $+10$ pp. Positive values mean more wins than Elo
  predicted.
- **Clutch:**
  $$
  \text{Clutch} = 100 \times
  \frac{\sum_i L_i(P_i - q_{m(i)})}{\sum_i L_i}
  $$
  Here, $P_i$ is 1 if the player won point $i$ and 0 otherwise; $q_{m(i)}$ is the
  player's point win rate in that match. $L_i$ measures the difference in match
  win probability between winning and losing the next point, assuming future
  points are 50/50 and using the resetting overtime rules. Before overtime, with
  target $T$ and pre-point scores $a$ and $b$:

  $$
  L_i = \frac{\binom{2T - a - b - 2}{T - a - 1}}{2^{2T - a - b - 2}}
  $$

  In overtime, $L_i = 0.5$. Sums run over points across
  eligible matches, each with its own baseline, so matches are weighted by their
  total point importance. Positive scores mean better performance on important
  points relative to the player's overall play in those matches, in percentage
  points. Only complete point histories that reproduce the final score count;
  small samples are provisional.

Net Points, SRS, and Performance expectation use all completed results, including
final-result-only matches. They show `—` without completed matches; Clutch shows
`—` without an eligible complete point history.

All reads and writes acquire `.write-lock` in the selected data directory using
exclusive directory creation.
Writes reread current data and replace one table via a flushed temporary file in
the same directory. A busy lock reports an error immediately; retry with Refresh.
The web service handles storage on the server; wait for a save response before closing the page.
Malformed files produce an error and are never silently reset. A missing table
is treated as empty (unless match references then fail validation); restore
accidentally deleted tables from a backup.

After a crash, a stale lock may remain. **First stop the service using those
tables and confirm no save is running.** Then remove the empty
`data/.write-lock` directory with `rmdir /path/to/shared/ping-pong/data/.write-lock`.
For the test service, use the lock in `test_data/` instead.
Never remove a lock while another instance is active. Leftover `.save-*` files
can be removed under the same conditions; they are not authoritative data.

The app cannot guarantee NAS behavior during connection or server failure. If a
save reports a network error, refresh and check history before retrying: the
server may have accepted the write even if the response was lost.

Before company rollout, use two **different computers** to register matches at
the same time. A successful write must appear on both after refreshing; a busy
operation must report an error and succeed on retry without losing prior rows.
Also test rename, retirement, deletion, and both NAS URL prefixes. Confirm that
test-server edits do not change the main tables.

Automated tests cover Elo, match-entry reset, live scoring and repeated overtime
resets, point history, resume/undo/completion, stale scoring attempts, all tournament systems, odd/even
player validation, byes, group qualification and loss routing, one-match finals,
completion/history/reopening, legacy CSV compatibility, failed writes, and
concurrent submissions (including duplicate tournament results). A second
physical browser client and the QNAP container deployment still need validation
before rollout.

### Tournament badges and titles

Tournament creation offers Minor and Major classifications and three trophy badges.
You can replace the selected trophy with a PNG, JPEG, or WebP image (up to 5 MB
and 16 million pixels). Uploaded badges are normalized to PNG, resized to fit
512 × 512 pixels, and stored in the selected data directory's `badges/` folder;
include that folder when backing up or moving the ledger.

Only the winner of a completed Major earns an achievement in Player Stats.
Clicking the badge opens that tournament's bracket and results. Undoing the
result that decided the title removes the achievement until the tournament
finishes again. Minor matches still contribute to normal match statistics.

Tournament CSV rows include `classification` (`minor` or `major`) and `badge`
(empty for Minors; `cup`, `shield`, `star`, or an uploaded PNG filename for Majors). There is no migration
for older tournament rows during development.
