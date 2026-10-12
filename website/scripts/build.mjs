import { copyFile, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { pages } from "../src/pages.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");
const dist = path.join(root, "dist");

const layout = await readFile(path.join(src, "layout.html"), "utf8");

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await copyDir(path.join(src, "assets"), path.join(dist, "assets"));

const nav = pages
  .filter((page) => page.nav)
  .map((page) => `<a href="${page.href}" data-route="${page.href}">${page.nav}</a>`)
  .join("");

for (const page of pages) {
  const html = layout
    .replaceAll("{{title}}", escapeHtml(page.title))
    .replaceAll("{{description}}", escapeHtml(page.description))
    .replaceAll("{{nav}}", nav)
    .replaceAll("{{active}}", page.href)
    .replaceAll("{{content}}", page.content)
    .replaceAll("{{bodyClass}}", page.bodyClass || "");
  const output = path.join(dist, page.href);
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, html, "utf8");
}

async function copyDir(from, to) {
  await mkdir(to, { recursive: true });
  for (const entry of await readdir(from)) {
    const source = path.join(from, entry);
    const target = path.join(to, entry);
    const details = await stat(source);
    if (details.isDirectory()) {
      await copyDir(source, target);
    } else {
      await copyFile(source, target);
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
