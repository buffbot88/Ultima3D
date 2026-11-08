Starting the brain visualizer
From File Explorer: open brain/ and double-click start-visualizer.bat.
From cmd / PowerShell: brain\start-visualizer.bat
It finds node on PATH (or in .runtime/node-*/ if one exists), starts the HTTP API plus the
dashboard, and opens the browser.

Default URL: 
Default port: 8791 - deliberately different from the 8765 used by the brain this template
came from, so the two can run side by side.
Override the port: set BRAIN_PORT=9999 before running, or
node brain/cli.js serve --port 9999.

Leave the window open to keep it running; close it or press Ctrl+C to stop.
The dashboard shows: entry counts per project scope, tags, types, search, add/view/edit/delete,
and "Export project brain" which writes one scope to a single portable markdown file.
API reference: 
If the port is already in use, pick another:

set BRAIN_PORT=8792
brain\start-visualizer.bat
