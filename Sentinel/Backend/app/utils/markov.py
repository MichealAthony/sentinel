def compute_markov_prediction(last_camera, transitions):
    predictions = []

    for t in transitions:
        if t["from"] == last_camera:
            predictions.append({
                "camera_id": t["to"],
                "probability": t["prob"]
            })

    return sorted(predictions, key=lambda x: x["probability"], reverse=True)