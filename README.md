# Indian Intraday Trading Assistant (NSE Cash, Intraday)

Rules-based intraday decision-support system for Indian equities (cash market only).

Daily flow:
- Stage-0 (around 09:10 IST): NSE pre-open + historical-context watchlist.
- Stage-1 (around 09:30 IST): second list using performance window 09:15-09:30 (configurable).

## Stage-1 Output Model
Stage-1 always publishes both sections:
- `Top 5 LONG (Ranked)`
- `Top 5 SHORT (Ranked)`

Each row includes:
- `Status: ACTIONABLE | NON-ACTIONABLE`
- full trade fields (entry/SL/targets/RR/etc.)
- `Why not actionable` when rejected by conservative constraints

If a side has fewer symbols, the list is padded with:
- `Stock Name: NO CANDIDATE`
- `Status: NON-ACTIONABLE`

If both directional lists are fully non-actionable:
- `NO TRADE - CONDITIONS NOT FAVORABLE`
- both lists are still shared as context.

## Rich Telegram Format + XLSX Audit
Telegram output uses HTML rich formatting for readability:
- section icons (`📊`, `🟢`, `🔴`, `⚠️`, `🧪`)
- bold labels for all mandatory fields
- chunked send for long messages (`TELEGRAM_MAX_CHARS`)

Stage-1 sends an XLSX audit workbook (`TELEGRAM_ATTACH_XLSX=true`) with:
- `Run_Summary`
- `Long_Ranked`
- `Short_Ranked`
- `Metrics`
- `Validation`
- `Process_Log`

## Dual-Source Data Architecture
- Primary: Shoonya
- Secondary: NSE public endpoints
- Validation: Shoonya vs NSE on key fields

Behavior:
- Validation fail => global no-trade
- Shoonya unavailable => NSE backup path
- NSE backup at 5m/15m => explicit reduced-confidence note

## Safety
- Decision support only. Not financial advice.
- Intraday only. No overnight carry.

## Setup
1. Create venv and install:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
2. Create `.env`:
```powershell
Copy-Item .env.example .env
```
3. Fill required keys:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Shoonya keys (optional while running `--source-force nse`)

Key controls:
- `TOP_LONG_CANDIDATES=5`
- `TOP_SHORT_CANDIDATES=5`
- `FILL_EMPTY_SLOTS=true`
- `ENABLE_NSE_BACKUP=true`
- `ENABLE_CROSS_VALIDATION=true`
- `PRICE_DIFF_TOLERANCE_PCT=0.35`
- `VOLUME_DIFF_TOLERANCE_PCT=10.0`
- `NSE_FALLBACK_INTERVALS=1m,5m,15m`
- `ADMIN_STATUS_NOTIFICATIONS=false`
- `TEST_MODE=false`
- `TEST_REPLAY_AUTO_LAST_TRADING_DAY=true`
- `TEST_REPLAY_DATE=`
- `TEST_SEND_TO_TELEGRAM=false`
- `TEST_TIME_PREOPEN=09:10`
- `TEST_TIME_OPENINGRANGE=09:30`
- `TELEGRAM_PARSE_MODE=HTML`
- `TELEGRAM_MESSAGE_PREFIX=`
- `TELEGRAM_ENABLE_RICH_FORMAT=true`
- `TELEGRAM_MAX_CHARS=3900`
- `TELEGRAM_ATTACH_XLSX=true`
- `REPORTS_DIR=data/reports`
- `REPORT_INCLUDE_CANDLES=false`

## Manual Runs
Dry-run:
```powershell
python -m src.engine.runner --stage preopen --dry-run
python -m src.engine.runner --stage openingrange --dry-run
```

Source override:
```powershell
python -m src.engine.runner --stage openingrange --source-force nse
python -m src.engine.runner --stage openingrange --source-force shoonya
```

Emergency manual bypass:
```powershell
python -m src.engine.runner --stage openingrange --source-force shoonya --skip-validation
```

## Replay Test Mode (Weekend/Off-hours)
Replay mode simulates a trading-day run (default: last trading day, e.g. Friday on weekends).

Commands:
```powershell
python -m src.engine.runner --stage both --test-mode --dry-run --show-process
python -m src.engine.runner --stage openingrange --test-mode --replay-date 2026-02-06 --dry-run
```

Optional Telegram send in replay mode:
```powershell
python -m src.engine.runner --stage both --test-mode --test-send
```

Caveat:
- Stage-0 in replay mode is a simulated pre-open reconstruction from early replay-day candles.
- Stage-0 replay wording explicitly states Stage-1 replay will be sent for the replay date.
- Replay messages are marked with `TEST MODE: ON` and replay date.

## Free Best-Effort Scheduler (GitHub Actions)
Workflows:
- `.github/workflows/preopen.yml`
- `.github/workflows/openingrange.yml`
- `.github/workflows/ci.yml`
- `.github/workflows/staging-validate.yml`
- `.github/workflows/promote-to-main.yml`

Cron mapping:
- `09:10 IST` -> `03:40 UTC` (`preopen.yml`)
- `09:30 IST` -> `04:00 UTC` (`openingrange.yml`)

Required GitHub repository secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- Optional Shoonya secrets (`SHOONYA_*`)

### Branch Model and Promotion
- `develop`: integration branch
- `staging`: pre-production validation branch
- `main`: production branch

Automatic promotion path:
1. push/merge to `develop`
2. merge `develop` -> `staging`
3. `staging-validate.yml` runs tests + staging preopen/openingrange
4. on success, `promote-to-main.yml` creates/updates PR `staging -> main` and enables auto-merge
5. production schedules continue from `main`

### GitHub Environments
Create two environments in repo settings:
- `staging`
- `production`

Secrets for `staging` environment:
- `TELEGRAM_BOT_TOKEN_STAGING`
- `TELEGRAM_CHAT_ID_STAGING`
- `SHOONYA_*` (use placeholder `NA` until Shoonya is live)

Secrets for `production` environment:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SHOONYA_*` (use placeholder `NA` until Shoonya is live)

Staging messages are prefixed with `[STAGING]` via `TELEGRAM_MESSAGE_PREFIX`.

### Branch Protection (Recommended)
- `main`:
  - require pull request before merge
  - require status check `CI / tests`
- `staging`:
  - require status check `CI / tests`

Properties:
- `workflow_dispatch` enabled for manual recovery runs
- runtime heartbeat/log artifacts uploaded every run
- free-tier cron can be delayed; this is expected

Recovery:
1. Open Actions tab
2. Run missed workflow manually (`Run workflow`)
3. Download runtime artifact logs for diagnosis

## Testing
Run:
```powershell
pytest -q
```

## Troubleshooting
- Telegram `chat not found`:
  - open bot chat and send `/start`
  - verify `TELEGRAM_CHAT_ID` from `message.chat.id`
- Validation mismatch:
  - inspect `data/validation/*_stage1.json`
  - rerun with explicit source override for manual check
- NSE instability:
  - backup may use 5m/15m fallback and lower confidence

