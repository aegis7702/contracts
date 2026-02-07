/* eslint-disable no-console */

import { network } from "hardhat";

async function main() {
  const { ethers } = await network.connect();
  const [deployer] = await ethers.getSigners();
  console.log("deployer:", deployer.address);

  // --- Deploy Registry
  const Registry = await ethers.getContractFactory("ImplSafetyRegistry");
  const registry = await Registry.deploy(5);
  await registry.waitForDeployment();
  console.log("ImplSafetyRegistry:", await registry.getAddress());

  // --- Deploy Fee Policy (disabled defaults)
  const FeePolicy = await ethers.getContractFactory("AegisFeePolicy");
  const feePolicy = await FeePolicy.deploy(
    ethers.ZeroAddress,
    ethers.ZeroAddress,
    0n
  );
  await feePolicy.waitForDeployment();
  console.log("AegisFeePolicy (disabled):", await feePolicy.getAddress());

  // --- Deploy Guard
  const Guard = await ethers.getContractFactory("AegisGuardDelegator");
  const guard = await Guard.deploy(await registry.getAddress(), await feePolicy.getAddress());
  await guard.waitForDeployment();
  console.log("AegisGuardDelegator:", await guard.getAddress());

  // --- Deploy implementations
  async function dep(name) {
    const F = await ethers.getContractFactory(name);
    const c = await F.deploy();
    await c.waitForDeployment();
    const addr = await c.getAddress();
    console.log(name + ":", addr);
    return addr;
  }

  const moduleA = await dep("ModuleA7702");
  const moduleB = await dep("ModuleB7702");
  const moduleC = await dep("ModuleC7702");
  const moduleD = await dep("ModuleD7702");
  const moduleE = await dep("ModuleE7702");
  const moduleF = await dep("ModuleF7702");
  const moduleG = await dep("ModuleG7702");
  const moduleH = await dep("ModuleH7702");
  const moduleI = await dep("ModuleI7702");

  // --- Populate registry verdicts
  const Verdict = {
    Unknown: 0,
    Safe: 1,
    Unsafe: 2,
  };

  async function mark(addr, v, reason = "") {
    const tx = await registry.setVerdictCurrent(addr, v, reason);
    await tx.wait();
  }

  await mark(moduleA, Verdict.Safe, "seed:ModuleA7702:Safe");
  await mark(moduleB, Verdict.Safe, "seed:ModuleB7702:Safe");
  await mark(moduleG, Verdict.Safe, "seed:ModuleG7702:Safe");
  await mark(moduleC, Verdict.Unsafe, "seed:ModuleC7702:Unsafe");
  await mark(moduleD, Verdict.Unsafe, "seed:ModuleD7702:Unsafe");
  await mark(moduleE, Verdict.Unsafe, "seed:ModuleE7702:Unsafe");
  await mark(moduleF, Verdict.Unsafe, "seed:ModuleF7702:Unsafe");
  await mark(moduleH, Verdict.Unsafe, "seed:ModuleH7702:Unsafe");
  await mark(moduleI, Verdict.Unsafe, "seed:ModuleI7702:Unsafe");

  console.log("\nRegistry seeded.");
  console.log("Fee policy is disabled by default (token=0x0, recipient=0x0, feePerCall=0).");
  console.log("\nNext:");
  console.log("1) Install EIP-7702 delegation: 0xef0100 || <GuardAddress>.");
  console.log("2) Self-call aegis_init(impl, recovery, sentinel).");
  console.log("3) Call wallet.dispatch(...) through delegated wallet; no fee is charged.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
