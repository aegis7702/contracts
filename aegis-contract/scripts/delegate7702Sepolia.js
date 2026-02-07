/* eslint-disable no-console */

import fs from "node:fs";
import { network } from "hardhat";

function loadDeployments() {
  const path = "deployments/sepolia-latest.json";
  if (!fs.existsSync(path)) {
    return null;
  }
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

async function main() {
  const { ethers } = await network.connect();
  const chain = await ethers.provider.getNetwork();
  if (chain.chainId !== 11155111n) {
    throw new Error(`Expected Sepolia (11155111), got ${chain.chainId.toString()}`);
  }

  const signerPk = process.env.SIGNER_PK;
  const wallet = signerPk
    ? new ethers.Wallet(signerPk, ethers.provider)
    : (await ethers.getSigners())[0];
  const expectedSigner = process.env.ADDRESS?.toLowerCase();
  if (expectedSigner && wallet.address.toLowerCase() !== expectedSigner) {
    throw new Error(`Signer mismatch. env ADDRESS=${process.env.ADDRESS}, signer=${wallet.address}`);
  }
  const deployments = loadDeployments();

  const defaultGuard = deployments?.contracts?.AegisGuardDelegator?.address;
  const defaultImpl = deployments?.contracts?.ModuleA7702?.address;

  const delegateTo = process.env.DELEGATE_TO || defaultGuard;
  if (!delegateTo) {
    throw new Error("Missing delegate target. Set DELEGATE_TO or provide deployments/sepolia-latest.json");
  }

  const withInit = process.env.WITH_INIT === "1";
  const recovery = process.env.RECOVERY || wallet.address;
  const sentinel = process.env.SENTINEL || ethers.ZeroAddress;
  const impl = process.env.IMPL || defaultImpl;

  if (withInit && !impl) {
    throw new Error("WITH_INIT=1 requires IMPL or deployments/sepolia-latest.json with ModuleA7702");
  }

  const codeBefore = await ethers.provider.getCode(wallet.address);
  const nonceLatest = await wallet.getNonce("latest");
  const noncePending = await wallet.getNonce("pending");
  const txNonce = process.env.TX_NONCE !== undefined ? Number(process.env.TX_NONCE) : noncePending;
  if (!Number.isInteger(txNonce) || txNonce < 0) {
    throw new Error(`Invalid TX_NONCE: ${process.env.TX_NONCE}`);
  }

  const defaultAuthNonce = txNonce + 1;
  const authNonce = process.env.AUTH_NONCE !== undefined ? Number(process.env.AUTH_NONCE) : defaultAuthNonce;
  if (!Number.isInteger(authNonce) || authNonce < 0) {
    throw new Error(`Invalid AUTH_NONCE: ${process.env.AUTH_NONCE}`);
  }
  if (authNonce !== defaultAuthNonce && process.env.ALLOW_NONSTANDARD_AUTH_NONCE !== "1") {
    throw new Error(
      `Invalid auth nonce for non-sponsored flow. expected ${defaultAuthNonce}, got ${authNonce}. ` +
      `Set ALLOW_NONSTANDARD_AUTH_NONCE=1 to override.`
    );
  }

  const auth = await wallet.authorize({
    address: delegateTo,
    // Non-sponsored 7702 rule: authorization nonce must be tx nonce + 1
    nonce: authNonce,
  });

  let data = "0x";
  if (withInit) {
    const Guard = await ethers.getContractFactory("AegisGuardDelegator");
    data = Guard.interface.encodeFunctionData("aegis_init", [impl, recovery, sentinel]);
  }

  console.log("network: sepolia");
  console.log("wallet:", wallet.address);
  console.log("delegateTo:", delegateTo);
  console.log("nonce.latest:", nonceLatest);
  console.log("nonce.pending:", noncePending);
  console.log("tx nonce:", txNonce);
  console.log("auth nonce:", authNonce);
  console.log("withInit:", withInit);
  if (withInit) {
    console.log("init.impl:", impl);
    console.log("init.recovery:", recovery);
    console.log("init.sentinel:", sentinel);
  }
  console.log("codeBefore:", codeBefore);

  const tx = await wallet.sendTransaction({
    type: 4,
    to: wallet.address,
    nonce: txNonce,
    data,
    authorizationList: [auth],
    gasLimit: withInit ? 400000n : 120000n,
  });
  console.log("txHash:", tx.hash);
  const receipt = await tx.wait();
  console.log("status:", receipt.status);
  console.log("block:", receipt.blockNumber);
  console.log("gasUsed:", receipt.gasUsed.toString());

  const txOnchain = await ethers.provider.send("eth_getTransactionByHash", [tx.hash]);
  const authNonceHex = txOnchain?.authorizationList?.[0]?.nonce ?? null;
  const txNonceHex = txOnchain?.nonce ?? null;
  console.log("onchain.txNonce:", txNonceHex);
  console.log("onchain.authNonce:", authNonceHex);

  const codeAfter = await ethers.provider.getCode(wallet.address);
  console.log("codeAfter:", codeAfter);

  const expectedIndicator = "0xef0100" + delegateTo.toLowerCase().replace(/^0x/, "");
  if (codeAfter.toLowerCase() !== expectedIndicator.toLowerCase()) {
    throw new Error(
      `Delegation indicator mismatch. expected=${expectedIndicator}, got=${codeAfter}`
    );
  }

  console.log("Delegation applied successfully.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
