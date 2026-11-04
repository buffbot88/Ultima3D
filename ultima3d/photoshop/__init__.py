"""Photoshop CS3 backend: offline-tolerant CS3 bridge + headless texture synthesis.

Portable CS3 is a GUI-only exe with no scriptable CLI, and its COM server is
unregistered, so live control runs through a socket bridge loaded inside the
open Photoshop; the client sends one JSON line and waits for the reply, so
failures surface instead of being assumed. Texture synthesis is pure stdlib and
works with Photoshop never open; the JSX presets under photoshop/scripts/ run
inside CS3 for the finish/export steps, and are driven through that bridge.
"""
