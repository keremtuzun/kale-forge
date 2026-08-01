# v18 reliability architecture

Status: implemented behind the current production winner; v15 remains selected until the
unchanged CUDA scorecard proves the full v18 path meets promotion gates.

The model now owns only the compact `kale-parametric-1.0` design decision layer. Geometry is
owned by a deterministic compiler. The boundary is:

1. constrained JSON sampling using the strict intent schema;
2. fail-closed schema and semantic validation (no synthesized empty fallback);
3. deterministic expansion into assemblies and separately named parts;
4. CAD validation for unique IDs/names, closed feature vocabulary, finite positive dimensions,
   catalog tube stock, pitch math and bounded non-zero repetition;
5. editable manifest generation for every part, mate and measurement;
6. validated FeatureScript generation, never OBJ/mesh flattening.

The Transformers provider requires `lm-format-enforcer` for structured requests and refuses to
sample without it. vLLM uses guided JSON and llama.cpp uses its JSON response grammar.

Future model improvement should use `build_preference_pairs.py` to create corrected
chosen/rejected examples from observed training-split failures. The builder rejects held-out,
evaluation and test rows, duplicate prompts, malformed JSON and identical pairs. Repeated broad
SFT is not the default continuation strategy.
