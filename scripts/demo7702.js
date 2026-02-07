/* eslint-disable no-console */

// Demonstration script for a local Hardhat chain with Prague hardfork.
//
// This script:
// 1) Deploys Registry + FeePolicy + Guard + FeeToken + one sample impl
// 2) Marks the sample impl as SAFE
// 3) Sends a single EIP-7702 (type=4) transaction that delegates the wallet to the Guard and calls aegis_init
// 4) Executes a batch call through the wallet (now delegated)
//
// Requires Hardhat Network to run with hardfork=prague.
// Ref: QuickNode guide for EIP-7702 + ethers.js authorize()/authorizationList. (updated Nov 12, 2025)

import { network } from "hardhat";

async function main() {
  const { ethers } = await network.connect();
  const [providerAdmin, wallet, recovery, sentinel, feeRecipient] = await ethers.getSigners();
  console.log("wallet:", wallet.address);

  // Deploy contracts
  const Registry = await ethers.getContractFactory("ImplSafetyRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  const Token = await ethers.getContractFactory("AegisToken");
  const token = await Token.deploy("Aegis Token", "AGT", ethers.parseUnits("1000000", 18));
  await token.waitForDeployment();

  const feePerCall = ethers.parseUnits("1", 18);
  const FeePolicy = await ethers.getContractFactory("AegisFeePolicy");
  const feePolicy = await FeePolicy.connect(providerAdmin).deploy(
    await token.getAddress(),
    feeRecipient.address,
    feePerCall
  );
  await feePolicy.waitForDeployment();

  const Guard = await ethers.getContractFactory("AegisGuardDelegator");
  const guard = await Guard.deploy(await registry.getAddress(), await feePolicy.getAddress());
  await guard.waitForDeployment();

  const Impl = await ethers.getContractFactory("ModuleA7702");
  const impl = await Impl.deploy();
  await impl.waitForDeployment();

  console.log("registry:", await registry.getAddress());
  console.log("feePolicy:", await feePolicy.getAddress());
  console.log("guard:", await guard.getAddress());
  console.log("token:", await token.getAddress());
  console.log("impl:", await impl.getAddress());

  // Mark SAFE
  await (await registry.setVerdictCurrent(await impl.getAddress(), 1)).wait();

  // Fund wallet with fee token
  // Service-side fee config for this wallet
  await (
    await feePolicy.connect(providerAdmin).setWalletFeeConfig(
      wallet.address,
      await token.getAddress(),
      feeRecipient.address,
      feePerCall
    )
  ).wait();
  await (await token.transfer(wallet.address, feePerCall * 10n)).wait();

  // Create wallet-as-guard contract instance at the wallet's EOA address
  const walletAsGuard = new ethers.Contract(wallet.address, guard.interface, wallet);

  // --- Send a SINGLE EIP-7702 transaction that both sets delegation AND calls aegis_init
  // Non-sponsored rule of thumb: authorization nonce must be (currentNonce + 1)
  const currentNonce = await wallet.getNonce();
  const auth = await wallet.authorize({
    address: await guard.getAddress(),
    nonce: currentNonce + 1,
    // chainId: (await wallet.provider.getNetwork()).chainId,
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

  // Now the wallet has delegation installed; we can call the implementation interface directly.
  const walletAsImpl = new ethers.Contract(wallet.address, impl.interface, wallet);
  const before = await token.balanceOf(feeRecipient.address);

  const txExec = await walletAsImpl.dispatch([{ to: feeRecipient.address, value: 0, data: "0x" }]);
  console.log("exec tx:", txExec.hash);
  await txExec.wait();

  const after = await token.balanceOf(feeRecipient.address);
  console.log("fee paid:", (after - before).toString());
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
