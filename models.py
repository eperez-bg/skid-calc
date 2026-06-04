from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Orientation:
    """
    One possible way a carton can be turned.

    Example:
    If the original carton is Length x Width x Height,
    one orientation may be:

    length = original Length
    width = original Width
    height = original Height

    Another orientation may be:

    length = original Width
    width = original Height
    height = original Length
    """

    length: float
    width: float
    height: float
    label: str


@dataclass(frozen=True)
class Carton:
    """
    Represents one carton type from the CSV.

    This only stores the dimensions.
    Quantity is handled by creating multiple Item objects.
    """

    length: float
    width: float
    height: float

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height


@dataclass(frozen=True)
class Item:
    """
    Represents one physical carton that must be packed.

    If a CSV row has quantity 5, the script creates 5 Item objects
    for that one row.
    """

    csv_row_number: int
    copy_number: int
    carton: Carton


@dataclass
class PackingStrip:
    """
    A strip inside one layer.

    This used to be called Shelf, but PackingStrip is clearer.

    Top-down view:

        +----------------+----------------+
        | Strip 1        | Strip 2        |
        | carton         | carton         |
        | carton         |                |
        +----------------+----------------+

    x:
        Where the strip starts along the skid length.

    used_width:
        How much width has been used inside this strip.

    length:
        The longest carton length inside this strip.
    """

    x: float
    used_width: float
    length: float


@dataclass(frozen=True)
class Position:
    """
    A possible place to put a carton before actually placing it.

    strip_index:
        Which strip to place into.
        None means we are creating a new strip.

    is_new_strip:
        True if this position creates a new strip.

    x, y:
        The top-down location on the skid.

    new_layer_length:
        What the layer length would become after placing the carton.

    new_layer_width:
        What the layer width would become after placing the carton.
    """

    strip_index: Optional[int]
    is_new_strip: bool
    x: float
    y: float
    new_layer_length: float
    new_layer_width: float


@dataclass(frozen=True)
class Placement:
    """
    A carton after it has been placed.

    x, y, z:
        Position on the skid.

    length, width, height:
        The dimensions of the carton in its chosen orientation.

    orientation:
        Human-readable explanation of how the carton was turned.

    layer_number:
        Which stacked layer the carton is on.
    """

    csv_row_number: int
    copy_number: int
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    orientation: str
    layer_number: int


@dataclass
class Layer:
    """
    One vertical layer of cartons on the skid.

    Example:
    Layer 1 starts at z = 0
    Layer 2 may start at z = 10
    Layer 3 may start at z = 20
    """

    z: float
    height: float
    strips: list[PackingStrip] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)

    @property
    def length(self) -> float:
        """
        Current used length of this layer.
        """
        if not self.strips:
            return 0.0

        return max(strip.x + strip.length for strip in self.strips)

    @property
    def width_used(self) -> float:
        """
        Current used width of this layer.
        """
        if not self.strips:
            return 0.0

        return max(strip.used_width for strip in self.strips)


@dataclass
class SkidPlan:
    """
    The final skid plan.

    This represents one skid that fits all cartons together.
    """

    skid_length: float
    skid_width: float
    skid_height: float
    layers: list[Layer]

    @property
    def area(self) -> float:
        """
        Footprint area of the skid.
        """
        return self.skid_length * self.skid_width

    @property
    def volume(self) -> float:
        """
        Loaded skid volume.
        """
        return self.skid_length * self.skid_width * self.skid_height

    @property
    def placements(self) -> list[Placement]:
        """
        All carton placements from all layers.
        """
        all_placements = []

        for layer in self.layers:
            all_placements.extend(layer.placements)

        return all_placements