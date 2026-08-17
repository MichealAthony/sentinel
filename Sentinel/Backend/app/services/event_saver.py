# event_saver.py

import os
import json
import cv2
from datetime import datetime


class EventSaver:
    def __init__(self, base_dir="events"):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def save(self, vehicle_id, packet, plate_text, image_crop):
        timestamp = datetime.utcfromtimestamp(packet["timestamp"]).isoformat()
        date_folder = timestamp.split("T")[0]

        folder = os.path.join(self.base_dir, date_folder)
        os.makedirs(folder, exist_ok=True)

        image_path = os.path.join(folder, f"{vehicle_id}.jpg")
        json_path = os.path.join(folder, f"{vehicle_id}.json")

        cv2.imwrite(image_path, image_crop)

        data = {
            "vehicle_id": vehicle_id,
            "plate_text": plate_text,
            "camera_id": packet["camera_id"],
            "timestamp": timestamp,
            "image_path": image_path,
        }

        with open(json_path, "w") as f:
            json.dump(data, f, indent=2)