from flask import Blueprint, request, jsonify
from services import cancel_task

task_bp = Blueprint("task_bp", __name__)

@task_bp.route("/cancel_task", methods=["POST"])
def cancel_task_route():
    """
    Route to handle task cancellation via a POST request.
    Expects a JSON payload containing the 'task_id' to be canceled.
    """
    try:
        task_id = request.json.get("task_id")
        if not task_id:
            return jsonify({"error": "task_id is required"}), 400

        response = cancel_task(task_id)
        return jsonify(response), 200 if "message" in response else 500

    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500
