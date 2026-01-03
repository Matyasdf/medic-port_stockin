"""
RELOCATION MODULE - MedicPort Warehouse System

SIMPLIFIED LOGIC:
- When item needs relocation (obstruction) -> Update VSU in ml_robot_updated.json
- Keep relocation_history.json for audit/reference only
- Single source of truth: ml_robot_updated.json

Features:
- Find empty VSU or create new VSU on same/nearby shelf
- Update item's VSU directly in main inventory
- Track relocation history for auditing
"""

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime
from pathlib import Path
import json

# File paths
RELOCATION_HISTORY_FILE = Path("data/relocation_history.json")


class RelocationRecord(BaseModel):
    """Record of a single relocation event (for audit only)"""
    item_id: int
    product_id: int
    barcode: str
    original_vsu_id: int
    original_vsu_code: str
    new_vsu_id: int
    new_vsu_code: str
    original_coordinates: Dict[str, float]
    new_coordinates: Dict[str, float]
    relocated_at: datetime
    reason: str = "obstruction_removal"

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class RelocationHistory(BaseModel):
    """Relocation history state (reference only)"""
    relocations: Dict[int, RelocationRecord] = {}  # relocation_id -> RelocationRecord
    metadata: Dict = {
        "total_relocations": 0,
        "last_updated": None,
        "version": "1.0"
    }

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# Global state
relocation_history_state: Optional[RelocationHistory] = None
next_relocation_id = 1


def load_relocation_history() -> RelocationHistory:
    """Load relocation history from JSON file"""
    global relocation_history_state, next_relocation_id

    if not RELOCATION_HISTORY_FILE.exists():
        print(f"[RELOCATION] Creating new history file at {RELOCATION_HISTORY_FILE}")
        relocation_history_state = RelocationHistory()
        save_relocation_history(relocation_history_state)
        return relocation_history_state

    try:
        with open(RELOCATION_HISTORY_FILE, 'r') as f:
            data = json.load(f)

        # Convert relocations dict
        relocations_dict = {}
        for reloc_id_str, reloc_data in data.get("relocations", {}).items():
            reloc_id = int(reloc_id_str)
            reloc_data["relocated_at"] = datetime.fromisoformat(reloc_data["relocated_at"])
            relocations_dict[reloc_id] = RelocationRecord(**reloc_data)

        # Update next ID
        if relocations_dict:
            next_relocation_id = max(relocations_dict.keys()) + 1

        relocation_history_state = RelocationHistory(
            relocations=relocations_dict,
            metadata=data.get("metadata", {
                "total_relocations": len(relocations_dict),
                "last_updated": None,
                "version": "1.0"
            })
        )

        print(f"[RELOCATION] Loaded {len(relocations_dict)} relocation records")
        return relocation_history_state

    except Exception as e:
        print(f"[RELOCATION] Error loading history: {e}")
        relocation_history_state = RelocationHistory()
        save_relocation_history(relocation_history_state)
        return relocation_history_state


def save_relocation_history(history: RelocationHistory):
    """Save relocation history to JSON file"""
    try:
        # Update metadata
        history.metadata["total_relocations"] = len(history.relocations)
        history.metadata["last_updated"] = datetime.now().isoformat()

        # Convert to dict for JSON serialization
        data = {
            "relocations": {
                str(reloc_id): reloc.dict()
                for reloc_id, reloc in history.relocations.items()
            },
            "metadata": history.metadata
        }

        # Ensure directory exists
        RELOCATION_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Write to file
        with open(RELOCATION_HISTORY_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)

        print(f"[RELOCATION] Saved {len(history.relocations)} records to {RELOCATION_HISTORY_FILE}")

    except Exception as e:
        print(f"[RELOCATION] Error saving history: {e}")
        raise


def _item_fits_vsu(item, vsu) -> bool:
    """Check if item dimensions fit within VSU dimensions"""
    item_width = item.metadata.dimensions.width
    item_height = item.metadata.dimensions.height
    item_depth = item.metadata.dimensions.depth

    vsu_width = vsu.dimensions.width
    vsu_height = vsu.dimensions.height
    vsu_depth = vsu.dimensions.depth

    # Item must fit in all dimensions
    fits = (item_width <= vsu_width and
            item_height <= vsu_height and
            item_depth <= vsu_depth)

    return fits


