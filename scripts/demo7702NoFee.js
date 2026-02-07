/* eslint-disable no-console */

// Demonstration script for 7702 flow without fee config.
//
// This script:
// 1) Deploys Registry + FeePolicy(disabled) + Guard + ModuleA
// 2) Marks ModuleA as SAFE
// 3) Sends one EIP-7702 (type=4) transaction: delegation + aegis_init
// 4) Executes a batch call through the delegated wallet (no fee charged)

import { network } from "hardhat";

async function main() {
  const { ethers } = await network.connect();
  const [wallet, recovery, sentinel, receiver] = await ethers.getSigners();
  console.log("wallet:", wallet.address);

  // Deploy contracts
  const Registry = await ethers.getContractFactory("ImplSafetyRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  const FeePolicy = await ethers.getContractFactory("AegisFeePolicy");
  const feePolicy = await FeePolicy.deploy(ethers.ZeroAddress, ethers.ZeroAddress, 0n);
  await feePolicy.waitForDeployment();

  const Guard = await ethers.getContractFactory("AegisGuardDelegator");
  const guard = await Guard.deploy(await registry.getAddress(), await feePolicy.getAddress());
  await guard.waitForDeployment();

  const Impl = await ethers.getContractFactory("ModuleA7702");
  const impl = await Impl.deploy();
  await impl.waitForDeployment();

  console.log("registry:", await registry.getAddress());
  console.log("feePolicy(disabled):", await feePolicy.getAddress());
  console.log("guard:", await guard.getAddress());
  console.log("impl:", await impl.getAddress());

  // Mark SAFE
  await (await registry.setVerdictCurrent(await impl.getAddress(), 1)).wait();

  // Create wallet-as-guard contract instance at the wallet's EOA address
  const walletAsGuard = new ethers.Contract(wallet.address, guard.interface, wallet);

  // Send one tx for authorization + init
  const currentNonce = await wallet.getNonce();
  const auth = await wallet.authorize({
    address: await guard.getAddress(),
    nonce: currentNonce + 1,
  });

  const txInit = await walletAsGuard.aegis_init(
    await impl.getAddress(),
    recovery.address,
    sentinel.address,
    {
      type: 4,
      authorizationList: [auth],
    }
  );
  console.log("init tx:", txInit.hash);
  await txInit.wait();

  const [feeToken, feeRecipient, feePerCall] = await walletAsGuard.aegis_getFeeConfig();
  console.log("wallet fee config:", { feeToken, feeRecipient, feePerCall: feePerCall.toString() });

  if (
    feeToken !== ethers.ZeroAddress ||
    feeRecipient !== ethers.ZeroAddress ||
    feePerCall !== 0n
  ) {
    throw new Error("Expected no-fee config to remain disabled");
  }

  // Execute through delegated wallet
  const walletAsImpl = new ethers.Contract(wallet.address, impl.interface, wallet);
  const txExec = await walletAsImpl.dispatch([{ to: receiver.address, value: 0, data: "0x" }]);
  console.log("exec tx:", txExec.hash);
  await txExec.wait();
  console.log("dispatch succeeded without fee charging");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

