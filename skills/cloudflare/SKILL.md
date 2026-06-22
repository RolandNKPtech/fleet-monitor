# Cloudflare Skills

Skills for managing Cloudflare zones, O2O configuration, WAF rules, DNS, cache, and analytics across 248+ WordPress sites.

## Shared Infrastructure
- `client.py` — Singleton async API client with rate limiting, pagination, zone caching
- `_resolve.py` — Target resolver: domain/account/"all" → zone IDs

## Skills

| Skill | Description | Inputs |
|-------|-------------|--------|
| `cloudflare.zone_inventory` | List all zones, cross-ref with site inventory | optional: account |
| `cloudflare.dns_check` | Verify DNS records for O2O (CNAME, proxy) | target (domain) |
| `cloudflare.audit_config` | Bulk audit zone settings against O2O standards | target (domain/account/all) |
| `cloudflare.o2o_verify` | Full 10-point O2O verification | target (domain) |
| `cloudflare.check_waf` | Audit custom WAF rules (non-US challenge, validator allowlist) | target (domain) |
| `cloudflare.push_country_challenge` | Add/remove standard country challenge rule. **Pre-checks traffic geography by default** — refuses to apply on sites with significant non-US traffic | target (domain), optional: mode, changelog, pre_check_geography (default True), min_us_pct (default 90), geography_days (default 7) |
| `cloudflare.push_validator_allowlist` | Add/remove Skip rule for schema validators, PSI, social previewers (idempotent, writes changelog, self-verifies with retry) | target (domain), optional: mode=dry_run\|apply\|remove, verify, changelog |
| `cloudflare.setup_o2o_waf` | **Composer** — full O2O WAF setup: country challenge + validator allowlist + audit. Use as single entry point from any O2O setup pipeline. | target (domain), optional: mode=dry_run\|apply, verify, changelog |
| `cloudflare.purge_cache` | Purge cache (everything or by URL) | target (domain), optional: urls |
| `cloudflare.bot_analysis` | Analyze bot/threat traffic via GraphQL | target (domain), optional: days |
| `cloudflare.check_traffic_geography` | Geographic traffic distribution + US-dominance verdict (pre-check before applying country challenge) | target (domain), optional: days, min_us_pct |
| `cloudflare.security_headers` | Check HSTS + security response headers | target (domain) |
| `cloudflare.wpe_crossref` | Cross-ref WPE sites against CF zones | optional: target (account) |

## Configuration
- `data/standards/cf-config.yml` — O2O standards (settings, WAF rules, DNS requirements)
- `CF_API_TOKEN` env var — Cloudflare API bearer token

## Usage
```bash
nkp run cloudflare.o2o_verify --target drjones.com
nkp run cloudflare.audit_config --target acctC
nkp run cloudflare.bot_analysis --target drjones.com
nkp run cloudflare.zone_inventory
nkp run cloudflare.wpe_crossref

# Validator allowlist (dry run by default — pass mode=apply to actually push)
nkp run cloudflare.push_validator_allowlist --target drjones.com
nkp run cloudflare.push_validator_allowlist --target drjones.com --mode apply
nkp run cloudflare.push_validator_allowlist --target drjones.com --mode remove

# Full O2O WAF setup (recommended entry point — runs both rules + audit)
nkp run cloudflare.setup_o2o_waf --target drjones.com               # dry run
nkp run cloudflare.setup_o2o_waf --target drjones.com --mode apply  # apply both

# Check traffic geography (pre-check before applying country challenge)
nkp run cloudflare.check_traffic_geography --target drjones.com
```

## Geography Pre-Check (Safety Rail)
`push_country_challenge` and `setup_o2o_waf` run `check_traffic_geography` by default before applying. If the site has < 90% US traffic over the last 7 days (configurable), the country challenge is **SKIPPED** to avoid blocking international medical-tourism patients or cross-border consultations. The validator allowlist is still pushed (harmless on its own). Disable per-call with `pre_check_geography=False` (e.g., for a US-only test site that hasn't built up traffic yet).

## O2O Pipeline Integration
For new O2O setups, call `cloudflare.setup_o2o_waf` (composer) — it runs the full WAF stack idempotently in the correct order. Future `scripts/o2o_pipeline.py` should call this skill rather than implementing rule-push logic. See [docs/superpowers/plans/2026-04-14-validator-allowlist-o2o-integration.md](../../docs/superpowers/plans/2026-04-14-validator-allowlist-o2o-integration.md).

## Changelog
Write-actions on production zones append a JSONL entry to `data/reports/cf-rule-changes-YYYY-MM-DD.jsonl` with `{timestamp, skill, domain, zone_id, action, rule_id, verification}`. Disable per-call with `changelog=False`.
