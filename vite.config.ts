import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Honour PORT so a second instance can run alongside the first. The bridge
    // only accepts sockets from localhost:5173-5199, so stay inside that range
    // or set JARVIS_ALLOWED_ORIGINS to match.
    port: Number(process.env.PORT) || 5173,
    // Kokoro's WebGPU backend is the multi-threaded ONNX runtime build, which
    // wants SharedArrayBuffer (only available in a cross-origin-isolated
    // context, via COOP/COEP headers) for its 4-thread pool. Deliberately not
    // set: `require-corp` and `credentialless` were both tried, and both broke
    // the YouTube/Vimeo embeds in src/ui/Blades.tsx (confirmed — the embed
    // loads but its player never renders, even under credentialless, which
    // should have kept it working). Without cross-origin isolation Kokoro just
    // runs single-threaded instead of four — still well under a second per
    // sentence on the fp32/WebGPU pairing — so the embeds win rather than an
    // optimisation neither this laptop's speaker nor its user was starved for.
  },
  optimizeDeps: {
    // kokoro-js pulls in `phonemizer`, which carries espeak-ng as inline WASM.
    // Vite's dependency pre-bundler rewrites that initialisation and the
    // language table ends up empty — the symptom is
    // `Invalid language identifier: "en". Should be one of: .` at generate()
    // time, long after the model has loaded successfully. Serving these
    // untouched fixes it.
    exclude: ['kokoro-js', 'phonemizer', '@huggingface/transformers'],
  },
})
