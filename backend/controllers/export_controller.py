"""
Export Controller - handles file export endpoints (Ecommerce version)

电商版本：不再支持 PPTX/PDF 导出，仅保留图片打包下载功能
"""
import logging
import os
import io
import zipfile
from datetime import datetime

from flask import Blueprint, request, current_app, send_file
from models import db, Project, Page, Task
from utils import (
    error_response, not_found, bad_request, success_response,
    parse_page_ids_from_query, parse_page_ids_from_body, get_filtered_pages
)
from services import FileService

logger = logging.getLogger(__name__)

export_bp = Blueprint('export', __name__, url_prefix='/api/projects')


@export_bp.route('/<project_id>/export/images', methods=['GET'])
def export_images_zip(project_id):
    """
    GET /api/projects/{project_id}/export/images?page_ids=id1,id2,id3 - Export all images as ZIP
    
    Query params:
        - page_ids: optional comma-separated page IDs to export (if not provided, exports all pages)
    
    Returns:
        ZIP file containing all generated images
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        # Get page_ids from query params and fetch filtered pages
        selected_page_ids = parse_page_ids_from_query(request)
        pages = get_filtered_pages(project_id, selected_page_ids if selected_page_ids else None)
        
        if not pages:
            return bad_request("No pages found for project")
        
        # Get image paths
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])
        
        image_files = []
        for page in pages:
            if page.generated_image_path:
                abs_path = file_service.get_absolute_path(page.generated_image_path)
                if os.path.exists(abs_path):
                    image_files.append((abs_path, f"page_{page.page_index:02d}.jpg"))
        
        if not image_files:
            return bad_request("No generated images found for project")
        
        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_path, arcname in image_files:
                zip_file.write(file_path, arcname)
        
        zip_buffer.seek(0)
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"images_{project_id}_{timestamp}.zip"
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    
    except Exception as e:
        logger.exception("Error exporting images")
        return error_response('SERVER_ERROR', str(e), 500)


@export_bp.route('/<project_id>/export/pptx', methods=['GET'])
def export_pptx(project_id):
    """
    PPTX export is not supported in ecommerce version.
    """
    return bad_request("PPTX export is not available in this version. Please use /export/images for image download.")


@export_bp.route('/<project_id>/export/pdf', methods=['GET'])
def export_pdf(project_id):
    """
    PDF export is not supported in ecommerce version.
    """
    return bad_request("PDF export is not available in this version. Please use /export/images for image download.")


@export_bp.route('/<project_id>/export/editable-pptx', methods=['POST'])
def export_editable_pptx(project_id):
    """
    Editable PPTX export is not supported in ecommerce version.
    """
    return bad_request("Editable PPTX export is not available in this version. Please use /export/images for image download.")
