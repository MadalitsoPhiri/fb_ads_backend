from flask import Blueprint, request, jsonify
import os
import shutil
import uuid

file_upload = Blueprint("file_upload", __name__)
UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@file_upload.route("/upload_creatives", methods=["POST"])
def upload_creatives():
    """Endpoint to handle video uploads."""
    if 'uploadFolders' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist('uploadFolders')
    folder_id = str(uuid.uuid4())
    folder_path = os.path.join(UPLOAD_DIR, folder_id)
    os.makedirs(folder_path, exist_ok=True)

    for file in files:
        if file.filename.startswith('.'):  # Skip hidden files like `.DS_Store`
            continue
        
        # Ensure directory exists before saving
        file_dir = os.path.dirname(os.path.join(folder_path, file.filename))
        os.makedirs(file_dir, exist_ok=True)
        
        file_path = os.path.join(folder_path, file.filename)
        file.save(file_path)

    return jsonify({"message": "Upload successful", "folder_id": folder_id}), 200

@file_upload.route("/cancel_upload", methods=["POST"])
def cancel_upload():
    """Endpoint to cancel an ongoing upload."""
    data = request.json
    folder_id = data.get("folder_id")

    if not folder_id:
        return jsonify({"error": "Invalid folder ID"}), 400

    folder_path = os.path.join(UPLOAD_DIR, folder_id)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        return jsonify({"message": "Upload canceled and folder deleted"}), 200
    
    return jsonify({"error": "Folder not found"}), 404
