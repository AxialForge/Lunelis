"""
Recognition: what's in a photo, worked out on this PC.

Everything that looks at pixels to understand them goes through one
interface, so the model can change - or move to a GPU server later -
without touching the library:

    class Recognizer:
        model_id: str                  # stored with each embedding
        dim: int
        def embed_images(self, images: list[PIL.Image]) -> np.ndarray   # (n, dim), unit length
        def embed_texts(self, texts: list[str]) -> np.ndarray           # (n, dim), same space

clip.py is the one in use (CLIP ViT-B/32, ONNX, opt-in download); scenes.py
turns its embeddings into scene-tag suggestions.
"""
