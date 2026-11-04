"""Intrinsic Custom profile parameters shared by editing and conversion."""
from server.design.schema import DesignConfig, FreeformConfig
from server.preview.translate import design_to_mesher_config


def _root(design: DesignConfig) -> FreeformConfig:
    if not isinstance(design.root, FreeformConfig):
        from hornlab_mesher.errors import UserRefusal
        raise UserRefusal("Family", "Profile editing needs a Custom profile design.", "Choose Custom profile under Family.")
    return design.root


def _params(design: DesignConfig) -> dict:
    root = _root(design)
    # Editing coordinates and handle vectors are intrinsic design mm. The
    # build translator scales them; this API deliberately queries at scale 1.
    unscaled = DesignConfig(root=root.model_copy(update={"scale": None}))
    return design_to_mesher_config(unscaled)["profile"]

