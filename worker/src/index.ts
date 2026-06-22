/**
 * Fleet Monitor CF Worker
 *
 * Three jobs:
 *   1. Serve dashboard HTML + per-site pages directly from R2
 *   2. Handle /trigger-run by firing GitHub Actions workflow_dispatch
 *      (operator-initiated full pipeline run — ~67min, full data, no
 *      shortcuts). The dashboard's Refresh button is JUST a page reload
 *      now — pulling fresh-from-R2 content the cron has produced.
 *   3. Serve /pipeline (renders run-log.jsonl from R2 as a status page
 *      + offers a "Trigger run" button)
 *
 * Auth gating is done by Cloudflare Access in front of the Worker —
 * this code assumes any request that arrives has already been
 * authenticated. We still read the CF-Access-Authenticated-User-Email
 * header so we can log who triggered manual runs.
 */

export interface Env {
  // R2 bucket containing pipeline outputs.
  STATE: R2Bucket;

  // GitHub PAT — same one used during deploy, but scoped down (just
  // 'actions: write' on the fleet-monitor repo). Stored as a Worker secret.
  GITHUB_TOKEN: string;
  GITHUB_OWNER: string;     // 'RolandNKPtech'
  GITHUB_REPO: string;      // 'fleet-monitor'
  GITHUB_WORKFLOW: string;  // 'pipeline.yml'
  GITHUB_BRANCH: string;    // 'main' or 'master'
}

// Map URL paths to R2 keys produced by r2_state.push_to_r2().
function pathToR2Key(pathname: string): string | null {
  if (pathname === "/" || pathname === "/dashboard.html") {
    return "fleet/dashboard.html";
  }
  if (pathname === "/console.html") {
    return "fleet/console.html";
  }
  if (pathname.startsWith("/sites/") && pathname.endsWith(".html")) {
    // /sites/example-com.html -> fleet/sites/example-com.html
    const safe = pathname.slice("/sites/".length);
    if (safe.includes("..") || safe.includes("/")) return null;   // traversal guard
    return `fleet/sites/${safe}`;
  }
  return null;
}

/**
 * Recompute the freshness pill HTML based on actual age. The Python
 * renderer bakes the pill text into HTML at render time, so a cached
 * dashboard.html shows "fresh - 0m ago" forever. We rewrite it on serve
 * so the operator sees how stale the data ACTUALLY is.
 *
 * Bands mirror models.freshness():
 *   - <= 30h: fresh
 *   - <= 48h: aging
 *   -  > 48h: STALE
 */
function freshnessPill(ageMs: number): { label: string; cls: "fresh" | "aging" | "stale" } {
  const ageH = ageMs / 1000 / 3600;
  const ageMin = ageMs / 1000 / 60;
  const age = ageH >= 1 ? `${Math.round(ageH)}h ago` : `${Math.floor(ageMin)}m ago`;
  if (ageH <= 30) return { label: `fresh - ${age}`, cls: "fresh" };
  if (ageH <= 48) return { label: `aging - ${age}`, cls: "aging" };
  return { label: `STALE - ${age}`, cls: "stale" };
}

const _PILL_RE = /<a class="pill (?:fresh|aging|stale)"([^>]*)>([^<]+)<\/a>/;

function rewriteFreshness(html: string, ageMs: number): string {
  const fresh = freshnessPill(ageMs);
  return html.replace(_PILL_RE,
    `<a class="pill ${fresh.cls}"$1>${fresh.label}</a>`);
}

/**
 * Injected onto the dashboard. Replaces the Refresh button's behaviour:
 * instead of POSTing to /refresh and polling for a 67-minute pipeline,
 * the button now just reloads the page so the operator sees whatever
 * the latest cron produced. Honest UX — "Refresh" actually refreshes
 * the view, doesn't lie about producing new data on the spot.
 *
 * Power users who genuinely want to fire a new pipeline run go to
 * /pipeline and use the "Trigger run" button there (with a clear
 * "~67 min" warning).
 */
const _REFRESH_REBIND_SCRIPT = `
<script>
(function(){
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  // Replace the baked-in onclick (which POSTed to /refresh + polled
  // /status for ~67min) with a plain reload.
  btn.onclick = function(e) {
    if (e && e.preventDefault) e.preventDefault();
    var label = btn.querySelector('.label');
    if (label) label.textContent = 'Reloading...';
    btn.disabled = true;
    location.reload();
  };
  // Rename for honesty.
  var label = btn.querySelector('.label');
  if (label) label.textContent = 'Reload';
  btn.title = 'Reload dashboard with latest data from R2. ' +
              'Use /pipeline to trigger a new pipeline run.';
  btn.classList.remove('refresh-error');
})();
</script>
</body>`;

