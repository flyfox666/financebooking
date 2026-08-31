from pydantic import BaseModel, ConfigDict


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    voucher_id: int
    original_filename: str
    content_type: str
    file_size: int
    sha256: str
    uploaded_by: int
