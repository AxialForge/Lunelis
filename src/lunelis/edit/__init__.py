"""
Non-destructive editing.

An edit is a small stack of instructions kept in the catalog (`edits`) and
mirrored into the photo's XMP sidecar (lunelis:EditStack) - the original
file is never written. The pieces:

- stack.py     the parameters, a filter + manual adjustments + geometry, and
               how a stack is stored as text;
- pipeline.py  applies a stack to pixels (numpy, float32);
- render.py    decodes a photo for editing (RAWs through rawpy), and keeps
               the rendered proxy (2560 px JPEG) and the grid thumbnail of
               the edited look in the cache - never re-running the RAW
               pipeline just to browse;
- store.py     reading/saving stacks and user filters in the catalog;
- presets.py   the built-in filters.
"""
