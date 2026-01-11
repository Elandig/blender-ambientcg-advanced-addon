#!/usr/bin/env python3
import shutil
from pathlib import Path

BLENDER_VERSION = "5.0"

def get_blender_addon_path():
    home = Path.home()
    blender_config = home / ".config" / "blender"

    version_path = blender_config / BLENDER_VERSION
    if not version_path.exists():
        versions = sorted([d.name for d in blender_config.iterdir() if d.is_dir()])
        if versions:
            version_path = blender_config / versions[-1]
        else:
            raise RuntimeError("No Blender installation found")

    extensions_path = version_path / "extensions" / "user_default"
    scripts_path = version_path / "scripts" / "addons"

    return extensions_path if extensions_path.parent.exists() else scripts_path

def main():
    project_root = Path(__file__).parent
    addon_path = get_blender_addon_path() / "ambientcg_material_importer"

    print(f"Installing to: {addon_path}")

    if addon_path.exists():
        shutil.rmtree(addon_path)

    addon_path.mkdir(parents=True, exist_ok=True)

    for file_name in ["__init__.py", "blender_manifest.toml"]:
        source = project_root / file_name
        if source.exists():
            print(f"Copying {file_name}")
            shutil.copy2(source, addon_path / file_name)

    print("Installation complete")

if __name__ == "__main__":
    main()
