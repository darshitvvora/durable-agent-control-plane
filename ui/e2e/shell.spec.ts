import { expect, test } from "@playwright/test";

/** The desktop shell, end to end against the real stack.
 *
 * Requires `make worker`, `make api`, `make mockoon` and `npm run dev` running,
 * with the three demo tenants and agents published. Every number these tests
 * assert on came from a real Temporal or AWS call — the UI has no fixtures to
 * fall back on (CLAUDE.md §7).
 */

test("four panes are always on", async ({ page }) => {
  await page.goto("/");
  for (const title of ["Agent Store", "Process Monitor", "Session", "System Controls"]) {
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: "Durable Agent OS" })).toBeVisible();
});

test("status strip reports a real worker count", async ({ page }) => {
  await page.goto("/");
  const workers = page.locator("header").first().getByText("Workers").locator("xpath=..");
  // A worker must actually be polling; "—" means the API never answered.
  await expect(workers).not.toContainText("—");
  await expect(workers).toContainText(/\d+/);
});

test("agent store lists published agents with tier badges", async ({ page }) => {
  await page.goto("/");
  const store = page.getByRole("heading", { name: "Agent Store" }).locator("xpath=../..");
  await expect(store.getByRole("heading", { name: "incident-triage" })).toBeVisible();
  await expect(store.getByRole("heading", { name: "invoice-exception" })).toBeVisible();
  await expect(store.getByRole("heading", { name: "dispute-resolution" })).toBeVisible();
  // Native vs hosted is the badge the store exists to show.
  await expect(store.getByText("Native").first()).toBeVisible();
});

test("process monitor shows one lane per tenant with real fairness weights", async ({ page }) => {
  await page.goto("/");
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(3);
  for (const tenant of ["acme", "globex", "initech"]) {
    await expect(page.locator("tbody").getByText(tenant, { exact: true })).toBeVisible();
  }
  // Weights are the mechanism proof 1 turns on, so they must be on screen.
  await expect(page.locator("tbody").getByText("w3.0")).toBeVisible();
  await expect(page.locator("tbody").getByText("w1.0")).toBeVisible();
});

test("nothing on screen is smaller than the 13px stage floor", async ({ page }) => {
  await page.goto("/");
  const floor = await page.evaluate(() => {
    const leaves = [...document.querySelectorAll("*")].filter(
      (e) => e.children.length === 0 && (e.textContent ?? "").trim(),
    );
    return Math.min(...leaves.map((e) => parseFloat(getComputedStyle(e).fontSize)));
  });
  expect(floor).toBeGreaterThanOrEqual(13);
});

test("flooding a tenant drives its lane and streams a real session", async ({ page }) => {
  await page.goto("/");

  // Flood initech — the tier-1 flood generator — through the UI's own control.
  // Scoped to System Controls: the page now has a second combobox and a
  // second "Run" button (Run session's agent picker and its submit button,
  // folded into the Session panel), so an unscoped query is ambiguous.
  const systemControls = page
    .getByRole("heading", { name: "System Controls", exact: true })
    .locator("xpath=../..");
  await systemControls.getByRole("combobox").selectOption("initech");
  await systemControls.getByRole("spinbutton", { name: "flood count" }).fill("3");
  await systemControls.getByRole("button", { name: "Run" }).click();

  // The lane's running count must actually move; it comes from Temporal's
  // visibility store, not from anything the UI made up.
  const initechRow = page.locator("tbody tr").filter({ hasText: "initech" });
  await expect(initechRow.locator("td").nth(3)).not.toHaveText("0");

  // The session terminal follows the newest job and streams model output.
  const session = page.getByRole("heading", { name: "Session", exact: true }).locator("xpath=../..");
  await expect(session).toContainText("session started", { timeout: 90_000 });
  await expect(session).toContainText("agent-control-plane:", { timeout: 90_000 });
  // Real tokens arrived, and the terminal event carries the model's stop reason
  // (regression guard: job_events nest their detail under `payload`).
  await expect(session).toContainText("session finished — end_turn", { timeout: 120_000 });
});

test("a session can be started from the UI and streams", async ({ page }) => {
  await page.goto("/");
  // Run session is folded into the Session panel (fix round 1: a standalone
  // panel clipped the Agent Store on a 1080p stage screen), so scope to that
  // panel rather than `getByRole("combobox").last()` — System Controls' tenant
  // picker is also a combobox on this page, and which one is last in the DOM
  // is an accident of layout, not a contract worth asserting on.
  const session = page.getByRole("heading", { name: "Session", exact: true }).locator("xpath=../..");
  // selectOption's `label` matcher requires an exact string, not a RegExp —
  // the rendered option text is `${name} · tier ${tier}`.
  await session.getByRole("combobox").selectOption({ label: "Returns Triage · tier 1" });
  await session
    .getByPlaceholder("Prompt for this session")
    .fill("Order A-1004, unopened, 6 days since delivery.");
  await session.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/session started/)).toBeVisible({ timeout: 60_000 });
});

