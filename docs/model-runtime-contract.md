# Model runtime integration contract

This is the proposed version 1 boundary between `local-rag` and `local-model-runtime`. The RAG repository owns this document; the runtime repository links to it. Changes affecting either service must update the contract and its integration tests together.

## Endpoints and configuration

Configure embedding, reranking, and chat base URLs independently. They may point to one backend or separate local services. Publish container ports on Windows loopback addresses, for example `127.0.0.1`; LAN access is a separate future requirement.

| Role | Operation | Required behavior |
|---|---|---|
| Embedding | `POST /v1/embeddings` | OpenAI-compatible input list and indexed vector response |
| Reranking | `POST /v1/rerank` | Query plus candidate passages; indexed relevance scores |
| Generation | `POST /v1/chat/completions` | OpenAI-compatible chat messages and completion response |
| Discovery | Configured backend discovery endpoint | Report supported model IDs and capabilities |
| Readiness | Configured backend health endpoint | Distinguish service availability from model readiness |

Use existing backend endpoints when they meet the contract. A thin adapter is justified only for a measured incompatibility. Backend health and discovery paths can differ; document their mappings in runtime configuration rather than inventing a wrapper service immediately.

The first answer milestone requires embedding and generation. Reranking is a later required capability for the evaluated retrieval pipeline. Start with non-streaming responses; streaming can extend the contract later.

## Embedding requests

```json
{
  "model": "configured-embedding-model",
  "input": ["First prepared passage", "Second prepared passage"]
}
```

```json
{
  "model": "configured-embedding-model",
  "data": [
    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
    {"index": 1, "embedding": [0.4, 0.5, 0.6]}
  ]
}
```

These three-component vectors illustrate the response shape, not a selected model's dimension. The client restores input order using `index` and rejects missing, duplicate, out-of-range, non-finite, or dimensionally inconsistent vectors. A successful HTTP status alone does not establish a valid response.

The RAG embedding profile defines query instructions, passage prefixes, tokenizer identity, limits, and normalization. The runtime performs inference on prepared inputs without silently adding another prefix or truncating over-limit input. Token limits apply to final prepared model input, not just raw chunk text. A model ID must remain bound to the same configured model revision; changing weights or quantization requires a profile update and evaluation.

## Reranking requests

```json
{
  "model": "configured-reranking-model",
  "query": "Where does the document explain backups?",
  "documents": ["Candidate passage one", "Candidate passage two"],
  "top_n": 2
}
```

The version 1 adapter returns a `results` list with `index` and `relevance_score` for each returned candidate. Higher scores sort first. Raw backend formats may differ; the runtime mapping specifies which shape its endpoint returns. The RAG client validates and normalizes that documented shape before joining scores to candidate IDs. Scores are not treated as probabilities or compared across different reranker profiles.

## Chat requests and citations

Send a configured model ID, `messages`, and `stream: false`. The response must contain `choices[0].message.content`. Generation parameters are backend-specific configuration with documented limits.

The RAG service supplies evidence and citation identifiers. The runtime is not given filesystem access or asked to locate documents. It returns generated text; the RAG service validates citation references and presents the corresponding source passages. The model runtime does not run document tools.

## Failures and resource limits

Distinguish invalid input, unknown model, service unavailable, model loading, request queue saturation, context overflow, out-of-memory, and timeout. Preserve backend HTTP status and a bounded error description; do not include full prompts in logs. Backend-specific errors are translated into these client categories.

Readiness probes and explicit warm-up runs use backend-supported operations. Configure separate readiness and inference deadlines from measured cold-start behavior. A server can be alive while its model is unloaded. Failure to become ready is surfaced to the user rather than silently changing model or calling a cloud provider.

Embedding and reranking requests may use bounded retries for transient failures. Do not automatically replay partially delivered generation responses. A failed inference request must not activate an incomplete replacement index.

The runtime provides documented start, status, warm-up, model-unload, and stop operations. Process shutdown and model unloading are distinct. Begin with explicit indexing and interactive profiles; add scheduling only when actual contention requires it. Do not assume all models fit concurrently in the RTX 3090's VRAM.

## Contract acceptance

Use synthetic requests to verify ordering, dimensions, formatting, model identity, limits, readiness, and failure behavior. Run a complete document query with outbound networking blocked after provisioning. Confirm that no failure path attempts external inference. Keep user documents out of repository fixtures.
