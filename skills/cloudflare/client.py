import asyncio
import os
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from core.errors import APIError, ConfigError
from core.logger import get_logger

log = get_logger("cloudflare.client")

BASE_URL = "https://api.cloudflare.com/client/v4"
GRAPHQL_URL = f"{BASE_URL}/graphql"


class _RetryableAPIError(Exception):
    """Internal: triggers tenacity retry."""
    pass


def _raise_retry_exhausted(retry_state):
    """Convert exhausted retries into APIError."""
    raise APIError("cloudflare", None, f"Failed after {retry_state.attempt_number} retries: {retry_state.outcome.exception()}")


class CloudflareClient:
    """Async Cloudflare API v4 client with rate limiting, pagination, and zone caching."""

    def __init__(self, api_token: str):
        self._token = api_token
        self._zone_cache: dict[str, str] = {}
        self._rest_semaphore = asyncio.Semaphore(4)
        self._graphql_semaphore = asyncio.Semaphore(1)
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    # --- REST Methods ---

    async def get(self, path: str, params: dict | None = None) -> dict:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict | None = None) -> dict:
        return await self._request("POST", path, json=json)

    async def patch(self, path: str, json: dict | None = None) -> dict:
        return await self._request("PATCH", path, json=json)

    async def delete(self, path: str) -> dict:
        return await self._request("DELETE", path)

    async def get_paginated(self, path: str, params: dict | None = None, per_page: int = 50) -> list:
        """Fetch all pages of a paginated endpoint."""
        all_results = []
        page = 1
        params = dict(params or {})
        while True:
            params["page"] = str(page)
            params["per_page"] = str(per_page)
            data = await self.get(path, params=params)
            all_results.extend(data.get("result", []))
            info = data.get("result_info", {})
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return all_results

    async def get_notifications(self, account_id: str | None = None, limit: int = 50) -> list[dict]:
        """Fetch recent delivered alert notifications.

        If account_id is not provided, reads CF_ACCOUNT_ID from the environment.
        Returns an empty list (logging a warning) if no account id can be found.
        """
        if account_id is None:
            account_id = os.environ.get("CF_ACCOUNT_ID")
        if not account_id:
            log.warning("CF_ACCOUNT_ID not set; skipping notifications fetch")
            return []
        try:
            data = await self.get(
                f"/accounts/{account_id}/alerting/v3/history",
                params={"per_page": str(limit)},
            )
        except APIError as e:
            log.warning(f"get_notifications failed: {e}")
            return []
        return data.get("result", []) or []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RetryableAPIError),
        retry_error_callback=lambda retry_state: _raise_retry_exhausted(retry_state),
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{BASE_URL}{path}" if path.startswith("/") else f"{BASE_URL}/{path}"
        async with self._rest_semaphore:
            try:
                async with httpx.AsyncClient(timeout=30) as http:
                    resp = await http.request(method, url, headers=self._headers, **kwargs)

                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "5"))
                    log.warning(f"Rate limited, waiting {wait}s")
                    await asyncio.sleep(wait)
                    raise _RetryableAPIError("rate limited")

                if resp.status_code >= 500:
                    raise _RetryableAPIError(f"server error {resp.status_code}")

                if resp.status_code in (401, 403):
                    raise APIError("cloudflare", resp.status_code, "Invalid or insufficient API token")

                data = resp.json()
                if not data.get("success", True) and "errors" in data:
                    errors = data.get("errors", [])
                    msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
                    raise APIError("cloudflare", resp.status_code, msg)

                return data

            except _RetryableAPIError:
                raise
            except APIError:
                raise
            except httpx.HTTPError as e:
                raise APIError("cloudflare", None, f"Network error: {e}")

    # --- GraphQL ---

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        async with self._graphql_semaphore:
            try:
                async with httpx.AsyncClient(timeout=60) as http:
                    resp = await http.post(
                        GRAPHQL_URL,
                        headers=self._headers,
                        json={"query": query, "variables": variables or {}},
                    )
                data = resp.json()
                if data.get("errors"):
                    msg = data["errors"][0].get("message", "GraphQL error")
                    raise APIError("cloudflare", None, f"GraphQL error: {msg}")
                return data

            except APIError:
                raise
            except httpx.HTTPError as e:
                raise APIError("cloudflare", None, f"GraphQL network error: {e}")

    async def get_firewall_events_count(
        self, zone_id: str, since_iso: str, until_iso: str,
        group_by_day: bool = False,
    ) -> int | list[dict]:
        """Return firewall blocked-events count for zone between since_iso and until_iso.

        When group_by_day=False (default): returns a single int (total count).
        When group_by_day=True: returns list[{"date": "YYYY-MM-DD", "count": int}]
          sorted ascending, one entry per day that had events. Days with no events
          are omitted. Returns [] on any error (logs warning).

        Uses CF GraphQL firewallEventsAdaptiveGroups.
        """
        if not group_by_day:
            # Original single-total query
            query = """
            query($zoneTag: String!, $since: String!, $until: String!) {
              viewer {
                zones(filter: {zoneTag: $zoneTag}) {
                  firewallEventsAdaptiveGroups(
                    limit: 1,
                    filter: {datetime_geq: $since, datetime_lt: $until}
                  ) {
                    count
                  }
                }
              }
            }
            """
            try:
                data = await self.graphql(
                    query,
                    variables={"zoneTag": zone_id, "since": since_iso, "until": until_iso},
                )
            except Exception as e:
                log.warning(f"firewall events query for {zone_id} failed: {e}")
                return 0
            try:
                zones = data["data"]["viewer"]["zones"]
                if not zones:
                    return 0
                groups = zones[0]["firewallEventsAdaptiveGroups"]
                return groups[0]["count"] if groups else 0
            except (KeyError, IndexError, TypeError):
                return 0
        else:
            # Daily-grouped: CF's firewallEventsAdaptiveGroups has a 24-hour
            # window cap, so we loop one query per day and aggregate.
            from datetime import datetime as _dt, timedelta as _td

            try:
                start = _dt.fromisoformat(since_iso.replace("Z", "+00:00"))
                end = _dt.fromisoformat(until_iso.replace("Z", "+00:00"))
            except ValueError as e:
                log.warning(f"firewall daily events bad timestamps for {zone_id}: {e}")
                return []

            per_day_query = """
            query($zoneTag: String!, $since: String!, $until: String!) {
              viewer {
                zones(filter: {zoneTag: $zoneTag}) {
                  firewallEventsAdaptiveGroups(
                    limit: 1,
                    filter: {datetime_geq: $since, datetime_lt: $until}
                  ) {
                    count
                  }
                }
              }
            }
            """
            out: list[dict] = []
            cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
            while cur < end:
                day_start = cur
                day_end = cur + _td(days=1)
                try:
                    data = await self.graphql(
                        per_day_query,
                        variables={
                            "zoneTag": zone_id,
                            "since": day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "until": day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                    )
                except Exception as e:
                    log.warning(f"firewall daily events for {zone_id} day {day_start.date()} failed: {e}")
                    cur = day_end
                    continue
                try:
                    zones = data["data"]["viewer"]["zones"]
                    if zones:
                        groups = zones[0]["firewallEventsAdaptiveGroups"]
                        count = groups[0]["count"] if groups else 0
                    else:
                        count = 0
                except (KeyError, IndexError, TypeError):
                    count = 0
                out.append({"date": day_start.date().isoformat(), "count": count})
                cur = day_end
            return out

    # --- Zone Helpers ---

    async def get_zone_id(self, domain: str) -> str:
        """Get zone ID for a domain, with caching."""
        if domain in self._zone_cache:
            return self._zone_cache[domain]

        data = await self.get("/zones", params={"name": domain})
        zones = data.get("result", [])
        if not zones:
            raise APIError("cloudflare", 404, f"Zone not found for domain: {domain}")

        zone_id = zones[0]["id"]
        self._zone_cache[domain] = zone_id
        log.debug(f"Cached zone ID: {domain} -> {zone_id}")
        return zone_id

    async def get_all_zones(self) -> list[dict]:
        """Get all zones with pagination, populates cache."""
        zones = await self.get_paginated("/zones")
        for z in zones:
            self._zone_cache[z["name"]] = z["id"]
        log.info(f"Loaded {len(zones)} zones into cache")
        return zones

    async def get_zone_settings(self, zone_id: str) -> dict:
        """Get all settings for a zone as a flat dict {setting_id: value}."""
        data = await self.get(f"/zones/{zone_id}/settings")
        settings = {}
        for item in data.get("result", []):
            settings[item["id"]] = item["value"]
        return settings

    async def get_ruleset(self, zone_id: str, phase: str) -> dict | None:
        """Get a ruleset for a zone by phase. Returns None if not found."""
        try:
            data = await self.get(f"/zones/{zone_id}/rulesets/phases/{phase}/entrypoint")
            return data.get("result")
        except APIError as e:
            if e.status_code == 404:
                return None
            raise


# --- Singleton ---

_client_instance: CloudflareClient | None = None


def get_cf_client() -> CloudflareClient:
    """Get or create the singleton CloudflareClient."""
    global _client_instance
    if _client_instance is None:
        token = os.environ.get("CF_API_TOKEN")
        if not token:
            raise ConfigError("CF_API_TOKEN environment variable not set")
        _client_instance = CloudflareClient(api_token=token)
    return _client_instance


def _reset_client():
    """Reset singleton for testing."""
    global _client_instance
    _client_instance = None
