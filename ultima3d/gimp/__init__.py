"""GIMP backend: offline-tolerant batch Script-Fu + headless texture synthesis.

GIMP ships a real CLI, so scripting goes through batch mode instead of a
socket bridge: `gimp -i -b '<script-fu>' -b '(gimp-quit 0)'` runs a script in a
throwaway GIMP and exits with a status code. Batch runs to completion — a
failure arrives as text on stderr, a hang is killed past the deadline — so no
call can wedge the editor the way the old CS3 bridge did. Texture synthesis is
pure stdlib and works with GIMP never installed; the `.scm` presets under
`gimp/scripts/` run inside batch GIMP for the finish/export steps.
"""
