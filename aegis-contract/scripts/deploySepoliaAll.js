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
  if (chain.chainId !== 11155111n) {
    throw new Error(`Expected Sepolia (11155111), got ${chain.chainId.toString()}`);
  }

  const [deployer] = await ethers.getSigners();
  const expected = process.env.ADDRESS?.toLowerCase();
  if (expected && deployer.address.toLowerCase() !== expected) {
    throw new Error(`Signer mismatch. env ADDRESS=${process.env.ADDRESS}, signer=${deployer.address}`);
  }

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log("network:", "sepolia");
  console.log("chainId:", chain.chainId.toString());
  console.log("deployer:", deployer.address);
  console.log("balance(ETH):", ethers.formatEther(balance));

  const deployments = {};

  // Core
  const registry = await deployByName(ethers, "ImplSafetyRegistry", [5]);
  deployments[registry.name] = registry;
  console.log(`${registry.name}: ${registry.address}`);

  const tokenFactory = await ethers.getContractFactory("AegisToken");
  const token = await tokenFactory.deploy(
    "Aegis Token",
    "AGT",
    ethers.parseUnits("1000000", 18)
  );
  const tokenDeployTx = token.deploymentTransaction();
  await token.waitForDeployment();
  deployments.AegisToken = {
    name: "AegisToken",
    address: await token.getAddress(),
    txHash: tokenDeployTx?.hash ?? null,
    instance: token,
  };
  console.log(`AegisToken: ${deployments.AegisToken.address}`);

  const feePolicyFactory = await ethers.getContractFactory("AegisFeePolicy");
  // default: no fee (disabled)
  const feePolicy = await feePolicyFactory.deploy(
    ethers.ZeroAddress,
    ethers.ZeroAddress,
    0n
  );
  const feePolicyDeployTx = feePolicy.deploymentTransaction();
  await feePolicy.waitForDeployment();
  deployments.AegisFeePolicy = {
    name: "AegisFeePolicy",
    address: await feePolicy.getAddress(),
    txHash: feePolicyDeployTx?.hash ?? null,
    instance: feePolicy,
  };
  console.log(`AegisFeePolicy: ${deployments.AegisFeePolicy.address}`);

  const guardFactory = await ethers.getContractFactory("AegisGuardDelegator");
  const guard = await guardFactory.deploy(await registry.instance.getAddress(), await feePolicy.getAddress());
  const guardDeployTx = guard.deploymentTransaction();
  await guard.waitForDeployment();
  deployments.AegisGuardDelegator = {
    name: "AegisGuardDelegator",
    address: await guard.getAddress(),
    txHash: guardDeployTx?.hash ?? null,
    instance: guard,
  };
  console.log(`AegisGuardDelegator: ${deployments.AegisGuardDelegator.address}`);

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

  // Registry verdict seeding
  const Verdict = { Safe: 1, Unsafe: 2 };
  const safeModules = ["ModuleA7702", "ModuleB7702", "ModuleG7702"];
  const unsafeModules = ["ModuleC7702", "ModuleD7702", "ModuleE7702", "ModuleF7702", "ModuleH7702", "ModuleI7702"];

  const seedTxs = [];
  for (const moduleName of safeModules) {
    const tx = await registry.instance.setVerdictCurrent(
      deployments[moduleName].address,
      Verdict.Safe,
      `seed:${moduleName}:Safe`
    );
    await tx.wait();
    seedTxs.push({ moduleName, verdict: "Safe", txHash: tx.hash });
  }
  for (const moduleName of unsafeModules) {
    const tx = await registry.instance.setVerdictCurrent(
      deployments[moduleName].address,
      Verdict.Unsafe,
      `seed:${moduleName}:Unsafe`
    );
    await tx.wait();
    seedTxs.push({ moduleName, verdict: "Unsafe", txHash: tx.hash });
  }

  // Post checks
  for (const [name, d] of Object.entries(deployments)) {
    const code = await ethers.provider.getCode(d.address);
    if (!code || code === "0x") {
      throw new Error(`No code at ${name} (${d.address})`);
    }
  }

  for (const moduleName of safeModules) {
    const ok = await registry.instance.isSafeCurrent(deployments[moduleName].address);
    if (!ok) throw new Error(`Registry SAFE check failed: ${moduleName}`);
  }
  for (const moduleName of unsafeModules) {
    const ok = await registry.instance.isSafeCurrent(deployments[moduleName].address);
    if (ok) throw new Error(`Registry UNSAFE check failed: ${moduleName}`);
  }

  const out = {
    network: "sepolia",
    chainId: chain.chainId.toString(),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    feeDefault: {
      token: ethers.ZeroAddress,
      recipient: ethers.ZeroAddress,
      feePerCall: "0",
    },
    contracts: Object.fromEntries(
      Object.entries(deployments).map(([name, d]) => [name, { address: d.address, txHash: d.txHash }])
    ),
    verdictSeeding: seedTxs,
  };

  await fs.mkdir("deployments", { recursive: true });
  const ts = out.deployedAt.replace(/[:.]/g, "-");
  const latestPath = "deployments/sepolia-latest.json";
  const snapshotPath = `deployments/sepolia-${ts}.json`;
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
