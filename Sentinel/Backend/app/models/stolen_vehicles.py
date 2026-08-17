import uuid
from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class StolenVehicle(Base):
    __tablename__ = "stolen_vehicles"

    # report_id replaces the old plate_text primary key so one plate can
    # appear in multiple reports and reports can exist before a plate is known.
    report_id    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate        = Column(String, index=True, nullable=True)
    # vehicle_id links to the vehicles table if the stolen vehicle was already
    # sighted in the system. Nullable — reports are filed before sightings.
    vehicle_id   = Column(String, index=True, nullable=True)
    reported_at  = Column(DateTime(timezone=True))
    reported_by  = Column(String)
    vehicle_type = Column(String)
    colour       = Column(String)
    risk_level   = Column(String)
