"""Make the pip-installed CUDA DLLs findable before ctranslate2 loads.

Since Python 3.8, PATH is ignored when resolving DLL dependencies of extension
modules on Windows. The nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels drop their
DLLs into site-packages/nvidia/*/bin, which nothing looks at by default, so
ctranslate2 fails to find cuBLAS and cuDNN and silently reports zero CUDA
devices. os.add_dll_directory() is the only fix.

enable_cuda_dlls() must run before `import faster_whisper` anywhere.
"""

import os
import site
import sys
from pathlib import Path

_done = False
_added_dirs = []


def _candidate_site_dirs():
    """Every directory that might hold an installed `nvidia` package."""
    dirs = []
    try:
        dirs.extend(site.getsitepackages())
    except AttributeError:
        # Some embedded/virtual setups omit getsitepackages().
        pass
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        dirs.append(user_site)
    else:
        dirs.extend(user_site)
    # The venv's own site-packages, derived from this module's location.
    for entry in sys.path:
        if entry.endswith("site-packages"):
            dirs.append(entry)
    seen, unique = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def enable_cuda_dlls(verbose=False):
    """Register nvidia/*/bin directories with the Windows DLL loader.

    Idempotent, and a no-op off Windows (there the loader uses rpath/LD_LIBRARY_PATH).
    Returns the list of directories that were added.
    """
    global _done
    if _done or sys.platform != "win32":
        if verbose:
            for d in _added_dirs:
                print(f"  [cuda] registered {d}")
        return list(_added_dirs)

    added = []
    for site_dir in _candidate_site_dirs():
        nvidia_root = Path(site_dir) / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for bin_dir in sorted(nvidia_root.glob("*/bin")):
            if not bin_dir.is_dir():
                continue
            if not any(bin_dir.glob("*.dll")):
                continue
            try:
                os.add_dll_directory(str(bin_dir))
            except OSError:
                continue
            # Some loaders still consult PATH; harmless to also set it.
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
            if verbose:
                print(f"  [cuda] registered {bin_dir}")

    _done = True
    _added_dirs.extend(added)
    if verbose and not added:
        print("  [cuda] no nvidia/*/bin directories found - will fall back to CPU")
    return added


def cuda_device_count():
    """Number of CUDA devices ctranslate2 can actually use. 0 means CPU only."""
    enable_cuda_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0
