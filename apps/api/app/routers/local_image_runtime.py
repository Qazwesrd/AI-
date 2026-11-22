# Keep ComfyUI in the same persistent data root selected by the NSIS installer.
# APP_DATA_DIR is supplied by Electron from the sibling <install-dir>-Data folder.
def _data_root() -> Path:
    configured = os.environ.get("APP_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else (Path.home() / "AIComicStudio" / "data")


def _install_root() -> Path:
    return _data_root() / "engines" / "ComfyUI"


def _log_path() -> Path:
    return _data_root() / "logs" / "comfyui-runtime.log"
