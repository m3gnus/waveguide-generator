"""Stable Stage 2 fields of a CAD return ingestion result."""

from server.cadlink.wgreturn import source_physical_name


def geometry_result(record: dict) -> dict:
    decision = record["domain_decision"]
    verification = record["symmetry_verification"]
    return {
        "accepted": True,
        "source_ids": [source["id"] for source in record["sources"]],
        "physical_names": {
            source_id: source_physical_name(
                int(tag),
                source_id,
                record["tag_map"][str(tag)]["instance_id"],
                record["tag_map"][str(tag)]["role"],
            )
            for source_id, tag in record["source_tags"].items()
        },
        "domain_kind": record["domain_interpretation"]["manifest_domain"],
        "decision_refusal": decision["refusal"],
        "solver_domain_planes": decision["solver_domain"]["planes"],
        "wg_cut_planes": decision["wg_cut_planes"],
        "reflected_axes": decision["reflected_axes"],
        "cut_planes": verification["cut_planes"],
        "capped_planes": verification["capped_planes"],
        "detected_planes": verification["detected_planes"],
        "declared_cut_planes": verification["declared_cut_planes"],
    }