// Every spec above starts from a fresh page, which is exactly why none of them
// caught the defect this one guards: a presenter runs several sessions in a row
// without reloading, on a page that also has a flood running underneath. Beats
// 0-3 of the demo script are exactly that shape.
//
// Three distinct causes have been found from this reproduction; the first two
// were fixed on 2026-09-01 (an unclosed EventSource reconnect-looping after
// `job_finished`, and the same loop when attaching to an already-terminal job).
// The third, fixed under E8.1 T0: the pane followed the tenant's *newest* job on
// a 2s poll, so a flood job arriving mid-session tore down a live EventSource
// (`readyState === 1`, `job_started` already delivered) and re-attached
// elsewhere — indistinguishable on stage from "the stream died without
// finishing". See docs/DECISIONS.md (2026-09-04).
//
// The assertions are scoped to `[data-job-id=...]` for the session this round
// actually started. That is the whole point: an unscoped "contains session
// started" passes on a flood job's transcript, which is how the previous
// version of this spec passed while the defect was live.
test("four consecutive sessions on one page all stream", async ({ page }) => {
  await page.goto("/");

  const session = page.getByRole("heading", { name: "Session", exact: true }).locator("xpath=../..");
  const systemControls = page
    .getByRole("heading", { name: "System Controls", exact: true })
    .locator("xpath=../..");

  await systemControls.getByRole("combobox").selectOption("initech");
  await systemControls.getByRole("spinbutton", { name: "flood count" }).fill("3");
  // Returns Triage is tier 1 and toolless — cheap enough to run four times, and
  // a different agent from the flood's (incident-triage), so a swapped-in flood
  // job is visible in the transcript rather than looking like the same session.
  await session.getByRole("combobox").selectOption({ label: "Returns Triage · tier 1" });

  const runOneSession = async (round: number) => {
    await session
      .getByPlaceholder("Prompt for this session")
      .fill(`Order A-100${round}, unopened, ${round + 3} days since delivery.`);
    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/jobs",
      ),
      session.getByRole("button", { name: "Run" }).click(),
    ]);
    const { job_id: jobId } = (await response.json()) as { job_id: string };

    // Flood *after* starting the session, so the flood's jobs are newer than it
    // — that is what used to yank the pane away. The demo runs a flood
    // continuously underneath these beats, so this is the real shape, not a
    // contrived race.
    await systemControls.getByRole("button", { name: "Run" }).click();

    const well = session.locator(`[data-job-id="${jobId}"]`);
    await expect(well).toBeVisible({ timeout: 30_000 });
    await expect(well).toContainText("session started — returns-triage", { timeout: 90_000 });
    await expect(well).toContainText("session finished", { timeout: 120_000 });
  };

  for (let round = 0; round < 4; round++) await runOneSession(round);
});

// Found during E8.1 T2's first timed rehearsal, on the real stack: beat 0's
// session completed correctly on the worker and in Event History, but the pane
// read `— waiting for output (completed)` and showed nothing. The stream was
// never at fault. Changing the tenant — the dropdown in System Controls, or a
// click on a Process Monitor lane, both `selectTenant` — released the pin on a
// *live* session, auto-follow re-targeted the newly selected tenant's newest
// job, and `setEntries([])` wiped the transcript already on screen.
//
// Unrecoverable, which is what makes it a stage defect rather than a blemish:
// Workflow Streams is a live log, not durable history, so a finished session
// has nothing left to replay. Re-running the beat is the only way back.
//
// The operator must still be able to look at another tenant's lane mid-session,
// so the fix keeps the selection working for every other pane and only refuses
// to abandon the transcript.
test("a live session survives a tenant change", async ({ page }) => {
  await page.goto("/");

  const session = page.getByRole("heading", { name: "Session", exact: true }).locator("xpath=../..");

  // Tenant selection is a Process Monitor lane click — System Controls' own
  // combobox now targets only the flood (see SystemControls.tsx).
  await page.getByRole("row", { name: /^initech/ }).click();
  await session.getByRole("combobox").selectOption({ label: "Returns Triage · tier 1" });
  await session
    .getByPlaceholder("Prompt for this session")
    .fill("Order A-2001, unopened, 5 days since delivery.");

  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/jobs",
    ),
    session.getByRole("button", { name: "Run" }).click(),
  ]);
  const { job_id: jobId } = (await response.json()) as { job_id: string };

  const well = session.locator(`[data-job-id="${jobId}"]`);
  await expect(well).toContainText("session started — returns-triage", { timeout: 90_000 });

  // The stray click a presenter makes while narrating. Before the fix this
  // swapped the pane onto acme's newest job and cleared the transcript.
  await page.getByRole("row", { name: /^acme/ }).click();

  // Still this session, still its transcript, still runs to completion.
  await expect(well).toContainText("session started — returns-triage");
  await expect(well).toContainText("session finished", { timeout: 120_000 });
});