function injectRebindScript(html: string): string {
  return html.replace(/<\/body>\s*<\/html>\s*$/i, _REFRESH_REBIND_SCRIPT + "</html>");
}

async function serveR2(env: Env, key: string): Promise<Response> {
  const obj = await env.STATE.get(key);
  if (obj === null) {
    return new Response(`Not found: ${key} — pipeline may not have run yet`,
      { status: 404 });
  }
  const headers = new Headers({
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
  });
  obj.writeHttpMetadata(headers);

  // For dashboard/console HTML, rewrite the freshness pill so it reflects
  // ACTUAL age, not what it was at render time. obj.uploaded is when the
  // pipeline pushed the file to R2 — a proxy for snapshot captured_at
  // (drift is seconds; the pipeline pushes right after render).
  if (key === "fleet/dashboard.html" || key === "fleet/console.html") {
    const text = await obj.text();
    const ageMs = Date.now() - obj.uploaded.getTime();
    let rewritten = rewriteFreshness(text, ageMs);
    // Only the main dashboard has the refresh-btn; inject the resume
    // script there so polling survives Overview <-> Console navigation.
    if (key === "fleet/dashboard.html") {
      rewritten = injectRebindScript(rewritten);
    }
    return new Response(rewritten, { headers });
  }
  return new Response(obj.body, { headers });
}

/**
 * Map the latest GitHub Actions run to the shape the dashboard JS expects
 * from /status. JS polls every 2s until state is "completed" or "error".
 *
 * GitHub status      -> our state
 *   queued/in_progress -> running
 *   completed + success -> completed
 *   completed + (failure|cancelled|timed_out) -> error
 */
async function fetchPipelineStatus(env: Env): Promise<Response> {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/runs?per_page=1`;
  const resp = await fetch(url, {
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fleet-monitor-worker/1.0",
    },
  });
  if (!resp.ok) {
    return Response.json({
      state: "error",
      message: `GitHub API ${resp.status}`,
    });
  }
  const body = await resp.json() as any;
  const run = body?.workflow_runs?.[0];
  if (!run) {
    return Response.json({ state: "idle", message: "no runs found" });
  }
  let state: "running" | "completed" | "error" = "running";
  let message = run.display_title || run.name || "";
  if (run.status === "completed") {
    if (run.conclusion === "success") {
      state = "completed";
    } else {
      state = "error";
      message = `Run ${run.conclusion}: ${message}`;
    }
  }
  return Response.json({
    state,
    message,
    started_at: run.run_started_at,
    finished_at: run.updated_at,
    html_url: run.html_url,
  });
}

/**
 * Fire a full pipeline run via GHA workflow_dispatch. Always full data —
 * no shortcuts. ~67 min wall-clock. Operator must opt in via the
 * /pipeline page "Trigger run" button; never auto-fired by Refresh.
 */
async function triggerPipelineRun(env: Env, reason: string): Promise<Response> {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fleet-monitor-worker/1.0",
    },
    body: JSON.stringify({
      ref: env.GITHUB_BRANCH,
      inputs: { reason },
    }),
  });
  if (resp.status === 204) {
    return Response.json({
      ok: true,
      message: "Pipeline run dispatched. ~30-67 min until fresh data lands in R2; reload the dashboard then.",
    });
  }
  const body = await resp.text();
  return Response.json({
    ok: false,
    status: resp.status,
    error: body.slice(0, 500),
  }, { status: 502 });
}

async function renderPipelinePage(env: Env): Promise<Response> {
  // Minimal HTML view of the latest run-log entries. The full
  // /pipeline page renderer lives in Python — for the Worker we ship
  // a stripped-down JSON-table view since rendering full HTML in TS
  // would duplicate render_pipeline.py. Operators get the same info,
  // just plainer.
  const obj = await env.STATE.get("fleet/run-log.jsonl");
  if (obj === null) {
    return new Response("No run-log yet — pipeline hasn't run", { status: 404 });
  }
  const text = await obj.text();
  const lines = text.trim().split("\n").filter(Boolean);
  const entries = lines.slice(-14).reverse().map((l) => {
    try { return JSON.parse(l); } catch { return null; }
  }).filter(Boolean);
  const rows = entries.map((e: any) => {
    const stages = (e.stages || []).map((s: any) =>
      `${s.ok ? "✅" : "❌"} ${s.name} (${s.duration_s}s)`).join(" · ");
    return `<tr><td>${e.date}</td><td>${e.status}</td><td>${e.duration_s}s</td>
            <td>${stages}</td><td>${e.error || ""}</td></tr>`;
  }).join("");
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
    <title>Fleet pipeline</title>
    <style>
      body{font-family:-apple-system,sans-serif;padding:24px;background:#fafbfc;color:#14151a}
      h1{font-size:20px;margin:0 0 8px}
      .head{display:flex;justify-content:space-between;align-items:center;
            margin-bottom:14px;flex-wrap:wrap;gap:12px}
      table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;
            border:1px solid #ececef;overflow:hidden}
      th,td{padding:10px;text-align:left;border-bottom:1px solid #f1f2f4;font-size:13px}
      th{background:#fafbfc;font-size:11px;text-transform:uppercase;color:#6a6f78}
      a{color:#2563eb}
      button.trigger{background:#0f1115;color:#fff;border:0;padding:8px 16px;
        border-radius:8px;cursor:pointer;font:inherit;font-weight:600}
      button.trigger:hover{background:#2563eb}
      button.trigger:disabled{opacity:.6;cursor:wait;background:#6a6f78}
      .note{font-size:12px;color:#6a6f78;margin-top:6px}
      #trigger-msg{margin-top:8px;font-size:13px;padding:8px 12px;border-radius:6px;display:none}
      #trigger-msg.ok{display:block;background:#dcfce7;color:#15803d}
      #trigger-msg.err{display:block;background:#fee2e2;color:#b91c1c}
    </style></head><body>
    <div class="head">
      <div>
        <h1>Fleet pipeline — last ${entries.length} runs</h1>
        <a href="/">← back to dashboard</a>
      </div>
      <div>
        <button class="trigger" id="trigger-btn" onclick="triggerRun()">
          Trigger pipeline run
        </button>
        <div class="note">Full pipeline, ~30-67 min until fresh data lands.<br>
          Daily cron runs automatically at 22:00 UTC.</div>
        <div id="trigger-msg"></div>
      </div>
    </div>
    <table>
      <thead><tr><th>Date</th><th>Status</th><th>Duration</th>
                 <th>Stages</th><th>Error</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <script>
      async function triggerRun() {
        var btn = document.getElementById('trigger-btn');
        var msg = document.getElementById('trigger-msg');
        if (!confirm('Trigger a full pipeline run? This takes ~30-67 minutes and consumes GitHub Actions minutes.')) return;
        btn.disabled = true;
        btn.textContent = 'Dispatching...';
        msg.className = ''; msg.textContent = '';
        try {
          var r = await fetch('/trigger-run', {method: 'POST'});
          var body = await r.json();
          if (body.ok) {
            msg.className = 'ok';
            msg.textContent = body.message;
            btn.textContent = 'Run dispatched';
          } else {
            msg.className = 'err';
            msg.textContent = 'Error: ' + (body.message || body.error || r.status);
            btn.disabled = false;
            btn.textContent = 'Trigger pipeline run';
          }
        } catch (e) {
          msg.className = 'err';
          msg.textContent = 'Network error: ' + e;
          btn.disabled = false;
          btn.textContent = 'Trigger pipeline run';
        }
      }
    </script>
    </body></html>`;
  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const method = req.method;
    const path = url.pathname;
    const actor = req.headers.get("CF-Access-Authenticated-User-Email") || "anon";

    // POST: pipeline triggers
    if (method === "POST") {
      if (path === "/trigger-run") {
        return triggerPipelineRun(env, `manual:${actor}`);
      }
      // Legacy /refresh + /refresh-site endpoints are removed — the
      // Refresh button now just reloads the page (injected script
      // rebinds onclick). If something still POSTs here, return a
      // helpful 410 so the operator knows the new path.
      if (path === "/refresh" || path === "/refresh-site") {
        return Response.json({
          ok: false,
          message: "This endpoint is removed. The Refresh button now " +
                   "just reloads the page (fresh from R2). To trigger a " +
                   "new pipeline run, POST /trigger-run or use the button " +
                   "on /pipeline (~67 min wall-clock).",
        }, { status: 410 });
      }
      return new Response("Method not allowed", { status: 405 });
    }

    // GET: serve content from R2
    if (path === "/status") {
      return fetchPipelineStatus(env);
    }
    if (path === "/pipeline") {
      return renderPipelinePage(env);
    }
    const key = pathToR2Key(path);
    if (key !== null) {
      return serveR2(env, key);
    }
    return new Response(`Not found: ${path}`, { status: 404 });
  },
};
