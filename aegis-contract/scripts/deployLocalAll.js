/* eslint-disable no-console */

import fs from "node:fs/promises";
import { network } from "hardhat";

async function deployByName(ethers, name, args = []) {
  const F = await ethers.getContractFactory(name);
  const c = await F.deploy(...args);
  const tx = c.deploymentTransaction();
  await c.waitForDeployment();
  return {
    name,
    address: await c.getAddress(),
    txHash: tx?.hash ?? null,
    instance: c,
  };
}

async function main() {
  const { ethers } = await network.connect();
  const chain = await ethers.provider.getNetwork();
  if (chain.chainId !== 31337n) {
    throw new Error(`Expected local chainId=31337, got ${chain.chainId.toString()}`);
  }

  const [deployer] = await ethers.getSigners();
  console.log("network:", "local");
  console.log("chainId:", chain.chainId.toString());
  console.log("deployer:", deployer.address);

  const deployments = {};

  // Core
  const registry = await deployByName(ethers, "ImplSafetyRegistry", [5]);
  deployments[registry.name] = registry;
  console.log(`${registry.name}: ${registry.address}`);

  const token = await deployByName(ethers, "AegisToken", [
    "Aegis Token",
    "AGT",
    ethers.parseUnits("1000000", 18),
  ]);
  deployments[token.name] = token;
  console.log(`${token.name}: ${token.address}`);

  const feePolicy = await deployByName(ethers, "AegisFeePolicy", [
    ethers.ZeroAddress,
    ethers.ZeroAddress,
    0n,
  ]);
  deployments[feePolicy.name] = feePolicy;
  console.log(`${feePolicy.name}: ${feePolicy.address}`);

  const guard = await deployByName(ethers, "AegisGuardDelegator", [
    registry.address,
    feePolicy.address,
  ]);
  deployments[guard.name] = guard;
  console.log(`${guard.name}: ${guard.address}`);

  // Optional: allow publisher key to write to registry (used by API server).
  const publisher = process.env.PUBLISHER_ADDRESS;
  if (publisher) {
    await (await registry.instance.setPublisher(publisher, true)).wait();
    console.log("publisher enabled:", publisher);
  }

  // Fund service keys for local testing (API uses these to send txs).
  const fundTargets = [];
  if (process.env.PUBLISHER_ADDRESS) fundTargets.push(process.env.PUBLISHER_ADDRESS);
  if (process.env.SENTINEL_ADDRESS) fundTargets.push(process.env.SENTINEL_ADDRESS);
  for (const addr of fundTargets) {
    const tx = await deployer.sendTransaction({ to: addr, value: ethers.parseEther("10") });
    await tx.wait();
    console.log("funded:", addr);
  }

  // Samples
  const moduleNames = [
    "ModuleA7702",
    "ModuleB7702",
    "ModuleC7702",
    "ModuleD7702",
    "ModuleE7702",
    "ModuleF7702",
    "ModuleG7702",
    "ModuleH7702",
    "ModuleI7702",
  ];

  for (const moduleName of moduleNames) {
    const mod = await deployByName(ethers, moduleName);
    deployments[moduleName] = mod;
    console.log(`${moduleName}: ${mod.address}`);
  }

  // Registry verdict seeding (matches README classification)
  const Verdict = { Safe: 1, Unsafe: 2 };
  const safeModules = ["ModuleA7702", "ModuleB7702", "ModuleG7702"];
  const unsafeModules = ["ModuleC7702", "ModuleD7702", "ModuleE7702", "ModuleF7702", "ModuleH7702", "ModuleI7702"];

  const seedTxs = [];
  async function seed(moduleName, verdictLabel, verdict) {
    const summary = `seed:${moduleName}:${verdictLabel}`;
    const description = "Seeded by deployLocalAll.";
    const reasons = summary;
    const tx = await registry.instance.setRecordCurrent(
      deployments[moduleName].address,
      verdict,
      moduleName,
      summary,
      description,
      reasons
    );
    await tx.wait();
    seedTxs.push({ moduleName, verdict: verdictLabel, txHash: tx.hash });
  }

  for (const moduleName of safeModules) {
    await seed(moduleName, "Safe", Verdict.Safe);
  }
  for (const moduleName of unsafeModules) {
    await seed(moduleName, "Unsafe", Verdict.Unsafe);
  }

  const out = {
    network: "local",
    chainId: chain.chainId.toString(),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    contracts: Object.fromEntries(
      Object.entries(deployments).map(([name, d]) => [name, { address: d.address, txHash: d.txHash }])
    ),
    verdictSeeding: seedTxs,
  };

  await fs.mkdir("deployments", { recursive: true });
  const latestPath = "deployments/chain-31337-latest.json";
  const ts = out.deployedAt.replace(/[:.]/g, "-");
  const snapshotPath = `deployments/chain-31337-${ts}.json`;
  await fs.writeFile(latestPath, JSON.stringify(out, null, 2));
  await fs.writeFile(snapshotPath, JSON.stringify(out, null, 2));

  console.log("\nDeployment complete.");
  console.log("Saved:", latestPath);
  console.log("Saved:", snapshotPath);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

