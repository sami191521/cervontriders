# Tour of Belize — live backend

Hosted "Option B" for the Caye Talkers Tour of Belize: one shared source of
truth so every floor TV and phone sees the same board. FastAPI + SQLite,
mirrors the reference HTML's importer / normalization / standings logic.

## Run

```bash
cd backend
.venv/bin/pip install -r requirements.txt          # first time

# dev
TOB_SECRET=change-me TOB_ADMIN_PASS=strongpass \
  .venv/bin/uvicorn app.main:app --reload --port 8099
```

- Wired board:  http://127.0.0.1:8099/
- API docs:     http://127.0.0.1:8099/docs

Open `/` over http → the page connects to the API (live, shared). Opening
`caye-talkers-tour-of-belize.html` straight from disk still works fully
offline (per-device), unchanged.

## Environment

| var | default | purpose |
|---|---|---|
| `TOB_SECRET` | random (ephemeral) | HMAC key for admin tokens — **set in prod** so tokens survive restarts |
| `TOB_ADMIN_USER` / `TOB_ADMIN_PASS` | `admin` / `admin` | admin credentials — **change in prod** |
| `TOB_TOKEN_TTL` | `43200` | token lifetime (seconds) |
| `TOB_DB` | `backend/tob.sqlite3` | SQLite path |

## API

| Method | Path | Auth | |
|---|---|---|---|
| POST | `/api/auth/login` | — | `{user,pass}` → `{token}` |
| PUT | `/api/admin/credentials` | admin | `{currentPass, newUser?, newPass?}` → admin changes own login |
| POST | `/api/ingest` | admin | upload `.xlsx`/`.csv` (server parses + maps) |
| POST | `/api/ingest/preview` | admin | parse only: headers + auto-mapping + sample |
| POST | `/api/ingest/json` | admin | `{riders:[…]}` already-normalized (wired UI posts these) |
| GET | `/api/standings?metric=&scope=` | — | ranks, jerseys, teams, movement |
| GET / PUT | `/api/config` | PUT=admin | raceMetric, teamGoal, visibleStats, videos |
| GET | `/api/profiles` | — | all rider profiles |
| GET / PUT | `/api/riders/{id}/profile` | — | nickname, quote, photo |
| GET | `/api/route` | — | the fixed 21-milestone course |

## Layout

```
app/
  constants.py   route (21 milestones), tenure buckets, team palette, cp/stage maths
  models.py      Pydantic data contract (rider, config, jerseys, profiles)
  ingest.py      Five9 parse (xlsx/csv) + auto-map + normalization
  standings.py   ranks, checkpoints, movement, jerseys, team totals
  db.py          SQLite: dataset, prev snapshot, Monday baseline, daily streak, profiles
  auth.py        HMAC bearer-token admin auth
  main.py        FastAPI app + endpoints + serves the wired UI
static/
  api-adapter.js front-end adapter (activates only when served over http)
```

## Notes

- Movement (`dRank`/`dSales`) is empty on the first upload, correct on the
  second — it diffs against the previous upload's snapshot.
- The weekly stage resets each Monday: score = cumulative − Monday baseline.
- `null` fields mean "column absent in this upload" — the UI hides that stat.
- **Admin credentials:** `TOB_ADMIN_USER`/`TOB_ADMIN_PASS` are only the *bootstrap*
  login. Once the admin changes them via `PUT /api/admin/credentials`, the new
  username + a salted PBKDF2 hash are stored in the DB (kv key `admin_creds`) and
  the env values stop being used. **Lockout recovery:** delete the `admin_creds`
  row (Supabase SQL editor: `delete from kv where key='admin_creds';`) to fall
  back to the env credentials.
