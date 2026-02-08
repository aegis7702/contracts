## base script 
python3 -m pip install --user openai

# from repo root
set -a && source ai/.env && set +a
python3 -m ai.scan_7702 --path "ai/samples/reference/ModuleA7702.sol" --rag-top-k 5                             

## test_samples (example)
python3 -m ai.scan_7702 --path "ai/samples/test_samples/ModuleH7702.sol"

## using grok search 
python3 -m ai.scan_7702 --path "ai/samples/reference/ModuleA7702.sol" --rag-top-k 5 --x-search --x-search-days 5 --x-search-limit 3


## output format
{  
    "label": safe | unsafe ,  
    "confidence": confidence score,  
    "reasons": evidence/rationale (optional, but helps the model reason),  
    "matched_patterns": code/bytecode fragments that correspond to the reasons,  
    "analysis_source": "llm-brief" | "llm-detail" (1-stage for easy cases, 2-stage for harder ones)  
}  

### output sample
{
  "label": "SAFE",   
  "confidence": 0.95,  
  "reasons": [  
    "Critical entrypoints (bootstrap, operator/flag views, dispatch) are gated to self-calls (msg.sender == address(this)), preventing direct third‑party invocation.",  
    "Operator-controlled setFlag is the only mutating privileged path and storage uses a dedicated non-zero slot, avoiding slot-0 collision risks."  
  ],  
  "matched_patterns": [  
    "self-call authorization (msg.sender == address(this)) on bootstrap/operator/flag/dispatch",  
    "operator-only setFlag() for runtime control of boolean gate",  
    "boolean flag pause gate that blocks dispatch when true",  
    "dedicated fixed storage slot _STATE_SLOT (non-zero)"  
  ],  
  "analysis_source": "llm-brief"  
}  
