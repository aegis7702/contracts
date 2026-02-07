/* eslint-disable no-console */

import assert from "node:assert/strict";
import { network } from "hardhat";

async function waitTx(txPromiseFactory) {
  const tx = await txPromiseFactory();
  if (tx?.wait !== undefined) {
    await tx.wait();
  }
}

export async function runAllDemos({ withFee }) {
  const { ethers } = await network.connect();
  const [providerAdmin, wallet, recovery, sentinel, feeRecipient, outsider] = await ethers.getSigners();

  console.log("wallet:", wallet.address);
  console.log("mode:", withFee ? "fee-enabled" : "no-fee");

  const feePerCall = ethers.parseUnits("1", 18);

  // Deploy contracts
  const Registry = await ethers.getContractFactory("ImplSafetyRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  const Token = await ethers.getContractFactory("AegisToken");
  const token = await Token.deploy("Aegis Token", "AGT", ethers.parseUnits("1000000", 18));
  await token.waitForDeployment();

  const FeePolicy = await ethers.getContractFactory("AegisFeePolicy");
  const feePolicy = await FeePolicy.connect(providerAdmin).deploy(
    withFee ? await token.getAddress() : ethers.ZeroAddress,
    withFee ? feeRecipient.address : ethers.ZeroAddress,
    withFee ? feePerCall : 0n
  );
  await feePolicy.waitForDeployment();

  const Guard = await ethers.getContractFactory("AegisGuardDelegator");
  const guard = await Guard.deploy(await registry.getAddress(), await feePolicy.getAddress());
  await guard.waitForDeployment();

  async function dep(name) {
    const F = await ethers.getContractFactory(name);
    const c = await F.deploy();
    await c.waitForDeployment();
    return c;
  }

  const moduleA = await dep("ModuleA7702");
  const moduleB = await dep("ModuleB7702");
  const moduleC = await dep("ModuleC7702");
  const moduleD = await dep("ModuleD7702");
  const moduleE = await dep("ModuleE7702");
  const moduleF = await dep("ModuleF7702");
  const moduleG = await dep("ModuleG7702");

  console.log("registry:", await registry.getAddress());
  console.log("feePolicy:", await feePolicy.getAddress());
  console.log("guard:", await guard.getAddress());
  console.log("token:", await token.getAddress());
  console.log("A..G:", await moduleA.getAddress(), await moduleB.getAddress(), await moduleC.getAddress(), await moduleD.getAddress(), await moduleE.getAddress(), await moduleF.getAddress(), await moduleG.getAddress());

  // Seed registry
  const Verdict = { Safe: 1, Unsafe: 2 };
  await (await registry.setVerdictCurrent(await moduleA.getAddress(), Verdict.Safe)).wait();
  await (await registry.setVerdictCurrent(await moduleB.getAddress(), Verdict.Safe)).wait();
  await (await registry.setVerdictCurrent(await moduleE.getAddress(), Verdict.Safe)).wait();
  await (await registry.setVerdictCurrent(await moduleF.getAddress(), Verdict.Safe)).wait();
  await (await registry.setVerdictCurrent(await moduleG.getAddress(), Verdict.Safe)).wait();
  await (await registry.setVerdictCurrent(await moduleC.getAddress(), Verdict.Unsafe)).wait();
  await (await registry.setVerdictCurrent(await moduleD.getAddress(), Verdict.Unsafe)).wait();

  if (withFee) {
    await (
      await feePolicy.connect(providerAdmin).setWalletFeeConfig(
        wallet.address,
        await token.getAddress(),
        feeRecipient.address,
        feePerCall
      )
    ).wait();
    await (await token.transfer(wallet.address, feePerCall * 100n)).wait();
  }

  const walletAsGuard = new ethers.Contract(wallet.address, guard.interface, wallet);
  const walletAsGBySentinel = new ethers.Contract(wallet.address, moduleG.interface, sentinel);
  const walletAsGByOutsider = new ethers.Contract(wallet.address, moduleG.interface, outsider);
  const walletAsBByOutsider = new ethers.Contract(wallet.address, moduleB.interface, outsider);

  // 7702 authorization + init
  const currentNonce = await wallet.getNonce();
  const auth = await wallet.authorize({
    address: await guard.getAddress(),
    nonce: currentNonce + 1,
  });
  const moduleAAddress = await moduleA.getAddress();

  await waitTx(async () =>
    walletAsGuard.aegis_init(
      moduleAAddress,
      recovery.address,
      sentinel.address,
      {
        type: 4,
        authorizationList: [auth],
      }
    )
  );

  const [cfgToken, cfgRecipient, cfgFee] = await walletAsGuard.aegis_getFeeConfig();
  if (withFee) {
    assert.equal(cfgToken.toLowerCase(), (await token.getAddress()).toLowerCase(), "fee token mismatch");
    assert.equal(cfgRecipient.toLowerCase(), feeRecipient.address.toLowerCase(), "fee recipient mismatch");
    assert.equal(cfgFee, feePerCall, "fee amount mismatch");
  } else {
    assert.equal(cfgToken, ethers.ZeroAddress, "no-fee token should be zero");
    assert.equal(cfgRecipient, ethers.ZeroAddress, "no-fee recipient should be zero");
    assert.equal(cfgFee, 0n, "no-fee amount should be zero");
  }

  async function runMeteredSuccess(label, txPromiseFactory) {
    const before = await token.balanceOf(feeRecipient.address);
    await waitTx(txPromiseFactory);
    const after = await token.balanceOf(feeRecipient.address);
    const expectedDelta = withFee ? feePerCall : 0n;
    assert.equal(after - before, expectedDelta, `${label}: fee delta mismatch`);
    console.log("[ok]", label);
  }

  async function runMeteredRevert(label, txPromiseFactory) {
    const before = await token.balanceOf(feeRecipient.address);
    let reverted = false;
    try {
      await waitTx(txPromiseFactory);
    } catch {
      reverted = true;
    }
    assert.equal(reverted, true, `${label}: expected revert`);
    const after = await token.balanceOf(feeRecipient.address);
    assert.equal(after - before, 0n, `${label}: fee must roll back on revert`);
    console.log("[ok]", label, "(reverted)");
  }

  async function runGuardSuccess(label, txPromiseFactory) {
    const before = await token.balanceOf(feeRecipient.address);
    await waitTx(txPromiseFactory);
    const after = await token.balanceOf(feeRecipient.address);
    assert.equal(after - before, 0n, `${label}: guard tx must not charge fee`);
    console.log("[ok]", label);
  }

  async function runGuardRevert(label, txPromiseFactory) {
    const before = await token.balanceOf(feeRecipient.address);
    let reverted = false;
    try {
      await waitTx(txPromiseFactory);
    } catch {
      reverted = true;
    }
    assert.equal(reverted, true, `${label}: expected revert`);
    const after = await token.balanceOf(feeRecipient.address);
    assert.equal(after - before, 0n, `${label}: reverted guard tx must not charge fee`);
    console.log("[ok]", label, "(reverted)");
  }

  async function runForceSuccess(label, payload) {
    await runMeteredSuccess(label, async () => walletAsGuard.aegis_forceExecute(payload));
  }

  async function runForceRevert(label, payload) {
    await runMeteredRevert(label, async () => walletAsGuard.aegis_forceExecute(payload));
  }

  // ModuleA demo
  const walletAsA = new ethers.Contract(wallet.address, moduleA.interface, wallet);
  await runMeteredSuccess("ModuleA SAFE dispatch", async () =>
    walletAsA.dispatch([{ to: outsider.address, value: 0, data: "0x" }])
  );

  // ModuleB demo
  await runGuardSuccess("Switch implementation -> ModuleB", async () =>
    walletAsGuard.aegis_setImplementation(await moduleB.getAddress())
  );
  const walletAsBByWallet = new ethers.Contract(wallet.address, moduleB.interface, wallet);
  const bRule = {
    start: 0,
    end: 0,
    target: outsider.address,
    selector: "0x00000000",
    maxValue: 0n,
    enabled: true,
  };
  await runMeteredSuccess("ModuleB setRule(self)", async () =>
    walletAsBByWallet.setRule(outsider.address, bRule)
  );
  await runMeteredSuccess("ModuleB outsider dispatch allowed", async () =>
    walletAsBByOutsider.dispatch([{ to: outsider.address, value: 0, data: "0x" }])
  );
  await runMeteredRevert("ModuleB outsider dispatch target mismatch", async () =>
    walletAsBByOutsider.dispatch([{ to: feeRecipient.address, value: 0, data: "0x" }])
  );
  await runMeteredSuccess("ModuleB disableRule(self)", async () =>
    walletAsBByWallet.disableRule(outsider.address)
  );
  await runMeteredRevert("ModuleB outsider dispatch blocked after disable", async () =>
    walletAsBByOutsider.dispatch([{ to: outsider.address, value: 0, data: "0x" }])
  );

  // ModuleC demo (UNSAFE)
  await runGuardRevert("ModuleC setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleC.getAddress())
  );
  await runGuardSuccess("ModuleC forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleC.getAddress())
  );
  const payloadCDispatch = moduleC.interface.encodeFunctionData("dispatch", [[]]);
  const payloadCBootstrap = moduleC.interface.encodeFunctionData("bootstrap", []);
  await runForceRevert("ModuleC forceExecute dispatch before bootstrap", payloadCDispatch);
  await runForceSuccess("ModuleC forceExecute bootstrap", payloadCBootstrap);
  await runForceSuccess("ModuleC forceExecute dispatch after bootstrap", payloadCDispatch);

  // ModuleD demo (UNSAFE side effect)
  await runGuardRevert("ModuleD setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleD.getAddress())
  );
  await runGuardSuccess("ModuleD forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleD.getAddress())
  );
  const payloadDBootstrap = moduleD.interface.encodeFunctionData("bootstrap", [await token.getAddress()]);
  const payloadDDispatch = moduleD.interface.encodeFunctionData("dispatch", [[]]);
  await runForceSuccess("ModuleD forceExecute bootstrap", payloadDBootstrap);
  await runForceSuccess("ModuleD forceExecute dispatch", payloadDDispatch);
  assert.equal(
    await token.allowance(wallet.address, wallet.address),
    ethers.MaxUint256,
    "ModuleD post-action allowance not applied"
  );
  console.log("[ok] ModuleD post-action allowance verified");

  // ModuleE/ModuleF collision demo
  await runGuardSuccess("Switch implementation -> ModuleE", async () =>
    walletAsGuard.aegis_setImplementation(await moduleE.getAddress())
  );
  const walletAsE = new ethers.Contract(wallet.address, moduleE.interface, wallet);
  await runMeteredSuccess("ModuleE bootstrap", async () => walletAsE.bootstrap());

  await runGuardSuccess("Switch implementation -> ModuleF", async () =>
    walletAsGuard.aegis_setImplementation(await moduleF.getAddress())
  );
  const walletAsF = new ethers.Contract(wallet.address, moduleF.interface, wallet);
  await runMeteredRevert("ModuleF dispatch blocked by storage collision", async () =>
    walletAsF.dispatch([])
  );

  // ModuleG operator-gated demo
  await runGuardSuccess("Switch implementation -> ModuleG", async () =>
    walletAsGuard.aegis_setImplementation(await moduleG.getAddress())
  );
  const walletAsGByWallet = new ethers.Contract(wallet.address, moduleG.interface, wallet);
  await runMeteredSuccess("ModuleG bootstrap(operator=sentinel)", async () =>
    walletAsGByWallet.bootstrap(sentinel.address)
  );
  await runMeteredRevert("ModuleG outsider cannot setFlag", async () =>
    walletAsGByOutsider.setFlag(true)
  );
  await runMeteredSuccess("ModuleG operator setFlag(true)", async () =>
    walletAsGBySentinel.setFlag(true)
  );
  await runMeteredRevert("ModuleG dispatch blocked when flag=true", async () =>
    walletAsGByWallet.dispatch([])
  );
  await runMeteredSuccess("ModuleG operator setFlag(false)", async () =>
    walletAsGBySentinel.setFlag(false)
  );
  await runMeteredSuccess("ModuleG dispatch after unblock", async () =>
    walletAsGByWallet.dispatch([])
  );

  assert.equal(await walletAsGuard.aegis_isFrozen(), false, "wallet should not be frozen in happy-path demo");

  console.log("\nAll module demos validated successfully.");
}
