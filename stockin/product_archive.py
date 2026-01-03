"""
Product Archive Module

Manages archived dispensed products. When items are dispensed and removed from
ml_robot_updated.json, they are saved here with full details for tracking.

Features:
- Stores complete item details including VSU, coordinates, shelf info
- Tracks dispensed_at timestamp
- Maintains archive history
"""

from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path
import json

ARCHIVE_FILE = Path("data/product_archive.json")


class ArchivedItem(BaseModel):
    """Archived product item"""
    item_id: int
    product_id: int
    barcode: str
    batch: str
    expiration: str
    dimensions: Dict[str, float]
    weight: float

    # Location info (where it was before dispensing)
    vsu_id: int
    vsu_code: str
    shelf_id: int
    shelf_name: str
    coordinates: Dict[str, float]
    stock_index: int

    # Archive metadata
    dispensed_at: datetime
    task_id: str

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProductArchive(BaseModel):
    """Product archive state"""
    items: Dict[int, ArchivedItem] = {}
    metadata: Dict = {
        "total_items": 0,
        "last_updated": None,
        "version": "1.0"
    }

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# Global state
product_archive_state: Optional[ProductArchive] = None


def load_product_archive() -> ProductArchive:
    """Load product archive from JSON file"""
    global product_archive_state

    if not ARCHIVE_FILE.exists():
        print(f"[ARCHIVE] Creating new product archive at {ARCHIVE_FILE}")
        product_archive_state = ProductArchive()
        save_product_archive(product_archive_state)
        return product_archive_state

    try:
        with open(ARCHIVE_FILE, 'r') as f:
            data = json.load(f)

        # Convert items dict keys to integers
        items_dict = {}
        for item_id_str, item_data in data.get("items", {}).items():
            item_id = int(item_id_str)
            items_dict[item_id] = ArchivedItem(**item_data)

        product_archive_state = ProductArchive(
            items=items_dict,
            metadata=data.get("metadata", {
                "total_items": len(items_dict),
                "last_updated": None,
                "version": "1.0"
            })
        )

        print(f"[ARCHIVE] Loaded {len(items_dict)} archived items")
        return product_archive_state

    except Exception as e:
        print(f"[ARCHIVE] Error loading archive: {e}")
        print(f"[ARCHIVE] Creating fresh archive")
        product_archive_state = ProductArchive()
        save_product_archive(product_archive_state)
        return product_archive_state


def save_product_archive(archive: ProductArchive):
    """Save product archive to JSON file"""
    try:
        # Update metadata
        archive.metadata["total_items"] = len(archive.items)
        archive.metadata["last_updated"] = datetime.now().isoformat()

        # Convert to dict for JSON serialization
        data = {
            "items": {
                str(item_id): item.dict()
                for item_id, item in archive.items.items()
            },
            "metadata": archive.metadata
        }

        # Ensure directory exists
        ARCHIVE_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Write to file
        with open(ARCHIVE_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)

        print(f"[ARCHIVE] Saved {len(archive.items)} items to {ARCHIVE_FILE}")

    except Exception as e:
        print(f"[ARCHIVE] Error saving archive: {e}")
        raise


def archive_dispensed_item(
    item_id: int,
    item: 'Item',
    vsu: 'VirtualStorageUnit',
    shelf_id: int,
    shelf_name: str,
    task_id: str
):
    """
    Archive a dispensed item

    Args:
        item_id: Item ID
        item: Item object from main inventory
        vsu: VSU object where item was stored
        shelf_id: Shelf ID
        shelf_name: Shelf name
        task_id: Dispense task ID
    """
    global product_archive_state

    if product_archive_state is None:
        product_archive_state = load_product_archive()

    # Convert expiration to string if it's a datetime
    expiration_str = item.metadata.expiration
    if isinstance(expiration_str, datetime):
        expiration_str = expiration_str.isoformat()

    # Create archived item
    archived_item = ArchivedItem(
        item_id=item_id,
        product_id=item.metadata.product_id,
        barcode=item.metadata.barcode,
        batch=item.metadata.batch,
        expiration=expiration_str,
        dimensions={
            "width": item.metadata.dimensions.width,
            "height": item.metadata.dimensions.height,
            "depth": item.metadata.dimensions.depth
        },
        weight=item.metadata.dimensions.weight if hasattr(item.metadata.dimensions, 'weight') else 0.0,
        vsu_id=vsu.id,
        vsu_code=vsu.code,
        shelf_id=shelf_id,
        shelf_name=shelf_name,
        coordinates={
            "x": vsu.position.x,
            "y": vsu.position.y,
            "z": vsu.position.z
        },
        stock_index=item.stock_index,
        dispensed_at=datetime.now(),
        task_id=task_id
    )

    # Add to archive
    product_archive_state.items[item_id] = archived_item

    # Save archive
    save_product_archive(product_archive_state)

    print(f"[ARCHIVE] Archived item {item_id} (Product {item.metadata.product_id}, Barcode {item.metadata.barcode})")
    print(f"  From: {vsu.code} on {shelf_name}")
    print(f"  Task: {task_id}")


def get_archive_stats() -> Dict:
    """Get archive statistics"""
    global product_archive_state

    if product_archive_state is None:
        product_archive_state = load_product_archive()

    # Group by product
    product_counts = {}
    for item in product_archive_state.items.values():
        product_id = item.product_id
        if product_id not in product_counts:
            product_counts[product_id] = {
                "product_id": product_id,
                "barcode": item.barcode,
                "count": 0
            }
        product_counts[product_id]["count"] += 1

    return {
        "total_items": len(product_archive_state.items),
        "total_products": len(product_counts),
        "products": list(product_counts.values()),
        "last_updated": product_archive_state.metadata.get("last_updated")
    }