def _calculate_vsu_fit_score(item, vsu, is_same_shelf: bool, is_same_rack: bool) -> float:
    """
    Calculate how well an item fits in a VSU (lower = better fit)

    Scoring priorities:
    1. Same shelf preferred (bonus -1000)
    2. Same rack preferred (bonus -500)
    3. Minimal wasted space (volume difference)
    """
    item_volume = (item.metadata.dimensions.width *
                   item.metadata.dimensions.height *
                   item.metadata.dimensions.depth)
    vsu_volume = vsu.dimensions.volume if hasattr(vsu.dimensions, 'volume') else (
        vsu.dimensions.width * vsu.dimensions.height * vsu.dimensions.depth
    )

    # Wasted space (lower is better)
    wasted_space = vsu_volume - item_volume

    # Location bonuses (negative = preferred)
    location_bonus = 0
    if is_same_shelf:
        location_bonus = -1000
    elif is_same_rack:
        location_bonus = -500

    return wasted_space + location_bonus


def find_empty_vsu_for_relocate(
    item_to_relocate,
    shelf_id: int,
    items: Dict,
    virtual_units: Dict,
    shelves: Dict
) -> Optional[tuple]:
    """
    Find OPTIMAL empty VSU for relocated item (NO new VSU creation)

    Logic:
    1. Collect all empty VSUs that fit the item dimensions
    2. Score each by: same shelf > same rack > other racks, then minimal wasted space
    3. Return the best fit

    Returns: (vsu_id, vsu_code, vsu_object) or None
    """
    item_dims = item_to_relocate.metadata.dimensions
    print(f"[RELOCATE] Finding optimal empty VSU for item {item_to_relocate.id}")
    print(f"  Item dimensions: {item_dims.width}W x {item_dims.height}H x {item_dims.depth}D")

    current_shelf = shelves.get(shelf_id)
    current_rack_id = current_shelf.rack_id if current_shelf else None

    # Collect all candidate VSUs with their scores
    candidates = []  # List of (score, vsu_id, vsu_code, vsu, location_desc)

    # Check all shelves
    for sid, shelf in shelves.items():
        is_same_shelf = (sid == shelf_id)
        is_same_rack = (shelf.rack_id == current_rack_id)

        for vsu_id in shelf.virtual_units:
            vsu = virtual_units.get(vsu_id)
            if not vsu:
                continue

            # Check if VSU is empty
            if vsu.items and len(vsu.items) > 0:
                continue

            # Check if item fits
            if not _item_fits_vsu(item_to_relocate, vsu):
                continue

            # Calculate fit score
            score = _calculate_vsu_fit_score(item_to_relocate, vsu, is_same_shelf, is_same_rack)

            # Build location description
            if is_same_shelf:
                loc_desc = "same shelf"
            elif is_same_rack:
                loc_desc = f"shelf {shelf.name} (same rack)"
            else:
                loc_desc = f"shelf {shelf.name} (rack {shelf.rack_id})"

            candidates.append((score, vsu_id, vsu.code, vsu, loc_desc))

    if not candidates:
        print(f"  No suitable empty VSUs found (none fit item dimensions)")
        return None

    # Sort by score (lower = better)
    candidates.sort(key=lambda x: x[0])

    # Return the best candidate
    best_score, best_vsu_id, best_vsu_code, best_vsu, best_loc = candidates[0]

    print(f"  Found {len(candidates)} suitable empty VSUs")
    print(f"  Best fit: VSU {best_vsu_code} on {best_loc}")
    print(f"    VSU dims: {best_vsu.dimensions.width}W x {best_vsu.dimensions.height}H x {best_vsu.dimensions.depth}D")

    return (best_vsu_id, best_vsu_code, best_vsu)


