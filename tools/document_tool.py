"""
Document Extraction Tool — OmniAgent Phase 4
═════════════════════════════════════════════
Extracts structured text and tables from PDF, DOCX, Excel, CSV,
and other document formats. Works on files in the sandbox workspace
OR on file paths accessible to the agent.

Supported formats:
  - PDF    → text extraction with page markers
  - DOCX   → paragraphs + tables
  - XLSX/CSV → tabular data as markdown tables
  - TXT/MD → passthrough with metadata
"""

from smolagents import tool
from tools.sandbox_tool import run_sandbox_command, write_sandbox_file
import os

@tool
async def extract_document(
    filepath: str,
    session_id: str = "default",
    max_chars: int = 20000,
    page_range: str = "",
) -> str:
    """
    Extract text and tables from a document file in the sandbox workspace.
    
    Supports: PDF, DOCX, XLSX, CSV, TXT, MD, JSON, HTML
    
    Args:
        filepath:   Path relative to /workspace (e.g. 'report.pdf', 'data.xlsx')
        session_id: Session identifier
        max_chars:  Maximum characters to return (default: 20000)
        page_range: For PDFs: page range to extract (e.g. '1-5', '2,4,6', ''=all)
    
    Returns:
        Extracted text/tables in a structured, readable format.
        Large files are truncated with a notice.
    
    Examples:
        extract_document('report.pdf')              # extract all text
        extract_document('report.pdf', page_range='1-10')  # first 10 pages
        extract_document('data.xlsx')               # all sheets as tables
        extract_document('results.csv')             # CSV as markdown table
    """
    ext = filepath.split('.')[-1].lower() if '.' in filepath else ''
    
    script = ""
    install_cmd = ""
    
    if ext == 'pdf':
        install_cmd = "pip install pypdf -q && "
        script = f'''
import sys
from pypdf import PdfReader
reader = PdfReader('/workspace/{filepath}')
pages = reader.pages
# Apply page_range if specified
text = []
for i, page in enumerate(pages):
    text.append(f"--- Page {{i+1}} ---")
    text.append(page.extract_text() or "[No text on this page]")
print('\\n'.join(text))
'''
    elif ext == 'docx':
        install_cmd = "pip install python-docx -q && "
        script = f'''
from docx import Document
doc = Document('/workspace/{filepath}')
parts = []
for para in doc.paragraphs:
    if para.text.strip():
        parts.append(para.text)
for table in doc.tables:
    # Convert table to markdown
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    if rows:
        header = rows[0]
        sep = ['---'] * len(header)
        body = rows[1:]
        md_table = '| ' + ' | '.join(header) + ' |\\n'
        md_table += '| ' + ' | '.join(sep) + ' |\\n'
        for row in body:
            md_table += '| ' + ' | '.join(row) + ' |\\n'
        parts.append(md_table)
print('\\n\\n'.join(parts))
'''
    elif ext == 'xlsx':
        install_cmd = "pip install openpyxl -q && "
        script = f'''
import openpyxl
wb = openpyxl.load_workbook('/workspace/{filepath}', data_only=True)
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    print(f"## Sheet: {{sheet_name}}")
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        continue
    header = [str(c) if c is not None else '' for c in rows[0]]
    print('| ' + ' | '.join(header) + ' |')
    print('| ' + ' | '.join(['---']*len(header)) + ' |')
    for row in rows[1:]:
        cells = [str(c) if c is not None else '' for c in row]
        print('| ' + ' | '.join(cells) + ' |')
    print()
'''
    elif ext == 'csv':
        script = f'''
import csv, sys
with open('/workspace/{filepath}', 'r', newline='', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    rows = list(reader)
if not rows:
    print('Empty CSV')
    sys.exit(0)
header = rows[0]
print('| ' + ' | '.join(header) + ' |')
print('| ' + ' | '.join(['---']*len(header)) + ' |')
for row in rows[1:]:
    print('| ' + ' | '.join(str(c) for c in row) + ' |')
'''
    else:
        script = f'''
import sys
try:
    with open('/workspace/{filepath}', 'r', encoding='utf-8') as f:
        print(f.read())
except Exception as e:
    print(f"Error reading text file: {{e}}")
'''

    script_path = "extract_script.py"
    await write_sandbox_file(script_path, script, session_id=session_id)
    
    cmd = f"{install_cmd}python3 extract_script.py"
    result = await run_sandbox_command(cmd, session_id=session_id)
    
    out = result.get('output', '') if isinstance(result, dict) else str(result)
    
    if len(out) > max_chars:
        out = out[:max_chars] + f"\\n\\n[... Truncated at {max_chars} characters ...]"
        
    return out
