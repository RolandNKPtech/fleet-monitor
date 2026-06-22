# Fleet Monitor — deployment checklist

This is the exact order I'll execute the deploy in. Each external action
gets explicit confirmation before it runs. Steps marked "auto" are run
by me from your tokens in `.deploy/.env.deploy`; steps marked "you" need
your hands or eyes.

---

## 0. Pre-flight (already done)

- [x] CF token verified (`Roland@example.com's Account`, id `ff987dca…`)
- [x] GitHub token verified (`RolandNKPtech`)
- [x] R2 + Workers + Pages accessible via API
- [x] Branch `feat/r2-state-migration` created with code changes
- [x] 422/422 tests green
- [ ] **You: enable Zero Trust / Access in CF dashboard**

---

## Phase 1 — GitHub repo

| # | Step | Who | Notes |
|---|---|---|---|
| 1 | Create `RolandNKPtech/fleet-monitor` (private) | auto | API `POST /user/repos` |
| 2 | Extract files into a clean tree | auto | Drop `builds-reference/`, `data/clients/`, unrelated `projects/*` |
| 3 | Push initial commit | auto | `git init` + push the extracted tree |
| 4 | Inject GitHub Secrets | auto | 8 secrets (R2, CF, WPE, GA4 client) |

After phase 1: empty workflow exists. Pipeline will run on cron but will
no-op because R2 isn't set up yet.

---

## Phase 2 — Cloudflare R2

| # | Step | Who | Notes |
|---|---|---|---|
| 5 | Create R2 bucket `fleet-monitor` | auto | API `POST /accounts/{id}/r2/buckets` |
| 6 | Mint scoped R2 access key (write/read on this bucket only) | auto | API |
| 7 | Save key + secret to GHA Secrets | auto | Worker secret |
| 8 | (Optional) Push historical state from local | auto | If you want trend rules to work day 1 |

---

## Phase 3 — Cloudflare Worker

| # | Step | Who | Notes |
|---|---|---|---|
| 9 | Install wrangler locally | auto | `npm install` in `worker/` |
| 10 | Mint Worker-side GitHub PAT (scoped: `actions:write` only) | auto | API |
| 11 | `wrangler secret put GITHUB_TOKEN` | auto | Pipes the PAT |
| 12 | `wrangler deploy` | auto | Worker live at `fleet-monitor-api.<account>.workers.dev` |

After phase 3: Worker live, `/refresh` endpoint can fire GHA. R2 bucket
empty so GET `/` returns 404.

---

## Phase 4 — Cloudflare Pages

Skipping — the Worker serves HTML from R2 directly. Pages adds nothing
on top for our use case. If we want pretty static URLs later, we add it.

---

## Phase 5 — Cloudflare Access

| # | Step | Who | Notes |
|---|---|---|---|
| 13 | Confirm Access enabled (from your UI click) | you | One-time |
| 14 | Create email-OTP identity provider | auto | API |
| 15 | Create Access app on the Worker URL | auto | API |
| 16 | Set policy: allow `roland@example.com` only | auto | API |

After phase 5: visiting the Worker URL prompts for email, sends OTP,
admits Roland.

---

## Phase 6 — First end-to-end run

| # | Step | Who | Notes |
|---|---|---|---|
| 17 | Trigger workflow_dispatch | auto | `gh workflow run pipeline.yml` |
| 18 | Watch run | both | ~13 min |
| 19 | Verify R2 populated | auto | `wrangler r2 object list` |
| 20 | Verify Worker serves dashboard | auto | `curl <url>/` |
| 21 | Verify Refresh button fires GHA | both | Click in browser |

---

## Phase 7 — Hand-off

- Document the URL
- Document the rotate-tokens steps (the original exposed ones)
- Document the cron schedule
- Recommend optional next steps (custom domain via DNS Edit token, etc.)

---

## Rollback plan

Each phase is reversible:

| Action | Undo |
|---|---|
| Create repo | `gh repo delete RolandNKPtech/fleet-monitor` |
| Create R2 bucket | `wrangler r2 bucket delete fleet-monitor` |
| Deploy Worker | `wrangler delete fleet-monitor-api` |
| Access app | API DELETE on the app id |

Nothing in this deploy touches the existing `nkp-ops` workspace or any
other CF zone you have running.
