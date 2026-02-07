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

  async function mark(name, addr, v) {
    const verdictLabel = v === Verdict.Safe ? "Safe" : v === Verdict.Unsafe ? "Unsafe" : "Unknown";
    const summary = `seed:${name}:${verdictLabel}`;
    const description = "Seeded by deploy script (no-fee).";
    const reasons = summary;
    const tx = await registry.setRecordCurrent(addr, v, name, summary, description, reasons);
    await tx.wait();
  }

  await mark("ModuleA7702", moduleA, Verdict.Safe);
  await mark("ModuleB7702", moduleB, Verdict.Safe);
  await mark("ModuleG7702", moduleG, Verdict.Safe);
  await mark("ModuleC7702", moduleC, Verdict.Unsafe);
  await mark("ModuleD7702", moduleD, Verdict.Unsafe);
  await mark("ModuleE7702", moduleE, Verdict.Unsafe);
  await mark("ModuleF7702", moduleF, Verdict.Unsafe);
  await mark("ModuleH7702", moduleH, Verdict.Unsafe);
  await mark("ModuleI7702", moduleI, Verdict.Unsafe);

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
