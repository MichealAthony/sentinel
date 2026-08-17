"""
app/services/vehicle_worker.py
"""

import numpy as np
import cv2
from ultralytics import YOLO

from app.services.embedding_extractor import EmbeddingExtractor
from app.services.identity_engine import IdentityEngine
from app.services.event_saver import EventSaver
from app.services.plate_ocr import PlateOCR
from app.services.colour_mapper import map_colour, map_colour_distribution
from app.services.vehicle_index import build_vehicle_index
from app.services.index_publisher import IndexPublisher
from app.services.stream_buffer import StreamBuffer


class VehicleWorker:
    VEHICLE_TYPE_MAP = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

    def __init__(self, lpr_model_path, vehicle_model_path,
                 publisher: IndexPublisher, stream_buffer: StreamBuffer,
                 clip_service=None,
                 query_registry=None):
        self.lpr_model = YOLO(lpr_model_path)
        self.vehicle_model = YOLO(vehicle_model_path)
        self.embedding_extractor = EmbeddingExtractor()
        self.registry = IdentityEngine()
        self.event_saver = EventSaver()
        self.ocr = PlateOCR()
        self.publisher = publisher
        self.stream_buffer = stream_buffer
        self.vehicle_classes = {2, 3, 5, 7}
        self.clip_service = clip_service      # CLIPEmbeddingService | None
        self.query_registry = query_registry  # ActiveQueryRegistry | None

    def process(self, packet):
        frame = packet["frame"]
        timestamp = packet["timestamp"]
        camera_id = packet["camera_id"]
        height, width = frame.shape[:2]

        vehicle_results = self.vehicle_model(frame, imgsz=960)
        plate_results = self.lpr_model(frame, imgsz=960)

        vboxes = vehicle_results[0].boxes
        pboxes = plate_results[0].boxes

        if vboxes is None:
            self.stream_buffer.write(camera_id, frame)
            return frame

        vehicle_boxes = vboxes.xyxy.cpu().numpy()
        vehicle_classes = vboxes.cls.cpu().numpy()
        plate_boxes = pboxes.xyxy.cpu().numpy() if pboxes is not None else []

        for vbox, cls_id in zip(vehicle_boxes, vehicle_classes):
            if int(cls_id) not in self.vehicle_classes:
                continue

            x1, y1, x2, y2 = map(int, vbox[:4])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)

            vehicle_crop = frame[y1:y2, x1:x2]
            if vehicle_crop.size == 0:
                continue

            embedding = self.embedding_extractor.extract(vehicle_crop)

            # ── CLIP visual feature extension (inert until clip_service is injected) ──
            # This block is completely skipped when clip_service is None. The current
            # instantiation in routes.py passes None. To activate, pass
            # clip_service=clip_service and query_registry=active_query_registry in routes.py.
            clip_embedding = None
            if self.clip_service is not None and self.clip_service.ready():
                clip_embedding = self.clip_service.encode_image(vehicle_crop)
            # ─────────────────────────────────────────────────────────────────────────

            plate_text, conf = self._associate_plate(frame, plate_boxes, vbox, width, height)

            vehicle_id, is_new = self.registry.match_or_create(
                embedding, timestamp, camera_id, plate_text, conf
            )

            if clip_embedding is not None and self.query_registry is not None:
                self.query_registry.check_crop(
                    clip_embedding, vehicle_id, camera_id, timestamp
                )

            best_plate = self.registry.get_plate(vehicle_id)
            plate_confirmed = self.registry.is_plate_confirmed(vehicle_id)
            vehicle_type = self.VEHICLE_TYPE_MAP.get(int(cls_id), "unknown")

            raw_distribution = self.get_colour_distribution(vehicle_crop)
            mapped_distribution = map_colour_distribution(raw_distribution)
            dominant = max(raw_distribution, key=lambda x: x["weight"])
            dominant_mapped = map_colour(tuple(dominant["rgb"]))

            index = build_vehicle_index(
                vehicle_id=vehicle_id,
                timestamp=timestamp,
                camera_id=camera_id,
                bbox=[x1, y1, x2, y2],
                plate=best_plate,
                plate_confidence=conf,
                plate_confirmed=plate_confirmed,
                vehicle_type=vehicle_type,
                colour_label=dominant_mapped["label"],
                colour_rgb=dominant_mapped["rgb"],
                colour_distribution=mapped_distribution,
                reid_embedding=embedding,
                clip_embedding=clip_embedding,
                image_path=None,
                additional_features={}
            )

            self.publisher.publish(index)

            color_draw = (0, 255, 0) if best_plate else (0, 165, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color_draw, 2)
            label = (
                f"{vehicle_type} | {dominant_mapped['label']}"
                + (f" | {best_plate}" if best_plate else "")
            )
            cv2.putText(frame, label, (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color_draw, 2)

            if self.registry.should_save(vehicle_id, timestamp):
                self.event_saver.save(vehicle_id, packet, best_plate, vehicle_crop)

        self.stream_buffer.write(camera_id, frame)
        return frame

    def _associate_plate(self, frame, plate_boxes, vehicle_box, width, height):
        vx1, vy1, vx2, vy2 = vehicle_box[:4]
        best_plate_box = None
        best_area = 0

        for pbox in plate_boxes:
            px1, py1, px2, py2 = pbox[:4]
            cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
            if vx1 <= cx <= vx2 and vy1 <= cy <= vy2:
                area = (px2 - px1) * (py2 - py1)
                if area > best_area:
                    best_area = area
                    best_plate_box = pbox

        if best_plate_box is None:
            return None, 0.0

        px1, py1, px2, py2 = map(int, best_plate_box[:4])
        px1, py1 = max(0, px1), max(0, py1)
        px2, py2 = min(width, px2), min(height, py2)
        plate_crop = frame[py1:py2, px1:px2]
        if plate_crop.size == 0:
            return None, 0.0

        return self.ocr.read(plate_crop)

    @staticmethod
    def get_colour_distribution(crop, k=3) -> list[dict]:
        if crop is None or crop.size == 0:
            return [{"rgb": [128, 128, 128], "weight": 1.0}]

        small = cv2.resize(crop, (50, 50))
        small_rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        pixels = small_rgb.reshape(-1, 3).astype(np.float32)

        _, labels, centers = cv2.kmeans(
            pixels, k, None,
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0),
            3, cv2.KMEANS_PP_CENTERS
        )

        counts = np.bincount(labels.flatten(), minlength=k)
        total = counts.sum()

        return [
            {"rgb": [int(c) for c in centers[i]], "weight": round(float(counts[i]) / total, 3)}
            for i in range(k)
        ]
