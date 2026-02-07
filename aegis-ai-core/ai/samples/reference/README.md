# Impl Profiles

Each implementation keeps neutral file/contract naming for model-evaluation purposes.
Use the documents below for semantic labels and scenario intent.

## Safety Classification (Common Criterion)
Registry verdicts in this repo are assigned with a single shared criterion:
- `SAFE`: No unauthorized third party can make the wallet perform arbitrary external calls, drain value via fee griefing, or enter a persistent DoS state through this implementation (including via hidden side effects).
- `UNSAFE`: Any realistic path exists for unauthorized third-party arbitrary execution, persistent DoS, or collision-prone auth/state behavior.

- `ModuleA7702.md`
- `ModuleB7702.md`
- `ModuleC7702.md`
- `ModuleD7702.md`
- `ModuleE7702.md`
- `ModuleF7702.md`
- `ModuleG7702.md`

Additional detector-stress samples live in `ai/samples/test_samples/`:
- `ModuleH7702.md`
- `ModuleI7702.md`
