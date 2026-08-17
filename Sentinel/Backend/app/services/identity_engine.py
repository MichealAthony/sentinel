import uuid
import numpy as np
import faiss
import threading


PLATE_CONFIRMATION_MIN_VOTES = 3


class IdentityEngine:
    def __init__(self, embedding_dim=512, similarity_threshold=0.78):
        self.embedding_dim = embedding_dim
        self.similarity_threshold = similarity_threshold

        # FIX #2 & #4: Switched from IndexFlatIP to IndexIDMap wrapping IndexFlatIP.
        # Enables add_with_ids() and remove_ids() so:
        #   - FAISS vectors are updated when centroids are refined
        #   - Stale vectors are removed during cleanup (no unbounded growth)
        flat_index = faiss.IndexFlatIP(embedding_dim)
        self.index = faiss.IndexIDMap(flat_index)

        # vehicle_id -> faiss int64 ID
        self.vehicle_to_faiss_id: dict[str, int] = {}

        # Metadata store
        self.vehicles = {}

        self.next_faiss_id = 0

        self.lock = threading.Lock()

    # =========================================================
    # PUBLIC: MATCH OR CREATE
    # =========================================================
    def match_or_create(self, embedding, timestamp, camera_id,
                        plate_text=None, plate_conf=0.0):
        if embedding is None:
            return None, False

        embedding = np.array([embedding]).astype("float32")

        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        with self.lock:
            if self.index.ntotal > 0:
                D, I = self.index.search(embedding, k=1)

                similarity = float(D[0][0])
                faiss_id = int(I[0][0])

                if similarity >= self.similarity_threshold and faiss_id != -1:
                    vehicle_id = self._faiss_id_to_vehicle(faiss_id)

                    if vehicle_id:
                        self._update_vehicle(
                            vehicle_id,
                            embedding[0],
                            timestamp,
                            camera_id,
                            plate_text,
                            plate_conf
                        )
                        return vehicle_id, False

            vehicle_id = self._create_vehicle(
                embedding[0],
                timestamp,
                camera_id,
                plate_text,
                plate_conf
            )
            return vehicle_id, True

    # =========================================================
    # PRIVATE: FAISS ID REVERSE LOOKUP
    # =========================================================
    def _faiss_id_to_vehicle(self, faiss_id: int):
        for vid, fid in self.vehicle_to_faiss_id.items():
            if fid == faiss_id:
                return vid
        return None

    # =========================================================
    # PRIVATE: CREATE VEHICLE
    # =========================================================
    def _create_vehicle(self, embedding, timestamp,
                        camera_id, plate_text, plate_conf):
        vehicle_id = str(uuid.uuid4())

        faiss_id = self.next_faiss_id
        self.next_faiss_id += 1

        # FIX #2: Use add_with_ids so vectors can be updated/removed later
        self.index.add_with_ids(
            np.array([embedding]).astype("float32"),
            np.array([faiss_id], dtype=np.int64)
        )
        self.vehicle_to_faiss_id[vehicle_id] = faiss_id

        plate_votes = {}
        best_plate = None

        if plate_text and plate_conf >= 0.85:
            plate_votes[plate_text] = 1
            best_plate = plate_text

        self.vehicles[vehicle_id] = {
            "embedding_centroid": embedding,
            "seen_count": 1,
            "plate_votes": plate_votes,
            "best_plate": best_plate,
            "last_seen": timestamp,
            "last_saved": None,
            "camera_history": [{
                "camera_id": camera_id,
                "timestamp": timestamp
            }]
        }

        return vehicle_id

    # =========================================================
    # PRIVATE: UPDATE VEHICLE
    # =========================================================
    def _update_vehicle(self, vehicle_id, embedding,
                        timestamp, camera_id,
                        plate_text, plate_conf):
        data = self.vehicles[vehicle_id]
        count = data["seen_count"]

        new_centroid = (
            data["embedding_centroid"] * count + embedding
        ) / (count + 1)

        norm = np.linalg.norm(new_centroid)
        if norm > 0:
            new_centroid = new_centroid / norm

        data["embedding_centroid"] = new_centroid
        data["seen_count"] += 1
        data["last_seen"] = timestamp

        data["camera_history"].append({
            "camera_id": camera_id,
            "timestamp": timestamp
        })

        # FIX #2: Sync refined centroid back into FAISS.
        # Previously the index held the original embedding forever,
        # causing match quality to degrade as the centroid drifted.
        faiss_id = self.vehicle_to_faiss_id[vehicle_id]
        self.index.remove_ids(np.array([faiss_id], dtype=np.int64))
        self.index.add_with_ids(
            np.array([new_centroid]).astype("float32"),
            np.array([faiss_id], dtype=np.int64)
        )

        # Plate stabilization via voting
        if plate_text and plate_conf >= 0.85:
            votes = data["plate_votes"]
            votes[plate_text] = votes.get(plate_text, 0) + 1
            data["best_plate"] = max(votes, key=votes.get)

    # =========================================================
    # PUBLIC: GET BEST PLATE
    # =========================================================
    def get_plate(self, vehicle_id):
        vehicle = self.vehicles.get(vehicle_id)
        if not vehicle:
            return None
        return vehicle.get("best_plate")

    # =========================================================
    # PUBLIC: IS PLATE CONFIRMED
    # =========================================================
    def is_plate_confirmed(self, vehicle_id: str,
                           min_votes: int = PLATE_CONFIRMATION_MIN_VOTES) -> bool:
        """
        Returns True once a plate has received enough consistent votes.
        Prevents early OCR misreads from being stored as confirmed plates.
        A plate is unconfirmed until seen at least min_votes times with
        high confidence (>= 0.85 as enforced by PlateOCR).
        """
        vehicle = self.vehicles.get(vehicle_id)
        if not vehicle:
            return False
        best_plate = vehicle.get("best_plate")
        if not best_plate:
            return False
        votes = vehicle.get("plate_votes", {})
        return votes.get(best_plate, 0) >= min_votes

    # =========================================================
    # PUBLIC: SHOULD SAVE
    # =========================================================
    def should_save(self, vehicle_id, timestamp, cooldown=5):
        vehicle = self.vehicles.get(vehicle_id)
        if not vehicle:
            return True

        last_saved = vehicle.get("last_saved")

        if last_saved is None:
            vehicle["last_saved"] = timestamp
            return True

        if (timestamp - last_saved) > cooldown:
            vehicle["last_saved"] = timestamp
            return True

        return False

    # =========================================================
    # PUBLIC: CLEANUP IDLE VEHICLES
    # =========================================================
    def cleanup(self, current_time, max_idle_seconds=600):
        with self.lock:
            to_delete = []

            for vid, data in self.vehicles.items():
                if (current_time - data["last_seen"]) > max_idle_seconds:
                    to_delete.append(vid)

            for vid in to_delete:
                # FIX #4: Remove vector from FAISS on cleanup so the
                # index doesn't grow unboundedly with stale entries.
                faiss_id = self.vehicle_to_faiss_id.pop(vid, None)
                if faiss_id is not None:
                    self.index.remove_ids(np.array([faiss_id], dtype=np.int64))
                del self.vehicles[vid]

    # =========================================================
    # PUBLIC: GET FULL VEHICLE DATA
    # =========================================================
    def get_vehicle_data(self, vehicle_id):
        return self.vehicles.get(vehicle_id)