/* eslint-disable no-console */

import { runAllDemos } from "./runAllDemos.js";

async function main() {
  await runAllDemos({ withFee: false });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
