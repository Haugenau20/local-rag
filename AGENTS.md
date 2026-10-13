# Local RAG development

- GPU execution requires explicit user approval for the particular action. Permission to implement, test, or debug this project is not permission to run GPU work.
- Until that approval is given, do not run models, download model weights, start model servers or GPU containers, start Docker/WSL services that might load models, or invoke GPU diagnostic/probing tools. Halt and ask if a required step would use the graphics card.
- Milestone 1 uses the deterministic CPU test embedder only. Do not silently replace it with inference, a network service, or a cloud fallback.
- Keep tests independent of CUDA, ML frameworks, network access, and user documents. Use synthetic temporary folders and the standard-library test runner.
- Preserve exact citation offsets and source boundaries. Do not delete indexed documents on read or source-enumeration failures.
- Write a failing behavior test before changing implementation. Keep model-runtime lifecycle management in its own repository.
