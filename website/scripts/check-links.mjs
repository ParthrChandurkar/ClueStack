import { access, readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const htmlFiles = await findHtml(dist);
const failures = [];

for (const file of htmlFiles) {
  const html = await readFile(file, "utf8");
  for (const [, attr, href] of html.matchAll(/\s(href|src)="([^"]+)"/g)) {
    if (href.startsWith("http://") || href.startsWith("https://") || href.startsWith("mailto:")) {
      continue;
    }
    if (href.startsWith("#") || href.startsWith("data:")) {
      continue;
    }
    const clean = href.split("#")[0].split("?")[0];
    if (!clean) {
      continue;
    }
    const target = path.resolve(path.dirname(file), clean);
    try {
      await access(target);
    } catch {
      failures.push(`${path.relative(dist, file)} has missing ${attr}: ${href}`);
    }
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log(`Checked ${htmlFiles.length} HTML files and internal links.`);

async function findHtml(directory) {
  const found = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...(await findHtml(fullPath)));
    } else if (entry.name.endsWith(".html")) {
      found.push(fullPath);
    }
  }
  return found;
}
