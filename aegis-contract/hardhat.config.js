import { defineConfig } from "hardhat/config";
import hardhatEthers from "@nomicfoundation/hardhat-ethers";
import hardhatMocha from "@nomicfoundation/hardhat-mocha";
import fs from "node:fs";

/**
 * Hardhat config (PoC)
 *
 * - hardfork: "prague" (EIP-7702)
 * - forking: set FORK_URL env var
 */

function loadDotEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return;
  }
  const raw = fs.readFileSync(filePath, "utf8");
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx === -1) continue;
    const key = trimmed.slice(0, idx).trim();
    const value = trimmed.slice(idx + 1).trim();
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

loadDotEnvFile(".env.testnet");

const FORK_URL = process.env.FORK_URL;
const FORK_BLOCK_NUMBER = process.env.FORK_BLOCK_NUMBER
  ? Number(process.env.FORK_BLOCK_NUMBER)
  : undefined;
const SEPOLIA_RPC_URL =
  process.env.SEPOLIA_RPC_URL ||
  process.env.RPC_URL ||
  "https://ethereum-sepolia-rpc.publicnode.com";
const SEPOLIA_ACCOUNTS = [
  process.env.DEPLOYER_PK,
  process.env.PUBLISHER_PK,
  process.env.SENTINEL_PK,
  process.env.PK,
  process.env.PRIVATE_KEY,
].filter(Boolean);
const SEPOLIA_ACCOUNTS_UNIQUE = [...new Set(SEPOLIA_ACCOUNTS)];

export default defineConfig({
  plugins: [hardhatEthers, hardhatMocha],
  paths: {
    // Keep core on-chain contracts under `contracts/`.
    // Optional: if `samples/` exists (typically a symlink to `../aegis-ai-core/ai/samples`),
    // include it so PoC scripts/tests can deploy ModuleA..I implementations.
    sources: ["contracts", ...(fs.existsSync("samples") ? ["samples"] : [])],
  },
  solidity: {
    profiles: {
      default: {
        version: "0.8.24",
        settings: {
          viaIR: true,
          optimizer: { enabled: true, runs: 200 },
        },
      },
    },
  },
  defaultChainType: "l1",
  networks: {
    hardhatMainnet: {
      type: "edr-simulated",
      chainType: "l1",
      chainId: 31337,
      hardfork: "prague",
      forking: FORK_URL
        ? {
            url: FORK_URL,
            blockNumber: FORK_BLOCK_NUMBER,
          }
        : undefined,
    },
    localhost: {
      type: "http",
      chainType: "l1",
      url: "http://127.0.0.1:8545",
      accounts: "remote",
    },
    sepolia: {
      type: "http",
      chainType: "l1",
      url: SEPOLIA_RPC_URL,
      accounts: SEPOLIA_ACCOUNTS_UNIQUE,
    },
  },
});
