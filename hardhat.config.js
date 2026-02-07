import { defineConfig } from "hardhat/config";
import hardhatEthers from "@nomicfoundation/hardhat-ethers";
import hardhatMocha from "@nomicfoundation/hardhat-mocha";

/**
 * Hardhat config (PoC)
 *
 * - hardfork: "prague" (EIP-7702)
 * - forking: set FORK_URL env var
 */

const FORK_URL = process.env.FORK_URL;
const FORK_BLOCK_NUMBER = process.env.FORK_BLOCK_NUMBER
  ? Number(process.env.FORK_BLOCK_NUMBER)
  : undefined;

export default defineConfig({
  plugins: [hardhatEthers, hardhatMocha],
  solidity: {
    profiles: {
      default: {
        version: "0.8.24",
        settings: {
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
  },
});
