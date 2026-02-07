## base script 
python -m ai.scan_7702 --path "contracts/contracts/samples/ModuleA7702.sol" --rag-top-k 5                             

## using grok search 
python -m ai.scan_7702 --path "contracts/contracts/samples/ModuleA7702.sol" --rag-top-k 5 --x-search --x-search-days 5 --x-search-limit 3


## output format
{  
    "label": safe | unsafe ,  
    "confidence": confidence score,  
    "reasons": 판단된 근거(꼭 필요는 없는데 추론성능 때문에)  
    "matched_patterns": 코드에서 reasons에 해당하는 부분  
    "analysis_source": "llm-brief" | "llm-detail" 쉬운거면 1stage(brief) 복잡한거면 2stage(detail)  
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