def relocate_item(
    item_id: int,
    items: Dict,
    virtual_units: Dict,
    shelves: Dict,
    reason: str = "obstruction_removal"
) -> Dict:
    """
    Relocate item to new VSU

    SIMPLIFIED LOGIC:
    - Find/create new VSU
    - Update item's VSU in main inventory (items dict)
    - Add relocation record to history file (for audit only)
    - Return new location details

    Args:
        item_id: Item to relocate
        items: Main inventory items dict
        virtual_units: VSU dict
        shelves: Shelf dict
        reason: Reason for relocation

    Returns:
        Dict with new location details
    """
    global relocation_history_state, next_relocation_id

    if relocation_history_state is None:
        relocation_history_state = load_relocation_history()

    # Get item details
    if item_id not in items:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    item = items[item_id]
    original_vsu_id = item.vsu_id

    if original_vsu_id is None:
        raise HTTPException(status_code=400, detail=f"Item {item_id} not in any VSU")

    original_vsu = virtual_units[original_vsu_id]
    shelf_id = original_vsu.shelf_id

    print(f"\n[RELOCATE] Relocating item {item_id} (product {item.metadata.product_id})")
    print(f"  From: VSU {original_vsu.code} (shelf {shelf_id})")

    # Find empty VSU for relocation (no new VSU creation)
    result = find_empty_vsu_for_relocate(
        item_to_relocate=item,
        shelf_id=shelf_id,
        items=items,
        virtual_units=virtual_units,
        shelves=shelves
    )

    if result is None:
        raise HTTPException(
            status_code=503,
            detail=f"No empty VSU available on shelf {shelf_id} or nearby shelves for relocation"
        )

    new_vsu_id, new_vsu_code, new_vsu = result

    # Remove item from original VSU
    if item_id in original_vsu.items:
        original_vsu.items.remove(item_id)
    if not original_vsu.items:
        original_vsu.occupied = False

    # Add item to new VSU
    if not new_vsu.items:
        new_vsu.items = []
    new_vsu.items.append(item_id)
    new_vsu.occupied = True

    # Calculate stock_index (position in new VSU)
    stock_index = len(new_vsu.items) - 1

    # Update item's VSU reference
    item.vsu_id = new_vsu_id
    item.stock_index = stock_index

    print(f"  To: VSU {new_vsu_code} (stock_index {stock_index})")

    # Add to relocation history (for audit only)
    relocation_record = RelocationRecord(
        item_id=item_id,
        product_id=item.metadata.product_id,
        barcode=item.metadata.barcode,
        original_vsu_id=original_vsu_id,
        original_vsu_code=original_vsu.code,
        new_vsu_id=new_vsu_id,
        new_vsu_code=new_vsu_code,
        original_coordinates={
            "x": original_vsu.position.x,
            "y": original_vsu.position.y,
            "z": original_vsu.position.z
        },
        new_coordinates={
            "x": new_vsu.position.x,
            "y": new_vsu.position.y,
            "z": new_vsu.position.z
        },
        relocated_at=datetime.now(),
        reason=reason
    )

    relocation_history_state.relocations[next_relocation_id] = relocation_record
    next_relocation_id += 1

    # Save history
    save_relocation_history(relocation_history_state)

    print(f"  Relocation complete - item updated in main inventory")

    return {
        "item_id": item_id,
        "original_vsu_id": original_vsu_id,
        "original_vsu_code": original_vsu.code,
        "new_vsu_id": new_vsu_id,
        "new_vsu_code": new_vsu_code,
        "new_coordinates": {
            "x": new_vsu.position.x,
            "y": new_vsu.position.y,
            "z": new_vsu.position.z
        },
        "stock_index": stock_index,
        "relocated_at": relocation_record.relocated_at.isoformat(),
        "reason": reason
    }


def get_relocation_history() -> List[Dict]:
    """Get all relocation records"""
    global relocation_history_state

    if relocation_history_state is None:
        relocation_history_state = load_relocation_history()

    records = []
    for reloc_id, reloc in relocation_history_state.relocations.items():
        records.append({
            "relocation_id": reloc_id,
            "item_id": reloc.item_id,
            "product_id": reloc.product_id,
            "barcode": reloc.barcode,
            "original_vsu_id": reloc.original_vsu_id,
            "original_vsu_code": reloc.original_vsu_code,
            "new_vsu_id": reloc.new_vsu_id,
            "new_vsu_code": reloc.new_vsu_code,
            "original_coordinates": reloc.original_coordinates,
            "new_coordinates": reloc.new_coordinates,
            "relocated_at": reloc.relocated_at.isoformat(),
            "reason": reloc.reason
        })

    return sorted(records, key=lambda x: x["relocated_at"], reverse=True)


def get_relocation_stats() -> Dict:
    """Get relocation statistics"""
    global relocation_history_state

    if relocation_history_state is None:
        relocation_history_state = load_relocation_history()

    return {
        "total_relocations": len(relocation_history_state.relocations),
        "last_updated": relocation_history_state.metadata.get("last_updated")
    }
