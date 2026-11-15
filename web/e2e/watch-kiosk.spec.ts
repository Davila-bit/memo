import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

// Exercise the actual kiosk script with synthetic camera tracks and a fake room.
// No physical camera, call, frame file or live backend is involved.
const kioskHtml = readFileSync(path.join(__dirname, "../../backend/hearth/calls/kiosk.html"), "utf8");
const fakeSdk = `
window.kioskTest = { cameras: 0, stopped: 0, frames: 0, calls: 0, pending: false, resolveCamera: null };
const testState = window.kioskTest;
navigator.mediaDevices.getUserMedia = async () => {
  testState.cameras++;
  const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 600;
  const stream = canvas.captureStream(1);
  for (const track of stream.getTracks()) {
    const stop = track.stop.bind(track);
    track.stop = () => { testState.stopped++; stop(); };
  }
  if (testState.pending) await new Promise(resolve => { testState.resolveCamera = resolve; });
  return stream;
};
window.LivekitClient = {
  createLocalTracks: async () => ['audio', 'video'].map(kind => ({
    kind, mute: async () => {}, unmute: async () => {}, attach: () => {}, detach: () => {}
  })),
  Track: { Source: { Microphone: 'mic', Camera: 'cam' } },
  RoomEvent: { ParticipantConnected: 'joined', ParticipantDisconnected: 'left' },
  Room: class {
    constructor() {
      this.remoteParticipants = new Map(); this.handlers = {};
      this.localParticipant = { publishTrack: async () => {} };
      testState.room = this;
    }
    on(name, fn) { this.handlers[name] = fn; return this; }
    async connect() {}
    async startAudio() {}
  }
};
`;

test("kiosk flag gates capture; disclosure and camera stop before a helper joins", async ({ page }) => {
  let active = false;
  let enabled = false;
  let stateReads = 0;
  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({ body: "", contentType: "text/css" }));
  await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
  await page.route("https://cdn.jsdelivr.net/**", (route) => route.fulfill({ body: fakeSdk, contentType: "application/javascript" }));
  await page.route("**/api/kiosk/token?**", (route) => route.fulfill({ json: { url: "fake", token: "fake" } }));
  await page.route("**/internal/watch/state?**", (route) => {
    stateReads++;
    return route.fulfill({ json: { active } });
  });
  await page.route("**/internal/watch/frame?**", (route) => route.fulfill({ json: {} }));
  await page.route("**/watch-kiosk-test?**", (route) => route.fulfill({
    contentType: "text/html", body: kioskHtml.replace("/* HEARTH_WATCH */ false", String(enabled)),
  }));
  const testState = () => page.evaluate(() => {
    const state = (window as unknown as { kioskTest: { cameras: number; stopped: number } }).kioskTest;
    return { cameras: state.cameras, stopped: state.stopped };
  });
  await page.clock.install();
  await page.goto("/watch-kiosk-test?key=fake&daughter=Ruth");
  await page.getByRole("button", { name: "Tap once to get ready" }).click();
  await page.clock.runFor(20000);
  expect(stateReads).toBe(0);
  expect((await testState()).cameras).toBe(0);
  await expect(page.locator("#watchDisclosure")).toBeHidden();

  enabled = true;
  active = true;
  await page.reload();
  await page.clock.runFor(10000);
  await expect.poll(async () => (await testState()).cameras).toBe(1);
  await expect(page.locator("#watchDisclosure")).toHaveText("Ruth's helper is keeping an eye on you");
  await expect(page.locator("#watchDisclosure")).toBeVisible();
  active = false;
  await page.clock.runFor(10000);
  await expect.poll(async () => (await testState()).stopped).toBe(1);
  await expect(page.locator("#watchDisclosure")).toBeHidden();

  // A pending permission request must also release its tracks before ringing.
  await page.evaluate(() => { (window as unknown as { kioskTest: { pending: boolean } }).kioskTest.pending = true; });
  active = true;
  await page.clock.runFor(10000);
  await expect.poll(async () => (await testState()).cameras).toBe(2);
  await page.evaluate(() => {
    const state = (window as unknown as { kioskTest: {
      room: { remoteParticipants: Map<string, unknown>; handlers: Record<string, () => void> };
    } }).kioskTest;
    state.room.remoteParticipants.set("helper", {});
    state.room.handlers.joined();
  });
  await expect(page.locator("#watchDisclosure")).toBeHidden();
  await expect(page.locator("#ringing")).toBeHidden();
  await page.evaluate(() => { (window as unknown as { kioskTest: { resolveCamera: () => void } }).kioskTest.resolveCamera(); });
  await expect.poll(async () => (await testState()).stopped).toBe(2);
  await expect(page.locator("#ringing")).toBeVisible();
});
