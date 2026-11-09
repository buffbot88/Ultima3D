# Building the Rust host on Windows (no VS / no MinGW-gcc)

Toolchain: `stable-x86_64-pc-windows-gnu` + LLVM tooling, all via winget:

```
winget install -e --id LLVM.LLVM
winget install -e --id MartinStorsjo.LLVM-MinGW.MSVCRT
rustup target add x86_64-pc-windows-gnullvm   # optional; gnu is the default
```

The gnu target's linker is `x86_64-w64-mingw32-clang` from the llvm-mingw
MSVCRT package (see `rust/.cargo/config.toml`). Two env requirements:

```
PATH=<llvm-mingw-msvcrt>/bin:<cargo bin>:<LLVM bin>:...
LIBRARY_PATH=<repo>/rust/.libalias
```

`rust/.libalias` holds `libgcc.a` / `libgcc_eh.a` aliases onto compiler-rt
builtins + libunwind — rustc's `-lgcc`/`-lgcc_eh` have no MinGW-gcc to come
from, and `-L` rustflags do not reach the linker reliably, so LIBRARY_PATH
is the mechanism. MSVCRT (not UCRT) is deliberate: this machine has no
`api-ms-win-crt-*` forwarders, but `msvcrt.dll` is present.

Then:

```
cd rust && cargo build --workspace
# -> target/debug/ultima3d-server.exe + target/debug/ultima3d-tray.exe
cargo test --workspace   # unit tests + 65-tool parity test (no Blender needed)
```

On machines with Visual Studio (including GitHub's `windows-latest` runners)
the default MSVC target just works — the llvm-mingw linker above is only for
linking the `gnu` target here.

Run the server (or point the MCP client) at the repo root: it resolves
`blender/worker.py` and `output/` from the current working directory.
`ULTIMA3D_WORKER` overrides the worker script path, `ULTIMA3D_SERVER`
tells `ultima3d-tray` which server binary to own (default: the
`ultima3d-server.exe` sitting next to the tray binary).

The Python host is the zero-build alternative: `python -m ultima3d.server` speaks the
same stdio MCP surface, and `python -m ultima3d.server --http 127.0.0.1:8791` serves it
to shells and Freebuff skills. Both hosts resolve `blender/worker.py` and `output/`
from the working directory and honour `ULTIMA3D_WORKER`, `ULTIMA3D_OUT` and
`ULTIMA3D_CALL_TIMEOUT`; only the Python host enforces the 40-minute
`ULTIMA3D_SESSION_MINUTES` budget.
