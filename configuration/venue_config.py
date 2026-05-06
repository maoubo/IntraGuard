"""
Venue Configuration (Layer Cake)
"""

from dataclasses import dataclass


@dataclass
class VenueConfig:
    """Venue configuration parameters"""
    target_width: float = 240.0        # Target text width
    target_x1: float = 50.0            # Left column x-coordinate
    target_x2: float = 315.0           # Right column x-coordinate (same as x1 for single-column)
    target_font_size: float = 10.0     # Inserted text font size
    width_tolerance: float = 0.05      # Width tolerance
    position_tolerance: float = 0.05   # Position tolerance
    insert_count: int = 1              # Max insertions per position
    use_random_count: bool = True      # Use random count
    insertion_probability: float = 1.0 # Probability of actually inserting (0.0-1.0)


# Supported venues
SUPPORTED_VENUES = [
    "CCS", "SP", "USENIX_Security", "NDSS", 
    "NeurIPS", "ICLR", "ICML",
    "Nature", "Nature_Biotechnology",
    "Advanced_Materials",
    "Psychological_Review",
    "ITS"
]

# Venue configurations (adjust parameters as needed)
VENUE_CONFIGS = {
    "CCS": VenueConfig(
        target_width=220.0, target_x1=55.0, target_x2=320.0, target_font_size=7.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "SP": VenueConfig(
        target_width=230.0, target_x1=55.0, target_x2=320.0, target_font_size=9.96,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "USENIX_Security": VenueConfig(
        target_width=230.0, target_x1=55.0, target_x2=320.0, target_font_size=9.96,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "NDSS": VenueConfig(
        target_width=240.0, target_x1=50.0, target_x2=320.0, target_font_size=8.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "NeurIPS": VenueConfig(
        target_width=370.0, target_x1=105.0, target_x2=105.0, target_font_size=10.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "ICLR": VenueConfig(
        target_width=370.0, target_x1=105.0, target_x2=105.0, target_font_size=10.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "ICML": VenueConfig(
        target_width=230.0, target_x1=55.0, target_x2=305.0, target_font_size=10.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "Nature": VenueConfig(
        target_width=250.0, target_x1=40.0, target_x2=310.0, target_font_size=8.25,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "Nature_Biotechnology": VenueConfig(
        target_width=240.0, target_x1=40.0, target_x2=310.0, target_font_size=8.25,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "Advanced_Materials": VenueConfig(
        target_width=235.0, target_x1=45.0, target_x2=305.0, target_font_size=8.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "Psychological_Review": VenueConfig(
        target_width=220.0, target_x1=50.0, target_x2=305.0, target_font_size=9.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
    "ITS": VenueConfig(
        target_width=245.0, target_x1=50.0, target_x2=315.0, target_font_size=9.0,
        width_tolerance=0.05, position_tolerance=0.05, insert_count=3, use_random_count=True,
    ),
}


def get_venue_config(venue: str) -> VenueConfig:
    if venue not in VENUE_CONFIGS:
        raise ValueError(f"Unsupported venue: '{venue}'. Supported: {SUPPORTED_VENUES}")
    return VENUE_CONFIGS[venue]
