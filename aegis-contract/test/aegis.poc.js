/* eslint-disable no-console */

import assert from "node:assert/strict";
import { network } from "hardhat";

const { ethers } = await network.connect();

function delegationCode(guardAddress) {
  // EIP-7702 delegation indicator: 0xef0100 || 20-byte address
  return "0xef0100" + guardAddress.toLowerCase().replace(/^0x/, "");
}

describe("7702 Aegis PoC", function () {
  it("end-to-end: init, fee policy, force paths, collision DoS", async function () {
    const [wallet, recovery, sentinel, feeRecipient] = await ethers.getSigners();

    // Deploy registry
    const Registry = await ethers.getContractFactory("ImplSafetyRegistry");
    const registry = await Registry.deploy(5);
    await registry.waitForDeployment();

    // Deploy fee token
    const Token = await ethers.getContractFactory("AegisToken");
    const token = await Token.deploy("Aegis Token", "AGT", ethers.parseUnits("1000000", 18));
    await token.waitForDeployment();

    // Deploy fee policy and guard
    const feePerCall = ethers.parseUnits("1", 18);
    const FeePolicy = await ethers.getContractFactory("AegisFeePolicy");
    const feePolicy = await FeePolicy.deploy(await token.getAddress(), feeRecipient.address, feePerCall);
    await feePolicy.waitForDeployment();

    const Guard = await ethers.getContractFactory("AegisGuardDelegator");
    const guard = await Guard.deploy(await registry.getAddress(), await feePolicy.getAddress());
    await guard.waitForDeployment();

    // Deploy implementations
    async function dep(name) {
      const F = await ethers.getContractFactory(name);
      const c = await F.deploy();
      await c.waitForDeployment();
      return c;
    }

    const moduleA = await dep("ModuleA7702");
    const moduleC = await dep("ModuleC7702");
    const moduleE = await dep("ModuleE7702");
    const moduleF = await dep("ModuleF7702");

    // seed registry
    const Verdict = { Safe: 1, Unsafe: 2 };
    await (await registry.setVerdictCurrent(await moduleA.getAddress(), Verdict.Safe, "seed:ModuleA7702:Safe")).wait();
    await (await registry.setVerdictCurrent(await moduleC.getAddress(), Verdict.Unsafe, "seed:ModuleC7702:Unsafe")).wait();
    await (await registry.setVerdictCurrent(await moduleE.getAddress(), Verdict.Unsafe, "seed:ModuleE7702:Unsafe")).wait();
    await (await registry.setVerdictCurrent(await moduleF.getAddress(), Verdict.Unsafe, "seed:ModuleF7702:Unsafe")).wait();

    // sanity: reason storage + "recent N" ring buffer order (newest-first)
    const moduleAAddr = await moduleA.getAddress();
    const moduleCAddr = await moduleC.getAddress();
    const moduleEAddr = await moduleE.getAddress();
    const moduleFAddr = await moduleF.getAddress();

    const codehashA = await registry.extcodehash(moduleAAddr);
    assert.equal(await registry.getReason(moduleAAddr, codehashA), "seed:ModuleA7702:Safe");

    const [impls] = await registry.getRecentPairs();
    assert.deepEqual(
      impls.map((x) => x.toLowerCase()),
      [moduleFAddr, moduleEAddr, moduleCAddr, moduleAAddr].map((x) => x.toLowerCase()),
      "getRecentPairs should be newest-first"
    );
    const [mostRecent] = await registry.getRecentPairAt(0);
    assert.equal(mostRecent.toLowerCase(), moduleFAddr.toLowerCase(), "getRecentPairAt(0) should be newest");

    // --- Install 7702 delegation code to the wallet address
    const guardAddr = await guard.getAddress();
    const code = delegationCode(guardAddr);

    // Hardhat simulated networks support setting code for testing.
    // This assumes the node interprets 0xef0100||address as EIP-7702 delegation.
    await ethers.provider.send("hardhat_setCode", [wallet.address, code]);

    // Service-side fee policy + wallet funding
    await (
      await feePolicy.setWalletFeeConfig(
        wallet.address,
        await token.getAddress(),
        feeRecipient.address,
        feePerCall
      )
    ).wait();
    await (await token.transfer(wallet.address, feePerCall * 10n)).wait();

    // Build a "wallet" contract instance at wallet.address with guard ABI.
    const walletAsGuard = new ethers.Contract(wallet.address, guard.interface, wallet);

    // init guard config + set impl
    await (
      await walletAsGuard.aegis_init(
        await moduleA.getAddress(),
        recovery.address,
        sentinel.address
      )
    ).wait();

    // Call dispatch through the wallet (self-call). This should charge fee.
    const walletAsA = new ethers.Contract(wallet.address, moduleA.interface, wallet);

    const balBefore = await token.balanceOf(feeRecipient.address);
    await (await walletAsA.dispatch([{ to: feeRecipient.address, value: 0, data: "0x" }])).wait();
    const balAfter = await token.balanceOf(feeRecipient.address);
    assert.equal((balAfter - balBefore).toString(), feePerCall.toString(), "fee should be transferred");

    // Unsafe impl should be blocked by registry
    try {
      await (
        await walletAsGuard.aegis_setImplementation(await moduleC.getAddress())
      ).wait();
      assert.fail("expected revert");
    } catch (e) {
      // ok
    }

    // Force-set allows unsafe impl assignment
    await (await walletAsGuard.aegis_forceSetImplementation(await moduleC.getAddress())).wait();
    assert.equal(
      (await walletAsGuard.aegis_getImplementation()).toLowerCase(),
      (await moduleC.getAddress()).toLowerCase(),
      "force set should update implementation"
    );

    // Force-execute bypasses SAFE check only; delegatecall failure still reverts and rolls fee back.
    const beforeForce = await token.balanceOf(feeRecipient.address);
    const forceData = moduleC.interface.encodeFunctionData("dispatch", [[]]);
    try {
      await (await walletAsGuard.aegis_forceExecute(forceData)).wait();
      assert.fail("expected forced execution to revert");
    } catch (e) {
      // ok
    }
    const afterForce = await token.balanceOf(feeRecipient.address);
    assert.equal((afterForce - beforeForce).toString(), "0", "fee should roll back on forced execution revert");

    // Collision DoS demo
    await (await walletAsGuard.aegis_forceSetImplementation(await moduleE.getAddress())).wait();
    const payloadEBootstrap = moduleE.interface.encodeFunctionData("bootstrap", []);
    await (await walletAsGuard.aegis_forceExecute(payloadEBootstrap)).wait();

    await (await walletAsGuard.aegis_forceSetImplementation(await moduleF.getAddress())).wait();
    const beforeFallbackFail = await token.balanceOf(feeRecipient.address);
    const payloadFDispatch = moduleF.interface.encodeFunctionData("dispatch", [[]]);

    try {
      await (await walletAsGuard.aegis_forceExecute(payloadFDispatch)).wait();
      assert.fail("expected paused revert");
    } catch (e) {
      // ok: collision bricked the wallet
    }
    const afterFallbackFail = await token.balanceOf(feeRecipient.address);
    assert.equal((afterFallbackFail - beforeFallbackFail).toString(), "0", "fee should roll back on fallback revert");
  });
});
