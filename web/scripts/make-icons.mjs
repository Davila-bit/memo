// Renders the PWA icons from SVG with sharp. Run: pnpm icons
import sharp from "sharp";
import { mkdirSync } from "node:fs";

const out = new URL("../public/icons/", import.meta.url);
mkdirSync(out, { recursive: true });

const house = (s) => `
  <path transform="translate(${s.tx} ${s.ty}) scale(${s.k})" d="M11 23.5 24 12l13 11.5V35a2 2 0 0 1-2 2H13a2 2 0 0 1-2-2V23.5Z" fill="#F6F1E9"/>
  <path transform="translate(${s.tx} ${s.ty}) scale(${s.k})" d="M20 37V29a4 4 0 0 1 8 0v8h-8Z" fill="#D98E2B"/>`;

// Rounded square for "any"; full-bleed with the mark inside the 80% safe zone for "maskable".
const any = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" rx="11" fill="#1F2A2E"/>${house({ tx: 0, ty: 0, k: 1 })}</svg>`;
const maskable = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" fill="#1F2A2E"/>${house({ tx: 7.2, ty: 6.2, k: 0.7 })}</svg>`;
const badge = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path d="M7 23 24 8l17 15v16a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V23Z" fill="#000"/></svg>`;

const jobs = [
  [any, 192, "icon-192.png"],
  [any, 512, "icon-512.png"],
  [maskable, 512, "maskable-512.png"],
  [any, 180, "apple-touch-icon.png"],
  [badge, 96, "badge-96.png"],
];
for (const [svg, size, name] of jobs) {
  await sharp(Buffer.from(svg)).resize(size, size).png().toFile(new URL(name, out).pathname);
  console.log("wrote", name);
}
