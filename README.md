# Ping-pong

A minimal PyQt6 scorekeeper for a shared NAS folder. Everyone uses the same data;
there are no accounts or administrator roles.

## Run

On Arch Linux x86_64, open the shared `ping-pong` executable in your file manager,
or run `/path/to/shared/ping-pong/ping-pong` in a terminal. No Python installation
or administrator permissions are required for the bundled version. The NAS must
permit executing files and writing to this directory. Keep the executable here:
copying it elsewhere creates a separate data location.

The bundle includes Python and Qt but still needs compatible system libraries
and a desktop session. Test it on a colleague's machine before rollout, especially
if their Arch installation is older than the build machine. File managers may ask
you to confirm that an executable is trusted.

For development, create a local virtual environment (outside the NAS if possible),
install `requirements.txt`, and run `python /path/to/shared/ping-pong/app.py`.
Build a release with `bash build.sh` using Python 3 with venv/pip and internet
access. It builds in a temporary local directory and replaces the root executable
only when successful. No sudo is needed. Run tests with
`QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v` in an environment
with PyQt6 installed.

## Use

1. Add members in **Players**.
2. Select two players in **Matches**, enter their final points, and register.
3. Open **Leaderboards** for rankings and totals. Use **Refresh** to pick up other
   people's changes; changing tabs also refreshes.
4. Use **Tournament** to create a named event, select its players and system, and
   enter results by selecting a ready match in the bracket. Successful ordinary
   match registration clears both player selections and resets both scores.

Delete an incorrect match from history and register it again. The replacement
counts at its new registration position. Scores must have a winner, but there is
no enforced target score or win-by-two rule. Removing a player retires them;
restore them from Players when needed. Renaming updates their name in all views.

Elo starts at 1000, with K=32 and the standard 400-point expected-score formula.
Point margin does not affect Elo. All historical matches, including retired
players' matches, are replayed in CSV row order. Only active players appear in
the leaderboard; players without matches are unranked. Exact Elo determines
ranking, with alphabetical ties; the displayed rating is rounded.

## Tournaments

- **Single-Elimination:** an even number of players, minimum two. A loss eliminates
  a player. Non-power-of-two fields (such as six) get first-round byes.
- **Double-Elimination with Group stage:** an even number of players, minimum four.
  Choose the number of groups; every group must contain at least two players.
  Each group plays a single round-robin. Its top half, rounded up for odd-sized
  groups, enters the upper bracket; the rest enters the lower bracket with one
  playoff loss. Group losses do not otherwise carry into the playoffs. Upper
  losers drop into lower; a lower-bracket loss eliminates the player. There is
  **one grand final, with no reset**, even if the lower-bracket finalist wins.
- **Round-robin:** any player count of at least two, including odd counts. Each
  pair plays once; the final table determines the winner.

The draw is randomized once at creation and saved. Groups are balanced by
distributing the draw in turn across groups. Group and round-robin tables sort
by wins, point difference, points scored, then saved draw order. The last tie
breaker is stable across renames. Playoff seeding uses group placing then group
number. Initial upper-bracket seeds get byes as needed; lower brackets can also
have byes. Each upper round runs alongside a lower elimination round, followed
by a lower round mixing its survivors with the upper losers. Further lower rounds
are played if needed to determine a finalist. Byes are not matches and do not
affect Elo.

The bracket displays all played and currently available rounds. Later rounds
appear once their prerequisites finish; group matches can be entered in any
order. Every actual tournament result appears in **Matches**, with its tournament
name, and counts toward the global leaderboard and Elo in registration order.
Participants are fixed at creation; a player retired afterward can still finish
an existing tournament. Completed events remain in the selector for review, along
with all scores, group tables, brackets, and their winner.

Use **Undo latest result** to correct an event. Tournament results must be undone
in reverse registration order, including when deleting through Matches. This
prevents changing an early winner while keeping later matches for the wrong
players. Undoing the final reopens a completed tournament. Re-entering results
adds them at the end of global history, so Elo is recalculated in that new order.

## Shared data and recovery

`data/players.csv`, `data/matches.csv`, and `data/tournaments.csv` are UTF-8 tables created as needed and
excluded from Git. Players have stable IDs, names, and `active` (1/0). Matches
have IDs, UTC timestamps, player IDs, integer scores, and optional tournament and
fixture IDs. Tournaments store their name, system, UTC creation time, saved player
draw (a JSON list in one CSV cell), and group count. Brackets and completion are
reconstructed from the ledger; saving a result only writes `matches.csv`.
Times display in the viewer's local timezone. Back up **all three files together
while all apps are closed**.
Do not edit CSV files manually while anyone is using the app.

**Upgrading from the original release:** close all old app instances before
launching the new executable. Existing players and matches are preserved. Old
match headers are read without modification and gain the two tournament columns
on the first match save/deletion. Old executables cannot read the extended match
table; everyone should use the updated shared executable.

All reads and writes acquire `data/.write-lock` using exclusive directory creation.
Writes reread current data and replace one table via a flushed temporary file in
the same directory. A busy lock reports an error immediately; retry with Refresh.
Network operations run in a background thread. Wait for them before closing.
Malformed files produce an error and are never silently reset. A missing table
is treated as empty (unless match references then fail validation); restore
accidentally deleted tables from a backup.

After a crash, a stale lock may remain. **First ensure all app instances on every
computer are closed and no save is running.** Then remove the empty
`data/.write-lock` directory with `rmdir /path/to/shared/ping-pong/data/.write-lock`.
Never remove a lock while another instance is active. Leftover `.save-*` files
can be removed under the same conditions; they are not authoritative data.

The app cannot guarantee NAS behavior during connection or server failure. If a
save reports a network error, refresh and check history before retrying: the
server may have accepted the write even if the response was lost.

Before company rollout, use two **different computers** to register matches at
the same time. A successful write must appear on both after refreshing; a busy
operation must report an error and succeed on retry without losing prior rows.
Also test rename, retirement, deletion, and launching the bundle from a different
working directory. Same-host tests do not establish cross-client SMB correctness.

Automated tests cover Elo, match-entry reset, all tournament systems, odd/even
player validation, byes, group qualification and loss routing, one-match finals,
completion/history/reopening, legacy CSV compatibility, failed writes, and
concurrent submissions (including duplicate tournament results). A second
physical client and a real desktop launch still need validation before rollout.
# Immer-Pong
