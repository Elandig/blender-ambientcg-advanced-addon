bl_info = {
    "name": "AmbientCG Material Importer Advanced",
    "author": "Nino Filiu & Ruslan Norberg",
    "version": (1, 5, 0),
    "blender": (4, 2, 0),
    "location": "Shader Editor > Sidebar > AmbientCG",
    "description": "One-click material creation from AmbientCG with advanced features",
    "category": "Material",
}

import bpy
import os
import urllib.request
import urllib.parse
import zipfile
import shutil
import json
from concurrent.futures import ThreadPoolExecutor
from bpy.props import StringProperty, EnumProperty, BoolProperty, FloatProperty, IntProperty, CollectionProperty
from pathlib import Path


class AmbientCGPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    cache_dir: StringProperty(
        name="Cache Folder",
        subtype="DIR_PATH",
        default=str(Path.home() / ".cache" / "ambientcg"),
        description="Directory where AmbientCG texture PNGs will be stored",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "cache_dir")


def get_cache_dir():
    prefs: AmbientCGPreferences = bpy.context.preferences.addons[__name__].preferences
    dir_path = Path(prefs.cache_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


preview_collections = {}
PREVIEW_AVAILABLE = False
_updating_page = False
MATERIALS_PER_PAGE = 40


def get_texture_path(path, use_relative):
    path_str = str(path)
    return bpy.path.relpath(path_str) if use_relative and bpy.data.filepath else path_str


class MaterialItem(bpy.types.PropertyGroup):
    asset_id: StringProperty(name="Asset ID")
    display_name: StringProperty(name="Display Name")
    preview_url: StringProperty(name="Preview URL")


def download_preview(url, asset_id):
    try:
        cache_dir = get_cache_dir() / "previews"
        cache_dir.mkdir(exist_ok=True)
        preview_path = cache_dir / f"{asset_id}.jpg"

        if not preview_path.exists():
            urllib.request.urlretrieve(url, preview_path)
        return True
    except Exception:
        return False


def get_preview_icon(asset_id, pcoll):
    try:
        cache_dir = get_cache_dir() / "previews"
        preview_path = cache_dir / f"{asset_id}.jpg"

        if not preview_path.exists():
            return 0

        if asset_id in pcoll:
            thumb = pcoll[asset_id]
        else:
            thumb = pcoll.load(asset_id, str(preview_path), 'IMAGE')

        return thumb.icon_id
    except Exception:
        return 0


def restore_unused_texture(file_path):
    file_path = Path(file_path)
    unused_path = Path(str(file_path) + ".unused")

    if unused_path.exists():
        unused_path.rename(file_path)
        return True
    return False


class MATERIAL_OT_fetch_and_create(bpy.types.Operator):
    bl_idname = "material.fetch_and_create"
    bl_label = "Fetch and Create Material"
    bl_options = {"REGISTER", "UNDO"}

    def process_texture(self, source_path, output_folder, resize_multiplier, use_relative_paths, material_name):
        restore_unused_texture(source_path)

        texture_type = source_path.stem.split('_')[-1]
        output_name = f"{material_name}_{texture_type}_{resize_multiplier:.2f}.png"
        output_path = output_folder / output_name

        restore_unused_texture(output_path)

        if output_path.exists():
            return get_texture_path(output_path, use_relative_paths)

        temp_image = bpy.data.images.load(str(source_path))
        new_width = int(temp_image.size[0] * resize_multiplier)
        new_height = int(temp_image.size[1] * resize_multiplier)

        if abs(resize_multiplier - 1.0) > 0.001:
            temp_image.scale(new_width, new_height)

        temp_image.filepath_raw = str(output_path)
        temp_image.file_format = 'PNG'
        temp_image.save()
        bpy.data.images.remove(temp_image)

        return get_texture_path(output_path, use_relative_paths)

    def execute(self, context):
        material_name = context.scene.ambientcg_material_name
        resolution = context.scene.ambientcg_resolution
        use_custom_folder = context.scene.ambientcg_use_custom_folder
        custom_folder = context.scene.ambientcg_custom_folder
        use_relative_paths = context.scene.ambientcg_use_relative_paths
        resize_multiplier = context.scene.ambientcg_resize_multiplier
        interpolation = context.scene.ambientcg_interpolation

        url = f"https://ambientcg.com/get?file={material_name}_{resolution}-PNG.zip"

        cache_dir = get_cache_dir()
        extract_path = cache_dir / f"{material_name}_{resolution}"

        if use_custom_folder and custom_folder:
            output_folder = Path(custom_folder)
            output_folder.mkdir(parents=True, exist_ok=True)
        else:
            output_folder = extract_path

        if not extract_path.exists():
            # Download and extract the zip file
            zip_path = cache_dir / f"{material_name}_{resolution}.zip"

            # Create a custom opener with a User-Agent
            opener = urllib.request.build_opener()
            opener.addheaders = [
                (
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                )
            ]
            urllib.request.install_opener(opener)

            # Download the file
            try:
                urllib.request.urlretrieve(url, zip_path)
            except Exception as e:
                self.report({"ERROR"}, f"Failed to download file: {str(e)}")
                return {"CANCELLED"}

            # Extract the zip file
            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(extract_path)
                zip_path.unlink()  # Remove the zip file after extraction
            except Exception as e:
                self.report({"ERROR"}, f"Failed to extract zip file: {str(e)}")
                return {"CANCELLED"}
        else:
            self.report(
                {"INFO"}, f"Using cached material: {material_name}_{resolution}"
            )

        # Create a new material
        material = bpy.data.materials.new(name=material_name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links

        # Clear default nodes
        nodes.clear()

        # Create shader nodes
        material_output = nodes.new(type="ShaderNodeOutputMaterial")
        material_output.location = (300, 0)

        principled = nodes.new(type="ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        links.new(principled.outputs["BSDF"], material_output.inputs["Surface"])

        # Creating a Texture Coordinate Node and a Mapping Node
        tex_coord = nodes.new(type="ShaderNodeTexCoord")
        tex_coord.location = (-1000, 0)

        mapping = nodes.new(type="ShaderNodeMapping")
        mapping.location = (-800, 0)
        links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

        for file in os.listdir(extract_path):
            source_path = extract_path / file
            if file.endswith("_Color.png") and context.scene.ambientcg_use_color:
                texture_path = self.process_texture(
                    source_path, output_folder, resize_multiplier, use_relative_paths, material_name
                )
                color_tex = nodes.new(type="ShaderNodeTexImage")
                color_tex.location = (-600, 600)
                color_tex.image = bpy.data.images.load(texture_path)
                color_tex.image.colorspace_settings.name = "sRGB"
                color_tex.interpolation = interpolation
                links.new(color_tex.outputs["Color"], principled.inputs["Base Color"])
                links.new(mapping.outputs["Vector"], color_tex.inputs["Vector"])
            elif file.endswith("_Metalness.png") and context.scene.ambientcg_use_metalness:
                texture_path = self.process_texture(
                    source_path, output_folder, resize_multiplier, use_relative_paths, material_name
                )
                metalness_tex = nodes.new(type="ShaderNodeTexImage")
                metalness_tex.location = (-600, 300)
                metalness_tex.image = bpy.data.images.load(texture_path)
                metalness_tex.image.colorspace_settings.name = "Non-Color"
                metalness_tex.interpolation = interpolation
                links.new(metalness_tex.outputs["Color"], principled.inputs["Metallic"])
                links.new(mapping.outputs["Vector"], metalness_tex.inputs["Vector"])
            elif file.endswith("_Roughness.png") and context.scene.ambientcg_use_roughness:
                texture_path = self.process_texture(
                    source_path, output_folder, resize_multiplier, use_relative_paths, material_name
                )
                roughness_tex = nodes.new(type="ShaderNodeTexImage")
                roughness_tex.location = (-600, 0)
                roughness_tex.image = bpy.data.images.load(texture_path)
                roughness_tex.image.colorspace_settings.name = "Non-Color"
                roughness_tex.interpolation = interpolation
                links.new(
                    roughness_tex.outputs["Color"], principled.inputs["Roughness"]
                )
                links.new(mapping.outputs["Vector"], roughness_tex.inputs["Vector"])
            elif file.endswith("_NormalGL.png") and context.scene.ambientcg_use_normal:
                texture_path = self.process_texture(
                    source_path, output_folder, resize_multiplier, use_relative_paths, material_name
                )
                normal_tex = nodes.new(type="ShaderNodeTexImage")
                normal_tex.location = (-600, -300)
                normal_tex.image = bpy.data.images.load(texture_path)
                normal_tex.image.colorspace_settings.name = "Non-Color"
                normal_tex.interpolation = interpolation
                normal_map = nodes.new(type="ShaderNodeNormalMap")
                normal_map.location = (-300, -300)
                links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
                links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
                links.new(mapping.outputs["Vector"], normal_tex.inputs["Vector"])
            elif file.endswith("_Displacement.png") and context.scene.ambientcg_use_displacement:
                texture_path = self.process_texture(
                    source_path, output_folder, resize_multiplier, use_relative_paths, material_name
                )
                displacement_tex = nodes.new(type="ShaderNodeTexImage")
                displacement_tex.location = (-600, -600)
                displacement_tex.image = bpy.data.images.load(texture_path)
                displacement_tex.image.colorspace_settings.name = "Non-Color"
                displacement_tex.interpolation = interpolation
                displacement = nodes.new(type="ShaderNodeDisplacement")
                displacement.location = (-300, -600)
                links.new(
                    displacement_tex.outputs["Color"], displacement.inputs["Height"]
                )
                links.new(
                    displacement.outputs["Displacement"],
                    material_output.inputs["Displacement"],
                )
                links.new(mapping.outputs["Vector"], displacement_tex.inputs["Vector"])

        if not context.scene.ambientcg_use_metalness:
            principled.inputs["Metallic"].default_value = context.scene.ambientcg_default_metallic
        if not context.scene.ambientcg_use_roughness:
            principled.inputs["Roughness"].default_value = context.scene.ambientcg_default_roughness

        if context.scene.ambientcg_use_backface_culling:
            material.use_backface_culling = True

        self.report(
            {"INFO"}, f"Material '{material_name}' has been created successfully."
        )
        return {"FINISHED"}


def material_previews_enum(self, context):
    global PREVIEW_AVAILABLE

    if context is None:
        return [("NONE", "No materials", "", 0, 0)]

    materials = context.scene.ambientcg_browser_materials
    pcoll = preview_collections.get("materials")

    if pcoll is None or len(materials) == 0:
        return [("NONE", "No materials", "", 0, 0)]

    enum_items = []
    for i, item in enumerate(materials):
        icon_id = 0
        if PREVIEW_AVAILABLE and item.preview_url:
            icon_id = get_preview_icon(item.asset_id, pcoll)

        enum_items.append((
            item.asset_id,
            item.display_name,
            "",
            icon_id,
            i
        ))

    return enum_items


def on_page_change(self, context):
    global _updating_page
    if _updating_page:
        return

    _updating_page = True
    try:
        page = context.scene.ambientcg_goto_page
        total_pages = context.scene.ambientcg_total_pages
        if page < 1:
            page = 1
        elif total_pages > 0 and page > total_pages:
            page = total_pages
        context.scene.ambientcg_browser_offset = (page - 1) * MATERIALS_PER_PAGE
        bpy.ops.material.fetch_browser_materials()
    finally:
        _updating_page = False




class MATERIAL_OT_fetch_browser_materials(bpy.types.Operator):
    bl_idname = "material.fetch_browser_materials"
    bl_label = "Fetch Materials"

    reset_to_first_page: BoolProperty(default=False)

    def execute(self, context):
        global _updating_page

        if self.reset_to_first_page:
            context.scene.ambientcg_browser_offset = 0
            _updating_page = True
            try:
                context.scene.ambientcg_goto_page = 1
            finally:
                _updating_page = False

        search_query = context.scene.ambientcg_search_query
        offset = context.scene.ambientcg_browser_offset

        url = f"https://ambientcg.com/api/v2/full_json?type=Material&limit={MATERIALS_PER_PAGE}&offset={offset}"
        if search_query:
            url += f"&q={urllib.parse.quote(search_query)}"

        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [("User-Agent", "Mozilla/5.0")]
            urllib.request.install_opener(opener)

            response = urllib.request.urlopen(url)
            data = json.loads(response.read())

            context.scene.ambientcg_browser_total = data.get("numberOfResults", 0)
            context.scene.ambientcg_browser_materials.clear()

            preview_downloads = []
            for asset in data.get("foundAssets", []):
                item = context.scene.ambientcg_browser_materials.add()
                item.asset_id = asset["assetId"]
                item.display_name = asset.get("displayName", asset["assetId"])

                preview_image = asset.get("previewImage", {})
                preview_url = preview_image.get("128-JPG-242424", "")
                item.preview_url = preview_url

                if preview_url:
                    preview_downloads.append((preview_url, asset["assetId"]))

            if preview_downloads:
                with ThreadPoolExecutor(max_workers=20) as executor:
                    list(executor.map(lambda p: download_preview(*p), preview_downloads))

            total = context.scene.ambientcg_browser_total
            current_page = (offset // MATERIALS_PER_PAGE) + 1
            total_pages = max(1, (total + MATERIALS_PER_PAGE - 1) // MATERIALS_PER_PAGE)
            context.scene.ambientcg_current_page = current_page
            context.scene.ambientcg_total_pages = total_pages

            _updating_page = True
            try:
                context.scene.ambientcg_goto_page = current_page
            finally:
                _updating_page = False

            self.report({"INFO"}, f"Found {total} materials")
            return {"FINISHED"}

        except Exception as e:
            self.report({"ERROR"}, f"Failed to fetch materials: {str(e)}")
            return {"CANCELLED"}


class MATERIAL_OT_mark_unused_textures(bpy.types.Operator):
    bl_idname = "material.mark_unused_textures"
    bl_label = "Mark Unused Textures"
    bl_description = "Rename unused textures in custom folder with .unused suffix to help identify files for cleanup"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not context.scene.ambientcg_use_custom_folder or not context.scene.ambientcg_custom_folder:
            self.report({"WARNING"}, "Custom folder not configured")
            return {"CANCELLED"}

        custom_folder = Path(context.scene.ambientcg_custom_folder)
        if not custom_folder.exists():
            self.report({"WARNING"}, "Custom folder does not exist")
            return {"CANCELLED"}

        loaded_images = set()
        for img in bpy.data.images:
            if img.filepath:
                filepath = bpy.path.abspath(img.filepath)
                loaded_images.add(Path(filepath).resolve())

        marked_count = 0
        for png_file in custom_folder.rglob("*.png"):
            if png_file.name.endswith(".unused"):
                continue

            if png_file.resolve() not in loaded_images:
                try:
                    unused_path = Path(str(png_file) + ".unused")
                    png_file.rename(unused_path)
                    marked_count += 1
                except Exception as e:
                    print(f"Failed to mark {png_file}: {e}")

        self.report({"INFO"}, f"Marked {marked_count} unused textures")
        return {"FINISHED"}


def on_material_preview_update(self, context):
    if context.window_manager.ambientcg_material_preview:
        context.scene.ambientcg_material_name = context.window_manager.ambientcg_material_preview


class MATERIAL_PT_ambientcg_fetcher(bpy.types.Panel):
    bl_label = "AmbientCG Fetcher"
    bl_idname = "MATERIAL_PT_ambientcg_fetcher"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "AmbientCG Fetcher"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "ambientcg_material_name", text="Material ID")
        layout.operator("material.fetch_and_create", text="Fetch and Create Material")

        layout.separator()

        box = layout.box()
        box.label(text="Material Browser:", icon='VIEWZOOM')

        row = box.row()
        row.prop(scene, "ambientcg_search_query", text="", icon='VIEWZOOM')
        row.operator("material.fetch_browser_materials", text="Search", icon='PLAY').reset_to_first_page = True

        wm = context.window_manager
        box.template_icon_view(wm, "ambientcg_material_preview", show_labels=True, scale=8.0)

        if scene.ambientcg_browser_total > 0:
            pag_box = box.box()
            row = pag_box.row(align=True)
            row.alignment = 'CENTER'
            row.prop(scene, "ambientcg_goto_page", text="Page")
            row.label(text="of")
            row.label(text=str(scene.ambientcg_total_pages))
            pag_box.label(text=f"Total: {scene.ambientcg_browser_total} materials")

        layout.separator()

        layout.prop(scene, "ambientcg_resolution", text="Resolution")

        box = layout.box()
        box.label(text="Texture Types:")
        col = box.column(align=True)
        col.prop(scene, "ambientcg_use_color")
        col.prop(scene, "ambientcg_use_metalness")
        if not scene.ambientcg_use_metalness:
            col.prop(scene, "ambientcg_default_metallic")
        col.prop(scene, "ambientcg_use_roughness")
        if not scene.ambientcg_use_roughness:
            col.prop(scene, "ambientcg_default_roughness")
        col.prop(scene, "ambientcg_use_normal")
        col.prop(scene, "ambientcg_use_displacement")

        box = layout.box()
        box.prop(scene, "ambientcg_use_backface_culling")

        box = layout.box()
        box.prop(scene, "ambientcg_use_custom_folder")
        if scene.ambientcg_use_custom_folder:
            box.prop(scene, "ambientcg_custom_folder")
            box.prop(scene, "ambientcg_use_relative_paths")
            box.separator()
            box.operator("material.mark_unused_textures", icon='TRASH')

        box = layout.box()
        box.label(text="Texture Resizing:")
        box.prop(scene, "ambientcg_resize_multiplier")

        layout.prop(scene, "ambientcg_interpolation")


classes = (
    AmbientCGPreferences,
    MaterialItem,
    MATERIAL_OT_fetch_and_create,
    MATERIAL_OT_fetch_browser_materials,
    MATERIAL_OT_mark_unused_textures,
    MATERIAL_PT_ambientcg_fetcher,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ambientcg_material_name = StringProperty(
        name="Material Name",
        description="Name of the AmbientCG material (e.g., Rock035)",
        default="Rock035",
    )
    bpy.types.Scene.ambientcg_resolution = EnumProperty(
        name="Resolution",
        description="Resolution of the material textures",
        items=[
            ("1K", "1K", "1K resolution"),
            ("2K", "2K", "2K resolution"),
            ("4K", "4K", "4K resolution"),
            ("8K", "8K", "8K resolution"),
        ],
        default="1K",
    )
    bpy.types.Scene.ambientcg_use_color = BoolProperty(name="Color", default=True)
    bpy.types.Scene.ambientcg_use_metalness = BoolProperty(name="Metallic", default=True)
    bpy.types.Scene.ambientcg_default_metallic = FloatProperty(
        name="Metallic Value",
        default=0.5,
        min=0.0,
        max=1.0,
    )
    bpy.types.Scene.ambientcg_use_roughness = BoolProperty(name="Roughness", default=True)
    bpy.types.Scene.ambientcg_default_roughness = FloatProperty(
        name="Roughness Value",
        default=0.5,
        min=0.0,
        max=1.0,
    )
    bpy.types.Scene.ambientcg_use_normal = BoolProperty(name="Normal", default=True)
    bpy.types.Scene.ambientcg_use_displacement = BoolProperty(name="Displacement", default=True)
    bpy.types.Scene.ambientcg_use_custom_folder = BoolProperty(
        name="Use Custom Save Folder",
        default=False,
    )
    bpy.types.Scene.ambientcg_custom_folder = StringProperty(
        name="Custom Folder",
        subtype="DIR_PATH",
        default="",
    )
    bpy.types.Scene.ambientcg_use_relative_paths = BoolProperty(
        name="Use Relative Paths",
        default=False,
    )
    bpy.types.Scene.ambientcg_resize_multiplier = FloatProperty(
        name="Resize Multiplier",
        default=1.0,
        min=0.01,
        max=4.0,
    )
    bpy.types.Scene.ambientcg_interpolation = EnumProperty(
        name="Interpolation",
        items=[
            ("Closest", "Closest", "No interpolation"),
            ("Linear", "Linear", "Linear interpolation"),
            ("Cubic", "Cubic", "Cubic interpolation"),
            ("Smart", "Smart", "Bicubic when magnifying"),
        ],
        default="Linear",
    )
    bpy.types.Scene.ambientcg_use_backface_culling = BoolProperty(
        name="Backface Culling",
        description="Enable backface culling for camera",
        default=False,
    )
    bpy.types.Scene.ambientcg_search_query = StringProperty(
        name="Search",
        description="Search materials",
        default="",
    )
    bpy.types.Scene.ambientcg_browser_materials = CollectionProperty(
        type=MaterialItem
    )
    bpy.types.Scene.ambientcg_browser_index = IntProperty(
        name="Browser Index",
        default=0,
    )
    bpy.types.Scene.ambientcg_browser_offset = IntProperty(
        name="Browser Offset",
        default=0,
    )
    bpy.types.Scene.ambientcg_browser_total = IntProperty(
        name="Browser Total",
        default=0,
    )
    bpy.types.Scene.ambientcg_current_page = IntProperty(
        name="Current Page",
        default=1,
    )
    bpy.types.Scene.ambientcg_total_pages = IntProperty(
        name="Total Pages",
        default=1,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.ambientcg_goto_page = IntProperty(
        name="Page",
        default=1,
        min=1,
        update=on_page_change,
    )

    # WindowManager property for material preview selection
    bpy.types.WindowManager.ambientcg_material_preview = EnumProperty(
        name="Material Preview",
        items=material_previews_enum,
        update=on_material_preview_update,
    )

    global PREVIEW_AVAILABLE
    try:
        from bpy.utils import previews as preview_module
        pcoll = bpy.utils.previews.new()
        preview_collections["materials"] = pcoll
        PREVIEW_AVAILABLE = True
    except (ImportError, AttributeError):
        PREVIEW_AVAILABLE = False


def unregister():
    global PREVIEW_AVAILABLE
    if PREVIEW_AVAILABLE:
        try:
            for pcoll in preview_collections.values():
                bpy.utils.previews.remove(pcoll)
        except:
            pass
    preview_collections.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.ambientcg_material_name
    del bpy.types.Scene.ambientcg_resolution
    del bpy.types.Scene.ambientcg_use_color
    del bpy.types.Scene.ambientcg_use_metalness
    del bpy.types.Scene.ambientcg_default_metallic
    del bpy.types.Scene.ambientcg_use_roughness
    del bpy.types.Scene.ambientcg_default_roughness
    del bpy.types.Scene.ambientcg_use_normal
    del bpy.types.Scene.ambientcg_use_displacement
    del bpy.types.Scene.ambientcg_use_custom_folder
    del bpy.types.Scene.ambientcg_custom_folder
    del bpy.types.Scene.ambientcg_use_relative_paths
    del bpy.types.Scene.ambientcg_resize_multiplier
    del bpy.types.Scene.ambientcg_interpolation
    del bpy.types.Scene.ambientcg_search_query
    del bpy.types.Scene.ambientcg_browser_materials
    del bpy.types.Scene.ambientcg_browser_index
    del bpy.types.Scene.ambientcg_browser_offset
    del bpy.types.Scene.ambientcg_browser_total
    del bpy.types.Scene.ambientcg_current_page
    del bpy.types.Scene.ambientcg_total_pages
    del bpy.types.Scene.ambientcg_goto_page
    del bpy.types.Scene.ambientcg_use_backface_culling
    del bpy.types.WindowManager.ambientcg_material_preview


if __name__ == "__main__":
    register()
