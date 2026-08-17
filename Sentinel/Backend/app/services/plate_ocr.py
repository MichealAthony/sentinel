from paddleocr import PaddleOCR
import cv2


class PlateOCR:
    def __init__(self):
        self.ocr = PaddleOCR(use_angle_cls=True, lang='en')

    def read(self, plate_crop):
        result = self.ocr.ocr(plate_crop)

        if not result or not result[0]:
            return None, 0.0

        text = ""
        confidence = 0.0

        for line in result[0]:
            text += line[1][0]
            confidence = max(confidence, line[1][1])

        return text.strip(), confidence