"""
app/services/clip_embedding_service.py

Import this file freely — the model is not loaded until encode_image or
encode_text is first called. The service is inert if the clip package is
not installed.
"""

import queue
import threading
import concurrent.futures

import numpy as np

CLIP_DEVICE = "cpu"
CLIP_MODEL_NAME = "ViT-B/32"


class CLIPEmbeddingService:
    """
    Singleton CLIP embedding service with lazy model loading.
    All encode operations are serialised through a single background daemon
    thread — PyTorch models are not thread-safe.
    """

    def __init__(self):
        self._model = None
        self._preprocess = None
        self._loaded = False
        self._load_failed = False
        self._queue: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True, name="CLIPWorker"
        )
        self._worker_thread.start()

    # =========================================================
    # INTERNAL — LAZY LOAD + WORKER LOOP
    # =========================================================

    def _load(self):
        """Load the CLIP model. Called once from the worker thread on first use."""
        if self._loaded or self._load_failed:
            return
        try:
            import clip  # type: ignore
            model, preprocess = clip.load(CLIP_MODEL_NAME, device=CLIP_DEVICE)
            model.eval()
            self._model = model
            self._preprocess = preprocess
            self._loaded = True
            print(f"[CLIPEmbeddingService] Model {CLIP_MODEL_NAME} loaded on {CLIP_DEVICE}")
        except ImportError:
            print("[CLIPEmbeddingService] clip package not installed — service disabled")
            self._load_failed = True
        except Exception as e:
            print(f"[CLIPEmbeddingService] Failed to load model: {e}")
            self._load_failed = True

    def _worker(self):
        """Single background thread. Processes all encode requests serially."""
        while True:
            work_type, payload, future = self._queue.get()
            try:
                if not self._loaded and not self._load_failed:
                    self._load()

                if self._load_failed:
                    future.set_result(np.zeros(512, dtype=np.float32))
                    continue

                import clip  # type: ignore
                import torch

                if work_type == "image":
                    import cv2
                    from PIL import Image
                    rgb = cv2.cvtColor(payload, cv2.COLOR_BGR2RGB)
                    pil_image = Image.fromarray(rgb)
                    tensor = self._preprocess(pil_image).unsqueeze(0).to(CLIP_DEVICE)
                    with torch.no_grad():
                        raw = self._model.encode_image(tensor)
                else:
                    tokens = clip.tokenize([payload]).to(CLIP_DEVICE)
                    with torch.no_grad():
                        raw = self._model.encode_text(tokens)

                vec = raw.cpu().numpy()[0].astype(np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm

                future.set_result(vec)

            except Exception as e:
                print(f"[CLIPEmbeddingService] Encode error ({work_type}): {e}")
                if not future.done():
                    future.set_result(np.zeros(512, dtype=np.float32))

    def _submit(self, work_type: str, payload) -> np.ndarray:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((work_type, payload, future))
        return future.result()

    # =========================================================
    # PUBLIC API
    # =========================================================

    def encode_image(self, bgr_crop: np.ndarray) -> np.ndarray:
        """Returns 512-dim L2-normalised float32 numpy array."""
        if self._load_failed:
            return np.zeros(512, dtype=np.float32)
        return self._submit("image", bgr_crop)

    def encode_text(self, description: str) -> np.ndarray:
        """Returns 512-dim L2-normalised float32 numpy array."""
        if self._load_failed:
            return np.zeros(512, dtype=np.float32)
        return self._submit("text", description)

    def ready(self) -> bool:
        """True only if the clip package is installed and the model has loaded."""
        return self._loaded and not self._load_failed

    def status(self) -> dict:
        return {
            "ready": self.ready(),
            "device": CLIP_DEVICE,
            "model": CLIP_MODEL_NAME,
            "queue_depth": self._queue.qsize(),
        }


# Module-level singleton — imported by VehicleWorker and the search pipeline.
# Never instantiated more than once.
clip_service = CLIPEmbeddingService()
