"""Photoshop CS3 backend: offline-tolerant CS3 bridge + headless texture synthesis.

Portable CS3 is a GUI-only exe with no scriptable CLI, and its COM server is
unregistered, so every Photoshop-driving call is fire-and-forget and reports
what it did without waiting on the GUI. Texture synthesis is pure stdlib and
works with Photoshop never open; the JSX presets under scripts/ run inside
CS3 for the finish/export steps.
"""
