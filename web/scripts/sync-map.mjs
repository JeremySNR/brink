// Copy the pipeline outputs into web/public. Run after `python -m brink.geo.build`
// (the pipeline already writes web/public/board.json + map.json; this is for when only build/ was updated).
import { copyFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const pairs = [
  [resolve(root, "build", "map.json"), resolve(here, "..", "public", "map.json")],
];
for (const [from, to] of pairs) {
  if (!existsSync(from)) {
    console.error(`missing ${from}: run \`.venv/bin/python -m brink.geo.build\` first`);
    process.exit(1);
  }
  copyFileSync(from, to);
  console.log(`copied ${from} -> ${to}`);
}
if (!existsSync(resolve(here, "..", "public", "board.json"))) {
  console.error("web/public/board.json missing: run the geometry pipeline");
  process.exit(1);
}
