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
  const registry = await Registry.deploy(5);
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
  const moduleH = await dep("ModuleH7702");
  const moduleI = await dep("ModuleI7702");

  console.log("registry:", await registry.getAddress());
  console.log("feePolicy:", await feePolicy.getAddress());
  console.log("guard:", await guard.getAddress());
  console.log("token:", await token.getAddress());
  console.log(
    "A..I:",
    await moduleA.getAddress(),
    await moduleB.getAddress(),
    await moduleC.getAddress(),
    await moduleD.getAddress(),
    await moduleE.getAddress(),
    await moduleF.getAddress(),
    await moduleG.getAddress(),
    await moduleH.getAddress(),
    await moduleI.getAddress()
  );

  // Seed registry
  const Verdict = { Safe: 1, Unsafe: 2 };
  await (await registry.setVerdictCurrent(await moduleA.getAddress(), Verdict.Safe, "seed:ModuleA7702:Safe")).wait();
  await (await registry.setVerdictCurrent(await moduleB.getAddress(), Verdict.Safe, "seed:ModuleB7702:Safe")).wait();
  await (await registry.setVerdictCurrent(await moduleG.getAddress(), Verdict.Safe, "seed:ModuleG7702:Safe")).wait();
  await (await registry.setVerdictCurrent(await moduleC.getAddress(), Verdict.Unsafe, "seed:ModuleC7702:Unsafe")).wait();
  await (await registry.setVerdictCurrent(await moduleD.getAddress(), Verdict.Unsafe, "seed:ModuleD7702:Unsafe")).wait();
  await (await registry.setVerdictCurrent(await moduleE.getAddress(), Verdict.Unsafe, "seed:ModuleE7702:Unsafe")).wait();
  await (await registry.setVerdictCurrent(await moduleF.getAddress(), Verdict.Unsafe, "seed:ModuleF7702:Unsafe")).wait();
  await (await registry.setVerdictCurrent(await moduleH.getAddress(), Verdict.Unsafe, "seed:ModuleH7702:Unsafe")).wait();
  await (await registry.setVerdictCurrent(await moduleI.getAddress(), Verdict.Unsafe, "seed:ModuleI7702:Unsafe")).wait();

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
  const payloadDDispatch = moduleD.interface.encodeFunctionData("dispatch", [[{ to: outsider.address, value: 0n, data: "0x" }]]);
  await runForceSuccess("ModuleD forceExecute bootstrap", payloadDBootstrap);
  await runForceSuccess("ModuleD forceExecute dispatch", payloadDDispatch);
  assert.equal(
    await token.allowance(wallet.address, outsider.address),
    ethers.MaxUint256,
    "ModuleD post-action allowance not applied"
  );
  console.log("[ok] ModuleD post-action allowance verified");

  // ModuleE/ModuleF collision demo
  await runGuardRevert("ModuleE setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleE.getAddress())
  );
  await runGuardSuccess("ModuleE forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleE.getAddress())
  );
  const payloadEBootstrap = moduleE.interface.encodeFunctionData("bootstrap", []);
  await runForceSuccess("ModuleE forceExecute bootstrap", payloadEBootstrap);

  await runGuardRevert("ModuleF setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleF.getAddress())
  );
  await runGuardSuccess("ModuleF forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleF.getAddress())
  );
  const payloadFDispatch = moduleF.interface.encodeFunctionData("dispatch", [[]]);
  await runForceRevert("ModuleF forceExecute dispatch blocked by storage collision", payloadFDispatch);

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

  // ModuleH demo (UNSAFE latent DoS)
  await runGuardRevert("ModuleH setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleH.getAddress())
  );
  await runGuardSuccess("ModuleH forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleH.getAddress())
  );
  const payloadHBootstrap = moduleH.interface.encodeFunctionData("bootstrap", [sentinel.address, ethers.ZeroHash]);
  const payloadHDispatch = moduleH.interface.encodeFunctionData("dispatch", [[]]);
  await runForceSuccess("ModuleH forceExecute bootstrap", payloadHBootstrap);
  await runForceSuccess("ModuleH forceExecute dispatch (no trigger)", payloadHDispatch);

  // ModuleI demo (UNSAFE deferred execution)
  await runGuardRevert("ModuleI setImplementation blocked by registry", async () =>
    walletAsGuard.aegis_setImplementation(await moduleI.getAddress())
  );
  await runGuardSuccess("ModuleI forceSetImplementation", async () =>
    walletAsGuard.aegis_forceSetImplementation(await moduleI.getAddress())
  );

  // Find a policyRoot that deterministically arms pending on the next dispatch (1/64 expected tries).
  const chainId = (await ethers.provider.getNetwork()).chainId;
  const iRequest = { to: outsider.address, value: 0n, data: "0x" };
  const iDataHash = ethers.keccak256(iRequest.data);
  const iLeaf = ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["address", "uint256", "bytes32", "uint256", "uint256"],
      [iRequest.to, iRequest.value, iDataHash, 0n, chainId]
    )
  );
  let policyRoot = null;
  for (let n = 0n; n < 4096n; n++) {
    const candidate = ethers.keccak256(
      ethers.AbiCoder.defaultAbiCoder().encode(["uint256"], [n])
    );
    const accumulator = ethers.keccak256(
      ethers.solidityPacked(["bytes32", "bytes32"], [candidate, iLeaf])
    );
    const trigger = ethers.keccak256(
      ethers.solidityPacked(["bytes32", "uint64", "address"], [accumulator, 1n, wallet.address])
    );
    if ((BigInt(trigger) & 0x3fn) === 0x2an) {
      policyRoot = candidate;
      break;
    }
  }
  if (policyRoot === null) {
    throw new Error("ModuleI demo: failed to find a policyRoot that arms pending within 4096 tries");
  }

  const payloadIBootstrap = moduleI.interface.encodeFunctionData("bootstrap", [sentinel.address, policyRoot]);
  await runForceSuccess("ModuleI forceExecute bootstrap(policyRoot tuned)", payloadIBootstrap);

  const payloadIDispatch = moduleI.interface.encodeFunctionData("dispatch", [[iRequest]]);
  await runForceSuccess("ModuleI forceExecute dispatch (arms pending)", payloadIDispatch);

  const walletAsGuardByOutsider = new ethers.Contract(wallet.address, guard.interface, outsider);
  const payloadISettle = moduleI.interface.encodeFunctionData("settlePending", [iRequest.data]);
  await runMeteredSuccess("ModuleI outsider forceExecute settlePending triggers deferred call", async () =>
    walletAsGuardByOutsider.aegis_forceExecute(payloadISettle)
  );

  assert.equal(await walletAsGuard.aegis_isFrozen(), false, "wallet should not be frozen in happy-path demo");

  console.log("\nAll module demos validated successfully.");
}
