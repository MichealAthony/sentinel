from pydantic import BaseModel
from datetime import datetime

class DetectionCreate(BaseModel):
    plate_text: str
    vehicle_color: str
    vehicle_make: str
    camera_id: str
    timestamp: datetime
    snapshot_reference: str