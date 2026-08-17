from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from app.core.database import Base

class VehicleDetection(Base):
    __tablename__ = "vehicle_detections"

    id = Column(Integer, primary_key=True, index=True)
    plate_text = Column(String, index=True)
    vehicle_color = Column(String)
    vehicle_make = Column(String)
    camera_id = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    snapshot_reference = Column(String)