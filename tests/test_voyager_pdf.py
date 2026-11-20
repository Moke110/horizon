import base64
import io
from pypdf import PdfWriter
from hostrun.voyager_materials import MaterialsService


def test_pdf_original_pages_and_replay_survive_restart(tmp_path):
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    stream=io.BytesIO();writer.write(stream);data=stream.getvalue()
    command=dict(schema_version=1,operation_id='pdf',title='Scanned page',source_label='User PDF',pdf_base64=base64.b64encode(data).decode())
    service=MaterialsService(tmp_path,token='s')
    result=service.register_pdf(command,token='s')
    assert result['pdf']['status']=='pending_text'
    assert result['pdf']['reason']=='scan_or_empty_page'
    assert result['pdf']['anchors'][0]['page']==1
    assert not result['pdf']['ocr']
    reopened=MaterialsService(tmp_path,token='s')
    assert reopened.register_pdf(command,token='s')==result
    assert reopened.pdf_original(result['material_id'],token='s')==data
    assert reopened.get_package(result['material_id'],token='s')['original_text']==''

def test_pdf_supplement_preserves_original_and_replays(tmp_path):
    data=b'%PDF-1.7\ninvalid'
    service=MaterialsService(tmp_path,token='s')
    result=service.register_pdf(dict(schema_version=1,operation_id='pdf',title='Broken',source_label='User',pdf_base64=base64.b64encode(data).decode()),token='s')
    assert result['pdf']['status']=='pending_text'
    command=dict(schema_version=1,operation_id='supplement',material_id=result['material_id'],expected_version=1,action='supplement',text='User supplied readable text')
    updated=service.resolve_pdf(command,token='s')
    assert updated['pdf']['status']=='supplemented'
    assert not updated['pdf']['supplement_has_page_mapping']
    assert service.resolve_pdf(command,token='s')==updated
    assert service.pdf_original(result['material_id'],token='s')==data
    assert service.get_package(result['material_id'],token='s')['original_text']=='User supplied readable text'
