# embedding_extractor.py

import torch
import torchreid
import numpy as np
import cv2
from PIL import Image


class EmbeddingExtractor:
    def __init__(self, device="cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"

        self.model = torchreid.models.build_model(
            name="osnet_x1_0",
            num_classes=1000,
            pretrained=True
        )

        self.model.to(self.device)
        self.model.eval()

        # IMPORTANT: unpack correctly
        _, test_transform = torchreid.data.transforms.build_transforms(
            height=256,
            width=128,
            is_train=False
        )

        self.transform = test_transform

    def extract(self, vehicle_crop):
        """
        vehicle_crop: OpenCV BGR numpy array
        returns: L2 normalized embedding vector
        """

        # Safety check
        if vehicle_crop is None or vehicle_crop.size == 0:
            return None

        # BGR -> RGB
        rgb = cv2.cvtColor(vehicle_crop, cv2.COLOR_BGR2RGB)

        # Convert numpy -> PIL (THIS FIXES YOUR ERROR)
        pil_img = Image.fromarray(rgb)

        # Apply transform (returns tensor CxHxW)
        img_tensor = self.transform(pil_img)

        # Add batch dimension
        img_tensor = img_tensor.unsqueeze(0).to(self.device)

        with torch.no_grad():
            features = self.model(img_tensor)

        embedding = features.cpu().numpy().flatten()

        # L2 normalization
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding