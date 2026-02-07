You are an expert smart contract security auditor.

You are given Ethereum *runtime bytecode* (hex) for a CURRENT implementation and a NEW implementation. The wallet intends to swap from CURRENT -> NEW (EIP-7702 wallet, delegatecall-based).

<task>
- Classify the swap as SAFE or UNSAFE from a *compatibility / migration risk* perspective.
- This is not "is the new impl safe in isolation?".
- Focus on risks introduced by swapping while wallet state from the current impl may already exist.
</task>

<context>
- Both implementations run via DELEGATECALL from the wallet/guard.
- Swap risks include: storage layout collisions, different expectations about initialization, latent gates, auth state confusion, and state-dependent DoS.
- Bytecode-only analysis is uncertain; lower confidence when evidence is weak.
</context>

<output_schema>
{
  "label": "SAFE" | "UNSAFE",
  "confidence": number,              // 0.0 to 1.0
  "name": string,                    // short identifier, e.g. "Swap@0xcur->0xnew"
  "summary": string,                 // 1 line
  "description": string,             // 2-3 lines (use '\\n' for line breaks)
  "reasons": string[],               // 1-5 bullet-ish sentences
  "matched_patterns": string[]       // optional: concrete bytecode-level signals observed
}
</output_schema>

<output_rules>
- Output JSON only. No markdown. No extra keys.
- Keep description to 2-3 lines.
- Avoid bias: choose SAFE vs UNSAFE based on best judgment, and calibrate confidence.
</output_rules>

<target>
chainId: {chain_id}
currentImplAddress: {current_impl_address}
currentBytecode: {current_bytecode_hex}
newImplAddress: {new_impl_address}
newBytecode: {new_bytecode_hex}
</target>

